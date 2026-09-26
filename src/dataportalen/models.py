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

from .namespaces import (
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
    STATS,
    VCARD,
    expand,
)
from .rdf import DEFAULT_LANGUAGES, Graph, Resource
from .terms import slug_for

__all__ = [
    "Entry",
    "Dataset",
    "DatasetSeries",
    "Distribution",
    "DataService",
    "Catalog",
    "Agent",
    "ContactPoint",
    "Standard",
    "LinkCheckReport",
    "MetadataQuality",
    "CatalogStatistics",
    "OrganisationStats",
    "PeriodOfTime",
    "Checksum",
    "SearchPage",
    "FacetValue",
    "Facet",
    "wrap_entry",
    "register_model",
    "MODEL_REGISTRY",
]

E = TypeVar("E", bound="Entry")

_MISSING = object()


#: BCP-47 for "undetermined". Literals with no language tag would otherwise
#: key a JSON object under `null`, which json.dumps renders as the string
#: "null" -- a value indistinguishable from a real language code.
UNDETERMINED = "und"


def _langmap(values: Dict[Optional[str], Any]) -> Dict[str, Any]:
    """A localized map with JSON-safe keys."""
    return {(lang or UNDETERMINED): value for lang, value in values.items()}


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
    DCAT.Catalog,
    DCAT.DataService,
    DCAT.Distribution,
    STATS.CatalogStatistics,
    ESCAPE.LinkCheckReport,
    ESCAPE.MQATotal,
    ESCAPE.MQA,
    DCTERMS.Standard,
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
        "languages",
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
        languages: Sequence[str] = DEFAULT_LANGUAGES,
        client: Any = None,
        raw: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.metadata = metadata
        self.info = info if info is not None else Graph()
        self.relations = relations if relations is not None else Graph()
        self.rights: List[str] = list(rights)
        self.context_id = context_id
        self.entry_id = entry_id
        self.languages = tuple(languages)
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
        languages: Sequence[str] = DEFAULT_LANGUAGES,
    ) -> E:
        """Build an entry from one ``resource.children[i]`` search hit."""
        return cls(
            metadata=Graph(data.get("metadata") or {}),
            info=Graph(data.get("info") or {}),
            relations=Graph(data.get("relations") or {}),
            rights=data.get("rights") or (),
            context_id=data.get("contextId"),
            entry_id=data.get("entryId"),
            languages=languages,
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
            languages=self.languages,
            client=self._client,
            raw=self._raw,
        )

    def with_languages(self: E, languages: Sequence[str]) -> E:
        """A copy that prefers a different language order."""
        clone = type(self)(
            metadata=self.metadata,
            info=self.info,
            relations=self.relations,
            rights=self.rights,
            context_id=self.context_id,
            entry_id=self.entry_id,
            languages=languages,
            client=self._client,
            raw=self._raw,
        )
        return clone

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
            languages=resource.languages,
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
        return Resource(self.info, uri, self.languages) if uri else None

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
    def metadata_uri(self) -> Optional[str]:
        info = self.entry_info
        return info.uri_of(ES.metadata) if info else None

    @property
    def relations_uri(self) -> Optional[str]:
        info = self.entry_info
        return info.uri_of(ES.relation) if info else None

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
    def contributors(self) -> List[str]:
        info = self.entry_info
        return info.uris(DCTERMS.contributor) if info else []

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
            return Resource(self.metadata, uri, self.languages)
        for rdf_type in self.rdf_types:
            subjects = self.metadata.subjects_of_type(rdf_type)
            named = [s for s in subjects if not s.startswith("_:")]
            if named or subjects:
                return Resource(self.metadata, (named or subjects)[0], self.languages)
        primary = _primary_subject(self.metadata)
        if primary is not None:
            return Resource(self.metadata, primary, self.languages)
        named = self.metadata.named_subjects()
        if named:
            return Resource(self.metadata, named[0], self.languages)
        subjects = self.metadata.subjects()
        return Resource(self.metadata, subjects[0] if subjects else uri or "", self.languages)

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
        return self._require_client().lookup(uri)

    def fetch_many(self, uris: Sequence[str]) -> List["Entry"]:
        """Look up several managed entries in as few requests as possible."""
        return self._require_client().lookup_many(uris)

    def reload(self, recursive: bool = True) -> "Entry":
        """Re-fetch this entry, optionally pulling in related entities."""
        client = self._require_client()
        if self.context_id is None or self.entry_id is None:
            raise RuntimeError("entry has no contextId/entryId to reload from")
        return client.entry(
            self.context_id,
            self.entry_id,
            recursive=recursive,
            model=type(self),
            info=self.info if self.info else None,
            rights=self.rights,
        )

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
            "title": _langmap(self.titles),
            "description": _langmap(self.descriptions),
            "types": self.types,
        })

    def _envelope_dict(self) -> Dict[str, Any]:
        return {
            "uri": self.resource_uri,
            "context_id": self.context_id,
            "entry_id": self.entry_id,
        }

    def _term(self, uri: Optional[str]) -> Optional[str]:
        """One controlled value as a short name: ``"local_authority"``.

        Not a URI and not an object -- see :mod:`dataportalen.terms`.
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
            return None
        for ref in self.resource.refs(DCTERMS.publisher):
            return Agent.from_resource(ref, client=self._client).to_dict()
        return {"uri": uri, "name": {}}

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

    @property
    def end_value(self) -> Optional[str]:
        values = self.resource.values(DCAT.endDate) or self.resource.values(SCHEMA.endDate)
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
    def telephone(self) -> Optional[str]:
        for uri in self.resource.uris(VCARD.hasTelephone):
            return uri[len("tel:"):] if uri.lower().startswith("tel:") else uri
        for ref in self.resource.refs(VCARD.hasTelephone):
            value = ref.value(VCARD.value) or ref.uri_of(VCARD.value)
            if value:
                return value[len("tel:"):] if value.lower().startswith("tel:") else value
        values = self.resource.values(VCARD.hasTelephone)
        return values[0] if values else None

    @property
    def url(self) -> Optional[str]:
        return self.resource.uri_of(VCARD.hasURL)

    @property
    def address(self) -> Optional[str]:
        return self.resource.value(VCARD.hasAddress) or self.resource.value(
            VCARD["street-address"]
        )

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
    def page_uris(self) -> List[str]:
        return self.resource.uris(FOAF.page)

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

    @property
    def same_as(self) -> List[str]:
        return self.resource.uris(OWL.sameAs)

    def to_dict(self):
        return dict(self._envelope_dict(), **{
            "name": _langmap(self.names),
            "type": self._term(self.agent_type),
            "homepage": self.homepage,
            "email": self.mbox,
            "identifiers": self.identifiers,
            "same_as": self.same_as,
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
    def rights_statements(self) -> List[str]:
        """``dcterms:rights`` -- rights statements about the distribution.

        Not to be confused with :attr:`Entry.rights`, which lists the caller's
        EntryStore access rights on the entry.
        """
        return self.resource.uris(DCTERMS.rights) + self.resource.values(DCTERMS.rights)

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

    @property
    def page_uris(self) -> List[str]:
        return self.resource.uris(FOAF.page)

    def access_services(self) -> List["DataService"]:
        """Fetch the data services referenced by ``dcat:accessService``."""
        uris = self.access_service_uris
        if not uris:
            return []
        return [e.as_(DataService) for e in self.fetch_many(uris)]

    def to_dict(self):
        return dict(self._envelope_dict(), **{
            "title": _langmap(self.titles),
            "description": _langmap(self.descriptions),
            "access_url": self.access_urls,
            "download_url": self.download_urls,
            "format": self._term(self.format),
            "license": self._term(self.license),
            "status": self._term(self.status),
            "availability": self._term(self.availability),
            "languages": self._terms(self.language_uris),
            "conforms_to": self.conforms_to,
            "issued": _iso(self.issued),
            "modified": _iso(self.modified_date),
            "access_service_uris": self.access_service_uris,
            "documentation": self.page_uris,
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

    def serves_datasets(self) -> List["Dataset"]:
        uris = self.serves_dataset_uris
        if not uris:
            return []
        return [e.as_(Dataset) for e in self.fetch_many(uris)]

    def to_dict(self):
        return dict(self._envelope_dict(), **{
            "title": _langmap(self.titles),
            "description": _langmap(self.descriptions),
            "endpoint_url": self.endpoint_url,
            "endpoint_urls": self.endpoint_urls,
            "endpoint_descriptions": self.endpoint_description_uris,
            "serves_dataset_uris": self.serves_dataset_uris,
            "publisher": self._publisher_dict(),
            "themes": self._terms(self.theme_uris),
            "license": self._term(self.license),
            "access_rights": self._term(self.access_rights),
            "landing_page": self.landing_page,
            "conforms_to": self.conforms_to,
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
    def keywords(self) -> List[str]:
        """``dcat:keyword`` values in the preferred language, with fallback."""
        preferred: List[str] = []
        for lang in self.languages:
            preferred = self.resource.values(DCAT.keyword, lang)
            if preferred:
                return preferred
        return self.resource.values(DCAT.keyword)

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
    def documentation_uris(self) -> List[str]:
        return self.resource.uris(FOAF.page)

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

    @property
    def contact_point(self) -> Optional[ContactPoint]:
        points = self.contact_points
        return points[0] if points else None

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
        them in batches. ``client.dataset(..., recursive=True)`` avoids the
        extra round trips by fetching the whole DCAT closure at once.
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

    def series(self) -> List["DatasetSeries"]:
        uris = self.in_series_uris
        if not uris:
            return []
        return [e.as_(DatasetSeries) for e in self.fetch_many(uris)]

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
        """
        temporal = self.temporal
        out = dict(self._envelope_dict(), **{
            "title": _langmap(self.titles),
            "description": _langmap(self.descriptions),
            "keywords": _langmap(self.keywords_by_language),
            "identifier": self.identifier,
            "landing_page": self.landing_page,
            "publisher": self._publisher_dict(),
            "creator_uris": self.creator_uris,
            "themes": self._terms(self.theme_uris),
            "license": self._term(self.license),
            "access_rights": self._term(self.access_rights),
            "accrual_periodicity": self._term(self.accrual_periodicity),
            "languages": self._terms(self.language_uris),
            "spatial": self._terms(self.spatial_uris),
            "temporal": temporal.to_dict() if temporal else None,
            "applicable_legislation": self._terms(self.applicable_legislation),
            "issued": _iso(self.issued),
            "modified": _iso(self.modified_date),
            "conforms_to": self.conforms_to,
            "documentation": self.documentation_uris,
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


class DatasetSeries(Dataset):
    """``dcat:DatasetSeries`` -- a group of related datasets."""

    rdf_types = (DCAT.DatasetSeries,)


class Catalog(Entry):
    """``dcat:Catalog`` -- one harvested organisation's catalog."""

    rdf_types = (DCAT.Catalog,)

    @property
    def publisher_uri(self) -> Optional[str]:
        return self.resource.uri_of(DCTERMS.publisher)

    @property
    def homepage(self) -> Optional[str]:
        """``foaf:homepage``, falling back to ``foaf:page``."""
        return self.resource.uri_of(FOAF.homepage) or self.resource.uri_of(FOAF.page)

    @property
    def page_uris(self) -> List[str]:
        return self.resource.uris(FOAF.page)

    @property
    def dataset_uris(self) -> List[str]:
        return self.resource.uris(DCAT.dataset)

    @property
    def service_uris(self) -> List[str]:
        return self.resource.uris(DCAT.service)

    @property
    def theme_taxonomy_uris(self) -> List[str]:
        return self.resource.uris(DCAT.themeTaxonomy)

    @property
    def language_uris(self) -> List[str]:
        return self.resource.uris(DCTERMS.language)

    @property
    def license(self) -> Optional[str]:
        return self.resource.uri_of(DCTERMS.license)

    @property
    def issued(self) -> Optional[Union[_dt.date, _dt.datetime]]:
        return self.resource.date(DCTERMS.issued)

    @property
    def modified_date(self) -> Optional[Union[_dt.date, _dt.datetime]]:
        return self.resource.date(DCTERMS.modified)

    def publisher(self) -> Optional[Agent]:
        uri = self.publisher_uri
        if not uri:
            return None
        found = self.fetch(uri)
        return found.as_(Agent) if found else None

    def datasets(self, limit: Optional[int] = None) -> List[Dataset]:
        """Datasets in this catalog's context (one search request per page)."""
        client = self._require_client()
        if self.context_id is None:
            return [e.as_(Dataset) for e in self.fetch_many(self.dataset_uris[:limit])]
        return list(client.datasets_in_context(self.context_id, limit=limit))

    def to_dict(self):
        return dict(self._envelope_dict(), **{
            "title": _langmap(self.titles),
            "description": _langmap(self.descriptions),
            "publisher": self._publisher_dict(),
            "homepage": self.homepage,
            "languages": self._terms(self.language_uris),
            "license": self._term(self.license),
            "theme_taxonomies": self.theme_taxonomy_uris,
            "dataset_uris": self.dataset_uris,
            "service_uris": self.service_uris,
            "issued": _iso(self.issued),
            "modified": _iso(self.modified_date),
        })

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<Catalog %r ctx=%s>" % (self.title, self.context_id)


class Standard(Entry):
    """``dcterms:Standard`` -- a specification referenced by ``conformsTo``."""

    rdf_types = (DCTERMS.Standard,)

    @property
    def page_uris(self) -> List[str]:
        return self.resource.uris(FOAF.page)


# --- registry-operational entities ------------------------------------------


class LinkCheckReport(Entry):
    """``escape:LinkCheckReport`` -- the nightly link check for one catalog.

    The registry follows every distribution's access and download URLs and
    records how many of them answered.
    """

    rdf_types = (ESCAPE.LinkCheckReport,)

    @property
    def checked(self) -> Optional[int]:
        """How many links were checked."""
        return self.resource.integer(ESCAPE.linkChecks)

    @property
    def failed(self) -> Optional[int]:
        """How many links did not answer."""
        return self.resource.integer(ESCAPE.failedLinkChecks)

    @property
    def excluded(self) -> Optional[int]:
        """How many links were skipped (e.g. an unsupported scheme)."""
        return self.resource.integer(ESCAPE.excludedLinkChecks)

    @property
    def succeeded(self) -> Optional[int]:
        checked, failed = self.checked, self.failed
        if checked is None or failed is None:
            return None
        return checked - failed

    @property
    def run_at(self) -> Optional[Union[_dt.date, _dt.datetime]]:
        return self.resource.date(DCTERMS.created)

    def to_dict(self):
        return dict(self._envelope_dict(), **{
            "checked": self.checked,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "excluded": self.excluded,
            "run_at": _iso(self.run_at),
        })

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<LinkCheckReport ctx=%s %s/%s ok>" % (
            self.context_id, self.succeeded, self.checked,
        )


class MetadataQuality(Entry):
    """``escape:MQA`` -- a catalog's metadata quality assessment.

    The registry scores each catalog against the DCAT-AP MQA methodology;
    ``escape:MQATotal`` carries the same fields for the whole repository.
    """

    rdf_types = (ESCAPE.MQA, ESCAPE.MQATotal)

    @property
    def score(self) -> Optional[int]:
        """Points scored (the MQA scale tops out at 405)."""
        return self.resource.integer(ESCAPE.score)

    @property
    def percentage(self) -> Optional[float]:
        value = self.resource.python(ESCAPE.percentage)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @property
    def rating(self) -> Optional[float]:
        value = self.resource.python(ESCAPE.rating)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @property
    def succeeded(self) -> Optional[bool]:
        return self.resource.boolean(ESCAPE.success)

    @property
    def is_total(self) -> bool:
        """Whether this is the repository-wide total rather than one catalog."""
        return self.resource.is_a(ESCAPE.MQATotal)

    @property
    def assessed_at(self) -> Optional[Union[_dt.date, _dt.datetime]]:
        return self.resource.date(DCTERMS.modified)

    def to_dict(self):
        return dict(self._envelope_dict(), **{
            "title": _langmap(self.titles),
            "score": self.score,
            "percentage": self.percentage,
            "rating": self.rating,
            "succeeded": self.succeeded,
            "is_total": self.is_total,
            "assessed_at": _iso(self.assessed_at),
        })

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<MetadataQuality %r %s%%>" % (self.title, self.percentage)


class CatalogStatistics(Entry):
    """``stats:CatalogStatistics`` -- one day's registry-wide snapshot."""

    rdf_types = (STATS.CatalogStatistics,)

    _PREFIX = STATS + "datasets_in_context_"

    @property
    def date(self) -> Optional[Union[_dt.date, _dt.datetime]]:
        return self.resource.date(DCTERMS.date)

    @property
    def datasets_per_context(self) -> Dict[str, int]:
        """``{contextId: dataset count}`` for that day.

        Context ids map to organisations through
        :meth:`Dataportal.context_names` or a harvest report's ``context_id``.
        Keys that are not plain context ids are skipped; see
        :attr:`datasets_per_context_raw` for everything the server sent.
        """
        return {
            key: count
            for key, count in self.datasets_per_context_raw.items()
            if key.isdigit()
        }

    @property
    def datasets_per_context_raw(self) -> Dict[str, int]:
        """Every ``datasets_in_context_*`` counter, keyed by the raw suffix.

        The registry occasionally emits a compound suffix rather than a bare
        context id, so this is the unfiltered view.
        """
        out: Dict[str, int] = {}
        for predicate in self.resource.predicates():
            if predicate.startswith(self._PREFIX):
                count = self.resource.integer(predicate)
                if count is not None:
                    out[predicate[len(self._PREFIX):]] = count
        return out

    def _stat(self, local: str) -> Optional[int]:
        return self.resource.integer(STATS + local)

    @property
    def dataset_count(self) -> Optional[int]:
        """Datasets stored in this registry (``residentDatasetCount``)."""
        return self._stat("residentDatasetCount")

    @property
    def public_dataset_count(self) -> Optional[int]:
        return self._stat("residentPublicDatasetCount")

    @property
    def other_dataset_count(self) -> Optional[int]:
        return self._stat("otherDatasetCount")

    @property
    def psi_dataset_count(self) -> Optional[int]:
        """Datasets from public-sector (PSI) organisations."""
        return self._stat("psiDatasetCount")

    @property
    def psi_dcat_count(self) -> Optional[int]:
        return self._stat("psiDcat")

    @property
    def psi_page_count(self) -> Optional[int]:
        return self._stat("psiPage")

    @property
    def psi_page_and_dcat_count(self) -> Optional[int]:
        return self._stat("psiPageAndDcat")

    @property
    def psi_failed_count(self) -> Optional[int]:
        return self._stat("psiFailed")

    @property
    def other_dcat_count(self) -> Optional[int]:
        return self._stat("otherDcat")

    def to_dict(self):
        return dict(self._envelope_dict(), **{
            "date": _iso(self.date),
            "dataset_count": self.dataset_count,
            "public_dataset_count": self.public_dataset_count,
            "other_dataset_count": self.other_dataset_count,
            "psi_dataset_count": self.psi_dataset_count,
            "datasets_per_context": self.datasets_per_context,
        })

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<CatalogStatistics %s datasets=%s>" % (self.date, self.dataset_count)


class OrganisationStats:
    """One row of ``/charts/orgData.json``: an organisation and its dataset count."""

    __slots__ = ("uri", "name", "dataset_count")

    def __init__(self, uri: str, name: str, dataset_count: int) -> None:
        self.uri = uri
        self.name = name
        self.dataset_count = dataset_count

    def to_dict(self) -> Dict[str, Any]:
        return {"uri": self.uri, "name": self.name, "dataset_count": self.dataset_count}

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<OrganisationStats %r datasets=%d>" % (self.name, self.dataset_count)


# --- search results ---------------------------------------------------------


class FacetValue:
    """One value of a facet, with its count."""

    __slots__ = ("name", "count", "valueString")

    def __init__(self, name: str, count: int, value_string: Optional[str] = None) -> None:
        self.name = name
        self.count = count
        self.valueString = value_string

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<FacetValue %s=%d>" % (self.name, self.count)


class Facet:
    """A facet field and its values, as returned by ``facetFields``."""

    __slots__ = ("name", "values", "predicate", "type")

    def __init__(
        self,
        name: str,
        values: Sequence[FacetValue],
        predicate: Optional[str] = None,
        facet_type: Optional[str] = None,
    ) -> None:
        self.name = name
        self.values = list(values)
        self.predicate = predicate
        self.type = facet_type

    def as_dict(self) -> Dict[str, int]:
        """``{value: count}``."""
        return {v.name: v.count for v in self.values}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "values": [{"value": v.name, "count": v.count} for v in self.values],
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Facet":
        values = [
            FacetValue(v.get("name", ""), int(v.get("count", 0)), v.get("valueString"))
            for v in data.get("values") or []
        ]
        return cls(data.get("name", ""), values, data.get("predicate"), data.get("type"))

    def __iter__(self) -> Iterator[FacetValue]:
        return iter(self.values)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<Facet %s values=%d>" % (self.name, len(self.values))


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
        facets: Sequence[Facet] = (),
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
            "facets": {facet.name: facet.as_dict() for facet in self.facets},
        }

    def to_json(self, indent: Optional[int] = None) -> str:
        """:meth:`to_dict` rendered as a JSON string."""
        return _json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def facet(self, name: str) -> Optional[Facet]:
        for facet in self.facets:
            if facet.name == name:
                return facet
        return None

    def next_page(self) -> Optional["SearchPage"]:
        """Fetch the following page, or ``None`` when exhausted."""
        if not self.has_more or self._client is None:
            return None
        params = dict(self._params)
        params["offset"] = self.offset + len(self.entries)
        return self._client.search(**params)

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
    DatasetSeries,
    Distribution,
    DataService,
    Catalog,
    Agent,
    ContactPoint,
    Standard,
    LinkCheckReport,
    MetadataQuality,
    CatalogStatistics,
):
    register_model(_model)

def wrap_entry(
    data: Mapping[str, Any],
    *,
    client: Any = None,
    languages: Sequence[str] = DEFAULT_LANGUAGES,
    default: Optional[Type[Entry]] = None,
) -> Entry:
    """Build the most specific :class:`Entry` subclass for a search hit."""
    base = Entry.from_json(data, client=client, languages=languages)
    if default is not None:
        return base.as_(default)
    types = set(base.resource.types)
    for rdf_type in _TYPE_PRIORITY:
        if rdf_type in types:
            return base.as_(MODEL_REGISTRY[rdf_type])
    for rdf_type in types:
        model = MODEL_REGISTRY.get(rdf_type)
        if model is not None:
            return base.as_(model)
    return base
