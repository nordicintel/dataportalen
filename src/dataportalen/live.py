"""The registry, asked directly: the same answers without a download.

    >>> from dataportalen import Catalog
    >>> live = Catalog(live=True)                                # doctest: +SKIP
    >>> live.search(theme="transport", limit=5).total            # doctest: +SKIP
    545

:class:`_LiveBackend` is what :class:`~dataportalen.Catalog` asks instead of
its database when it is built with ``live=True``, or when one call passes
``live=True``. It hands back the same record dicts the database does, built
by the same code, so the models a caller gets are the same either way. Every
call is a request to ``admin.dataportal.se``; nothing is downloaded and
nothing is written. The price is speed -- a page of 100 is a few seconds
against hundredths locally -- and the things the registry's search index
cannot do:

* No link health. The registry cannot filter on it, so nothing is excluded
  and no distribution carries ``broken`` or ``unverified``.
* ``kind`` and ``publisher_type`` are refused as filters on datasets and
  data services: the index has neither field. ``publisher_type`` does narrow
  :meth:`Catalog.publishers`, where it is read off the publisher.
* Facets have no ``keyword``, ``publisher_type`` or ``kind``.
* ``query`` is the registry's full-text search: a phrase of whole words
  anywhere in the entry, where the database matches a substring of the
  title, description and keywords.
* A filter can match a few more records than it does locally. The index
  covers every node of an entry's graph and every value a field states, and
  a date from any node: ``modified_after="2025"`` finds a few more here.
* ``format`` is the registry's dataset-level index of file formats, which
  lacks a few (63 datasets with ``microsoft_excel`` against 73 locally).
* Results come in URI order; the database's order is its own.
"""

from __future__ import annotations

import concurrent.futures
import math
import re
import warnings
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .client import (
    _ACCESS_NONE,
    _DATE_FILTERS,
    _EVERY_FILTER,
    _PUBLISHER_FACETS,
    _RDF_FORMATS,
    _WORKERS,
    MAX_LIMIT,
    STABLE_SORT,
    _assemble,
    _Counter,
    _fold_keyword,
    _local_slugs,
    _present,
    _publisher_dict,
    _publisher_row,
    _Publishers,
    _Registry,
    _report_missing,
    _require_date,
    _require_values,
    _targeted_indexes,
)
from .core import DataportalWarning, QueryError, logger, progress_reporter
from .entries import (
    _EMPTY_AGENT,
    AgentEntry,
    DataServiceEntry,
    DatasetEntry,
    Entry,
)
from .models import (
    DATA_SERVICE_FILTERS,
    DATASET_FILTERS,
    PUBLISHER_FILTERS,
    Facets,
    FacetValue,
)
from .query import Q, predicate_field
from .rdf import DCAT, DCTERMS, label_for, slug_for, slugify

__all__: List[str] = []


#: Where the registry's index keeps each filter. `format` is the one that is
#: not a URI index: publishers state a file type as a plain media type on
#: ~16,400 datasets and as a URI on ~120, and the exact-string index holds
#: both where the URI index holds two values.
_FIELDS = {
    "publisher": predicate_field(DCTERMS.publisher, "uri"),
    "theme": predicate_field(DCAT.theme, "uri"),
    "format": predicate_field(DCTERMS.format, "literal_s"),
    "access_rights": predicate_field(DCTERMS.accessRights, "uri"),
    "accrual_periodicity": predicate_field(DCTERMS.accrualPeriodicity, "uri"),
    "service_type": predicate_field(DCTERMS.type, "uri"),
}
_KEYWORD = predicate_field(DCAT.keyword, "literal_s")
_MODIFIED = predicate_field(DCTERMS.modified, "date")

#: Per kind of record: its rdf:type, its model, and the filters the registry
#: can facet. Not `keyword` -- it returns the top 1,000 spellings of 23,373
#: and cannot page -- and not `publisher_type` or `kind`, which it does not
#: index.
_KINDS = {
    "dataset": (DCAT.Dataset, DatasetEntry,
                tuple(n for n in DATASET_FILTERS if n in _FIELDS)),
    "data_service": (DCAT.DataService, DataServiceEntry,
                     tuple(n for n in DATA_SERVICE_FILTERS if n in _FIELDS)),
}

#: More values than any filter has (publisher, the largest, has 365), so a
#: facet is never cut short.
_FACET_LIMIT = 5000

#: Above this many records an unpaged call warns before it starts: it is
#: ten pages and more, each a few requests.
LARGE = 1000

#: Roughly how long one page of 100 full records takes: the page, and the
#: distributions, publishers and contacts it refers to, 20 to a request.
_SECONDS_PER_PAGE = 8

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


class _LiveBackend:
    """The registry's search index, answering what a database would.

    What it keeps, for as long as the object lives: which values the registry
    holds for each filter (one request, the first time a filter is used) and
    who the publishers are (about 20 requests, the first time a search or a
    publisher call needs them). Neither is a copy of the catalogue.
    """

    def __init__(self, registry: _Registry) -> None:
        self._registry = registry
        self._values: Dict[str, Dict[str, Dict[str, List[str]]]] = {}
        self._publishers: Optional[_Publishers] = None
        self._publisher_rows: Optional[List[Dict[str, Any]]] = None

    # -- what the registry holds -------------------------------------------

    def _base(self, kind: str) -> Q:
        return Q.rdf_type(_KINDS[kind][0])

    def _index(self, kind: str) -> Dict[str, Dict[str, List[str]]]:
        """``{filter: {short name: [the registry's values]}}`` for one kind."""
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
        themselves, 20 to a request. Built the way the database builds its
        own, by the same class, over datasets and data services -- so an id
        means the same organisation either way.
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
                        lambda batch: self._registry._lookup_many(batch, model=AgentEntry),
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
                    "publisher of a type does not fit in a request. Use the "
                    "local Catalog, or publishers(publisher_type=...) to pick "
                    "the ids yourself.")
            if name == "kind":
                raise QueryError(
                    "kind is not something the registry can search by: it is "
                    "worked out from each distribution after it is fetched. "
                    "Every record still carries it; use the local Catalog "
                    "to filter.")
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

        if name == "modified_after":
            # The dataset's `modified`, or its `issued` where it has no
            # `modified` -- the same rule the database applies.
            bound = _require_date(value, name)
            modified = Q.predicate_range(DCTERMS.modified, bound, None)
            issued = Q.predicate_range(DCTERMS.issued, bound, None)
            never = ~Q.term(_MODIFIED, "*", escape_value=False)
            return (modified | (issued & never).group()).group()

        values = _require_values(value, name)
        if name == "keyword":
            return self._keyword_clause(values)

        if name == "publisher":
            index = self._publisher_index()
            uris = [uri for item in values
                    for uri in index.uris.get(index.resolve(item), ())]
            return Q.any_of(_FIELDS[name], uris) if uris else _NOTHING

        unset = False
        if name == "access_rights":
            unset = any(slugify(str(item)) == _ACCESS_NONE for item in values)
            values = [item for item in values if slugify(str(item)) != _ACCESS_NONE]

        known = self._index(kind)[name]
        raw: List[str] = []
        for item in values:
            for slug in sorted(_local_slugs(item, name, set(known))):
                raw.extend(known.get(slug, ()))
        parts = []
        if raw:
            # A format is a string that may hold anything; the rest are URIs.
            parts.append(Q.phrase(_FIELDS[name], *raw) if name == "format"
                         else Q.any_of(_FIELDS[name], raw))
        if unset:
            parts.append(Q.raw("(*:* -%s:*)" % _FIELDS[name]))
        if not parts:
            return _NOTHING
        return Q.join(parts, "OR").group() if len(parts) > 1 else parts[0]

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
            Q.rdf_type(DCAT.Dataset, DCAT.DataService),
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

    def _query(self, kind: str, filters: Dict[str, Any]) -> Q:
        clauses = [self._clause(kind, name, value) for name, value in filters.items()]
        return Q.join([self._base(kind)] + clauses)

    # -- searching ---------------------------------------------------------

    def find(
        self,
        kind: str,
        filters: Dict[str, Any],
        *,
        query: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        facet_limit: Optional[int] = None,
        facets: bool = True,
    ) -> Tuple[List[Dict[str, Any]], int, Optional[Facets]]:
        """``(records, total, facets)``, as the database answers it.

        ``limit=None`` is every match from ``offset`` on: the registry serves
        100 to a page, so that is one request per hundred and the lookups of
        what each page refers to. Above :data:`LARGE` records it warns first.
        Pages are asked for one after another and the fetch stops at the
        first short or empty page -- the registry's ``total`` is an estimate,
        and a loop that trusted it could ask past the end.
        """
        if query is not None:
            filters = dict(filters, query=query)
        self._check(kind, filters)
        _, model, names = _KINDS[kind]
        q = self._query(kind, filters)
        first = MAX_LIMIT if limit is None else min(limit, MAX_LIMIT)
        extra = ({"facet_fields": [_FIELDS[name] for name in names],
                  "facet_limit": _FACET_LIMIT, "facet_min_count": 1}
                 if facets else {})
        page = self._registry._search(q, model=model, limit=first, offset=offset,
                                      sort=STABLE_SORT, **extra)
        total = page.total
        counted = self._facets(page.raw, names, facet_limit) if facets else None
        wanted = max(0, total - offset) if limit is None else limit
        if wanted > LARGE:
            pages = math.ceil(wanted / MAX_LIMIT)
            message = (
                "fetching %s records from the registry, %d pages of %d: about "
                "%d minutes. Pass limit= to take fewer, or use the local "
                "Catalog, which answers this at once."
                % (f"{wanted:,}", pages, MAX_LIMIT,
                   max(1, round(pages * _SECONDS_PER_PAGE / 60))))
            warnings.warn(DataportalWarning(message), stacklevel=4)
            logger.warning(message)

        entries: List[Entry] = list(page.entries[:min(first, wanted)])
        records = self._records(entries)
        progress = progress_reporter("live %ss" % kind.replace("_", " "),
                                     log_every=500)
        seen, step = page.entries, offset + len(page.entries)
        while (len(records) < wanted and len(seen) >= MAX_LIMIT
               and step < total):
            page = self._registry._search(q, model=model, limit=MAX_LIMIT,
                                          offset=step, sort=STABLE_SORT)
            seen = page.entries
            if not seen:
                break
            step += len(seen)
            records.extend(self._records(seen[:wanted - len(records)]))
            progress(len(records), wanted)
        return records, total, counted

    def count(self, kind: str) -> int:
        """How many records of one kind the registry holds."""
        return self._registry._count(self._base(kind))

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
                with_distributions=not isinstance(entry, DataServiceEntry))
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

    def get(self, uri: str, format: str = "dict") -> Any:
        """One dataset or data service by its URI, or ``None``.

        ``format="dict"`` is the record, in four requests. Any other format
        -- ``"turtle"``, ``"rdf/xml"``, ``"n-triples"`` or a media type -- is
        the entry's own RDF as text. Where two entries share a URI the same
        one answers every time.
        """
        if not isinstance(uri, str) or not uri.strip():
            return None
        page = self._registry._search(
            Q.resource(uri.strip()) & Q.rdf_type(DCAT.Dataset, DCAT.DataService),
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

    def _publisher_counts(self, filters: Dict[str, Any]) -> Dict[str, List[int]]:
        """``{id: [datasets, data services]}`` among what the filters select."""
        index = self._publisher_index()
        by_uri = {uri: pid for pid, uris in index.uris.items() for uri in uris}
        counts: Dict[str, List[int]] = {}
        for column, kind in enumerate(("dataset", "data_service")):
            raw = self._registry._search_raw(
                self._query(kind, filters), limit=1, sort=None,
                facet_fields=[_FIELDS["publisher"]],
                facet_limit=_FACET_LIMIT, facet_min_count=1)
            for value in _facet_values(raw).get(_FIELDS["publisher"], []):
                pid = by_uri.get(value.get("name"))
                if pid:
                    counts.setdefault(pid, [0, 0])[column] += int(
                        value.get("count") or 0)
        return counts

    def publishers(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """One row per publisher with something that matches, counts and all.

        Two facet requests, one per kind of record, once the publishers are
        known (about 20 requests the first time). ``publisher_type`` is read
        off each publisher rather than asked of the index, which has no such
        field. The unfiltered list is held for as long as the object lives.
        """
        if not filters and self._publisher_rows is not None:
            return self._publisher_rows
        types = None
        rest = {}
        for name, value in filters.items():
            if name not in PUBLISHER_FILTERS:
                raise QueryError("publishers have no %r. Available: %s"
                                 % (name, ", ".join(PUBLISHER_FILTERS)))
            if name == "publisher_type":
                types = {slugify(str(item)) for item in _require_values(value, name)}
            else:
                rest[name] = value
        index = self._publisher_index()
        rows = [_publisher_row(index.entities[pid], held, served)
                for pid, (held, served) in self._publisher_counts(rest).items()
                if types is None or index.entities[pid].get("type") in types]
        rows.sort(key=lambda row: (-row["dataset_count"], row["id"]))
        if not filters:
            self._publisher_rows = rows
        return rows

    def publisher(self, value: str) -> Optional[Tuple[Dict[str, Any], Facets]]:
        """One publisher's row and the facets of its datasets; ``None`` if
        it has nothing in the registry."""
        pid = self._publisher_index().resolve(value)
        row = next((row for row in self.publishers({}) if row["id"] == pid), None)
        if row is None:
            return None
        names = tuple(n for n in _PUBLISHER_FACETS if n in _FIELDS)
        raw = self._registry._search_raw(
            self._query("dataset", {"publisher": pid}), limit=1, sort=None,
            facet_fields=[_FIELDS[name] for name in names],
            facet_limit=_FACET_LIMIT, facet_min_count=1)
        return row, self._facets(raw, names, None)

    def info(self) -> Dict[str, Any]:
        """``{"datasets", "data_services", "publishers"}``: three counts."""
        return {
            "datasets": self.count("dataset"),
            "data_services": self.count("data_service"),
            "publishers": len(self.publishers({})),
        }
