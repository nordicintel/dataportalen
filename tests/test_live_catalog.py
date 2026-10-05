"""Catalog(live=True) offline: what it asks the registry, and what it makes of the answer.

A small fake registry stands in for ``admin.dataportal.se``. It answers a
facet request from a canned table, a ``resource:`` lookup from canned agents,
and anything else with the two real datasets of ``search_datasets.json`` --
enough to pin the queries that are sent and the records that come back. A
second fake, :class:`PagingRegistry`, holds any number of small synthetic
datasets and honours ``offset``, to pin how an unpaged search walks the
pages. That the real registry's answers agree with the local catalogue is the
live suite's job (``test_live.py``).
"""

from __future__ import annotations

import dataclasses
import json
import re
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import FakeTransport, load_fixture, write_catalog
from dataportalen import (
    Catalog,
    DataportalWarning,
    DataService,
    Dataset,
    Publisher,
    QueryError,
    SearchResult,
)
from dataportalen.core import Response
from dataportalen.live import _FIELDS, _KEYWORD, _MODIFIED
from dataportalen.query import predicate_field
from dataportalen.rdf import DCAT, DCTERMS, FOAF, RDF

# The fake holds no distributions, so every page of records reports them
# unresolved -- as a download would. One test asserts that; the rest ignore it.
pytestmark = pytest.mark.filterwarnings("ignore:.*referenced URIs")

TRV = "http://dataportal.se/organisation/SE2021006297"
TRV_OTHER = "https://example.org/agents/trafikverket"
OTHER = "https://example.org/agents/vaderverket"
TRANSPORT = "http://publications.europa.eu/resource/authority/data-theme/TRAN"
PUBLIC = "http://publications.europa.eu/resource/authority/access-right/PUBLIC"
FREQUENCY = "http://publications.europa.eu/resource/authority/frequency/"

#: What the fake registry's facets hold, by index field.
FACETS = {
    _FIELDS["publisher"]: [(TRV, 5), (TRV_OTHER, 2), (OTHER, 3),
                           ("https://example.org/agents/nobody", 1)],
    _FIELDS["theme"]: [(TRANSPORT, 6)],
    _FIELDS["format"]: [("text/csv", 4), ("application/zip", 2),
                        ("application/x-zip-compressed", 1),
                        ("Some Odd Format", 1)],
    _FIELDS["access_rights"]: [(PUBLIC, 9)],
    _FIELDS["accrual_periodicity"]: [(FREQUENCY + "CONT", 2)],
    _FIELDS["service_type"]: [
        ("http://www.wikidata.org/entity/Q749568", 4),
        ("http://inspire.ec.europa.eu/metadata-codelist/DegreeOfConformity/conformant", 2)],
    _KEYWORD: [("Kommun", 40), ("kommun", 2), (" KOMMUN ", 1), ("Öppna data", 7),
               ("kommunal", 3)],
}


def agent(context, entry, uri, name):
    """One foaf:Agent as a search hit."""
    return {
        "contextId": context, "entryId": entry,
        "info": {"https://admin.dataportal.se/store/%s/entry/%s" % (context, entry): {
            "http://entrystore.org/terms/resource": [{"type": "uri", "value": uri}]}},
        "metadata": {uri: {
            RDF.type: [{"type": "uri", "value": FOAF.Agent}],
            FOAF.name: [{"type": "literal", "lang": "sv", "value": name}],
        }},
    }


AGENTS = [agent("1", "1", TRV, "Trafikverket"),
          agent("2", "1", TRV_OTHER, "Trafikverket"),
          agent("3", "1", OTHER, "Väderverket")]


class FakeRegistry(FakeTransport):
    """Answers by what was asked rather than in the order it was queued."""

    def __init__(self):
        super().__init__()
        self.datasets = load_fixture("search_datasets.json")["resource"]["children"]

    def hits(self, offset, limit):
        """``(children, total)`` for a search of datasets. Ignores ``offset``:
        the same two datasets answer every page."""
        return self.datasets, 545

    def request(self, method, url, *, headers=None, timeout=None):
        self.requests.append(url)
        parsed = urlparse(url)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if not parsed.path.endswith("/store/search"):
            return Response(200, {"content-type": "text/turtle"},
                            b"@prefix dcat: <http://www.w3.org/ns/dcat#> .", url)
        query = params["query"]
        limit, offset = int(params["limit"]), int(params["offset"])
        if query.startswith("resource:") and "rdfType" not in query:
            # A lookup by URI: the agents asked for, and nothing else exists.
            children = [a for a in AGENTS
                        if next(iter(a["metadata"])).replace(":", "\\:").replace(
                            "/", "\\/") in query]
            total = len(children)
        elif "nothing\\-here" in query or "NOT *:*" in query:
            children, total = [], 0
        else:
            children, total = self.hits(offset, limit)
        fields = [f for f in params.get("facetFields", "").split(",") if f]
        matches = params.get("facetMatches")
        facets = []
        for field in fields:
            values = FACETS.get(field, [])
            if matches:                     # Java's (?iu) is Python's (?i)
                pattern = re.compile(matches.replace("(?iu)", "(?i)"))
                values = [v for v in values if pattern.fullmatch(v[0])]
            facets.append({"name": field, "valueCount": len(values),
                           "values": [{"name": n, "count": c} for n, c in values]})
        payload = {"offset": offset, "limit": limit,
                   "results": total,
                   "facetFields": facets,
                   "resource": {"children": children[:limit]}}
        return Response(200, {"content-type": "application/json"},
                        json.dumps(payload).encode("utf-8"), url)

    def queries(self):
        return [parse_qs(urlparse(u).query)["query"][0] for u in self.requests]


def synthetic(i):
    """A dataset of nothing but a type and a title, as a search hit."""
    uri = "https://example.org/datasets/%05d" % i
    return {
        "contextId": "9", "entryId": str(i),
        "info": {"https://admin.dataportal.se/store/9/entry/%d" % i: {
            "http://entrystore.org/terms/resource": [{"type": "uri", "value": uri}]}},
        "metadata": {uri: {
            RDF.type: [{"type": "uri", "value": DCAT.Dataset}],
            DCTERMS.title: [{"type": "literal", "lang": "sv", "value": "Data %d" % i}],
        }},
    }


class PagingRegistry(FakeRegistry):
    """``n`` synthetic datasets, served a page at a time from ``offset``.

    ``total`` is what the registry claims, which -- as in life -- need not be
    what it holds: a loop that trusted it would ask past the end.
    """

    def __init__(self, n, total=None):
        super().__init__()
        self.datasets = [synthetic(i) for i in range(n)]
        self.total = n if total is None else total

    def hits(self, offset, limit):
        return self.datasets[offset:offset + limit], self.total

    def pages(self):
        """``(offset, limit)`` of every search of datasets that was sent."""
        out = []
        for url in self.requests:
            params = parse_qs(urlparse(url).query)
            if "sort=uri" in url and not params["query"][0].startswith("resource:"):
                out.append((int(params["offset"][0]), int(params["limit"][0])))
        return out


def live_catalog(transport):
    with pytest.warns(DataportalWarning, match="asks the registry for every call"):
        return Catalog(live=True, _transport=transport)


@pytest.fixture
def registry():
    return FakeRegistry()


@pytest.fixture
def live(registry):
    with live_catalog(registry) as catalog:
        yield catalog


def last_query(registry):
    """The search itself -- not the lookups that fill in its records."""
    return [q for u, q in zip(registry.requests, registry.queries())
            if "sort=uri" in u and not q.startswith("resource:")][-1]


# -- building one --------------------------------------------------------------


def test_a_live_catalog_warns_when_it_is_built_and_reads_no_database(registry):
    catalog = live_catalog(registry)
    assert catalog.live and catalog.database is None
    assert registry.requests == []


@pytest.mark.parametrize("extra", [{"database": "x.sqlite"}, {"rebuild": True}])
def test_a_live_catalog_takes_no_database(registry, extra):
    with pytest.raises(QueryError, match="reads no database"):
        Catalog(live=True, _transport=registry, **extra)


def test_access_rights_is_not_an_argument_any_more(registry):
    with pytest.raises(TypeError):
        Catalog(live=True, access_rights=None, _transport=registry)


def test_live_true_on_a_live_catalog_warns_that_it_does_nothing(live):
    with pytest.warns(DataportalWarning, match="no effect"):
        live.search(limit=0, live=True)


# -- scope ---------------------------------------------------------------------


def test_there_is_no_implicit_scope(live, registry):
    live.search(limit=0)
    query = last_query(registry)
    assert query == "rdfType:http\\:\\/\\/www.w3.org\\/ns\\/dcat#Dataset AND public:true"


def test_access_rights_none_is_a_record_that_sets_none(live, registry):
    field = _FIELDS["access_rights"]
    live.search(access_rights="none", limit=0)
    query = last_query(registry)
    assert "(*:* -%s:*)" % field in query
    assert "AND ((*:* -%s:*)))" % field in query
    assert "\\/PUBLIC" not in query and " OR " not in query


def test_access_rights_none_or_a_value_is_any_of(live, registry):
    live.search(access_rights=["public", "none"], limit=0)
    query = last_query(registry)
    assert "(*:* -%s:*)" % _FIELDS["access_rights"] in query
    assert "\\/PUBLIC" in query and " OR " in query


def test_access_rights_takes_the_values_catalog_takes(live):
    with pytest.raises(QueryError):
        live.search(access_rights="secret", limit=0)


# -- paging --------------------------------------------------------------------


@pytest.mark.parametrize("limit", [-1, True])
def test_a_limit_that_is_not_a_count_is_refused(live, registry, limit):
    with pytest.raises(QueryError, match="limit must be 0 or more"):
        live.search(limit=limit)
    assert registry.requests == []


def test_datasets_takes_no_limit(live, registry):
    with pytest.raises(QueryError, match="search\\(limit=...\\) does"):
        live.datasets(limit=10)
    assert registry.requests == []


def test_limit_zero_is_the_count_and_the_facets_in_one_request(live, registry):
    result = live.search(theme="transport", limit=0)
    assert result.datasets == [] and result.total == 545 and result.limit == 0
    assert result.facets["theme"][0] == ("transport", 6)
    searches = [q for q in registry.queries() if "\\/TRAN" in q]
    assert len(searches) == 1


def test_limit_zero_fetches_no_records():
    registry = PagingRegistry(250)
    result = live_catalog(registry).search(limit=0)
    assert len(result) == 0 and result.total == 250 and result.has_more
    assert len(registry.pages()) == 1


def test_an_unpaged_search_fetches_every_page():
    registry = PagingRegistry(250)
    result = live_catalog(registry).search()
    assert len(result) == result.total == 250 and not result.has_more
    assert [d.uri for d in result][-1] == "https://example.org/datasets/00249"
    assert len({d.uri for d in result}) == 250
    assert registry.pages() == [(0, 100), (100, 100), (200, 100)]


def test_an_unpaged_search_stops_on_a_short_page():
    """The registry's total is an estimate; the end is the first short page."""
    registry = PagingRegistry(250, total=400)
    result = live_catalog(registry).search()
    assert len(result) == 250 and result.total == 400
    assert registry.pages() == [(0, 100), (100, 100), (200, 100)]


def test_an_unpaged_search_stops_on_an_empty_page():
    registry = PagingRegistry(200, total=400)
    assert len(live_catalog(registry).search()) == 200
    assert registry.pages() == [(0, 100), (100, 100), (200, 100)]


def test_a_limit_above_one_page_pages_past_it():
    registry = PagingRegistry(250)
    result = live_catalog(registry).search(limit=150)
    assert len(result) == 150 and result.has_more
    assert result.datasets[-1].uri == "https://example.org/datasets/00149"
    assert registry.pages() == [(0, 100), (100, 100)]


def test_an_unpaged_list_is_every_page_too():
    registry = PagingRegistry(250)
    found = live_catalog(registry).datasets()
    assert len(found) == 250 and all(isinstance(d, Dataset) for d in found)


def test_more_than_a_thousand_matches_warns_before_fetching():
    registry = PagingRegistry(150, total=1500)
    catalog = live_catalog(registry)
    with pytest.warns(DataportalWarning, match="fetching 1,500 records"):
        result = catalog.search()
    assert len(result) == 150


def test_a_thousand_or_fewer_or_a_limit_does_not_warn(recwarn):
    catalog = live_catalog(PagingRegistry(150, total=1500))
    catalog.search(limit=150)
    catalog = live_catalog(PagingRegistry(150, total=1000))
    catalog.search()
    assert not [w for w in recwarn if issubclass(w.category, DataportalWarning)]


def test_len_is_a_count_and_iteration_is_every_dataset():
    registry = PagingRegistry(150, total=150)
    catalog = live_catalog(registry)
    assert len(catalog) == 150
    assert [d.uri for d in catalog] == [d.uri for d in catalog.search()]


def test_a_negative_offset_is_refused(live):
    with pytest.raises(QueryError, match="offset"):
        live.search(offset=-1)


def test_offset_and_the_stable_sort_are_sent(live, registry):
    live.search(limit=2, offset=40)
    search = next(u for u in registry.requests if "offset=40" in u)
    assert "sort=uri%20asc" in search and "limit=2" in search


# -- filters as queries --------------------------------------------------------


@pytest.mark.parametrize("name, message", [
    ("language", "language is not a filter"),
    ("license", "license is not a filter"),
    ("issued_before", "issued_before is gone"),
    ("modified_before", "modified_before is gone"),
])
def test_a_removed_filter_says_what_to_do_and_asks_nothing(live, registry, name,
                                                           message):
    with pytest.raises(QueryError, match=message):
        live.search(limit=0, **{name: "sv"})
    assert registry.requests == []


def test_updated_is_accrual_periodicity_now(live, registry):
    with pytest.raises(QueryError, match="accrual_periodicity='continuous'"):
        live.search(updated="continuous", limit=0)
    live.search(accrual_periodicity="continuous", limit=0)
    assert "frequency\\/CONT" in last_query(registry)


def test_a_format_is_every_spelling_the_registry_holds_as_a_phrase(live, registry):
    """ZIP is stored under two media types; the package's table knows one."""
    live.search(format="zip", limit=0)
    assert '%s:("application/zip" OR "application/x-zip-compressed")' % (
        _FIELDS["format"]) in last_query(registry)


def test_a_value_the_registry_holds_but_no_table_knows_still_filters(live, registry):
    """Everything a facet reports can be fed back in."""
    row = next(r for r in live.facets()["format"] if r.value == "some_odd_format")
    live.search(format=row.value, limit=0)
    assert '"Some Odd Format"' in last_query(registry)


def test_a_known_value_nothing_carries_matches_nothing(live, registry):
    result = live.search(theme="energy", limit=0)
    assert "(*:* AND NOT *:*)" in last_query(registry)
    assert result.total == 0


def test_an_unknown_value_is_an_error_with_suggestions(live):
    with pytest.raises(QueryError, match="Did you mean: transport"):
        live.search(theme="transprt")


def test_a_list_is_any_of(live, registry):
    live.search(format=["csv", "zip"], limit=0)
    query = last_query(registry)
    assert '"text/csv" OR "application/zip" OR "application/x-zip-compressed"' in query


def test_modified_after_falls_back_to_issued(live, registry):
    live.search(modified_after="2025", limit=0)
    query = last_query(registry)
    issued = predicate_field(DCTERMS.issued, "date")
    assert ("(%s:[2025-01-01T00:00:00Z TO *] OR (%s:[2025-01-01T00:00:00Z TO *] "
            "AND NOT %s:*))" % (_MODIFIED, issued, _MODIFIED)) in query


def test_a_date_that_is_not_a_date_is_refused(live):
    with pytest.raises(QueryError, match="real date"):
        live.search(modified_after="2024-13-45")


def test_query_is_a_phrase_on_the_full_text_index(live, registry):
    live.search("air quality", limit=0)
    assert 'AND "air quality") AND public:true' in last_query(registry)


def test_a_blank_query_is_refused(live):
    with pytest.raises(QueryError, match="query needs a value"):
        live.search("  ")


def test_an_empty_filter_is_refused(live):
    with pytest.raises(QueryError, match="theme needs a value"):
        live.search(theme=[])


def test_publisher_type_is_refused_and_says_what_to_do(live, registry):
    with pytest.raises(QueryError, match="publisher_type is not something"):
        live.search(publisher_type="national_authority")
    assert registry.requests == []


def test_kind_is_refused_and_says_what_to_do(live, registry):
    with pytest.raises(QueryError, match="kind is not something"):
        live.datasets(kind="pxweb")
    assert registry.requests == []


def test_text_points_at_query_and_a_typo_is_unknown(live):
    with pytest.raises(QueryError, match="called 'query' now"):
        live.search(text="cykel")
    with pytest.raises(QueryError, match="unknown filter 'colour'"):
        live.search(colour="red")


def test_a_data_service_has_no_format_and_no_dates(live):
    with pytest.raises(QueryError, match="data services have no 'format'"):
        live.data_services(format="csv")
    with pytest.raises(QueryError, match="data services have no 'modified_after'"):
        live.data_services(modified_after="2024")


def test_a_search_too_long_for_the_registry_says_so(live):
    with pytest.raises(QueryError, match="ask for fewer values"):
        live.search("x" * 9000)


# -- keyword -------------------------------------------------------------------


def test_a_keyword_matches_every_spelling_in_use(live, registry):
    live.search(keyword="kommun", limit=0)
    asked = next(u for u in registry.requests if "facetMatches" in u)
    assert parse_qs(urlparse(asked).query)["facetMatches"][0] == r"(?iu)\s*(?:kommun)\s*"
    assert '%s:(" KOMMUN " OR "Kommun" OR "kommun")' % _KEYWORD in last_query(registry)
    assert "kommunal" not in last_query(registry)


def test_a_keyword_with_a_space_and_one_without_are_any_of(live, registry):
    live.search(keyword=["Öppna Data", "KOMMUN"], limit=0)
    query = last_query(registry)
    assert '"Öppna data"' in query and '"Kommun"' in query


def test_a_keyword_is_escaped_for_the_pattern(live, registry):
    with pytest.raises(QueryError):
        live.search(keyword="c++ (programming)")
    asked = next(u for u in registry.requests if "facetMatches" in u)
    pattern = parse_qs(urlparse(asked).query)["facetMatches"][0]
    assert r"c\+\+" in pattern and r"\(programming\)" in pattern


def test_an_unknown_keyword_is_an_error(live):
    with pytest.raises(QueryError, match="unknown keyword 'zzz'"):
        live.search(keyword=["kommun", "zzz"])


# -- facets --------------------------------------------------------------------


def test_facets_are_the_registry_s_minus_what_it_cannot_count(live):
    facets = live.facets()
    assert list(facets) == ["publisher", "theme", "format", "access_rights",
                            "accrual_periodicity"]
    for name in ("keyword", "publisher_type", "kind", "license", "language",
                 "updated"):
        assert name not in facets


def test_spellings_of_one_value_are_one_row(live):
    facets = live.facets()
    assert ("zip", 3) in facets["format"]
    assert facets["accrual_periodicity"] == [("continuous", 2)]
    assert facets["theme"][0].label["en"] == "Transport"


def test_the_publisher_facet_is_by_id_with_the_publisher_s_name(live):
    rows = live.facets()["publisher"]
    assert rows == [("trafikverket", 7), ("vaderverket", 3)]
    assert rows[0].label == {"sv": "Trafikverket"}


def test_a_nested_conformity_verdict_is_not_a_service_type(live, registry):
    """Data services have no public facets; the backend still counts them."""
    _, _, facets = live._live().find("data_service", {}, limit=0)
    assert list(facets) == ["publisher", "service_type", "theme", "access_rights"]
    assert facets["service_type"] == [("rest", 4)]
    live.data_services(service_type="rest")
    query = last_query(registry)
    assert "Q749568" in query and "DegreeOfConformity" not in query


def test_facet_limit_cuts_and_counts(live):
    facets = live.search(limit=0, facet_limit=1).facets
    assert len(facets["format"]) == 1 and facets.omitted["format"] == 2


def test_facets_counts_is_the_compact_shape(live):
    assert live.facets().counts()["format"] == {
        "csv": 4, "zip": 3, "some_odd_format": 1}


# -- records -------------------------------------------------------------------


def test_a_page_is_dataset_models(live):
    # The fake holds no distributions, and an unresolved file is reported
    # here as it is by a download -- never dropped in silence.
    with pytest.warns(UserWarning, match="referenced URIs were not found"):
        result = live.search(limit=2)
    assert isinstance(result, SearchResult)
    assert len(result) == 2 and result.total == 545 and result.has_more
    fields = {f.name for f in dataclasses.fields(Dataset)}
    for dataset in result:
        assert isinstance(dataset, Dataset)
        assert set(dataset.to_dict()) == fields
        assert dataset.publisher.id == "trafikverket"
        assert dataset.publisher.name == {"sv": "Trafikverket"}


def test_as_dict_is_the_models_to_dict(live):
    assert live.search(limit=2, as_dict=True) == live.search(limit=2).to_dict()


def test_a_record_carries_no_link_health(live):
    for dataset in live.search(limit=2):
        for dist in dataset.distributions:
            assert dist.broken is None and dist.unverified is None


def test_get_is_one_model_or_none(live, registry):
    dataset = live.get("https://metadata.trafikverket.se/store/1/resource/735")
    assert isinstance(dataset, Dataset)
    assert dataset.type == "dataset" and dataset.context_id == "50"
    asked = next(q for q in registry.queries()
                 if "resource:" in q and "rdfType" in q)
    assert "dcat#Dataset OR " in asked and _FIELDS["access_rights"] not in asked
    assert live.get("https://example.org/nothing-here") is None
    assert live.get("") is None and live.get(None) is None


def test_get_as_rdf_is_the_entry_s_own(live, registry):
    text = live.get("https://metadata.trafikverket.se/store/1/resource/735", "turtle")
    assert text.startswith("@prefix")
    assert "/store/50/metadata/6088" in registry.requests[-1]
    assert "format=text%2Fturtle" in registry.requests[-1]


# -- one call live, on a local catalogue ---------------------------------------


@pytest.fixture
def local(registry, tmp_path):
    with Catalog(write_catalog(tmp_path), max_age=None, _transport=registry) as catalog:
        yield catalog


def test_a_local_catalog_asks_nothing_until_one_call_says_live(local, registry):
    local.search()
    local.datasets()
    assert registry.requests == []
    local.search(limit=0, live=True)
    assert registry.requests != []


def test_one_call_live_gives_the_same_models(local):
    here, there = local.search(limit=1), local.search(limit=1, live=True)
    assert type(here) is type(there) is SearchResult
    assert type(here.datasets[0]) is type(there.datasets[0]) is Dataset
    assert set(here.datasets[0].to_dict()) == set(there.datasets[0].to_dict())
    assert ("transport", 1) in here.facets["theme"]
    assert there.total == 545
    assert all(isinstance(d, Dataset) for d in local.datasets(live=True))
    assert all(isinstance(s, DataService) for s in local.data_services(live=True))
    assert all(isinstance(p, Publisher) for p in local.publishers(live=True))


def test_one_call_live_has_the_live_refusals(local):
    local.search(publisher_type="national_authority", limit=0)
    with pytest.raises(QueryError, match="publisher_type is not something"):
        local.search(publisher_type="national_authority", limit=0, live=True)


# -- publishers ----------------------------------------------------------------


def test_publishers_are_publisher_models(live):
    rows = live.publishers()
    assert [(r.id, r.dataset_count, r.data_service_count) for r in rows] == [
        ("trafikverket", 7, 7), ("vaderverket", 3, 3)]
    assert all(isinstance(row, Publisher) for row in rows)
    # The agent on most records describes the publisher; both URIs find it.
    assert rows[0].uri == TRV


def test_the_publisher_index_is_fetched_once(live, registry):
    live.publishers()
    live.facets()
    before = len(registry.requests)
    live.publishers()
    live.publisher("trafikverket")
    lookups = [q for q in registry.queries() if q.startswith("resource:")]
    assert len(lookups) == 1
    assert len(registry.requests) == before + 1        # publisher()'s one search


def test_publisher_filter_asks_for_every_uri_the_publisher_goes_by(live, registry):
    for value in ("trafikverket", "Trafikverket", TRV_OTHER):
        live.search(publisher=value, limit=0)
        query = last_query(registry)
        assert "SE2021006297" in query and "agents\\/trafikverket" in query


def test_publisher_is_a_model_plus_its_facets(live):
    detail = live.publisher("Väderverket")
    assert isinstance(detail, Publisher)
    assert detail.id == "vaderverket" and detail.dataset_count == 3
    assert list(detail.facets) == ["theme", "format", "access_rights",
                                   "accrual_periodicity"]
    assert detail.facets.counts()["accrual_periodicity"] == {"continuous": 2}


def test_a_known_publisher_with_nothing_here_is_none_and_an_unknown_one_raises(live):
    assert live.publisher("skolverket") is None
    assert live.search(publisher="skolverket", limit=0).total == 0
    with pytest.raises(QueryError, match="unknown publisher"):
        live.publisher("nosuchorganisation")


def test_info_is_three_counts(live):
    assert live.info() == {"datasets": 545, "data_services": 545, "publishers": 2,
                           "live": True, "sources": None}


def test_there_is_no_livecatalog_any_more():
    import dataportalen

    assert "LiveCatalog" not in dataportalen.__all__
    assert not hasattr(dataportalen, "LiveCatalog")
