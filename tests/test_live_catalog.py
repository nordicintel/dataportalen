"""LiveCatalog offline: what it asks the registry, and what it makes of the answer.

A small fake registry stands in for ``admin.dataportal.se``. It answers a
facet request from a canned table, a ``resource:`` lookup from canned agents,
and anything else with the two real datasets of ``search_datasets.json`` --
enough to pin the queries that are sent and the records that come back. That
the real registry's answers agree with the local catalogue is the live
suite's job (``test_live.py``).
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import FakeTransport, load_fixture
from dataportalen import Catalog, DatasetRecord, LiveCatalog, Publisher, QueryError
from dataportalen.core import Response
from dataportalen.live import _FIELDS, _KEYWORD
from dataportalen.query import predicate_field
from dataportalen.rdf import DCTERMS, FOAF, RDF
from test_records import problems

# The fake holds no distributions, so every page of records reports them
# unresolved -- as a download would. One test asserts that; the rest ignore it.
pytestmark = pytest.mark.filterwarnings("ignore:.*referenced URIs")

TRV = "http://dataportal.se/organisation/SE2021006297"
TRV_OTHER = "https://example.org/agents/trafikverket"
OTHER = "https://example.org/agents/vaderverket"
SWEDISH = ["http://publications.europa.eu/resource/authority/language/SWE",
           "http://id.loc.gov/vocabulary/iso639-1/sv"]
TRANSPORT = "http://publications.europa.eu/resource/authority/data-theme/TRAN"
PUBLIC = "http://publications.europa.eu/resource/authority/access-right/PUBLIC"

#: What the fake registry's facets hold, by index field.
FACETS = {
    _FIELDS["publisher"]: [(TRV, 5), (TRV_OTHER, 2), (OTHER, 3),
                           ("https://example.org/agents/nobody", 1)],
    _FIELDS["theme"]: [(TRANSPORT, 6)],
    _FIELDS["format"]: [("text/csv", 4), ("application/zip", 2),
                        ("application/x-zip-compressed", 1),
                        ("Some Odd Format", 1)],
    _FIELDS["license"]: [("http://creativecommons.org/publicdomain/zero/1.0/", 7)],
    _FIELDS["access_rights"]: [(PUBLIC, 9)],
    _FIELDS["updated"]: [
        ("http://publications.europa.eu/resource/authority/frequency/CONT", 2)],
    _FIELDS["language"]: [(SWEDISH[0], 5), (SWEDISH[1], 3)],
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

    def request(self, method, url, *, headers=None, timeout=None):
        self.requests.append(url)
        parsed = urlparse(url)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if not parsed.path.endswith("/store/search"):
            return Response(200, {"content-type": "text/turtle"},
                            b"@prefix dcat: <http://www.w3.org/ns/dcat#> .", url)
        query = params["query"]
        if query.startswith("resource:") and "rdfType" not in query:
            # A lookup by URI: the agents asked for, and nothing else exists.
            children = [a for a in AGENTS
                        if next(iter(a["metadata"])).replace(":", "\\:").replace(
                            "/", "\\/") in query]
            total = len(children)
        elif "nothing\\-here" in query or "NOT *:*" in query:
            children, total = [], 0
        else:
            children, total = self.datasets, 545
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
        limit = int(params["limit"])
        payload = {"offset": int(params["offset"]), "limit": limit,
                   "results": total,
                   "facetFields": facets,
                   "resource": {"children": children[:limit]}}
        return Response(200, {"content-type": "application/json"},
                        json.dumps(payload).encode("utf-8"), url)

    def queries(self):
        return [parse_qs(urlparse(u).query)["query"][0] for u in self.requests]


@pytest.fixture
def registry():
    return FakeRegistry()


@pytest.fixture
def live(registry):
    with LiveCatalog(_transport=registry) as catalog:
        yield catalog


def last_query(registry):
    """The search itself -- not the lookups that fill in its records."""
    return [q for u, q in zip(registry.requests, registry.queries())
            if "sort=uri" in u and not q.startswith("resource:")][-1]


# -- scope ---------------------------------------------------------------------


def test_the_default_scope_is_public_and_rides_on_every_search(live, registry):
    live.datasets(limit=0)
    query = last_query(registry)
    assert query.startswith("(rdfType:http\\:\\/\\/www.w3.org\\/ns\\/dcat#Dataset AND (")
    assert "\\/PUBLIC)" in query
    assert query.endswith("AND public:true")


def test_a_record_that_sets_no_access_rights_is_none(registry):
    with LiveCatalog(access_rights=("public", "none"), _transport=registry) as live:
        live.datasets(limit=0)
    field = _FIELDS["access_rights"]
    assert "(*:* -%s:*)" % field in last_query(registry)
    assert " OR " in last_query(registry)


def test_no_scope_sends_no_access_clause(registry):
    with LiveCatalog(access_rights=None, _transport=registry) as live:
        live.datasets(limit=0)
    assert _FIELDS["access_rights"] not in last_query(registry)


def test_the_scope_takes_the_values_catalog_takes(registry):
    with pytest.raises(QueryError, match="access_rights takes"):
        LiveCatalog(access_rights=("secret",), _transport=registry)


# -- one page, and no more -----------------------------------------------------


@pytest.mark.parametrize("limit", [None, 101, -1, True, "10"])
def test_limit_is_a_page(live, registry, limit):
    with pytest.raises(QueryError, match="limit must be 0 to 100"):
        live.datasets(limit=limit)
    assert registry.requests == []


def test_limit_zero_is_the_count_and_the_facets_in_one_request(live, registry):
    page = live.datasets(theme="transport", limit=0)
    assert list(page) == [] and page.total == 545 and page.limit == 0
    assert page.facets["theme"][0] == ("transport", 6)
    searches = [q for q in registry.queries() if "\\/TRAN" in q]
    assert len(searches) == 1


def test_there_is_no_len_and_no_iteration(live):
    with pytest.raises(TypeError):
        len(live)
    with pytest.raises(TypeError):
        iter(live)


def test_a_negative_offset_is_refused(live):
    with pytest.raises(QueryError, match="offset"):
        live.datasets(offset=-1)


def test_offset_and_the_stable_sort_are_sent(live, registry):
    live.datasets(limit=2, offset=40)
    search = next(u for u in registry.requests if "offset=40" in u)
    assert "sort=uri%20asc" in search and "limit=2" in search


# -- filters as queries --------------------------------------------------------


def test_a_value_becomes_every_spelling_the_registry_holds(live, registry):
    """Swedish is stored under three URIs; the package's table knows one."""
    live.datasets(language="sv", limit=0)
    query = last_query(registry)
    assert "language\\/SWE" in query and "iso639\\-1\\/sv" in query


def test_a_format_is_matched_as_a_phrase(live, registry):
    live.datasets(format="zip", limit=0)
    assert '%s:("application/zip" OR "application/x-zip-compressed")' % (
        _FIELDS["format"]) in last_query(registry)


def test_a_value_the_registry_holds_but_no_table_knows_still_filters(live, registry):
    """Everything a facet reports can be fed back in."""
    row = next(r for r in live.facets()["format"] if r.value == "some_odd_format")
    live.datasets(format=row.value, limit=0)
    assert '"Some Odd Format"' in last_query(registry)


def test_a_known_value_nothing_carries_matches_nothing(live, registry):
    page = live.datasets(theme="energy", limit=0)
    assert "(*:* AND NOT *:*)" in last_query(registry)
    assert page.total == 0


def test_an_unknown_value_is_an_error_with_suggestions(live):
    with pytest.raises(QueryError, match="Did you mean: transport"):
        live.datasets(theme="transprt")


def test_a_list_is_any_of(live, registry):
    live.datasets(format=["csv", "zip"], limit=0)
    query = last_query(registry)
    assert '"text/csv" OR "application/zip" OR "application/x-zip-compressed"' in query


def test_the_date_filters_are_ranges_on_the_date_index(live, registry):
    live.datasets(modified_after="2025", issued_before="2015-06", limit=0)
    query = last_query(registry)
    assert "%s:[2025-01-01T00:00:00Z TO *]" % predicate_field(
        DCTERMS.modified, "date") in query
    assert "%s:[* TO 2015-06-01T00:00:00Z]" % predicate_field(
        DCTERMS.issued, "date") in query


def test_a_date_that_is_not_a_date_is_refused(live):
    with pytest.raises(QueryError, match="real date"):
        live.datasets(modified_after="2024-13-45")


def test_query_is_a_phrase_on_the_full_text_index(live, registry):
    live.datasets(query="air quality", limit=0)
    assert 'AND "air quality") AND public:true' in last_query(registry)


def test_a_blank_query_is_refused(live):
    with pytest.raises(QueryError, match="query needs a value"):
        live.datasets(query="  ")


def test_an_empty_filter_is_refused(live):
    with pytest.raises(QueryError, match="theme needs a value"):
        live.datasets(theme=[])


def test_publisher_type_is_refused_and_says_what_to_do(live, registry):
    with pytest.raises(QueryError, match="publisher_type is not something"):
        live.datasets(publisher_type="national_authority")
    assert registry.requests == []


def test_text_points_at_query_and_a_typo_is_unknown(live):
    with pytest.raises(QueryError, match="called 'query' now"):
        live.datasets(text="cykel")
    with pytest.raises(QueryError, match="unknown filter 'colour'"):
        live.datasets(colour="red")


def test_a_data_service_has_no_format_and_no_dates(live):
    with pytest.raises(QueryError, match="data services have no 'format'"):
        live.data_services(format="csv")
    with pytest.raises(QueryError, match="data services have no 'modified_after'"):
        live.data_services(modified_after="2024")


def test_a_search_too_long_for_the_registry_says_so(live):
    with pytest.raises(QueryError, match="ask for fewer values"):
        live.datasets(query="x" * 9000)


# -- keyword -------------------------------------------------------------------


def test_a_keyword_matches_every_spelling_in_use(live, registry):
    live.datasets(keyword="kommun", limit=0)
    asked = next(u for u in registry.requests if "facetMatches" in u)
    assert parse_qs(urlparse(asked).query)["facetMatches"][0] == r"(?iu)\s*(?:kommun)\s*"
    assert '%s:(" KOMMUN " OR "Kommun" OR "kommun")' % _KEYWORD in last_query(registry)
    assert "kommunal" not in last_query(registry)


def test_a_keyword_with_a_space_and_one_without_are_any_of(live, registry):
    live.datasets(keyword=["Öppna Data", "KOMMUN"], limit=0)
    query = last_query(registry)
    assert '"Öppna data"' in query and '"Kommun"' in query


def test_a_keyword_is_escaped_for_the_pattern(live, registry):
    with pytest.raises(QueryError):
        live.datasets(keyword="c++ (programming)")
    asked = next(u for u in registry.requests if "facetMatches" in u)
    pattern = parse_qs(urlparse(asked).query)["facetMatches"][0]
    assert r"c\+\+" in pattern and r"\(programming\)" in pattern


def test_an_unknown_keyword_is_an_error(live):
    with pytest.raises(QueryError, match="unknown keyword 'zzz'"):
        live.datasets(keyword=["kommun", "zzz"])


# -- facets --------------------------------------------------------------------


def test_facets_are_the_registry_s_minus_what_it_cannot_count(live):
    facets = live.facets()
    assert list(facets) == ["publisher", "theme", "format", "license",
                            "access_rights", "updated", "language"]
    assert "keyword" not in facets and "publisher_type" not in facets


def test_spellings_of_one_value_are_one_row(live):
    facets = live.facets()
    assert facets["language"] == [("sv", 8)]
    assert ("zip", 3) in facets["format"]
    assert facets["language"][0].label["en"] == "Swedish"


def test_the_publisher_facet_is_by_id_with_the_publisher_s_name(live):
    rows = live.facets()["publisher"]
    assert rows == [("trafikverket", 7), ("vaderverket", 3)]
    assert rows[0].label == {"sv": "Trafikverket"}


def test_a_nested_conformity_verdict_is_not_a_service_type(live):
    facets = live.data_services(limit=0).facets
    assert list(facets) == ["publisher", "service_type", "theme", "license",
                            "access_rights"]
    assert facets["service_type"] == [("rest", 4)]


def test_facet_limit_cuts_and_counts(live):
    facets = live.datasets(limit=0, facet_limit=1).facets
    assert len(facets["format"]) == 1 and facets.omitted["format"] == 2


# -- records -------------------------------------------------------------------


def test_a_page_is_records_of_the_declared_shape(live):
    # The fake holds no distributions, and an unresolved file is reported
    # here as it is by a download -- never dropped in silence.
    with pytest.warns(UserWarning, match="referenced URIs were not found"):
        page = live.datasets(limit=2)
    assert len(page) == 2 and page.total == 545 and page.has_more
    for record in page:
        assert problems(record, DatasetRecord) == []
        assert record["publisher"]["id"] == "trafikverket"
        assert record["publisher"]["name"] == {"sv": "Trafikverket"}


def test_a_record_carries_no_link_health(live):
    for record in live.datasets(limit=2):
        for dist in record["distributions"]:
            assert "broken" not in dist and "unverified" not in dist


def test_get_is_one_record_or_none(live, registry):
    record = live.get("https://metadata.trafikverket.se/store/1/resource/735")
    assert record["type"] == "dataset" and record["context_id"] == "50"
    asked = next(q for q in registry.queries()
                 if "resource:" in q and "rdfType" in q)
    assert "dcat#Dataset OR " in asked and "\\/PUBLIC)" in asked
    assert live.get("https://example.org/nothing-here") is None
    assert live.get("") is None and live.get(None) is None


def test_get_as_rdf_is_the_entry_s_own(live, registry):
    text = live.get("https://metadata.trafikverket.se/store/1/resource/735", "turtle")
    assert text.startswith("@prefix")
    assert "/store/50/metadata/6088" in registry.requests[-1]
    assert "format=text%2Fturtle" in registry.requests[-1]


# -- publishers ----------------------------------------------------------------


def test_publishers_are_rows_of_the_declared_shape(live):
    rows = live.publishers()
    assert [(r["id"], r["dataset_count"], r["data_service_count"]) for r in rows] == [
        ("trafikverket", 7, 7), ("vaderverket", 3, 3)]
    for row in rows:
        assert problems(row, Publisher) == []
    # The agent on most records describes the publisher; both URIs find it.
    assert rows[0]["uri"] == TRV


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
        live.datasets(publisher=value, limit=0)
        query = last_query(registry)
        assert "SE2021006297" in query and "agents\\/trafikverket" in query


def test_publisher_is_a_row_plus_its_facets(live):
    detail = live.publisher("Väderverket")
    assert detail["id"] == "vaderverket" and detail["dataset_count"] == 3
    assert list(detail["facets"]) == ["theme", "format", "license",
                                      "access_rights", "updated", "language"]
    assert detail["facets"]["language"] == {"sv": 8}


def test_a_known_publisher_with_nothing_here_is_none_and_an_unknown_one_raises(live):
    assert live.publisher("skolverket") is None
    assert live.datasets(publisher="skolverket", limit=0).total == 0
    with pytest.raises(QueryError, match="unknown publisher"):
        live.publisher("nosuchorganisation")


def test_info_is_three_counts(live):
    assert live.info() == {"datasets": 545, "data_services": 545, "publishers": 2}


# -- the two classes are one interface -----------------------------------------


def test_livecatalog_has_catalog_s_methods_with_catalog_s_arguments():
    import inspect

    for name in ("datasets", "data_services", "facets", "publishers", "publisher",
                 "get", "info", "close"):
        mine = inspect.signature(getattr(LiveCatalog, name))
        theirs = inspect.signature(getattr(Catalog, name))
        assert list(mine.parameters) == list(theirs.parameters), name


def test_it_is_exported():
    import dataportalen

    assert "LiveCatalog" in dataportalen.__all__
