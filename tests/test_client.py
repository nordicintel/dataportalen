"""Client behaviour: request shaping, paging, lookups, retries."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from conftest import FakeTransport, load_fixture

from dataportal_se import (
    Dataportal,
    Dataset,
    HTTPError,
    NotFoundError,
    Q,
    RateLimitError,
    ServerError,
    TransportError,
)
from dataportal_se.namespaces import DCAT


def query_of(url: str) -> str:
    return parse_qs(urlparse(url).query)["query"][0]


def params_of(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


# -- request shaping ---------------------------------------------------------


def test_search_sends_solr_parameters(client, transport, search_response):
    transport.push(search_response)
    client.search(Q.rdf_type(DCAT.Dataset), limit=10, offset=20)
    params = params_of(transport.requests[-1])
    assert params["type"] == "solr"
    assert params["limit"] == "10"
    assert params["offset"] == "20"
    assert params["sort"] == "modified desc"
    assert "rdfType" in params["query"]


def test_public_filter_is_added_by_default(client, transport, search_response):
    transport.push(search_response)
    client.search(Q.title("x"))
    assert "public:true" in query_of(transport.requests[-1])


def test_public_filter_can_be_turned_off(transport, search_response):
    transport.push(search_response)
    with Dataportal(transport=transport, public_only=False) as dp:
        dp.search(Q.title("x"))
    assert "public:true" not in query_of(transport.requests[-1])


def test_an_empty_query_becomes_match_all(transport, search_response):
    transport.push(search_response)
    with Dataportal(transport=transport, public_only=False) as dp:
        dp.search()
    assert query_of(transport.requests[-1]) == "*:*"


def test_limit_is_clamped_to_the_solr_maximum(client, transport, search_response):
    transport.push(search_response)
    client.search(limit=5000)
    assert params_of(transport.requests[-1])["limit"] == "100"


def test_colons_in_uris_are_escaped_in_the_query(client, transport, search_response):
    transport.push(search_response)
    client.lookup("http://example.org/dataset1")
    assert r"http\:\/\/example.org\/dataset1" in query_of(transport.requests[-1])


def test_facet_parameters_are_passed_through(client, transport, search_response):
    transport.push(search_response)
    client.search(facet_fields=["rdfType", "lang"], facet_limit=7, facet_min_count=2)
    params = params_of(transport.requests[-1])
    assert params["facetFields"] == "rdfType,lang"
    assert params["facetLimit"] == "7"
    assert params["facetMinCount"] == "2"


def test_datasets_filters_compose_into_one_query(client, transport, search_response):
    transport.push(search_response)
    client.datasets(title="bidrag", title_lang="sv", keyword="cykel", language="swe")
    query = query_of(transport.requests[-1])
    assert "title.sv:bidrag" in query
    assert "tag.literal:cykel" in query
    assert "lang:swe" in query
    assert "rdfType" in query


def test_context_filter_uses_the_context_resource_uri(client, transport, search_response):
    transport.push(search_response)
    client.datasets(context=50)
    assert r"store\/50" in query_of(transport.requests[-1])


# -- responses ---------------------------------------------------------------


def test_search_page_exposes_totals_and_typed_entries(client, transport, search_response):
    transport.push(search_response)
    page = client.search()
    assert page.total == search_response["results"]
    assert len(page) == len(search_response["resource"]["children"])
    assert all(isinstance(e, Dataset) for e in page)
    assert page[0].title


def test_search_page_is_a_sequence(client, transport, search_response):
    transport.push(search_response)
    page = client.search()
    assert list(page) == page.entries
    assert page[0] is page.entries[0]


def test_count_asks_for_a_single_row(client, transport, search_response):
    transport.push(search_response)
    assert client.count() == search_response["results"]
    assert params_of(transport.requests[-1])["limit"] == "1"


def test_facet_returns_an_empty_facet_when_the_server_sends_none(
    client, transport, search_response
):
    transport.push(search_response)
    facet = client.facet("rdfType")
    assert facet.name == "rdfType"
    assert facet.as_dict() == {}


# -- paging ------------------------------------------------------------------


def _page(children, total, offset, limit):
    return {
        "results": total,
        "offset": offset,
        "limit": limit,
        "resource": {"children": children},
        "facetFields": [],
    }


def test_iter_search_walks_pages_until_exhausted(client, transport, search_response):
    children = search_response["resource"]["children"]
    transport.push(_page(children, 4, 0, 2))
    transport.push(_page(children, 4, 2, 2))
    entries = list(client.iter_search(page_size=2))
    assert len(entries) == 4
    assert params_of(transport.requests[0])["offset"] == "0"
    assert params_of(transport.requests[1])["offset"] == "2"


def test_iter_search_honours_an_overall_limit(client, transport, search_response):
    children = search_response["resource"]["children"]
    transport.push(_page(children, 100, 0, 2))
    entries = list(client.iter_search(page_size=2, limit=1))
    assert len(entries) == 1
    assert len(transport.requests) == 1


def test_iter_search_stops_on_an_empty_page(client, transport):
    transport.push(_page([], 100, 0, 2))
    assert list(client.iter_search(page_size=2)) == []


def test_next_page_continues_from_the_current_offset(client, transport, search_response):
    children = search_response["resource"]["children"]
    transport.push(_page(children, 10, 0, 2))
    page = client.search(limit=2)
    assert page.has_more
    transport.push(_page(children, 10, 2, 2))
    following = page.next_page()
    assert following is not None
    assert params_of(transport.requests[-1])["offset"] == "2"


def test_last_page_reports_no_more(client, transport, search_response):
    children = search_response["resource"]["children"]
    transport.push(_page(children, 2, 0, 2))
    page = client.search(limit=2)
    assert not page.has_more
    assert page.next_page() is None


# -- single entries ----------------------------------------------------------


def test_entry_fetches_metadata_and_envelope(client, transport):
    transport.push(load_fixture("dataset_recursive.json"))
    transport.push(load_fixture("dataset_entry.json"))
    entry = client.entry(547, 28672, model=Dataset)
    assert entry.title
    assert entry.resource_uri
    assert len(transport.requests) == 2
    assert "/store/547/metadata/28672" in transport.requests[0]
    assert params_of(transport.requests[0])["recursive"] == "dcat"
    assert "/store/547/entry/28672" in transport.requests[1]


def test_entry_can_skip_the_envelope_request(client, transport):
    transport.push(load_fixture("dataset_recursive.json"))
    entry = client.entry(547, 28672, with_info=False, model=Dataset)
    assert len(transport.requests) == 1
    assert entry.title


def test_entry_raw_rejects_unknown_parts(client):
    with pytest.raises(ValueError):
        client.entry_raw(1, 2, part="nonsense")


def test_lookup_returns_none_when_nothing_matches(client, transport):
    transport.push(_page([], 0, 0, 1))
    assert client.lookup("http://example.org/missing") is None


def test_lookup_caches_by_uri(client, transport, search_response):
    transport.push(search_response)
    entry = client.lookup(search_response["resource"]["children"][0]["info"] and
                          "http://example.org/x")
    assert entry is not None
    again = client.lookup("http://example.org/x")
    assert again is entry
    assert len(transport.requests) == 1


def test_lookup_many_batches_into_one_request(client, transport, search_response):
    transport.push(search_response)
    uris = ["http://example.org/%d" % i for i in range(5)]
    client.lookup_many(uris, batch_size=20)
    assert len(transport.requests) == 1
    assert query_of(transport.requests[-1]).count(" OR ") == 4


def test_lookup_many_splits_oversized_batches(client, transport, search_response):
    transport.push(search_response)
    transport.push(search_response)
    client.lookup_many(["http://example.org/%d" % i for i in range(4)], batch_size=2)
    assert len(transport.requests) == 2


# -- statistics endpoints ----------------------------------------------------


def test_organisations_zips_labels_values_and_series(transport, org_data):
    transport.routes["/charts/orgData.json"] = org_data
    with Dataportal(transport=transport) as dp:
        orgs = dp.organisations()
    assert len(orgs) == len(org_data["values"])
    assert orgs[0].name == org_data["labels"][0]
    assert orgs[0].dataset_count == org_data["series"][0][0]
    assert orgs[0].to_dict()["uri"] == org_data["values"][0]


def test_organisation_summary_reports_registry_totals(transport, org_data):
    transport.routes["/charts/orgData.json"] = org_data
    with Dataportal(transport=transport) as dp:
        summary = dp.organisation_summary()
    assert summary["datasets"] == org_data["datasetCount"]
    assert summary["publishers"] == org_data["publisherCount"]


def test_catalog_statistics_sorts_newest_first(client, transport):
    transport.push(load_fixture("catalog_statistics.json"))
    stats = client.catalog_statistics(limit=1)
    assert stats and stats[0].dataset_count
    assert params_of(transport.requests[-1])["sort"] == "modified desc"


# -- the dump ----------------------------------------------------------------


def test_download_dump_streams_to_disk(client, transport, tmp_path):
    transport.push(b"<rdf:RDF/>", content_type="application/rdf+xml")
    destination = tmp_path / "all.rdf"
    client.download_dump(str(destination))
    assert destination.read_bytes() == b"<rdf:RDF/>"


def test_download_dump_reports_progress(client, transport, tmp_path):
    transport.push(b"x" * 100, content_type="application/rdf+xml")
    seen = []
    client.download_dump(str(tmp_path / "all.rdf"), progress=seen.append)
    assert seen and seen[-1] == 100


# -- errors and retries ------------------------------------------------------


def test_404_raises_not_found(client, transport):
    transport.push({"error": "gone"}, status=404)
    with pytest.raises(NotFoundError) as info:
        client.search()
    assert info.value.status == 404


def test_5xx_raises_server_error_after_retries_are_exhausted(transport):
    for _ in range(3):
        transport.push({"error": "boom"}, status=503)
    with Dataportal(transport=transport, max_retries=2, backoff_factor=0) as dp:
        with pytest.raises(ServerError):
            dp.search()
    assert len(transport.requests) == 3


def test_a_transient_5xx_is_retried_then_succeeds(transport, search_response):
    transport.push({"error": "boom"}, status=503)
    transport.push(search_response)
    with Dataportal(transport=transport, max_retries=2, backoff_factor=0) as dp:
        page = dp.search()
    assert page.total == search_response["results"]
    assert len(transport.requests) == 2


def test_429_carries_retry_after(transport):
    transport.push({"error": "slow down"}, status=429)
    with Dataportal(transport=transport, max_retries=0) as dp:
        with pytest.raises(RateLimitError):
            dp.search()


def test_other_4xx_raises_plain_http_error(client, transport):
    transport.push({"error": "bad"}, status=400)
    with pytest.raises(HTTPError) as info:
        client.search()
    assert info.value.status == 400
    assert not isinstance(info.value, (NotFoundError, RateLimitError, ServerError))


def test_transport_failures_are_retried(transport, search_response):
    calls = {"n": 0}
    original = transport.request

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TransportError("connection reset")
        return original(*args, **kwargs)

    transport.request = flaky  # type: ignore[assignment]
    transport.push(search_response)
    with Dataportal(transport=transport, max_retries=2, backoff_factor=0) as dp:
        assert dp.search().total == search_response["results"]


def test_closing_the_client_closes_a_transport_it_owns():
    from dataportal_se.transport import UrllibTransport

    owned = UrllibTransport()
    dp = Dataportal(transport=owned)
    dp.close()  # supplied transports are the caller's to close
    fake = FakeTransport()
    with Dataportal(transport=fake):
        pass
    assert not fake.closed


def test_clear_cache_forces_a_second_lookup(client, transport, search_response):
    transport.push(search_response)
    client.lookup("http://example.org/x")
    client.clear_cache()
    transport.push(search_response)
    client.lookup("http://example.org/x")
    assert len(transport.requests) == 2


def test_link_check_reports_query_the_report_type(client, transport):
    payload = load_fixture("link_check_report.json")
    transport.push(payload)
    transport.push(_page([], payload["results"], 2, 100))
    reports = client.link_check_reports()
    assert reports
    assert "LinkCheckReport" in query_of(transport.requests[0])
    assert all(r.checked is not None for r in reports)


def test_link_check_reports_can_filter_to_failures(client, transport):
    payload = load_fixture("link_check_report.json")
    transport.push(payload)
    transport.push(_page([], payload["results"], 2, 100))
    everything = client.link_check_reports()
    transport.push(payload)
    transport.push(_page([], payload["results"], 2, 100))
    failing = client.link_check_reports(failing_only=True)
    assert len(failing) <= len(everything)
    assert all((r.failed or 0) > 0 for r in failing)
    assert [r.context_id for r in failing] == [
        r.context_id for r in everything if (r.failed or 0) > 0
    ]


def test_link_check_reports_can_be_scoped_to_one_context(client, transport):
    payload = load_fixture("link_check_report.json")
    transport.push(payload)
    transport.push(_page([], payload["results"], 2, 100))
    client.link_check_reports(context=762)
    assert r"store\/762" in query_of(transport.requests[0])


def test_metadata_quality_includes_the_repository_total(client, transport):
    payload = load_fixture("metadata_quality.json")
    transport.push(payload)
    transport.push(_page([], payload["results"], 2, 100))
    scores = client.metadata_quality()
    assert scores
    assert all(s.percentage is not None for s in scores)
    assert "MQATotal" in query_of(transport.requests[0])


def test_metadata_quality_can_exclude_the_total(client, transport):
    payload = load_fixture("metadata_quality.json")
    transport.push(payload)
    transport.push(_page([], payload["results"], 2, 100))
    client.metadata_quality(include_total=False)
    query = query_of(transport.requests[0])
    assert "MQATotal" not in query
    assert "MQA" in query
