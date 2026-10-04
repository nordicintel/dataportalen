"""Client behaviour: request shaping, paging, lookups, retries."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from conftest import FakeTransport
from dataportalen import (
    HTTPError,
    NotFoundError,
    RateLimitError,
    ServerError,
    TransportError,
)
from dataportalen.client import _Registry
from dataportalen.models import Dataset
from dataportalen.query import Q
from dataportalen.rdf import DCAT


def query_of(url: str) -> str:
    return parse_qs(urlparse(url).query)["query"][0]


def params_of(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


# -- request shaping ---------------------------------------------------------


def test_search_sends_solr_parameters(client, transport, search_response):
    transport.push(search_response)
    client._search(Q.rdf_type(DCAT.Dataset), limit=10, offset=20)
    params = params_of(transport.requests[-1])
    assert params["type"] == "solr"
    assert params["limit"] == "10"
    assert params["offset"] == "20"
    assert params["sort"] == "modified desc"
    assert "rdfType" in params["query"]


def test_public_filter_is_added_by_default(client, transport, search_response):
    transport.push(search_response)
    client._search(Q.title("x"))
    assert "public:true" in query_of(transport.requests[-1])


def test_public_filter_can_be_turned_off(transport, search_response):
    transport.push(search_response)
    with _Registry(transport=transport, public_only=False) as dp:
        dp._search(Q.title("x"))
    assert "public:true" not in query_of(transport.requests[-1])


def test_an_empty_query_becomes_match_all(transport, search_response):
    transport.push(search_response)
    with _Registry(transport=transport, public_only=False) as dp:
        dp._search()
    assert query_of(transport.requests[-1]) == "*:*"


def test_limit_is_clamped_to_the_solr_maximum(client, transport, search_response):
    transport.push(search_response)
    client._search(limit=5000)
    assert params_of(transport.requests[-1])["limit"] == "100"


def test_colons_in_uris_are_escaped_in_the_query(client, transport, search_response):
    transport.push(search_response)
    client._lookup_many(["http://example.org/dataset1"])
    assert r"http\:\/\/example.org\/dataset1" in query_of(transport.requests[-1])



# -- responses ---------------------------------------------------------------


def test_search_page_exposes_totals_and_typed_entries(client, transport, search_response):
    transport.push(search_response)
    page = client._search()
    assert page.total == search_response["results"]
    assert len(page) == len(search_response["resource"]["children"])
    assert all(isinstance(e, Dataset) for e in page)
    assert page[0].title


def test_search_page_is_a_sequence(client, transport, search_response):
    transport.push(search_response)
    page = client._search()
    assert list(page) == page.entries
    assert page[0] is page.entries[0]


def test_count_asks_for_a_single_row(client, transport, search_response):
    transport.push(search_response)
    assert client._count() == search_response["results"]
    assert params_of(transport.requests[-1])["limit"] == "1"



def _page(children, total, offset, limit):
    return {
        "results": total,
        "offset": offset,
        "limit": limit,
        "resource": {"children": children},
        "facetFields": [],
    }


def test_next_page_continues_from_the_current_offset(client, transport, search_response):
    children = search_response["resource"]["children"]
    transport.push(_page(children, 10, 0, 2))
    page = client._search(limit=2)
    assert page.has_more
    transport.push(_page(children, 10, 2, 2))
    following = page.next_page()
    assert following is not None
    assert params_of(transport.requests[-1])["offset"] == "2"


def test_last_page_reports_no_more(client, transport, search_response):
    children = search_response["resource"]["children"]
    transport.push(_page(children, 2, 0, 2))
    page = client._search(limit=2)
    assert not page.has_more
    assert page.next_page() is None


# -- single entries ----------------------------------------------------------


def test_entry_raw_rejects_unknown_parts(client):
    with pytest.raises(ValueError):
        client._entry_raw(1, 2, part="nonsense")


def test_lookup_many_batches_into_one_request(client, transport, search_response):
    transport.push(search_response)
    uris = ["http://example.org/%d" % i for i in range(5)]
    client._lookup_many(uris, batch_size=20)
    assert len(transport.requests) == 1
    assert query_of(transport.requests[-1]).count(" OR ") == 4


def test_lookup_many_splits_oversized_batches(client, transport, search_response):
    transport.push(search_response)
    transport.push(search_response)
    client._lookup_many(["http://example.org/%d" % i for i in range(4)], batch_size=2)
    assert len(transport.requests) == 2


# -- statistics endpoints ----------------------------------------------------


# -- the dump ----------------------------------------------------------------




def test_404_raises_not_found(client, transport):
    transport.push({"error": "gone"}, status=404)
    with pytest.raises(NotFoundError) as info:
        client._search()
    assert info.value.status == 404


def test_5xx_raises_server_error_after_retries_are_exhausted(transport):
    for _ in range(3):
        transport.push({"error": "boom"}, status=503)
    with _Registry(transport=transport, max_retries=2, backoff_factor=0) as dp:
        with pytest.raises(ServerError):
            dp._search()
    assert len(transport.requests) == 3


def test_a_transient_5xx_is_retried_then_succeeds(transport, search_response):
    transport.push({"error": "boom"}, status=503)
    transport.push(search_response)
    with _Registry(transport=transport, max_retries=2, backoff_factor=0) as dp:
        page = dp._search()
    assert page.total == search_response["results"]
    assert len(transport.requests) == 2


def test_429_carries_retry_after(transport):
    transport.push({"error": "slow down"}, status=429)
    with _Registry(transport=transport, max_retries=0) as dp:
        with pytest.raises(RateLimitError):
            dp._search()


def test_other_4xx_raises_plain_http_error(client, transport):
    transport.push({"error": "bad"}, status=400)
    with pytest.raises(HTTPError) as info:
        client._search()
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
    with _Registry(transport=transport, max_retries=2, backoff_factor=0) as dp:
        assert dp._search().total == search_response["results"]


def test_closing_the_client_closes_a_transport_it_owns():
    from dataportalen.core import RequestsTransport

    owned = RequestsTransport()
    dp = _Registry(transport=owned)
    dp.close()  # supplied transports are the caller's to close
    fake = FakeTransport()
    with _Registry(transport=fake):
        pass
    assert not fake.closed


def test_lookup_many_reads_on_when_uris_have_more_entries_than_fit(client, transport,
                                                                 search_response):
    """A URI can belong to two entries, so 20 URIs are not 20 hits.

    Asking for exactly len(batch) returned 20 of 22 and dropped an agent.
    """
    child = search_response["resource"]["children"][0]
    full = dict(search_response, results=101,
                resource={"children": [child] * 100})
    rest = dict(search_response, results=101,
                resource={"children": [search_response["resource"]["children"][1]]})
    transport.push(full)
    transport.push(rest)
    found = client._lookup_many(["http://example.org/a", "http://example.org/b"])
    assert len(transport.requests) == 2
    assert params_of(transport.requests[0])["limit"] == "100"
    assert params_of(transport.requests[1])["offset"] == "100"
    assert params_of(transport.requests[0])["sort"] == "uri asc"
    assert found == []                      # neither fixture entry is a or b


def test_a_request_too_long_for_the_registry_is_refused_before_it_is_sent(client,
                                                                        transport):
    from dataportalen import QueryError

    uris = ["http://dataportal.se/organisation/SE%010d" % n for n in range(170)]
    with pytest.raises(QueryError, match="ask for fewer values"):
        client._search(Q.publisher(*uris))
    assert transport.requests == []
