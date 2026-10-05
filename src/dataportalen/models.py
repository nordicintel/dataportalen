"""The things a :class:`~dataportalen.Catalog` hands back.

Four models -- :class:`Publisher`, :class:`Dataset`, :class:`DataService` and
:class:`Distribution` -- and :class:`SearchResult`, which carries datasets
and their :class:`Facets`. They are the same whether a call was answered by
the local catalogue or by the registry itself.

Every model reads by attribute (``dataset.title``, ``dataset.publisher.id``)
and turns into plain JSON with ``to_dict()``, keeping the field names.
Printing one prints that JSON. Every method that returns models also takes
``as_dict=True`` and hands back the dicts instead.

Text a publisher wrote is a :class:`MultilingualText`: Swedish, English or
both, as written. ``.text()`` picks the catalogue's language and falls back
to the other one.

The fields are measured, not designed: every one of the 23,582 datasets in
the registry fills exactly these, and ``tests/test_records.py`` holds the
package to it. A field is always there. Where the publisher said nothing it
is ``None``, ``[]`` or an empty text -- and ``broken``, ``unverified``,
``byte_size`` and ``stale`` are ``None`` unless they have something to say.
"""

from __future__ import annotations

import dataclasses as _dc
import json as _json
from collections import namedtuple as _namedtuple
from collections.abc import Mapping as _Mapping
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence

from .core import QueryError
from .entries import ENGLISH, SWEDISH

__all__ = [
    "MultilingualText",
    "Keywords",
    "License",
    "Contact",
    "Temporal",
    "LinkMark",
    "Publisher",
    "Distribution",
    "Dataset",
    "DataService",
    "SearchResult",
    "FacetValue",
    "Facets",
    "Facet",
    "DATASET_FILTERS",
    "DATA_SERVICE_FILTERS",
    "PUBLISHER_FILTERS",
]

#: The languages a catalogue can prefer.
LANGUAGES = (SWEDISH, ENGLISH)


def _dumps(value: Any) -> str:
    return _json.dumps(value, indent=4, ensure_ascii=False)


def _plain(value: Any) -> Any:
    """``value`` as JSON-compatible dicts, lists and scalars, all the way down."""
    to_dict = getattr(value, "to_dict", None)
    if to_dict is not None and not isinstance(value, type):
        return to_dict()
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


# --- text -------------------------------------------------------------------


class MultilingualText(_Mapping):
    """Text as the publisher wrote it: ``{"sv": ..., "en": ...}``, either or both.

    53% of datasets are Swedish only, 36% carry both, 10% are English only,
    so ``["sv"]`` is not safe to write and :meth:`text` is::

        dataset.title.text()          # the catalogue's language, else the other
        dataset.title.text("en")      # English if there is any
        dataset.title["sv"]           # still a mapping, for exactly one language

    It compares equal to the plain dict, and :meth:`to_dict` is that dict.

        >>> title = MultilingualText({"sv": "Vägtrafiknät", "en": "Road traffic network"})
        >>> title.text()
        'Vägtrafiknät'
        >>> title.text("en")
        'Road traffic network'
        >>> MultilingualText({"en": "Road traffic network"}).text()
        'Road traffic network'
        >>> MultilingualText({}).text() is None
        True
    """

    __slots__ = ("_values", "_lang")

    def __init__(self, values: Optional[Mapping[str, str]] = None,
                 lang: str = SWEDISH) -> None:
        self._values: Dict[str, str] = dict(values or {})
        self._lang = lang

    def text(self, lang: Optional[str] = None) -> Optional[str]:
        """One string: ``lang`` (default: the catalogue's), else the other
        language, else whatever there is. ``None`` when there is nothing."""
        first = lang or self._lang
        other = ENGLISH if first == SWEDISH else SWEDISH
        found = self._values.get(first) or self._values.get(other)
        if found:
            return found
        return next((value for value in self._values.values() if value), None)

    def to_dict(self) -> Dict[str, str]:
        """The language-keyed values, as a new dict."""
        return dict(self._values)

    def _in(self, lang: str) -> "MultilingualText":
        return MultilingualText(self._values, lang)

    def __getitem__(self, lang: str) -> str:
        return self._values[lang]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __str__(self) -> str:
        return _dumps(self.to_dict())

    def __repr__(self) -> str:
        return "MultilingualText(%r)" % (self._values,)


class Keywords(_Mapping):
    """Keywords per language: ``{"sv": [...], "en": [...]}``; empty when none.

        >>> keywords = Keywords({"sv": ["väg", "trafik"], "en": ["road"]})
        >>> keywords.list()
        ['väg', 'trafik']
        >>> keywords.list("en")
        ['road']
        >>> keywords.all()
        ['väg', 'trafik', 'road']
    """

    __slots__ = ("_values", "_lang")

    def __init__(self, values: Optional[Mapping[str, Sequence[str]]] = None,
                 lang: str = SWEDISH) -> None:
        self._values: Dict[str, List[str]] = {
            key: list(items) for key, items in (values or {}).items()}
        self._lang = lang

    def list(self, lang: Optional[str] = None) -> List[str]:
        """The keywords in ``lang`` (default: the catalogue's), else the other."""
        first = lang or self._lang
        other = ENGLISH if first == SWEDISH else SWEDISH
        return [*(self._values.get(first) or self._values.get(other) or ())]

    def all(self) -> List[str]:
        """Every keyword in either language, each once, in order."""
        seen: Dict[str, None] = {}
        for items in self._values.values():
            seen.update(dict.fromkeys(items))
        return [*seen]

    def to_dict(self) -> Dict[str, List[str]]:
        """The language-keyed lists, as a new dict."""
        return {key: [*items] for key, items in self._values.items()}

    def __getitem__(self, lang: str) -> List[str]:
        return self._values[lang]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __str__(self) -> str:
        return _dumps(self.to_dict())

    def __repr__(self) -> str:
        return "Keywords(%r)" % (self._values,)


# --- models -----------------------------------------------------------------


class _Model:
    """What every model shares: ``to_dict()``, and printing as that JSON."""

    def to_dict(self) -> Dict[str, Any]:
        """Every field, in order, as JSON-compatible values -- nested models too."""
        return {field.name: _plain(getattr(self, field.name))
                for field in _dc.fields(self)}  # type: ignore[arg-type]

    def __str__(self) -> str:
        return _dumps(self.to_dict())


@_dc.dataclass(repr=False)
class License(_Model):
    """A licence: its id, what it is called, where to read it."""

    id: Optional[str]
    label: MultilingualText
    uri: Optional[str]

    def __repr__(self) -> str:
        return "<License %s>" % (self.id,)


@_dc.dataclass(repr=False)
class Contact(_Model):
    """A contact point: a person or a function, and how to reach them."""

    uri: Optional[str]
    name: Optional[str]
    email: Optional[str]

    def __repr__(self) -> str:
        return "<Contact %r %s>" % (self.name, self.email or "")


@_dc.dataclass(repr=False)
class Temporal(_Model):
    """The period a dataset covers. Either end may be ``None``."""

    start: Optional[str]
    end: Optional[str]

    def __repr__(self) -> str:
        return "<Temporal %s..%s>" % (self.start or "", self.end or "")


@_dc.dataclass(repr=False)
class LinkMark(_Model):
    """What was said about a distribution, or a record's source: why, and when.

    The registry said it, unless ``by`` is ``"local"``: a verdict asked from
    this machine.
    """

    reason: Optional[str]
    checked: Optional[str]
    by: Optional[str] = None

    def __repr__(self) -> str:
        return "<LinkMark %r %s>" % (self.reason, self.checked or "")


@_dc.dataclass(repr=False)
class Publisher(_Model):
    """A publisher: an authority, a municipality, a company, a university.

    ``id`` is what ``publisher=`` takes and what the ``publisher`` facet
    reports; ``alias`` is the one short name some go by (``scb``), accepted
    wherever the id is. The counts are filled on what
    :meth:`Catalog.publishers` and :meth:`Catalog.publisher` return, and
    ``facets`` on what :meth:`Catalog.publisher` returns; a publisher nested
    in a dataset leaves them ``None``. A record with no publisher still has
    one, with every field ``None``.
    """

    id: Optional[str]
    uri: Optional[str]
    name: MultilingualText
    alias: Optional[str]
    type: Optional[str]
    homepage: Optional[str]
    email: Optional[str]
    identifiers: List[str]
    dataset_count: Optional[int] = None
    data_service_count: Optional[int] = None
    facets: Optional["Facets"] = None

    def __repr__(self) -> str:
        return "<Publisher %s>" % (self.id,)


@_dc.dataclass(repr=False)
class Distribution(_Model):
    """One distribution of a dataset: a file, an API or a web page.

    ``kind`` says which, read from the metadata with no request -- see
    :data:`dataportalen.retrieval.KINDS`. ``broken`` is set on a distribution
    the registry got an HTTP error for or found no host for (held only with
    ``exclude_broken=False``), ``unverified`` on one its checker could not
    get through to, and ``byte_size`` on the 1.4% whose publisher states a
    size.
    """

    uri: Optional[str]
    title: MultilingualText
    description: MultilingualText
    access_url: Optional[str]
    download_url: Optional[str]
    format: Optional[str]
    license: Optional[License]
    status: Optional[str]
    availability: Optional[str]
    languages: List[str]
    issued: Optional[str]
    modified: Optional[str]
    access_service_uris: List[str]
    kind: str
    broken: Optional[LinkMark] = None
    unverified: Optional[LinkMark] = None
    byte_size: Optional[int] = None

    def __repr__(self) -> str:
        return "<Distribution %s %s>" % (
            self.kind, self.download_url or self.access_url or "")


@_dc.dataclass(repr=False)
class Dataset(_Model):
    """A dataset, and the distributions it is published as.

    ``stale`` is set when the source catalogue it was harvested from failed
    its latest harvest: the record is what the last good one left.
    """

    uri: Optional[str]
    context_id: str
    entry_id: str
    type: str
    title: MultilingualText
    description: MultilingualText
    keywords: Keywords
    identifier: Optional[str]
    landing_page: Optional[str]
    publisher: Publisher
    themes: List[str]
    license: Optional[License]
    access_rights: Optional[str]
    accrual_periodicity: Optional[str]
    languages: List[str]
    spatial: List[str]
    temporal: Optional[Temporal]
    issued: Optional[str]
    modified: Optional[str]
    contact_points: List[Contact]
    distributions: List[Distribution]
    stale: Optional[LinkMark] = None

    def __repr__(self) -> str:
        return "<Dataset %r %s>" % (self.title.text(), self.uri or "")


@_dc.dataclass(repr=False)
class DataService(_Model):
    """A data service -- an API rather than a file. ``stale`` as on a dataset."""

    uri: Optional[str]
    context_id: str
    entry_id: str
    type: str
    title: MultilingualText
    description: MultilingualText
    keywords: Keywords
    service_type: Optional[str]
    endpoint_url: Optional[str]
    endpoint_description: Optional[str]
    serves_datasets: List[str]
    conforms_to: List[str]
    publisher: Publisher
    themes: List[str]
    license: Optional[License]
    access_rights: Optional[str]
    landing_page: Optional[str]
    contact_points: List[Contact]
    stale: Optional[LinkMark] = None

    def __repr__(self) -> str:
        return "<DataService %r %s>" % (self.title.text(), self.uri or "")


@_dc.dataclass(repr=False)
class SearchResult(_Model):
    """What :meth:`Catalog.search` returns: datasets, and what all matches are made of.

    ::

        result = catalog.search(theme="transport")
        result.total        # how many matched
        result.datasets     # every one of them, or the window you asked for
        result.facets       # counted over every match, not just the window

    With no ``limit`` it holds every match, so ``len(result) == result.total``.
    """

    datasets: List[Dataset]
    facets: "Facets"
    total: int
    offset: int = 0
    limit: Optional[int] = None

    @property
    def has_more(self) -> bool:
        """Whether more matched than :attr:`datasets` holds past ``offset``."""
        return self.offset + len(self.datasets) < self.total

    def to_dict(self) -> Dict[str, Any]:
        """``{"total", "offset", "limit", "datasets", "facets", "facets_omitted"}``."""
        return {"total": self.total, "offset": self.offset, "limit": self.limit,
                "datasets": [dataset.to_dict() for dataset in self.datasets],
                "facets": self.facets.to_dict(),
                "facets_omitted": self.facets.omitted}

    def __len__(self) -> int:
        return len(self.datasets)

    def __iter__(self) -> Iterator[Dataset]:
        return iter(self.datasets)

    def __repr__(self) -> str:
        return "<SearchResult %d of %d>" % (len(self.datasets), self.total)


# --- from the dicts the engines build ----------------------------------------
#
# Both engines -- the local catalogue and the registry -- build the same
# record dicts (`client._present`). These turn one into a model, so whatever
# answered, the caller gets the same shape.


def _text(value: Any, lang: str) -> MultilingualText:
    return MultilingualText(value if isinstance(value, dict) else None, lang)


def _keywords(value: Any, lang: str) -> Keywords:
    return Keywords(value if isinstance(value, dict) else None, lang)


def _mark(value: Any) -> Optional[LinkMark]:
    if not value:
        return None
    return LinkMark(value.get("reason"), value.get("checked"), value.get("by"))


def _license(value: Any, lang: str) -> Optional[License]:
    if not value:
        return None
    return License(value.get("id"), _text(value.get("label"), lang), value.get("uri"))


def _contacts(values: Any) -> List[Contact]:
    return [Contact(c.get("uri"), c.get("name"), c.get("email")) for c in values or ()]


def _publisher(value: Any, lang: str, facets: Optional["Facets"] = None) -> Publisher:
    value = value or {}
    return Publisher(
        id=value.get("id"), uri=value.get("uri"), name=_text(value.get("name"), lang),
        alias=value.get("alias"), type=value.get("type"),
        homepage=value.get("homepage"), email=value.get("email"),
        identifiers=[*(value.get("identifiers") or ())],
        dataset_count=value.get("dataset_count"),
        data_service_count=value.get("data_service_count"),
        facets=_localize(facets, lang) if facets is not None else None)


def _distribution(d: Mapping[str, Any], lang: str) -> Distribution:
    return Distribution(
        uri=d.get("uri"), title=_text(d.get("title"), lang),
        description=_text(d.get("description"), lang),
        access_url=d.get("access_url"), download_url=d.get("download_url"),
        format=d.get("format"), license=_license(d.get("license"), lang),
        status=d.get("status"), availability=d.get("availability"),
        languages=[*(d.get("languages") or ())], issued=d.get("issued"),
        modified=d.get("modified"),
        access_service_uris=[*(d.get("access_service_uris") or ())],
        kind=d.get("kind") or "unknown", broken=_mark(d.get("broken")),
        unverified=_mark(d.get("unverified")), byte_size=d.get("byte_size"))


def _dataset(d: Mapping[str, Any], lang: str) -> Dataset:
    temporal = d.get("temporal")
    return Dataset(
        uri=d.get("uri"), context_id=d.get("context_id"), entry_id=d.get("entry_id"),
        type=d.get("type") or "dataset", title=_text(d.get("title"), lang),
        description=_text(d.get("description"), lang),
        keywords=_keywords(d.get("keywords"), lang),
        identifier=d.get("identifier"), landing_page=d.get("landing_page"),
        publisher=_publisher(d.get("publisher"), lang),
        themes=[*(d.get("themes") or ())], license=_license(d.get("license"), lang),
        access_rights=d.get("access_rights"),
        accrual_periodicity=d.get("accrual_periodicity"),
        languages=[*(d.get("languages") or ())], spatial=[*(d.get("spatial") or ())],
        temporal=Temporal(temporal.get("start"), temporal.get("end")) if temporal else None,
        issued=d.get("issued"), modified=d.get("modified"),
        contact_points=_contacts(d.get("contact_points")),
        distributions=[_distribution(dist, lang) for dist in d.get("distributions") or ()],
        stale=_mark(d.get("stale")))


def _data_service(d: Mapping[str, Any], lang: str) -> DataService:
    return DataService(
        uri=d.get("uri"), context_id=d.get("context_id"), entry_id=d.get("entry_id"),
        type=d.get("type") or "data_service", title=_text(d.get("title"), lang),
        description=_text(d.get("description"), lang),
        keywords=_keywords(d.get("keywords"), lang),
        service_type=d.get("service_type"), endpoint_url=d.get("endpoint_url"),
        endpoint_description=d.get("endpoint_description"),
        serves_datasets=[*(d.get("serves_datasets") or ())],
        conforms_to=[*(d.get("conforms_to") or ())],
        publisher=_publisher(d.get("publisher"), lang),
        themes=[*(d.get("themes") or ())], license=_license(d.get("license"), lang),
        access_rights=d.get("access_rights"), landing_page=d.get("landing_page"),
        contact_points=_contacts(d.get("contact_points")), stale=_mark(d.get("stale")))


def _record(d: Mapping[str, Any], lang: str) -> Any:
    """A dataset or a data service, by its ``type``."""
    if d.get("type") == "data_service":
        return _data_service(d, lang)
    return _dataset(d, lang)


def _localize(facets: "Facets", lang: str) -> "Facets":
    """The same facets, their labels reading in ``lang``."""
    return Facets({
        name: Facet([FacetValue(row.value, row.count, row.label._in(lang))
                     for row in values], values.omitted)
        for name, values in facets._counts.items()})


# --- facets -----------------------------------------------------------------


class FacetValue(_namedtuple("FacetValue", "value count")):
    """One value a filter accepts, and how many records carry it.

    Still a plain ``(value, count)`` pair, so it unpacks in a loop and
    compares equal to one::

        for value, count in result.facets["theme"]:
            ...

    :attr:`label` rides alongside rather than in the tuple -- a
    :class:`MultilingualText`, from the vocabulary for a controlled value and
    from the records themselves for a publisher. It is empty for values
    that are their own label, such as keywords and kinds::

        row = result.facets["publisher"][0]
        row.value                     # 'trafikverket', what you filter with
        row.label.text()              # 'Trafikverket', what you show

    It is not part of the tuple, so ``_replace`` and pickling drop it, and
    the class carries a ``__dict__`` to hold it -- a namedtuple subclass
    cannot use ``__slots__``.
    """

    def __new__(cls, value, count, label=None):
        row = super().__new__(cls, value, count)
        row.label = (label if isinstance(label, MultilingualText)
                     else MultilingualText(label or None))
        return row

    def to_dict(self) -> Dict[str, Any]:
        """``{"value", "count", "label"}``."""
        return {"value": self.value, "count": self.count, "label": self.label.to_dict()}

    def __str__(self) -> str:
        return _dumps(self.to_dict())


#: What a dataset can be filtered and faceted by. Every one was measured
#: over all 23,575 datasets: publisher is on 100% of them, keyword 94.8%,
#: access_rights 82.3%, theme 78.2%, format 69.7%, accrual_periodicity 63.3%.
#:
#: `license` and `language` were filters and are not any more: every
#: dataset still carries them (`dataset.license.id`, `dataset.languages`),
#: but they are read, not searched by. `updated` is `accrual_periodicity`,
#: named after the field it reads.
#:
#: `place` was one of these and is not any more. 4,471 datasets set a
#: spatial coverage (19%), and 3,068 of those say "Sweden". The 1,902 with a
#: sub-national place spread over 540 values, 223 of them on exactly one
#: dataset, and the common ones -- malmo 185, linkopings_kommun 160,
#: sodertalje_kommun 103 -- are municipal publishers tagging their own
#: municipality, which `publisher` already gives you. The field stays on the
#: record as `spatial`.
#:
#: `creator` was one of these and is not any more. 7,104 datasets named one,
#: and on 6,174 of them it was the publisher again -- the same agent URI on
#: 5,292, the publisher's name plus a survey or system suffix on 634, an
#: internal department on 150. The ~930 that named someone else were citing a
#: source, not a second publisher. A filter whose two largest values are
#: 4,468 datasets pointing at their own publisher is not a search axis.
#:
#: `kind` is the one filter that is not a field of the registry's: it is what
#: `retrieval.classify` reads out of each distribution, so it exists in a
#: downloaded catalogue and not in the registry's index.
DATASET_FILTERS = ("publisher", "publisher_type", "theme",
                   "keyword", "format", "kind", "access_rights",
                   "accrual_periodicity")

#: The same for a data service, and it is a different list. Over all 599:
#: access_rights 97.8%, publisher 97.3%, keyword 83.5%, service_type 55.9%,
#: theme 53.8%. A data service has no distributions (so no `format` or
#: `kind`) and no `accrual_periodicity`.
DATA_SERVICE_FILTERS = ("publisher", "publisher_type",
                        "service_type", "theme", "keyword", "access_rights")

#: What :meth:`Catalog.publishers` can be narrowed by: the filters datasets
#: and data services share, so a publisher's two counts answer the same
#: question.
PUBLISHER_FILTERS = ("publisher", "publisher_type", "theme", "keyword",
                     "access_rights")


class Facet(list):
    """One facet: the values of one filter, with a count of any left out.

    A plain list of ``(value, count)`` pairs, biggest first::

        for value, count in result.facets["publisher"]:
            ...

    ``omitted`` is how many further values there were, above whatever
    ``facet_limit`` the search was given -- 0 when nothing was cut.
    """

    __slots__ = ("omitted",)

    def __init__(self, values: Sequence["FacetValue"] = (), omitted: int = 0) -> None:
        super().__init__(values)
        self.omitted = omitted

    def to_dict(self) -> List[Dict[str, Any]]:
        """Each value as ``{"value", "count", "label"}``, biggest first."""
        return [row.to_dict() for row in self]

    def __str__(self) -> str:
        return _dumps(self.to_dict())

    def __repr__(self) -> str:                            # pragma: no cover
        more = " +%d more" % self.omitted if self.omitted else ""
        return "<Facet %d%s>" % (len(self), more)


class Facets(_Mapping):
    """The facets of a result: per filter, which values exist and how often.

    Every filter you can search by, counted over everything that matched --
    not just the rows you are holding::

        result = catalog.search(query="cykel")
        result.total                   # 388
        result.facets["publisher"]     # [('trafikverket', 88), ...]
        result.facets["theme"]         # [('transport', 201), ...]

    You filter with a value; a facet tells you which values there are. Each
    one can be fed straight back in to narrow the search::

        catalog.search(query="cykel", publisher="trafikverket")

    Counts are per dataset: a dataset with three CSV files counts once under
    ``format`` -> ``csv``.
    """

    __slots__ = ("_counts",)

    def __init__(
        self,
        counts: Mapping[str, Sequence["FacetValue"]],
        limit: Optional[int] = None,
    ) -> None:
        self._counts = {}
        for name in counts:
            values = list(counts[name])
            omitted = getattr(counts[name], "omitted", 0)
            if limit is not None and len(values) > limit:
                self._counts[name] = Facet(values[:limit], omitted + len(values) - limit)
            else:
                self._counts[name] = Facet(values, omitted)

    @property
    def omitted(self) -> Dict[str, int]:
        """``{filter: how many values were cut}``, for the ones that were.

        Empty unless the search was given a ``facet_limit``.
        """
        return {name: values.omitted
                for name, values in self._counts.items() if values.omitted}

    def __getitem__(self, filter: str) -> "Facet":
        if filter in self._counts:
            return self._counts[filter]
        raise QueryError(
            "no facet %r; this result has: %s"
            % (filter, ", ".join(self._counts)))

    def __contains__(self, filter: object) -> bool:
        # Mapping's default catches KeyError, and __getitem__ raises
        # QueryError -- so `"format" in facets` would propagate instead of
        # answering False.
        return filter in self._counts

    def __iter__(self) -> Iterator[str]:
        return iter(self._counts)

    def __len__(self) -> int:
        return len(self._counts)

    def top(self, filter: str) -> Optional["FacetValue"]:  # noqa: D401
        """The commonest value for one filter, or ``None`` if there is none."""
        found = self[filter]
        return found[0] if found else None

    def to_dict(self) -> Dict[str, List[Dict[str, Any]]]:
        """``{filter: [{"value", "count", "label"}, ...]}``, biggest first.

        Any values cut by a ``facet_limit`` are counted in :attr:`omitted`
        rather than here.
        """
        return {name: values.to_dict() for name, values in self._counts.items()}

    def counts(self) -> Dict[str, Dict[str, int]]:
        """``{filter: {value: count}}``: the compact form, without labels."""
        return {
            name: {value: count for value, count in values}
            for name, values in self._counts.items()
        }

    def __str__(self) -> str:
        return _dumps(self.to_dict())

    def __repr__(self) -> str:                            # pragma: no cover
        return "<Facets %s>" % " ".join(
            "%s=%d" % (name, len(values)) for name, values in self._counts.items())
