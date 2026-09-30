"""Typed views over registry entries.

Every managed thing in the registry -- a dataset, a distribution, a catalog,
an agent, a harvest report -- is an *entry*: an EntryStore envelope
(``contextId``/``entryId``, an info graph, an access-rights list) wrapping an
RDF metadata graph.

:class:`Entry` models that envelope. Subclasses add DCAT-AP-SE accessors on
top; :func:`wrap_entry` picks the right one from the metadata's ``rdf:type``.
"""

from __future__ import annotations

import datetime as _dt
import json as _json
from collections import namedtuple as _namedtuple
from collections.abc import Mapping as _Mapping
from collections.abc import Sequence as _ABCSequence
from typing import (
    Any,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Type,
    TypeVar,
    Union,
)

from .core import QueryError
from .rdf import (
    ADMS,
    DCAT,
    DCATAP,
    DCTERMS,
    ES,
    ESCAPE,
    FOAF,
    OWL,
    PROV,
    RDF,
    SCHEMA,
    SKOS,
    SPDX,
    VCARD,
    Graph,
    Resource,
    expand,
    slug_for,
)

__all__ = [
    "Entry",
    "Dataset",
    "Distribution",
    "DataService",
    "Agent",
    "ContactPoint",
    "ValueCount",
    "PeriodOfTime",
    "Breakdown",
    "ValueList",
    "BREAKDOWN_FILTERS",
    "text",
    "DATASET_FILTERS",
    "DATA_SERVICE_FILTERS",
    "Results",
    "SearchPage",
    "wrap_entry",
]

E = TypeVar("E", bound="Entry")

_MISSING = object()


#: The two languages a record is ever keyed by. The registry is Swedish and
#: publishes a partial English translation: over the whole corpus 53% of
#: datasets are Swedish only, 36% carry both, 10% are English only.
SWEDISH = "sv"
ENGLISH = "en"


def _fold(lang: Optional[str]) -> str:
    """Which of the two keys a literal's language tag belongs under.

    Publishers tag a fair amount of text ``und`` (undetermined) and the odd
    literal in a third language -- there is one Norwegian organisation name.
    Untagged or foreign-tagged text in a Swedish registry is Swedish, so
    everything that is not English folds into ``"sv"``. That keeps every
    localized field to exactly the two keys a reader can rely on.
    """
    if not lang:
        return SWEDISH
    return ENGLISH if lang.split("-")[0].lower() == ENGLISH else SWEDISH


def _langmap(values: Dict[Optional[str], Any]) -> Dict[str, Any]:
    """Localized values as ``{"sv": ..., "en": ...}``, folded and deduplicated.

    A key is present only when that language has something in it, so a
    Swedish-only title is ``{"sv": ...}`` rather than ``{"sv": ..., "en": None}``
    -- absence says "not translated" where ``None`` would say "translated to
    nothing".
    """
    out: Dict[str, Any] = {}
    for lang, value in values.items():
        if value is None or value == [] or value == "":
            continue
        key = _fold(lang)
        if key not in out:
            # Copied, so merging a second tag below cannot mutate the caller's.
            out[key] = list(value) if isinstance(value, list) else value
        elif isinstance(out[key], list) and isinstance(value, list):
            # Two tags folded together: merge rather than let one win.
            for item in value:
                if item not in out[key]:
                    out[key].append(item)
    return out


def text(value: Any, prefer: str = SWEDISH) -> Optional[str]:
    """One string out of a language map: the best there is.

        >>> text({"sv": "Vägtrafiknät", "en": "Road traffic network"})
        'Vägtrafiknät'
        >>> text({"en": "Road traffic network"})
        'Road traffic network'
        >>> text({}) is None
        True

    Every piece of publisher-written text in a record is a map, because 36% of
    datasets carry both languages and throwing one away would be a choice made
    for you. But 10% of them have no Swedish at all, so ``record["title"]["sv"]``
    is not safe to write -- this is.

    ``prefer="en"`` flips the order. Passing a plain string returns it
    unchanged, so it is safe on a field whose shape you are unsure of.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return value
    other = ENGLISH if prefer == SWEDISH else SWEDISH
    found = value.get(prefer) or value.get(other)
    if found is None:
        found = next((v for v in value.values() if v), None)
    return found


#: The shape a publisher or creator always has, even when there is none. A
#: record never hands back a bare None where a dict is documented.
_EMPTY_AGENT = {
    "uri": None,
    "context_id": None,
    "entry_id": None,
    "name": {},
    "type": None,
    "identifiers": [],
    "email": None,
    "homepage": None,
}


def _iso(value: Any) -> Optional[str]:
    """A date/datetime as an ISO-8601 string; anything else passed through."""
    if value is None:
        return None
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    return str(value)

#: Most specific first; used to pick the subject a graph is really about.
_TYPE_PRIORITY = [
    DCAT.DatasetSeries,
    DCAT.Dataset,
    DCAT.DataService,
    DCAT.Distribution,
    FOAF.Organization,
    FOAF.Agent,
]


def _primary_subject(graph: Graph) -> Optional[str]:
    """The subject of the most specific known type described in ``graph``."""
    for rdf_type in _TYPE_PRIORITY:
        subjects = graph.subjects_of_type(rdf_type)
        named = [s for s in subjects if not s.startswith("_:")]
        if named:
            return named[0]
        if subjects:
            return subjects[0]
    return None


class Entry:
    """One registry entry: the EntryStore envelope plus its metadata graph."""

    #: ``rdf:type`` values that :func:`wrap_entry` maps to this class.
    rdf_types: Sequence[str] = ()

    __slots__ = (
        "metadata",
        "info",
        "relations",
        "rights",
        "context_id",
        "entry_id",
        "_client",
        "_raw",
        "_resource",
    )

    def __init__(
        self,
        metadata: Graph,
        info: Optional[Graph] = None,
        relations: Optional[Graph] = None,
        rights: Sequence[str] = (),
        context_id: Optional[str] = None,
        entry_id: Optional[str] = None,
        client: Any = None,
        raw: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.metadata = metadata
        self.info = info if info is not None else Graph()
        self.relations = relations if relations is not None else Graph()
        self.rights: List[str] = list(rights)
        self.context_id = context_id
        self.entry_id = entry_id
        self._client = client
        self._raw = dict(raw) if raw is not None else None
        self._resource: Any = _MISSING

    # -- construction ------------------------------------------------------

    @classmethod
    def from_json(
        cls: Type[E],
        data: Mapping[str, Any],
        *,
        client: Any = None,
    ) -> E:
        """Build an entry from one ``resource.children[i]`` search hit."""
        return cls(
            metadata=Graph(data.get("metadata") or {}),
            info=Graph(data.get("info") or {}),
            relations=Graph(data.get("relations") or {}),
            rights=data.get("rights") or (),
            context_id=data.get("contextId"),
            entry_id=data.get("entryId"),
            client=client,
            raw=data,
        )

    def as_(self, model: Type[E]) -> E:
        """Reinterpret this entry through another model class."""
        return model(
            metadata=self.metadata,
            info=self.info,
            relations=self.relations,
            rights=self.rights,
            context_id=self.context_id,
            entry_id=self.entry_id,
            client=self._client,
            raw=self._raw,
        )

    @classmethod
    def from_resource(
        cls: Type[E],
        resource: Resource,
        *,
        client: Any = None,
        context_id: Optional[str] = None,
        entry_id: Optional[str] = None,
    ) -> E:
        """Wrap a subject that is already present in some graph.

        Used for entities that are delivered inline -- contact points, and
        distributions pulled in by a ``recursive=dcat`` fetch.
        """
        entry = cls(
            metadata=resource.graph,
            client=client,
            context_id=context_id,
            entry_id=entry_id,
        )
        entry._resource = resource
        return entry

    # -- envelope ----------------------------------------------------------

    @property
    def entry_uri(self) -> Optional[str]:
        """The registry's own URI for this entry (``.../store/<ctx>/entry/<id>``)."""
        subjects = self.info.named_subjects()
        return subjects[0] if subjects else None

    @property
    def entry_info(self) -> Optional[Resource]:
        uri = self.entry_uri
        return Resource(self.info, uri) if uri else None

    @property
    def resource_uri(self) -> Optional[str]:
        """The URI of the described thing -- the publisher's own dataset URI."""
        info = self.entry_info
        if info is not None:
            uri = info.uri_of(ES.resource)
            if uri:
                return uri
        # Without an envelope, fall back to whichever subject this entry is
        # about -- not simply the first subject, since one graph may describe
        # a dataset together with its distributions.
        subject = self.resource.subject
        return subject if subject and not subject.startswith("_:") else None

    @property
    def created(self) -> Optional[Union[_dt.date, _dt.datetime]]:
        """When the registry first harvested this entry."""
        info = self.entry_info
        return info.date(DCTERMS.created) if info else None

    @property
    def modified(self) -> Optional[Union[_dt.date, _dt.datetime]]:
        """When the registry last changed this entry."""
        info = self.entry_info
        return info.date(DCTERMS.modified) if info else None

    @property
    def creator(self) -> Optional[str]:
        info = self.entry_info
        return info.uri_of(DCTERMS.creator) if info else None

    @property
    def entry_type(self) -> Optional[str]:
        """``es:Link``, ``es:Local``, ``es:Reference``, ..."""
        info = self.entry_info
        return info.uri_of(RDF.type) if info else None

    @property
    def is_public(self) -> bool:
        """Whether the guest user may read this entry's metadata."""
        return "readmetadata" in self.rights

    # -- metadata ----------------------------------------------------------

    @property
    def resource(self) -> Resource:
        """The metadata subject that describes this entry's resource."""
        if self._resource is _MISSING:
            self._resource = self._find_resource()
        return self._resource

    def _find_resource(self) -> Resource:
        """Locate the subject this entry is *about*.

        A recursive fetch returns the dataset together with its distributions
        and publisher, so "the first subject" is not good enough: prefer the
        resource URI from the envelope, then this model's own rdf:type, then
        the most specific type present in the graph.
        """
        uri = None
        info = self.entry_info
        if info is not None:
            uri = info.uri_of(ES.resource)
        if uri and uri in self.metadata:
            return Resource(self.metadata, uri)
        for rdf_type in self.rdf_types:
            subjects = self.metadata.subjects_of_type(rdf_type)
            named = [s for s in subjects if not s.startswith("_:")]
            if named or subjects:
                return Resource(self.metadata, (named or subjects)[0])
        primary = _primary_subject(self.metadata)
        if primary is not None:
            return Resource(self.metadata, primary)
        named = self.metadata.named_subjects()
        if named:
            return Resource(self.metadata, named[0])
        subjects = self.metadata.subjects()
        return Resource(self.metadata, subjects[0] if subjects else uri or "")

    @property
    def uri(self) -> Optional[str]:
        """Alias of :attr:`resource_uri`."""
        return self.resource_uri

    @property
    def types(self) -> List[str]:
        return self.resource.types

    def is_a(self, rdf_type: str) -> bool:
        return self.resource.is_a(rdf_type)

    # Convenience passthroughs so simple cases never touch ``.resource``.
    def value(self, predicate: str, languages: Optional[Sequence[str]] = None) -> Optional[str]:
        return self.resource.value(predicate, languages)

    def values(self, predicate: str, lang: Optional[str] = None) -> List[str]:
        return self.resource.values(predicate, lang)

    def uris(self, predicate: str) -> List[str]:
        return self.resource.uris(predicate)

    @property
    def title(self) -> Optional[str]:
        return self.resource.value(DCTERMS.title)

    @property
    def titles(self) -> Dict[Optional[str], str]:
        return self.resource.localized(DCTERMS.title)

    @property
    def description(self) -> Optional[str]:
        return self.resource.value(DCTERMS.description)

    @property
    def descriptions(self) -> Dict[Optional[str], str]:
        return self.resource.localized(DCTERMS.description)

    # -- linked entries ----------------------------------------------------

    def _require_client(self) -> Any:
        if self._client is None:
            raise RuntimeError(
                "this entry was built without a client; fetch it through "
                "Dataportal(...) to follow references"
            )
        return self._client

    def fetch(self, uri: str) -> Optional["Entry"]:
        """Look up another managed entry by its resource URI."""
        found = self._require_client()._lookup_many([uri])
        return found[0] if found else None

    def fetch_many(self, uris: Sequence[str]) -> List["Entry"]:
        """Look up several managed entries in as few requests as possible."""
        return self._require_client()._lookup_many(list(uris))

    # -- output ------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """A plain, JSON-serializable dict of this entry.

        This is the package's primary output: no RDF terms, no URI-only
        vocabulary values, no objects that :func:`json.dumps` chokes on.
        Dates are ISO-8601 strings and every controlled-vocabulary field is
        ``{"uri": ..., "label": ...}``.

        Subclasses shape this per entity type; the base gives the envelope
        plus whatever title and description are present.
        """
        return dict(self._envelope_dict(), **{
            "title": self._text(self.titles),
            "description": self._text(self.descriptions),
            "types": self.types,
        })

    def _envelope_dict(self) -> Dict[str, Any]:
        return {
            "uri": self.resource_uri,
            "context_id": self.context_id,
            "entry_id": self.entry_id,
        }

    def _text(self, values: Dict[Optional[str], Any], empty: Any = None) -> Any:
        """Localized values as ``{"sv": ..., "en": ...}``.

        There is no language setting: the record carries what the publisher
        wrote, in both languages when both exist. ``empty`` is kept for the
        few callers that want ``None`` over ``{}`` for a field with nothing
        in it at all.
        """
        mapped = _langmap(values)
        return mapped if mapped else ({} if empty is None else empty)

    def _term(self, uri: Optional[str]) -> Optional[str]:
        """One controlled value as a short name: ``"local_authority"``.

        Not a URI and not an object -- see :mod:`dataportalen.rdf`.
        """
        return slug_for(uri)

    def _terms(self, uris: Sequence[str]) -> List[str]:
        out = []
        for uri in uris:
            slug = slug_for(uri)
            if slug and slug not in out:
                out.append(slug)
        return out

    def _publisher_dict(self) -> Optional[Dict[str, Any]]:
        """The publishing organisation, named when the graph describes it.

        A search hit carries only the publisher URI; a ``recursive=True``
        fetch carries the agent too, in which case the name comes along for
        free instead of costing another request.
        """
        uri = self.resource.uri_of(DCTERMS.publisher)
        if not uri:
            # Never None. 10 datasets and 16 data services name no publisher
            # at all, and every documented way of reading one subscripts it --
            # `text(record["publisher"]["name"])` would raise on exactly those.
            # An empty value is `{}` or None inside the dict, as everywhere
            # else in a record, so the shape is the same for all of them.
            return dict(_EMPTY_AGENT)
        for ref in self.resource.refs(DCTERMS.publisher):
            return Agent.from_resource(ref, client=self._client).to_dict()
        return dict(_EMPTY_AGENT, uri=uri)

    def to_json(self, indent: Optional[int] = None) -> str:
        """:meth:`to_dict` rendered as a JSON string."""
        return _json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def raw_json(self) -> Dict[str, Any]:
        """The registry's own JSON payload for this entry, untouched.

        The escape hatch for anything :meth:`to_dict` does not surface.
        """
        if self._raw is not None:
            return dict(self._raw)
        return {
            "contextId": self.context_id,
            "entryId": self.entry_id,
            "rights": list(self.rights),
            "info": self.info.to_json(),
            "metadata": self.metadata.to_json(),
            "relations": self.relations.to_json(),
        }

    def to_rdf(self) -> Dict[str, Any]:
        """The metadata graph as RDF/JSON, for callers who want the triples."""
        return self.metadata.to_json()

    def to_rdf_dict(self) -> Dict[str, List[Any]]:
        """Every predicate on this entry's subject, keyed by CURIE.

        Lossy but complete-ish: useful when a publisher uses a predicate the
        typed accessors do not cover.
        """
        return self.resource.to_dict()

    def __repr__(self) -> str:
        label = self.title or self.resource_uri or self.entry_uri or "?"
        return "<%s %s/%s %r>" % (type(self).__name__, self.context_id, self.entry_id, label)


# --- supporting (non-managed) structures ------------------------------------


class _Wrapped:
    """A thin wrapper around a :class:`Resource` describing a nested node."""

    __slots__ = ("resource",)

    def __init__(self, resource: Resource) -> None:
        self.resource = resource

    @property
    def uri(self) -> str:
        return self.resource.uri

    def to_dict(self) -> Dict[str, Any]:
        return self.resource.to_dict()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<%s %s>" % (type(self).__name__, self.uri)


class PeriodOfTime(_Wrapped):
    """``dcterms:PeriodOfTime`` -- a dataset's temporal coverage.

    Publishers use ``dcat:startDate``/``endDate`` or the ``schema.org``
    equivalents, and some give only a year. :attr:`start` and :attr:`end`
    therefore return a date when one can be parsed and the raw lexical value
    otherwise; :attr:`start_value`/:attr:`end_value` always give the raw text.
    """

    def _bound(
        self, dcat_term: str, schema_term: str
    ) -> Optional[Union[_dt.date, _dt.datetime, str]]:
        for predicate in (dcat_term, schema_term):
            parsed = self.resource.date(predicate)
            if parsed is not None:
                return parsed
            values = self.resource.values(predicate)
            if values:
                return values[0]
        return None

    @property
    def start(self) -> Optional[Union[_dt.date, _dt.datetime, str]]:
        return self._bound(DCAT.startDate, SCHEMA.startDate)

    @property
    def end(self) -> Optional[Union[_dt.date, _dt.datetime, str]]:
        return self._bound(DCAT.endDate, SCHEMA.endDate)

    @property
    def start_value(self) -> Optional[str]:
        values = self.resource.values(DCAT.startDate) or self.resource.values(SCHEMA.startDate)
        return values[0] if values else None

    def to_dict(self):
        """``{"start": ..., "end": ...}`` as ISO strings where parseable."""
        return {"start": _iso(self.start), "end": _iso(self.end)}

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<PeriodOfTime %s..%s>" % (self.start, self.end)


class Checksum(_Wrapped):
    """``spdx:Checksum`` on a distribution."""

    @property
    def algorithm(self) -> Optional[str]:
        return self.resource.uri_of(SPDX.algorithm)

    @property
    def value(self) -> Optional[str]:
        return self.resource.value(SPDX.checksumValue)

    def to_dict(self):
        return {"algorithm": self.algorithm, "value": self.value}


class ContactPoint(Entry):
    """A ``vcard:Kind`` contact point.

    Contact points usually live inline in a dataset's graph as blank nodes,
    so this is normally built via :attr:`Dataset.contact_points` rather than
    fetched on its own.
    """

    rdf_types = (VCARD.Organization, VCARD.Organisation, VCARD.Individual, VCARD.Kind)

    @property
    def name(self) -> Optional[str]:
        return self.resource.value(VCARD.fn) or self.resource.value(VCARD["organization-name"])

    @property
    def email(self) -> Optional[str]:
        """The contact e-mail, with any ``mailto:`` prefix removed."""
        for candidate in self.emails:
            return candidate
        return None

    @property
    def emails(self) -> List[str]:
        out: List[str] = []
        for uri in self.resource.uris(VCARD.hasEmail):
            out.append(uri[len("mailto:"):] if uri.lower().startswith("mailto:") else uri)
        for text in self.resource.values(VCARD.hasEmail):
            out.append(text[len("mailto:"):] if text.lower().startswith("mailto:") else text)
        for ref in self.resource.refs(VCARD.hasEmail):
            value = ref.value(VCARD.value) or ref.uri_of(VCARD.value)
            if value:
                out.append(value[len("mailto:"):] if value.lower().startswith("mailto:") else value)
        return out

    @property
    def url(self) -> Optional[str]:
        return self.resource.uri_of(VCARD.hasURL)

    @property
    def title(self) -> Optional[str]:  # type: ignore[override]
        return self.name or super().title

    def to_dict(self):
        return {
            "uri": None if self.resource.is_bnode else self.resource.uri,
            "name": self.name,
            "email": self.email,
        }

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<ContactPoint %r %s>" % (self.name, self.email or "")


# --- primary DCAT-AP-SE classes ---------------------------------------------


class Agent(Entry):
    """``foaf:Agent`` -- a publisher, creator or contact organisation."""

    rdf_types = (FOAF.Agent, FOAF.Organization, PROV.Agent)

    @property
    def name(self) -> Optional[str]:
        return self.resource.value(FOAF.name) or self.resource.value(DCTERMS.title)

    @property
    def names(self) -> Dict[Optional[str], str]:
        names = self.resource.localized(FOAF.name)
        if not names:
            names = self.resource.localized(DCTERMS.title)
        return names

    @property
    def title(self) -> Optional[str]:  # type: ignore[override]
        return self.name

    @property
    def homepage(self) -> Optional[str]:
        """``foaf:homepage``, falling back to ``foaf:page``."""
        return self.resource.uri_of(FOAF.homepage) or self.resource.uri_of(FOAF.page)

    @property
    def mbox(self) -> Optional[str]:
        uri = self.resource.uri_of(FOAF.mbox)
        if uri and uri.lower().startswith("mailto:"):
            return uri[len("mailto:"):]
        return uri

    @property
    def agent_type(self) -> Optional[str]:
        """``dcterms:type`` -- e.g. the EU authority type of the organisation."""
        return self.resource.uri_of(DCTERMS.type)

    @property
    def identifiers(self) -> List[str]:
        """Organisation identifiers (``dcterms:identifier``, ``adms:identifier``)."""
        out = self.resource.values(DCTERMS.identifier)
        out += self.resource.uris(DCTERMS.identifier)
        for ref in self.resource.refs(ADMS.identifier):
            value = ref.value(SKOS.notation) or ref.value(DCTERMS.identifier)
            if value:
                out.append(value)
        return out

    def to_dict(self):
        return dict(self._envelope_dict(), **{
            "name": self._text(self.names),
            "type": self._term(self.agent_type),
            "homepage": self.homepage,
            "email": self.mbox,
            "identifiers": self.identifiers,
        })

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<Agent %r %s>" % (self.name, self.resource_uri or "")


class Distribution(Entry):
    """``dcat:Distribution`` -- one downloadable or accessible form of a dataset."""

    rdf_types = (DCAT.Distribution,)

    @property
    def access_url(self) -> Optional[str]:
        urls = self.access_urls
        return urls[0] if urls else None

    @property
    def access_urls(self) -> List[str]:
        return self.resource.uris(DCAT.accessURL)

    @property
    def download_url(self) -> Optional[str]:
        urls = self.download_urls
        return urls[0] if urls else None

    @property
    def download_urls(self) -> List[str]:
        return self.resource.uris(DCAT.downloadURL)

    @property
    def format(self) -> Optional[str]:
        """``dcterms:format`` -- usually an EU file-type authority URI."""
        return self.resource.uri_of(DCTERMS.format) or self.resource.value(DCTERMS.format)

    @property
    def media_type(self) -> Optional[str]:
        return self.resource.uri_of(DCAT.mediaType) or self.resource.value(DCAT.mediaType)

    @property
    def byte_size(self) -> Optional[int]:
        return self.resource.integer(DCAT.byteSize)

    @property
    def license(self) -> Optional[str]:
        return self.resource.uri_of(DCTERMS.license)

    @property
    def access_service_uris(self) -> List[str]:
        """``dcat:accessService`` -- data services serving this distribution."""
        return self.resource.uris(DCAT.accessService)

    @property
    def conforms_to(self) -> List[str]:
        return self.resource.uris(DCTERMS.conformsTo)

    @property
    def checksum(self) -> Optional[Checksum]:
        ref = self.resource.ref(SPDX.checksum)
        return Checksum(ref) if ref is not None else None

    @property
    def language_uris(self) -> List[str]:
        return self.resource.uris(DCTERMS.language)

    @property
    def issued(self) -> Optional[Union[_dt.date, _dt.datetime]]:
        return self.resource.date(DCTERMS.issued)

    @property
    def modified_date(self) -> Optional[Union[_dt.date, _dt.datetime]]:
        return self.resource.date(DCTERMS.modified)

    @property
    def status(self) -> Optional[str]:
        return self.resource.uri_of(ADMS.status)

    @property
    def availability(self) -> Optional[str]:
        """``dcatap:availability`` -- how long the distribution is guaranteed."""
        return self.resource.uri_of(DCATAP.availability)

    def to_dict(self):
        return dict(self._envelope_dict(), **{
            "title": self._text(self.titles),
            "description": self._text(self.descriptions),
            "access_url": self.access_urls,
            "download_url": self.download_urls,
            "format": self._term(self.format),
            "license": self._term(self.license),
            "status": self._term(self.status),
            "availability": self._term(self.availability),
            "languages": self._terms(self.language_uris),
            "issued": _iso(self.issued),
            "modified": _iso(self.modified_date),
            "access_service_uris": self.access_service_uris,
        })

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<Distribution %r %s>" % (self.title, self.download_url or self.access_url or "")


class DataService(Entry):
    """``dcat:DataService`` -- an API that serves datasets."""

    rdf_types = (DCAT.DataService, ESCAPE.IndependentDataService)

    @property
    def endpoint_url(self) -> Optional[str]:
        urls = self.endpoint_urls
        return urls[0] if urls else None

    @property
    def endpoint_urls(self) -> List[str]:
        return self.resource.uris(DCAT.endpointURL)

    @property
    def endpoint_description_uris(self) -> List[str]:
        return self.resource.uris(DCAT.endpointDescription)

    @property
    def serves_dataset_uris(self) -> List[str]:
        return self.resource.uris(DCAT.servesDataset)

    @property
    def service_type(self) -> Optional[str]:
        """What kind of service, from ``dcterms:type``.

        Present on 56% of the 599 in the registry, over five values:
        ``rest`` for the 288 tagged with the Wikidata term, and the INSPIRE
        ``view_service`` (31), ``download_service`` (14),
        ``transformation_service`` and ``discovery_service`` (1 each).
        """
        return slug_for(self.resource.uri_of(DCTERMS.type))

    @property
    def keywords_by_language(self) -> Dict[Optional[str], List[str]]:
        out: Dict[Optional[str], List[str]] = {}
        for lit in self.resource.literals(DCAT.keyword):
            out.setdefault(lit.lang, []).append(lit.value)
        return out

    @property
    def creator_uris(self) -> List[str]:
        return self.resource.uris(DCTERMS.creator)

    @property
    def publisher_uri(self) -> Optional[str]:
        return self.resource.uri_of(DCTERMS.publisher)

    @property
    def theme_uris(self) -> List[str]:
        return self.resource.uris(DCAT.theme)

    @property
    def license(self) -> Optional[str]:
        return self.resource.uri_of(DCTERMS.license)

    @property
    def access_rights(self) -> Optional[str]:
        return self.resource.uri_of(DCTERMS.accessRights)

    @property
    def landing_page(self) -> Optional[str]:
        return self.resource.uri_of(DCAT.landingPage)

    @property
    def conforms_to(self) -> List[str]:
        return self.resource.uris(DCTERMS.conformsTo)

    @property
    def contact_point_uris(self) -> List[str]:
        return self.resource.uris(DCAT.contactPoint)

    @property
    def contact_points(self) -> List[ContactPoint]:
        """Contact points described in this service's own graph."""
        return _contact_points(self)

    def publisher(self) -> Optional[Agent]:
        uri = self.publisher_uri
        if not uri:
            return None
        found = self.fetch(uri)
        return found.as_(Agent) if found else None

    def to_dict(self):
        """This data service as a plain dict, shaped like a dataset record.

        The same keys mean the same things, so a reader does not have to learn
        two shapes -- but a data service has no distributions, no periodicity
        and no spatial coverage, and those keys are absent rather than empty.
        """
        return dict(self._envelope_dict(), **{
            "type": "data_service",
            "title": self._text(self.titles),
            "description": self._text(self.descriptions),
            "keywords": self._text(self.keywords_by_language, empty=[]),
            "service_type": self.service_type,
            "endpoint_url": self.endpoint_url,
            "endpoint_urls": self.endpoint_urls,
            "endpoint_descriptions": self.endpoint_description_uris,
            "serves_dataset_uris": self.serves_dataset_uris,
            "conforms_to": self.conforms_to,
            "publisher": self._publisher_dict(),
            "creators": [{"uri": uri} for uri in self.creator_uris],
            "themes": self._terms(self.theme_uris),
            "license": self._term(self.license),
            "access_rights": self._term(self.access_rights),
            "landing_page": self.landing_page,
            "contact_points": [c.to_dict() for c in self.contact_points],
        })

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<DataService %r %s>" % (self.title, self.endpoint_url or "")


def _contact_points(entry: Entry) -> List[ContactPoint]:
    out: List[ContactPoint] = []
    for ref in entry.resource.refs(DCAT.contactPoint):
        out.append(ContactPoint.from_resource(ref, client=entry._client))
    return out


class Dataset(Entry):
    """``dcat:Dataset`` -- the central class of the registry."""

    rdf_types = (DCAT.Dataset,)

    # -- descriptive -------------------------------------------------------

    @property
    def keywords(self) -> Dict[str, List[str]]:
        """``dcat:keyword`` values as ``{"sv": [...], "en": [...]}``.

        Keywords are the field publishers tag most erratically -- a quarter of
        them arrive ``und`` -- so the fold in :func:`_langmap` does real work
        here.
        """
        return _langmap(self.keywords_by_language)

    @property
    def keywords_by_language(self) -> Dict[Optional[str], List[str]]:
        out: Dict[Optional[str], List[str]] = {}
        for lit in self.resource.literals(DCAT.keyword):
            out.setdefault(lit.lang, []).append(lit.value)
        return out

    @property
    def identifier(self) -> Optional[str]:
        values = self.resource.values(DCTERMS.identifier)
        if values:
            return values[0]
        return self.resource.uri_of(DCTERMS.identifier)

    @property
    def landing_page(self) -> Optional[str]:
        return self.resource.uri_of(DCAT.landingPage)

    @property
    def theme_uris(self) -> List[str]:
        """``dcat:theme`` -- data theme / category URIs."""
        return self.resource.uris(DCAT.theme)

    @property
    def subject_uris(self) -> List[str]:
        return self.resource.uris(DCTERMS.subject)

    @property
    def language_uris(self) -> List[str]:
        return self.resource.uris(DCTERMS.language)

    @property
    def spatial_uris(self) -> List[str]:
        return self.resource.uris(DCTERMS.spatial)

    @property
    def accrual_periodicity(self) -> Optional[str]:
        return self.resource.uri_of(DCTERMS.accrualPeriodicity)

    @property
    def access_rights(self) -> Optional[str]:
        return self.resource.uri_of(DCTERMS.accessRights)

    @property
    def license(self) -> Optional[str]:
        return self.resource.uri_of(DCTERMS.license)

    @property
    def version(self) -> Optional[str]:
        return self.resource.value(OWL.versionInfo) or self.resource.value(DCAT.version)

    @property
    def provenance(self) -> List[str]:
        return self.resource.values(DCTERMS.provenance) + self.resource.uris(DCTERMS.provenance)

    @property
    def conforms_to(self) -> List[str]:
        return self.resource.uris(DCTERMS.conformsTo)

    @property
    def source_uris(self) -> List[str]:
        return self.resource.uris(DCTERMS.source)

    @property
    def hvd_categories(self) -> List[str]:
        """``dcatap:hvdCategory`` -- high-value dataset categories."""
        return self.resource.uris(DCATAP.hvdCategory)

    @property
    def applicable_legislation(self) -> List[str]:
        return self.resource.uris(DCATAP.applicableLegislation)

    # -- dates -------------------------------------------------------------

    @property
    def issued(self) -> Optional[Union[_dt.date, _dt.datetime]]:
        return self.resource.date(DCTERMS.issued)

    @property
    def modified_date(self) -> Optional[Union[_dt.date, _dt.datetime]]:
        """``dcterms:modified`` from the publisher (not the harvest time)."""
        return self.resource.date(DCTERMS.modified)

    @property
    def temporal(self) -> Optional[PeriodOfTime]:
        ref = self.resource.ref(DCTERMS.temporal)
        return PeriodOfTime(ref) if ref is not None else None

    @property
    def temporal_resolution(self) -> Optional[str]:
        return self.resource.value(DCAT.temporalResolution)

    @property
    def spatial_resolution_in_meters(self) -> Optional[float]:
        value = self.resource.python(DCAT.spatialResolutionInMeters)
        return float(value) if isinstance(value, (int, float)) else None

    # -- relationships -----------------------------------------------------

    @property
    def publisher_uri(self) -> Optional[str]:
        return self.resource.uri_of(DCTERMS.publisher)

    @property
    def creator_uris(self) -> List[str]:
        return self.resource.uris(DCTERMS.creator)

    @property
    def distribution_uris(self) -> List[str]:
        return self.resource.uris(DCAT.distribution)

    @property
    def in_series_uris(self) -> List[str]:
        return self.resource.uris(DCAT.inSeries)

    @property
    def is_part_of_uris(self) -> List[str]:
        return self.resource.uris(DCTERMS.isPartOf)

    @property
    def contact_point_uris(self) -> List[str]:
        """Every ``dcat:contactPoint`` reference, resolved or not."""
        return self.resource.uris(DCAT.contactPoint)

    @property
    def contact_points(self) -> List[ContactPoint]:
        """Contact points described in this dataset's own graph.

        A search hit carries only the reference when the contact point is a
        URI rather than a blank node; use :meth:`fetch_contact_points` (or
        fetch the dataset with ``recursive=True``) to resolve those.
        """
        return _contact_points(self)

    def fetch_contact_points(self) -> List[ContactPoint]:
        """Contact points, resolving any that are referenced by URI only."""
        inline = _contact_points(self)
        described = {c.uri for c in inline}
        missing = [u for u in self.contact_point_uris if u not in described]
        if not missing:
            return inline
        return inline + [e.as_(ContactPoint) for e in self.fetch_many(missing)]

    # -- lazy lookups ------------------------------------------------------

    def publisher(self) -> Optional[Agent]:
        """Fetch the publishing agent (one extra request)."""
        uri = self.publisher_uri
        if not uri:
            return None
        found = self.fetch(uri)
        return found.as_(Agent) if found else None

    def distributions(self) -> List[Distribution]:
        """Fetch this dataset's distributions.

        A search hit carries only the distribution URIs; calling this resolves
        them in batches. The catalogue download avoids the round trips
        entirely by crawling every distribution once and splicing them in.
        """
        inline = [
            Distribution.from_resource(ref, client=self._client, context_id=self.context_id)
            for ref in self.resource.refs(DCAT.distribution)
            if ref.is_a(DCAT.Distribution)
        ]
        uris = self.distribution_uris
        if inline and len(inline) == len(uris):
            # A recursive fetch already delivered every distribution inline.
            return inline
        if not uris:
            return inline
        return [e.as_(Distribution) for e in self.fetch_many(uris)]

    def to_dict(self, distributions=True):
        """This dataset as a plain, JSON-serializable dict.

        One key per concept: ``title``, ``description`` and ``keywords`` are
        language maps, never a scalar plus a plural. Vocabulary URIs come back
        as short names -- ``"transport"``, not a URI -- dates as ISO strings,
        and the publisher and any inline distributions as nested dicts.

        Fields that are empty for ~97%+ of the registry are left out to keep
        the output workable -- ``version``, ``provenance``, ``subjects``,
        ``hvd_categories``, ``source_uris`` and similar. They remain available
        as typed properties on the model (``dataset.version``) and in
        :meth:`to_rdf_dict`, which holds everything the publisher supplied.

        ``distributions`` includes the distributions present in this entry's
        graph -- all of them after a ``recursive=True`` fetch, none of them
        for a plain search hit, where ``distribution_uris`` still lists the
        references.

        ``creators`` starts as ``[{"uri": ...}]`` here and is filled in with
        the name and type during a download, where the 146 distinct creator
        URIs across the corpus are resolved in about two batched requests.
        """
        temporal = self.temporal
        out = dict(self._envelope_dict(), **{
            "type": "dataset",
            "title": self._text(self.titles),
            "description": self._text(self.descriptions),
            "keywords": self._text(self.keywords_by_language, empty=[]),
            "identifier": self.identifier,
            "landing_page": self.landing_page,
            "publisher": self._publisher_dict(),
            "creators": [{"uri": uri} for uri in self.creator_uris],
            "themes": self._terms(self.theme_uris),
            "license": self._term(self.license),
            "access_rights": self._term(self.access_rights),
            "accrual_periodicity": self._term(self.accrual_periodicity),
            "languages": self._terms(self.language_uris),
            "spatial": self._terms(self.spatial_uris),
            "temporal": temporal.to_dict() if temporal else None,
            "issued": _iso(self.issued),
            "modified": _iso(self.modified_date),
            "contact_points": [c.to_dict() for c in self.contact_points],
        })
        if distributions:
            out["distributions"] = [
                Distribution.from_resource(ref, client=self._client).to_dict()
                for ref in self.resource.refs(DCAT.distribution)
                if ref.is_a(DCAT.Distribution)
            ]
        return out

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<Dataset %r %s/%s>" % (self.title, self.context_id, self.entry_id)


class ValueCount(_namedtuple("ValueCount", "value dataset_count")):
    """One value a filter accepts, and how many records carry it.

    Still a plain ``(value, dataset_count)`` pair, so it unpacks in a loop and
    compares equal to one::

        for value, count in page.breakdown["theme"]:
            ...

    :attr:`label` rides alongside rather than in the tuple -- ``{"sv": ...,
    "en": ...}``, from the vocabulary for a controlled value and from the
    records themselves for a publisher or creator. It is ``{}`` for values
    that are their own label, such as keywords.

        row = page.breakdown["publisher"][0]
        row.value                     # 'trafikverket', what you filter with
        row.label["sv"]               # 'Trafikverket', what you show

    It is not part of the tuple, so ``_replace`` and pickling drop it, and
    the class carries a ``__dict__`` to hold it -- a namedtuple subclass
    cannot use ``__slots__``.
    """

    def __new__(cls, value, dataset_count, label=None):
        row = super().__new__(cls, value, dataset_count)
        row.label = label or {}
        return row


# --- search results ---------------------------------------------------------


#: What a dataset can be filtered and broken down by. Every one was measured
#: over all 23,575 datasets: publisher and license are on 100% of them,
#: keyword 94.8%, language 89.3%, access_rights 82.3%, theme 78.2%, format
#: 69.7%, updated 63.3%, creator 30.1%, place 23.6%.
DATASET_FILTERS = ("publisher", "publisher_type", "creator", "theme",
                   "keyword", "format", "license", "access_rights",
                   "updated", "language", "place", "link")

#: The same for a data service, and it is a different list. Over all 599:
#: access_rights 97.8%, publisher 97.3%, keyword 83.5%, service_type 55.9%,
#: theme 53.8%, license 51.8%. The four that are missing are missing for a
#: reason -- a data service has no distributions (so no `format`) and no
#: `accrual_periodicity` (no `updated`), `place` is set on 7.8% of them and
#: `language` has one single value across all 599.
DATA_SERVICE_FILTERS = ("publisher", "publisher_type", "creator",
                        "service_type", "theme", "keyword", "license",
                        "access_rights", "link")

#: Kept as the union, for code that asks "is this a filter at all".
BREAKDOWN_FILTERS = DATASET_FILTERS


class ValueList(list):
    """The values for one filter, with a count of any left out.

    A plain list of ``(value, dataset_count)`` pairs::

        for value, count in page.breakdown["publisher"]:
            ...

    ``omitted`` is how many further values there were, above whatever
    ``breakdown_limit`` the search was given -- 0 when nothing was cut.
    """

    __slots__ = ("omitted",)

    def __init__(self, values: Sequence["ValueCount"] = (), omitted: int = 0) -> None:
        super().__init__(values)
        self.omitted = omitted

    def __repr__(self) -> str:                            # pragma: no cover
        more = " +%d more" % self.omitted if self.omitted else ""
        return "<ValueList %d%s>" % (len(self), more)


class Breakdown(_Mapping):
    """What a search result is made of, per filter, biggest first.

    Every filter you can search by, counted over everything that matched --
    not just the rows you are holding::

        page = dp.datasets(text="cykel")
        page.total                      # 388
        page.breakdown["publisher"]     # [('trafikverket', 88), ...]
        page.breakdown["theme"]         # [('transport', 201), ...]

    Each value is one you can feed straight back in to narrow the search::

        dp.datasets(text="cykel", publisher="trafikverket")

    Counts are per dataset: a dataset with three CSV files counts once under
    ``format`` -> ``csv``.
    """

    __slots__ = ("_counts",)

    def __init__(
        self,
        counts: Mapping[str, Sequence["ValueCount"]],
        limit: Optional[int] = None,
    ) -> None:
        self._counts = {}
        for name in counts:
            values = list(counts[name])
            if limit is not None and len(values) > limit:
                self._counts[name] = ValueList(values[:limit], len(values) - limit)
            else:
                self._counts[name] = ValueList(values)

    @property
    def omitted(self) -> Dict[str, int]:
        """``{filter: how many values were cut}``, for the ones that were.

        Empty unless the search was given a ``breakdown_limit``.
        """
        return {name: values.omitted
                for name, values in self._counts.items() if values.omitted}

    def __getitem__(self, filter: str) -> "ValueList":
        if filter in self._counts:
            return self._counts[filter]
        raise QueryError(
            "nothing is broken down by %r; this result knows: %s"
            % (filter, ", ".join(self._counts)))

    def __contains__(self, filter: object) -> bool:
        # Mapping's default catches KeyError, and __getitem__ raises
        # QueryError -- so `"format" in breakdown` would propagate instead of
        # answering False.
        return filter in self._counts

    def __iter__(self) -> Iterator[str]:
        return iter(self._counts)

    def __len__(self) -> int:
        return len(self._counts)

    def top(self, filter: str) -> Optional["ValueCount"]:  # noqa: D401
        """The commonest value for one filter, or ``None`` if there is none."""
        found = self[filter]
        return found[0] if found else None

    def to_dict(self) -> Dict[str, Dict[str, int]]:
        """``{filter: {value: count}}``, JSON-serializable and still ordered.

        Any values cut by a ``breakdown_limit`` are counted in
        :attr:`omitted` rather than here.
        """
        return {
            name: {value: count for value, count in values}
            for name, values in self._counts.items()
        }

    def __repr__(self) -> str:                            # pragma: no cover
        return "<Breakdown %s>" % " ".join(
            "%s=%d" % (name, len(values)) for name, values in self._counts.items())


class Results(list):
    """What a search gives you: a list of dataset dicts, and the total.

    It *is* a list -- index it, slice it, loop over it, pass it to
    ``pandas.DataFrame`` -- and it carries what the registry said about the
    wider result::

        page = dp.datasets(theme="transport")
        len(page)        # what you got, at most `limit`
        page.total       # how many matched altogether
        page.has_more    # whether anything follows
        page.breakdown   # what all of them are made of, per filter

    The same type comes back whether the search ran against the local
    catalogue or the registry, so code does not care which it used.
    """

    __slots__ = ("total", "offset", "limit", "facets", "breakdown")

    def __init__(
        self,
        records: Sequence[Dict[str, Any]] = (),
        total: Optional[int] = None,
        offset: int = 0,
        limit: Optional[int] = None,
        facets: Sequence[Any] = (),
        breakdown: Optional[Breakdown] = None,
    ) -> None:
        super().__init__(records)
        self.total = len(self) if total is None else int(total)
        self.offset = offset
        self.limit = limit
        self.facets = list(facets)
        #: What everything that matched is made of -- see :class:`Breakdown`.
        self.breakdown = breakdown if breakdown is not None else Breakdown({})

    @property
    def has_more(self) -> bool:
        """Whether more matched than you are holding.

        Over the API ``total`` is the index's estimate, so treat it as a hint;
        against a local catalogue it is exact.
        """
        return self.offset + len(self) < self.total

    def facet(self, name: str) -> Optional[Any]:
        for facet in self.facets:
            if facet.name == name:
                return facet
        return None

    def __repr__(self) -> str:                            # pragma: no cover
        return "<Results %d of %d>" % (len(self), self.total)


class SearchPage(_ABCSequence):
    """One page of search results.

    Behaves like a list of entries and additionally carries ``total``,
    ``offset``, ``limit`` and any requested facets.
    """

    __slots__ = ("entries", "total", "offset", "limit", "facets", "raw", "_client", "_params")

    def __init__(
        self,
        entries: Sequence[Entry],
        total: int,
        offset: int,
        limit: int,
        facets: Sequence[Any] = (),
        raw: Optional[Mapping[str, Any]] = None,
        client: Any = None,
        params: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.entries = list(entries)
        self.total = total
        self.offset = offset
        self.limit = limit
        self.facets = list(facets)
        self.raw = dict(raw or {})
        self._client = client
        self._params = dict(params or {})

    def __getitem__(self, index):  # type: ignore[override]
        return self.entries[index]

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[Entry]:
        return iter(self.entries)

    @property
    def has_more(self) -> bool:
        """Whether another page exists.

        ``total`` is a Solr estimate that can exceed the number of entries the
        caller may actually read, so treat this as a hint.
        """
        return self.offset + len(self.entries) < self.total and bool(self.entries)

    def to_dict(self) -> Dict[str, Any]:
        """The whole page as a plain dict: totals, entries and facets."""
        return {
            "total": self.total,
            "offset": self.offset,
            "limit": self.limit,
            "count": len(self.entries),
            "has_more": self.has_more,
            "results": [entry.to_dict() for entry in self.entries],
        }

    def to_json(self, indent: Optional[int] = None) -> str:
        """:meth:`to_dict` rendered as a JSON string."""
        return _json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def next_page(self) -> Optional["SearchPage"]:
        """Fetch the following page, or ``None`` when exhausted."""
        if not self.has_more or self._client is None:
            return None
        params = dict(self._params)
        params["offset"] = self.offset + len(self.entries)
        return self._client._search(**params)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<SearchPage %d-%d of ~%d>" % (
            self.offset,
            self.offset + len(self.entries),
            self.total,
        )


# --- rdf:type -> model mapping ----------------------------------------------

MODEL_REGISTRY: Dict[str, Type[Entry]] = {}


def register_model(model: Type[Entry], *rdf_types: str) -> Type[Entry]:
    """Register a model class for one or more ``rdf:type`` URIs."""
    for rdf_type in rdf_types or model.rdf_types:
        MODEL_REGISTRY[expand(rdf_type)] = model
    return model


for _model in (
    Dataset,
    Distribution,
    DataService,
    Agent,
    ContactPoint,
):
    register_model(_model)

def wrap_entry(
    data: Mapping[str, Any],
    *,
    client: Any = None,
    default: Optional[Type[Entry]] = None,
) -> Entry:
    """Build the most specific :class:`Entry` subclass for a search hit."""
    base = Entry.from_json(data, client=client)
    if default is not None:
        return base.as_(default)
    types = set(base.resource.types)
    for rdf_type in _TYPE_PRIORITY:
        # `in MODEL_REGISTRY` rather than a bare lookup: _TYPE_PRIORITY names
        # dcat:DatasetSeries, which has no model, and the registry holds 13
        # of them. A hit would have raised KeyError instead of giving back a
        # plain Entry.
        if rdf_type in types and rdf_type in MODEL_REGISTRY:
            return base.as_(MODEL_REGISTRY[rdf_type])
    for rdf_type in types:
        model = MODEL_REGISTRY.get(rdf_type)
        if model is not None:
            return base.as_(model)
    return base
