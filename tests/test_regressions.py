"""Regression tests, each named for the behaviour it protects.

The docstrings say what went wrong once, because that is what the test is
guarding against; the name says what must hold.
"""

from __future__ import annotations

import threading

import pytest

from conftest import CATALOG_RECORDS, write_catalog
from dataportalen import Catalog, QueryError


@pytest.fixture
def catalog(tmp_path, transport):
    return Catalog(write_catalog(tmp_path), max_age=None,
                   _transport=transport)


# -- an organisation number is a publisher, not a dead end -------------------


def test_an_organisation_number_finds_the_same_datasets_as_the_name(catalog):
    """publishers.json indexes both, so both have to match the same thing.

    The number validated and was then matched, as itself, against records
    filed under the name slug, so it matched nothing and said nothing.
    """
    by_name = catalog.search(publisher="trafikverket", limit=0).total
    by_number = catalog.search(publisher="SE2021006297", limit=0).total
    assert by_name == by_number == 1
    assert catalog.search(publisher="se2021006297", limit=0).total == by_name


# -- publisher is always a dict ---------------------------------------------


def test_a_record_without_a_publisher_still_has_a_publisher_dict():
    """10 datasets and 16 data services name none, and every documented way
    of reading one subscripts it."""
    from dataportalen.client import _present
    from dataportalen.entries import DatasetEntry, wrap_entry
    from dataportalen.models import _dataset

    entry = wrap_entry({"metadata": {"http://x/1": {
        "http://purl.org/dc/terms/title": [{"type": "literal", "value": "T"}],
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#type": [
            {"type": "uri", "value": "http://www.w3.org/ns/dcat#Dataset"}],
    }}}, default=DatasetEntry)
    record = entry.to_dict()
    assert record["publisher"] is not None
    assert record["publisher"]["name"] == {}
    assert record["publisher"]["uri"] is None
    assert record["publisher"]["identifiers"] == []
    publisher = _dataset(_present(record), "sv").publisher
    assert publisher.name.text() is None                  # the documented read
    assert (publisher.id, publisher.uri, publisher.identifiers) == (None, None, [])


# -- a facet value filters to exactly its own count ----------------------


def test_a_keyword_matches_exactly_when_the_file_holds_it(tmp_path, transport):
    """`BARN` is on 24 datasets and matched 680, because it is inside
    `BARNOMSORG`. 68 of the 120 commonest keywords disagreed with their row."""
    records = [
        dict(CATALOG_RECORDS[0], uri="https://example.org/a",
             keywords={"sv": ["BARN"]}),
        dict(CATALOG_RECORDS[1], uri="https://example.org/b",
             keywords={"sv": ["BARNOMSORG"]}),
    ]
    cat = Catalog(write_catalog(tmp_path, records), max_age=None,
                  _transport=transport)
    counts = dict((v, c) for v, c in cat.facets()["keyword"])
    assert counts == {"BARN": 1, "BARNOMSORG": 1}
    for value, count in counts.items():
        assert cat.search(keyword=value, limit=0).total == count, value


def test_case_is_folded_and_the_facet_agrees(tmp_path, transport):
    """`BARN` and `Barn` are one keyword: one row, and it counts both.

    1,765 keywords in the registry are spelt more than one way (`Hälsa` 290,
    `HÄLSA` 102, `hälsa` 9). The row shows a spelling a record carries -- the
    commonest, and on a tie the first alphabetically -- never the folded form.
    """
    records = [
        dict(CATALOG_RECORDS[0], uri="https://example.org/a",
             keywords={"sv": ["BARN"]}),
        dict(CATALOG_RECORDS[1], uri="https://example.org/b",
             keywords={"sv": ["Barn"]}),
    ]
    cat = Catalog(write_catalog(tmp_path, records), max_age=None,
                  _transport=transport)
    assert list(cat.facets()["keyword"]) == [("BARN", 2)]
    for spelling in ("BARN", "Barn", "barn", " bArN "):
        assert cat.search(keyword=spelling, limit=0).total == 2, spelling


def test_the_commonest_spelling_is_the_one_shown(tmp_path, transport):
    records = [
        dict(CATALOG_RECORDS[0], uri="https://example.org/%d" % i,
             context_id=str(i), keywords={"sv": [spelling]})
        for i, spelling in enumerate(["Kommun", "Kommun", "kommun", "KOMMUN\n"])
    ]
    cat = Catalog(write_catalog(tmp_path, records), max_age=None,
                  _transport=transport)
    assert list(cat.facets()["keyword"]) == [("Kommun", 4)]


def test_a_record_with_two_spellings_counts_once(tmp_path, transport):
    """395 dataset-keyword pairs in the registry are this."""
    record = dict(CATALOG_RECORDS[0], keywords={"sv": ["Kommun"], "en": ["kommun"]})
    cat = Catalog(write_catalog(tmp_path, [record]), max_age=None,
                  _transport=transport)
    assert [row.count for row in cat.facets()["keyword"]] == [1]
    assert cat.search(keyword="KOMMUN", limit=0).total == 1


def test_an_unknown_keyword_raises_and_suggests(tmp_path, transport):
    """It used to fall back to a substring match; that is what query= is for."""
    record = dict(CATALOG_RECORDS[0], keywords={"sv": ["Geodata"]})
    cat = Catalog(write_catalog(tmp_path, [record]), max_age=None,
                  _transport=transport)
    with pytest.raises(QueryError) as info:
        cat.datasets(keyword="geoda")
    assert "unknown keyword" in str(info.value)
    assert "geodata" in str(info.value)
    assert cat.search(query="geoda", limit=0).total == 1


def test_a_keyword_list_is_any_of_like_every_other_filter(tmp_path, transport):
    """It was all-of: the one filter whose list meant something else."""
    records = [
        dict(CATALOG_RECORDS[0], uri="https://example.org/a",
             keywords={"sv": ["Kommun"]}),
        dict(CATALOG_RECORDS[1], uri="https://example.org/b",
             keywords={"sv": ["Region"]}),
    ]
    cat = Catalog(write_catalog(tmp_path, records), max_age=None,
                  _transport=transport)
    assert cat.search(keyword=["kommun", "region"], limit=0).total == 2
    assert cat.search(keyword=["kommun"], limit=0).total == 1


def test_a_keyword_only_the_other_kind_has_is_zero_not_an_error(catalog):
    """`vägnät` is on a dataset and on no data service.

    A keyword has no vocabulary to vouch for it, so "known" means somewhere
    in this Catalog. Checked per kind, data_services(keyword="kommun") would
    raise for the commonest keyword in the registry.
    """
    assert catalog.search(keyword="vägnät", limit=0).total == 1
    assert catalog.data_services(keyword="vägnät") == []
    assert catalog.search(keyword="innovation", limit=0).total == 0


def test_a_blank_value_never_reaches_the_facets(tmp_path, transport):
    """33 datasets carry a keyword that is a newline and four spaces."""
    record = dict(CATALOG_RECORDS[0], keywords={"sv": ["\n    ", "riktig"]})
    cat = Catalog(write_catalog(tmp_path, [record]), max_age=None,
                  _transport=transport)
    assert [v for v, _ in cat.facets()["keyword"]] == ["riktig"]


# -- garbage is refused, not coerced into a plausible number -----------------


@pytest.mark.parametrize("filters", [
    {"keyword": []}, {"theme": []}, {"publisher": []},
    {"query": ""}, {"query": "   "}, {"keyword": None}, {"theme": "   "},
])
def test_an_empty_value_is_refused_not_read_as_everything(catalog, filters):
    """`keyword=[]` returned all 23,576 while `theme=[]` returned none."""
    with pytest.raises(QueryError) as info:
        catalog.search(limit=0, **filters)
    assert "needs a value" in str(info.value)


@pytest.mark.parametrize("value", [None, "", "notadate", "2024-13-45", "20240101"])
def test_a_date_bound_has_to_be_a_date(catalog, value):
    """`modified_after=None` became "" and matched every dated record."""
    with pytest.raises(QueryError) as info:
        catalog.search(limit=0, modified_after=value)
    assert "date" in str(info.value)


@pytest.mark.parametrize("value", ["2024", "2024-01", "2024-01-01",
                                   "2024-01-01T09:00:00"])
def test_the_date_shapes_publishers_write_are_still_accepted(catalog, value):
    catalog.search(limit=0, modified_after=value)


# -- smaller contracts -------------------------------------------------------


def test_limit_none_is_reported_as_none_not_as_the_row_count(catalog):
    """`.limit` is documented as what you asked for."""
    assert catalog.search(limit=None).limit is None
    assert catalog.search(limit=1).limit == 1
    assert catalog.search(limit=0).limit == 0


@pytest.mark.parametrize("window", [{"limit": 0}, {"limit": None}, {"offset": 1},
                                    {"facet_limit": 5}, {"query": "väg"}])
def test_a_complete_list_refuses_a_window_and_points_at_search(catalog, window):
    """datasets() returns every match; a limit there would be silently ignored
    or silently obeyed, and either would surprise someone."""
    with pytest.raises(QueryError) as info:
        catalog.datasets(**window)
    assert "search(" in str(info.value)


def test_an_unregistered_rdf_type_gives_a_plain_entry_not_a_keyerror():
    """_TYPE_PRIORITY names dcat:DatasetSeries and no model answers for it."""
    from dataportalen.entries import Entry, wrap_entry

    entry = wrap_entry({"metadata": {"http://x/1": {
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#type": [
            {"type": "uri", "value": "http://www.w3.org/ns/dcat#DatasetSeries"}],
    }}})
    assert isinstance(entry, Entry)


def test_the_request_path_quotes_the_ids_it_interpolates(tmp_path, transport):
    """Catalog(path) accepts any file; a crafted id must not redirect."""
    record = dict(CATALOG_RECORDS[0], context_id="../../evil", entry_id="9")
    fresh = Catalog(write_catalog(tmp_path, [record]), max_age=None,
                    _transport=transport)
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
    """requests.Session is not thread-safe and the download uses 8 workers.

    A barrier rather than a pool: handing N tasks to a pool does not
    guarantee N threads take one each, and a pool that drains the queue on
    one thread would make this pass or fail by timing. One run in two
    hundred did exactly that.
    """
    from dataportalen.core import RequestsTransport

    transport = RequestsTransport()
    workers = 4
    ready = threading.Barrier(workers)
    seen, lock = set(), threading.Lock()

    def look():
        ready.wait(timeout=10)          # nobody proceeds until all are here
        with lock:
            seen.add(id(transport._session))

    threads = [threading.Thread(target=look) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert len(seen) == workers, "threads shared a session"
    assert len(transport._made) == workers
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


def test_an_empty_file_is_not_a_catalogue(tmp_path, transport):
    """Reading it as "0 datasets" would hide the problem for as long as the
    file sat there: every search would answer nothing and nothing would look
    wrong."""
    from dataportalen import ParseError

    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ParseError) as info:
        Catalog(str(path), max_age=None, _transport=transport)
    assert "empty" in str(info.value)
