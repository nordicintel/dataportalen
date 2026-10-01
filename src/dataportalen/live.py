"""The registry, asked directly: the same searches without a download.

    >>> from dataportalen import LiveCatalog
    >>> live = LiveCatalog()                              # doctest: +SKIP
    >>> live.datasets(theme="transport", limit=5).total   # doctest: +SKIP
    545

:class:`LiveCatalog` has :class:`~dataportalen.Catalog`'s methods and hands
out the same records, but every call is a request to ``admin.dataportal.se``.
Nothing is downloaded and nothing is written. The price is speed -- a page of
50 is about four seconds against 0.07 locally -- and the things the registry's
search index cannot do, which are listed on the class.
"""

from __future__ import annotations

import concurrent.futures
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .client import (
    _DATE_FILTERS,
    _EVERY_FILTER,
    _PUBLISHER_FACETS,
    _RDF_FORMATS,
    _WORKERS,
    MAX_LIMIT,
    STABLE_SORT,
    Catalog,
    _access_scope,
    _assemble,
    _copy_publisher,
    _Counter,
    _fold_keyword,
    _local_slugs,
    _present,
    _publisher_dict,
    _Publishers,
    _Registry,
    _report_missing,
    _require_date,
    _require_values,
    _targeted_indexes,
)
from .core import BaseTransport, QueryError
from .models import (
    _EMPTY_AGENT,
    DATA_SERVICE_FILTERS,
    DATASET_FILTERS,
    Agent,
    DataService,
    Dataset,
    Entry,
    Facets,
    FacetValue,
    Results,
)
from .query import Q, predicate_field
from .rdf import DCAT, DCTERMS, label_for, resolve, slug_for
from .records import DataServiceRecord, DatasetRecord, Publisher, PublisherDetail

__all__ = ["LiveCatalog"]


#: Where the registry's index keeps each filter. `format` is the one that is
#: not a URI index: publishers state a file type as a plain media type on
#: ~16,400 datasets and as a URI on ~120, and the exact-string index holds
#: both where the URI index holds two values.
_FIELDS = {
    "publisher": predicate_field(DCTERMS.publisher, "uri"),
    "theme": predicate_field(DCAT.theme, "uri"),
    "format": predicate_field(DCTERMS.format, "literal_s"),
    "license": predicate_field(DCTERMS.license, "uri"),
    "access_rights": predicate_field(DCTERMS.accessRights, "uri"),
    "updated": predicate_field(DCTERMS.accrualPeriodicity, "uri"),
    "language": predicate_field(DCTERMS.language, "uri"),
    "service_type": predicate_field(DCTERMS.type, "uri"),
}
_KEYWORD = predicate_field(DCAT.keyword, "literal_s")

#: Per kind of record: its rdf:type, its model, and the filters the registry
#: can facet. Not `keyword` -- it returns the top 1,000 spellings of 23,373
#: and cannot page -- and not `publisher_type`, which it does not index.
_KINDS = {
    "dataset": (DCAT.Dataset, Dataset,
                tuple(n for n in DATASET_FILTERS if n in _FIELDS)),
    "data_service": (DCAT.DataService, DataService,
                     tuple(n for n in DATA_SERVICE_FILTERS if n in _FIELDS)),
}

#: More values than any filter has (publisher, the largest, has 365), so a
#: facet is never cut short.
_FACET_LIMIT = 5000

#: A clause nothing matches, for a value that is known and that no record in
#: the registry carries.
_NOTHING = Q.raw("(*:* AND NOT *:*)")

#: `dcterms:type` is indexed for every node of a data service's graph, so the
#: facet also yields the conformity verdict of 47 nested INSPIRE statements.
#: It is not a kind of service and no record's `service_type` is ever it.
_NOT_A_SERVICE_TYPE = "DegreeOfConformity"


def _fold(name: str, values: Sequence[Dict[str, Any]]) -> Dict[str, List[Tuple[str, int]]]:
    """A facet response as ``{short name: [(what the registry holds, count)]}``.

    Several raw values can share a short name: Swedish is stored under three
    URIs, ZIP under two media types. The package's tables alone cannot list
    them -- 15 of the 57 format strings are unreachable from a slug -- so the
    registry's own values are folded through the function that names a
    record's fields.
    """
    out: Dict[str, List[Tuple[str, int]]] = {}
    for value in values:
        raw = value.get("name")
        if not raw or (name == "service_type" and _NOT_A_SERVICE_TYPE in raw):
            continue
        slug = slug_for(raw)
        if slug:
            out.setdefault(slug, []).append((raw, int(value.get("count") or 0)))
    return out


def _facet_values(raw: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    return {facet.get("name"): facet.get("values") or []
            for facet in raw.get("facetFields") or []}


def _ordered(counts: Dict[str, int]) -> List[Tuple[str, int]]:
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))


class LiveCatalog:
    """Sweden's open-data catalogue, searched at the registry itself.

    The same methods and the same records as :class:`~dataportalen.Catalog`,
    with no database::

        from dataportalen import LiveCatalog

        live = LiveCatalog()
        page = live.datasets(theme="transport", format="csv", limit=10)
        page.total                       # the registry's own count
        page.facets["publisher"]         # who publishes them

    Use it for a quick question, or where 95 MB on disk is not welcome. For
    anything repeated, :class:`Catalog` answers in a fiftieth of the time.

    What it keeps, for as long as the object lives: which values the registry
    holds for each filter (one request, the first time a filter is used) and
    who the publishers are (about 20 requests, the first time a search or a
    publisher call needs them). Neither is a copy of the catalogue.

    What differs from :class:`Catalog`, because the registry's search index
    differs from a record:

    * ``limit`` is 0 to 100 -- a page is all the registry serves -- and there
      is no ``len()`` or iteration, each of which would be a full download.
    * No link health. The registry cannot filter on it, so nothing is
      excluded and no file carries ``broken`` or ``unverified``.
    * ``publisher_type`` is refused: the index has no such field.
    * Facets have no ``keyword`` and no ``publisher_type``.
    * ``query`` is the registry's full-text search: a phrase of whole words
      anywhere in the entry, where :class:`Catalog` matches a substring of the
      title, description and keywords.
    * A filter can match a few more records than it does locally. The index
      covers every node of an entry's graph and every value a field states:
      a dataset under two licences is found by either, where its record names
      the first (``cc_by_nc_4_0``: 20 here, 17 locally), and a specification
      it links to lends it that document's themes and keywords.
    * The date filters can differ either way. The index holds a date from any
      node of the graph, not necessarily the dataset's own:
      ``modified_after="2025"`` finds 12,672 here and 12,584 locally,
      ``issued_before="2015-06"`` 1,887 against 1,934.
    * ``format`` is the registry's dataset-level index of file formats, which
      lacks a few (63 datasets with ``microsoft_excel`` against 73 locally).
      A format stated two ways is one facet row with the two counts added, so
      a dataset that states both is counted twice (``zip``: 292, filter 291).

    :param access_rights: which ``access_rights`` values to search within,
        as for :class:`Catalog`: ``"public"``, ``"non_public"``,
        ``"restricted"``, ``"none"`` for a record that sets nothing, or
        ``None`` for everything.
    """

    def __init__(
        self,
        *,
        access_rights: Optional[Sequence[str]] = ("public",),
        _transport: Optional[BaseTransport] = None,
    ) -> None:
        self.access_rights = _access_scope(access_rights)
        self._registry = _Registry(transport=_transport)
        self._values: Dict[str, Dict[str, Dict[str, List[str]]]] = {}
        self._publishers: Optional[_Publishers] = None
        self._publisher_rows: Optional[List[Dict[str, Any]]] = None

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Release the HTTP connection. Never required -- nothing leaks."""
        self._registry.close()

    def __enter__(self) -> "LiveCatalog":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:                            # pragma: no cover
        return "<LiveCatalog %s>" % self._registry.base_url

    # -- what the registry holds -------------------------------------------

    def _scope(self) -> Q:
        """``access_rights`` as a clause every request carries."""
        if self.access_rights is None:
            return Q.empty()
        field = _FIELDS["access_rights"]
        parts = []
        uris = [uri for value in sorted(self.access_rights - {"none"})
                for uri in resolve(value, "access_rights")]
        if uris:
            parts.append(Q.any_of(field, uris))
        if "none" in self.access_rights:
            parts.append(Q.raw("(*:* -%s:*)" % field))
        return Q.join(parts, "OR").group()

    def _base(self, kind: str) -> Q:
        return Q.rdf_type(_KINDS[kind][0]) & self._scope()

    def _index(self, kind: str) -> Dict[str, Dict[str, List[str]]]:
        """``{filter: {short name: [the registry's values]}}`` for one kind.

        Asked of the whole registry rather than of ``access_rights``: what a
        value is called does not depend on which records are in scope.
        """
        if kind not in self._values:
            rdf_type, _, names = _KINDS[kind]
            names = [name for name in names if name != "publisher"]
            raw = self._registry._search_raw(
                Q.rdf_type(rdf_type), limit=1, sort=None,
                facet_fields=[_FIELDS[name] for name in names],
                facet_limit=_FACET_LIMIT, facet_min_count=1)
            found = _facet_values(raw)
            self._values[kind] = {
                name: {slug: [value for value, _ in pairs]
                       for slug, pairs in _fold(name, found.get(_FIELDS[name], [])).items()}
                for name in names}
        return self._values[kind]

    def _publisher_index(self) -> _Publishers:
        """Who publishes, from the registry's publisher facet and its agents.

        One facet request for the URIs and how much each has, then the agents
        themselves, 20 to a request. Built the way :class:`Catalog` builds
        its own, by the same class, over datasets and data services of every
        ``access_rights`` -- so an id means the same organisation in both.
        """
        if self._publishers is None:
            raw = self._registry._search_raw(
                Q.rdf_type(DCAT.Dataset, DCAT.DataService), limit=1, sort=None,
                facet_fields=[_FIELDS["publisher"]],
                facet_limit=_FACET_LIMIT, facet_min_count=1)
            counts = {value["name"]: int(value.get("count") or 0)
                      for value in _facet_values(raw).get(_FIELDS["publisher"], [])
                      if value.get("name")}
            uris = sorted(counts)
            batches = [uris[start:start + 20] for start in range(0, len(uris), 20)]
            agents: Dict[str, Dict[str, Any]] = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=_WORKERS) as pool:
                for found in pool.map(
                        lambda batch: self._registry._lookup_many(batch, model=Agent),
                        batches):
                    for agent in found:
                        if agent.resource_uri:
                            agents[agent.resource_uri] = agent.to_dict()
            self._publishers = _Publishers(
                (_publisher_dict(agents.get(uri) or dict(_EMPTY_AGENT, uri=uri)), n)
                for uri, n in counts.items())
        return self._publishers

    # -- filters as a query ------------------------------------------------

    def _check(self, kind: str, filters: Dict[str, Any]) -> None:
        """Refuse what this kind of record, or the registry, cannot answer."""
        allowed = _KINDS[kind][2] + ("keyword", "query")
        if kind == "dataset":
            allowed += _DATE_FILTERS
        what = "datasets" if kind == "dataset" else "data services"
        for name in filters:
            if name in allowed:
                continue
            if name == "publisher_type":
                raise QueryError(
                    "publisher_type is not something the registry can search "
                    "by: its index has no such field, and naming every "
                    "publisher of a type does not fit in a request. Use "
                    "Catalog, or publishers() to pick the ids yourself.")
            if name == "text":
                raise QueryError("'text' is called 'query' now: datasets(query=%r)"
                                 % (filters[name],))
            if name in ("sort", "page_size"):
                raise QueryError("%r is not an argument; results come in a "
                                 "fixed order, %d to a page at most"
                                 % (name, MAX_LIMIT))
            if name in _EVERY_FILTER:
                raise QueryError("%s have no %r. Available: %s"
                                 % (what, name, ", ".join(allowed)))
            raise QueryError("unknown filter %r; supported: %s"
                             % (name, ", ".join(allowed)))

    def _clause(self, kind: str, name: str, value: Any) -> Q:
        """One filter as a Solr clause."""
        if name == "query":
            _require_values(value, name)
            return Q.phrase(None, str(value).strip())

        if name in _DATE_FILTERS:
            field, _, edge = name.rpartition("_")
            bound = _require_date(value, name)
            predicate = DCTERMS.modified if field == "modified" else DCTERMS.issued
            return Q.predicate_range(
                predicate, bound if edge == "after" else None,
                bound if edge == "before" else None)

        values = _require_values(value, name)
        if name == "keyword":
            return self._keyword_clause(values)

        if name == "publisher":
            index = self._publisher_index()
            uris = [uri for item in values
                    for uri in index.uris.get(index.resolve(item), ())]
            return Q.any_of(_FIELDS[name], uris) if uris else _NOTHING

        known = self._index(kind)[name]
        raw: List[str] = []
        for item in values:
            for slug in sorted(_local_slugs(item, name, set(known))):
                raw.extend(known.get(slug, ()))
        if not raw:
            return _NOTHING
        # A format is a string that may hold anything; the rest are URIs.
        return (Q.phrase(_FIELDS[name], *raw) if name == "format"
                else Q.any_of(_FIELDS[name], raw))

    def _keyword_clause(self, values: Sequence[str]) -> Q:
        """Keywords, matched whatever their case, as the spellings in use.

        The exact-string index is case-sensitive and ``Kommun`` is 4,611
        datasets where ``kommun`` is 6. So the registry is asked first which
        spellings exist -- one facet request with a case-insensitive pattern
        -- and then for any of those. A keyword nothing is spelt like is an
        error, as it is locally; without suggestions, because suggesting
        would take the whole list.
        """
        wanted = {_fold_keyword(value): str(value).strip() for value in values}
        forms = sorted(set(wanted) | {form.lower() for form in wanted.values()})
        pattern = r"(?iu)\s*(?:%s)\s*" % "|".join(re.escape(form) for form in forms)
        raw = self._registry._search_raw(
            Q.rdf_type(DCAT.Dataset, DCAT.DataService) & self._scope(),
            limit=1, sort=None, facet_fields=[_KEYWORD], facet_matches=pattern,
            facet_limit=_FACET_LIMIT, facet_min_count=1)
        spellings: Dict[str, List[str]] = {}
        for value in _facet_values(raw).get(_KEYWORD, []):
            spellings.setdefault(_fold_keyword(value["name"]), []).append(value["name"])
        for key in sorted(wanted):
            if key not in spellings:
                raise QueryError("unknown keyword %r." % (wanted[key],))
        return Q.phrase(_KEYWORD, *[spelling for key in sorted(wanted)
                                    for spelling in sorted(spellings[key])])

    # -- searching ---------------------------------------------------------

    def _search(
        self,
        kind: str,
        query: Optional[str],
        limit: int,
        offset: int,
        facet_limit: Optional[int],
        filters: Dict[str, Any],
        facets: Optional[Sequence[str]] = None,
    ) -> Results:
        if query is not None:
            filters = dict(filters, query=query)
        if isinstance(limit, bool) or not isinstance(limit, int) or not (
                0 <= limit <= MAX_LIMIT):
            raise QueryError(
                "limit must be 0 to %d here; got %r. The registry serves a "
                "page at a time -- ask for the next with offset=, or use "
                "Catalog for every match at once." % (MAX_LIMIT, limit))
        Catalog._window(limit, offset)
        self._check(kind, filters)

        rdf_type, model, names = _KINDS[kind]
        names = tuple(facets) if facets is not None else names
        clauses = [self._clause(kind, name, value) for name, value in filters.items()]
        page = self._registry._search(
            Q.join([self._base(kind)] + clauses), model=model,
            limit=limit, offset=offset, sort=STABLE_SORT,
            facet_fields=[_FIELDS[name] for name in names],
            facet_limit=_FACET_LIMIT, facet_min_count=1)
        return Results(
            self._records(page.entries[:limit]), total=page.total, offset=offset,
            limit=limit, facets=self._facets(page.raw, names, facet_limit))

    def _records(self, entries: Sequence[Entry]) -> List[Dict[str, Any]]:
        """Search hits as full records: files, publisher and contacts nested.

        A hit carries only the URIs of what it refers to. They are resolved
        the way a partial download resolves them, by the same code, which is
        why a record from here equals the one in the database.
        """
        if not entries:
            return []
        distributions, agents, contacts = _targeted_indexes(
            self._registry, entries, _WORKERS, _Counter())
        missing: Dict[str, None] = {}
        out = []
        for entry in entries:
            record, _ = _assemble(
                entry, distributions, agents, contacts, missing,
                with_distributions=not isinstance(entry, DataService))
            out.append(_present(record))
        if missing:
            _report_missing(missing)
        return out

    def _facets(self, raw: Dict[str, Any], names: Sequence[str],
                limit: Optional[int]) -> Facets:
        """The registry's facet response in the package's own values."""
        found = _facet_values(raw)
        out: Dict[str, List[FacetValue]] = {}
        for name in names:
            values = found.get(_FIELDS[name], [])
            if name == "publisher":
                index = self._publisher_index()
                by_uri = {uri: pid for pid, uris in index.uris.items() for uri in uris}
                counts: Dict[str, int] = {}
                for value in values:
                    pid = by_uri.get(value.get("name"))
                    if pid:
                        counts[pid] = counts.get(pid, 0) + int(value.get("count") or 0)
                out[name] = [FacetValue(pid, n, index.names.get(pid) or {})
                             for pid, n in _ordered(counts)]
            else:
                counts = {slug: sum(n for _, n in pairs)
                          for slug, pairs in _fold(name, values).items()}
                out[name] = [FacetValue(slug, n, label_for(slug))
                             for slug, n in _ordered(counts)]
        return Facets(out, limit=limit)

    def datasets(
        self,
        *,
        query: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        facet_limit: Optional[int] = None,
        **filters: Any,
    ) -> Results[DatasetRecord]:
        """Search datasets at the registry. One page, and the total.

            >>> page = live.datasets(theme="transport", limit=10)   # doctest: +SKIP
            >>> page.total                                          # doctest: +SKIP
            545

        The arguments of :meth:`Catalog.datasets
        <dataportalen.Catalog.datasets>`, less ``publisher_type``. ``limit``
        is 0 to 100; ``0`` is the count and the facets alone, in one request.
        """
        return self._search("dataset", query, limit, offset, facet_limit, filters)

    def data_services(
        self,
        *,
        query: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        facet_limit: Optional[int] = None,
        **filters: Any,
    ) -> Results[DataServiceRecord]:
        """Search data services at the registry -- its APIs, not its files."""
        return self._search("data_service", query, limit, offset, facet_limit, filters)

    def facets(self, limit: Optional[int] = None) -> Facets:
        """Every dataset facet the registry can count, in one request.

        ``datasets(limit=0).facets``: publisher, theme, format, license,
        access_rights, updated and language. Data service facets are
        ``data_services(limit=0).facets``.
        """
        return self._search("dataset", None, 0, 0, limit, {}).facets

    def get(self, uri: str, format: str = "dict") -> Any:
        """One dataset or data service by its URI, or ``None``.

        ``format="dict"`` is the record, in four requests. Any other format
        -- ``"turtle"``, ``"rdf/xml"``, ``"n-triples"`` or a media type -- is
        the entry's own RDF as text. Only what ``access_rights`` holds is
        found; where two entries share a URI the same one answers every time.
        """
        if not isinstance(uri, str) or not uri.strip():
            return None
        page = self._registry._search(
            Q.resource(uri.strip())
            & Q.rdf_type(DCAT.Dataset, DCAT.DataService) & self._scope(),
            limit=1, sort=STABLE_SORT)
        if not page.entries:
            return None
        entry = page.entries[0]
        if format != "dict":
            return self._registry._entry_raw(
                entry.context_id, entry.entry_id,
                format=_RDF_FORMATS.get(format, format)).text
        return self._records([entry])[0]

    # -- publishers --------------------------------------------------------

    def _rows(self) -> List[Dict[str, Any]]:
        """One row per publisher with something in scope, counts and all."""
        if self._publisher_rows is None:
            index = self._publisher_index()
            by_uri = {uri: pid for pid, uris in index.uris.items() for uri in uris}
            counts: Dict[str, List[int]] = {}
            for column, kind in enumerate(("dataset", "data_service")):
                raw = self._registry._search_raw(
                    self._base(kind), limit=1, sort=None,
                    facet_fields=[_FIELDS["publisher"]],
                    facet_limit=_FACET_LIMIT, facet_min_count=1)
                for value in _facet_values(raw).get(_FIELDS["publisher"], []):
                    pid = by_uri.get(value.get("name"))
                    if pid:
                        counts.setdefault(pid, [0, 0])[column] += int(
                            value.get("count") or 0)
            rows = [dict(index.entities[pid],
                         dataset_count=datasets, data_service_count=services)
                    for pid, (datasets, services) in counts.items()]
            rows.sort(key=lambda row: (-row["dataset_count"], row["id"]))
            self._publisher_rows = rows
        return self._publisher_rows

    def publishers(self) -> List[Publisher]:
        """Every publisher with something in scope, biggest first.

        The rows of :meth:`Catalog.publishers
        <dataportalen.Catalog.publishers>`. About 20 requests the first time,
        none after: the list is held for as long as the object lives.
        """
        return [_copy_publisher(row) for row in self._rows()]

    def publisher(self, value: str) -> Optional[PublisherDetail]:
        """One publisher, with what it publishes; ``None`` if nothing in scope.

        ``value`` is an id, an alias, a URI, an organisation number or a name,
        as for :meth:`Catalog.publisher <dataportalen.Catalog.publisher>`.
        """
        pid = self._publisher_index().resolve(value)
        row = next((row for row in self._rows() if row["id"] == pid), None)
        if row is None:
            return None
        found = self._search("dataset", None, 0, 0, None, {"publisher": pid},
                             facets=_PUBLISHER_FACETS)
        return dict(_copy_publisher(row), facets=found.facets.to_dict())

    def info(self) -> Dict[str, Any]:
        """How much is in scope right now::

            {"datasets": 21849, "data_services": 586, "publishers": 305}

        Three counts and nothing else: there is no file to describe.
        """
        return {
            "datasets": self._registry._count(self._base("dataset")),
            "data_services": self._registry._count(self._base("data_service")),
            "publishers": len(self._rows()),
        }
