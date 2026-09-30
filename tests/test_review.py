"""Regressions for the defects the pre-release review of 0.7.0 turned up.

Each one is named for what went wrong rather than for the function, because
the point is the behaviour, not the implementation that happened to cause it.
"""

from __future__ import annotations

import threading

import pytest

from conftest import CATALOG_RECORDS, write_catalog
from dataportalen import Catalog, QueryError, text


@pytest.fixture
def catalog(tmp_path, transport):
    return Catalog(write_catalog(tmp_path), refresh="never",
                   transport=transport, progress=None)


# -- an organisation number is a publisher, not a dead end -------------------


def test_an_organisation_number_finds_the_same_datasets_as_the_name(catalog):
    """organisations.json indexes both, so both have to match the same thing.

    The number validated and was then matched, as itself, against records
    filed under the name slug -- so it matched nothing and said nothing. 186
    accepted values could never match, 184 of them naming a real publisher.
    """
    by_name = catalog.datasets(publisher="trafikverket", limit=0).total
    by_number = catalog.datasets(publisher="SE2021006297", limit=0).total
    assert by_name == by_number == 1
    assert catalog.datasets(publisher="se2021006297", limit=0).total == by_name


def test_a_creator_takes_an_organisation_number_too(tmp_path, transport):
    record = dict(
        CATALOG_RECORDS[0],
        creators=[{"uri": "http://dataportal.se/organisation/SE2021006297",
                   "name": {"sv": "Trafikverket"},
                   "type": "national_authority"}],
    )
    cat = Catalog(write_catalog(tmp_path, [record]), refresh="never",
                  transport=transport, progress=None)
    assert cat.datasets(creator="SE2021006297", limit=0).total == 1


# -- publisher is always a dict ---------------------------------------------


def test_a_record_without_a_publisher_still_has_a_publisher_dict():
    """10 datasets and 16 data services name none, and every documented way
    of reading one subscripts it."""
    from dataportalen.models import Dataset, wrap_entry

    entry = wrap_entry({"metadata": {"http://x/1": {
        "http://purl.org/dc/terms/title": [{"type": "literal", "value": "T"}],
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#type": [
            {"type": "uri", "value": "http://www.w3.org/ns/dcat#Dataset"}],
    }}}, default=Dataset)
    record = entry.to_dict()
    assert record["publisher"] is not None
    assert record["publisher"]["name"] == {}
    assert text(record["publisher"]["name"]) is None      # the documented read
    assert record["publisher"]["uri"] is None
    assert record["publisher"]["identifiers"] == []


# -- a breakdown value filters to exactly its own count ----------------------


def test_a_keyword_matches_exactly_when_the_file_holds_it(tmp_path, transport):
    """`BARN` is on 24 datasets and matched 680, because it is inside
    `BARNOMSORG`. 68 of the 120 commonest keywords disagreed with their row."""
    records = [
        dict(CATALOG_RECORDS[0], uri="https://example.org/a",
             keywords={"sv": ["BARN"]}),
        dict(CATALOG_RECORDS[1], uri="https://example.org/b",
             keywords={"sv": ["BARNOMSORG"]}),
    ]
    cat = Catalog(write_catalog(tmp_path, records), refresh="never",
                  transport=transport, progress=None)
    counts = dict((v, c) for v, c in cat.filters()["keyword"])
    assert counts == {"BARN": 1, "BARNOMSORG": 1}
    for value, count in counts.items():
        assert cat.datasets(keyword=value, limit=0).total == count, value


def test_case_is_not_folded_in_the_exact_branch(tmp_path, transport):
    """The breakdown counts `BARN` and `Barn` separately, so matching must."""
    records = [
        dict(CATALOG_RECORDS[0], uri="https://example.org/a",
             keywords={"sv": ["BARN"]}),
        dict(CATALOG_RECORDS[1], uri="https://example.org/b",
             keywords={"sv": ["Barn"]}),
    ]
    cat = Catalog(write_catalog(tmp_path, records), refresh="never",
                  transport=transport, progress=None)
    assert cat.datasets(keyword="BARN", limit=0).total == 1
    assert cat.datasets(keyword="Barn", limit=0).total == 1


def test_a_keyword_the_file_lacks_still_matches_on_substring(tmp_path, transport):
    record = dict(CATALOG_RECORDS[0], keywords={"sv": ["Geodata"]})
    cat = Catalog(write_catalog(tmp_path, [record]), refresh="never",
                  transport=transport, progress=None)
    assert cat.datasets(keyword="geoda", limit=0).total == 1


def test_a_blank_value_never_reaches_the_breakdown(tmp_path, transport):
    """33 datasets carry a keyword that is a newline and four spaces."""
    record = dict(CATALOG_RECORDS[0], keywords={"sv": ["\n    ", "riktig"]})
    cat = Catalog(write_catalog(tmp_path, [record]), refresh="never",
                  transport=transport, progress=None)
    assert [v for v, _ in cat.filters()["keyword"]] == ["riktig"]


# -- garbage is refused, not coerced into a plausible number -----------------


@pytest.mark.parametrize("filters", [
    {"keyword": []}, {"theme": []}, {"publisher": []},
    {"text": ""}, {"text": None}, {"keyword": None}, {"theme": "   "},
])
def test_an_empty_value_is_refused_not_read_as_everything(catalog, filters):
    """`keyword=[]` returned all 23,576 while `theme=[]` returned none."""
    with pytest.raises(QueryError) as info:
        catalog.datasets(limit=0, **filters)
    assert "needs a value" in str(info.value)


@pytest.mark.parametrize("value", [None, "", "notadate", "2024-13-45", "20240101"])
def test_a_date_bound_has_to_be_a_date(catalog, value):
    """`updated_after=None` became "" and matched every dated record."""
    with pytest.raises(QueryError) as info:
        catalog.datasets(limit=0, updated_after=value)
    assert "date" in str(info.value)


@pytest.mark.parametrize("value", ["2024", "2024-01", "2024-01-01",
                                   "2024-01-01T09:00:00"])
def test_the_date_shapes_publishers_write_are_still_accepted(catalog, value):
    catalog.datasets(limit=0, updated_after=value)


# -- smaller contracts -------------------------------------------------------


def test_limit_none_is_reported_as_none_not_as_the_row_count(catalog):
    """`.limit` is documented as what you asked for."""
    assert catalog.datasets(limit=None).limit is None
    assert catalog.datasets(limit=1).limit == 1
    assert catalog.datasets(limit=0).limit == 0


def test_an_unregistered_rdf_type_gives_a_plain_entry_not_a_keyerror():
    """_TYPE_PRIORITY names dcat:DatasetSeries and no model answers for it."""
    from dataportalen.models import Entry, wrap_entry

    entry = wrap_entry({"metadata": {"http://x/1": {
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#type": [
            {"type": "uri", "value": "http://www.w3.org/ns/dcat#DatasetSeries"}],
    }}})
    assert isinstance(entry, Entry)


def test_the_request_path_quotes_the_ids_it_interpolates(cat, transport):
    """Catalog(path=...) accepts any file; a crafted id must not redirect."""
    record = dict(CATALOG_RECORDS[0], context_id="../../evil", entry_id="9")
    import json as _json
    path = cat.info()["path"]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(_json.dumps(record) + "\n")
    fresh = Catalog(path, refresh="never", transport=transport)
    transport.push("x", content_type="text/turtle")
    fresh.get(record["uri"], format="turtle")
    assert "/store/..%2F..%2Fevil/metadata/9" in transport.requests[-1]


# -- the cache and the transport are reached from several threads ------------


def test_the_lru_survives_eviction_from_several_threads():
    """Three crawl threads share one; the eviction pop was the race."""
    from dataportalen.client import _LRU

    cache = _LRU(maxsize=8)
    errors = []

    def hammer(start):
        try:
            for i in range(start, start + 400):
                cache.set(i, i)
                cache.get(i - 1)
        except Exception as exc:                          # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(n * 400,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(cache) <= 8


def test_each_thread_gets_its_own_requests_session():
    """requests.Session is not thread-safe and the download uses 8 workers."""
    import concurrent.futures

    from dataportalen.core import RequestsTransport

    transport = RequestsTransport()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        seen = set(pool.map(lambda _: id(transport._session), range(24)))
    assert len(seen) > 1, "every thread was handed the same session"
    assert len(transport._made) == len(seen)
    transport.close()
    assert transport._made == []


def test_a_session_the_caller_supplies_is_used_and_left_open():
    import requests

    from dataportalen.core import RequestsTransport

    given = requests.Session()
    transport = RequestsTransport(session=given)
    assert transport._session is given
    transport.close()
    given.get                                   # still usable; not closed by us
