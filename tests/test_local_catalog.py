"""Searching the catalogue: the filters, the values and the breakdown.

No network anywhere in here. The two records come from `conftest`, so the
shape these tests assert against is the shape the download writes.
"""

from __future__ import annotations

import pytest

from conftest import CATALOG_RECORDS, write_catalog
from dataportalen import Catalog, QueryError


@pytest.fixture
def catalog(tmp_path, transport):
    return Catalog(write_catalog(tmp_path), max_age=None,
                   _transport=transport, access_rights=None)


def uris(results):
    return [r["uri"].rsplit("/", 1)[-1] for r in results]


@pytest.mark.parametrize("filters,expected", [
    ({"theme": "transport"}, ["roads"]),
    ({"theme": ["transport", "economy_and_finance"]}, ["roads", "budget"]),
    ({"format": "csv"}, ["roads"]),
    ({"format": "xlsx"}, ["budget"]),
    ({"license": "cc_by_4_0"}, ["roads"]),
    ({"access_rights": "public"}, ["roads"]),
    ({"updated": "monthly"}, ["budget"]),
    ({"language": "english"}, ["budget"]),
    ({"place": "kingdom_of_sweden"}, ["roads"]),
    ({"publisher": "trafikverket"}, ["roads"]),
    ({"publisher_type": "national_authority"}, ["roads", "budget"]),
    ({"text": "cykel"}, ["roads"]),
    ({"text": "bidrag"}, ["budget"]),
    ({"keyword": "geodata"}, ["roads"]),
    ({"theme": "transport", "format": "csv"}, ["roads"]),
    ({"theme": "transport", "format": "xlsx"}, []),
])
def test_every_filter_matches_what_it_says(catalog, filters, expected):
    assert uris(catalog.datasets(limit=None, **filters)) == expected


def test_text_searches_both_languages(catalog):
    """`text=` reads the whole language map, not one key of it."""
    assert uris(catalog.datasets(text="Road traffic")) == ["roads"]
    assert uris(catalog.datasets(text="Vägtrafiknät")) == ["roads"]


@pytest.mark.parametrize("filters,expected", [
    ({"updated_after": "2024-01-01"}, ["roads"]),
    ({"updated_before": "2020-01-01"}, ["budget"]),
    ({"published_after": "2020"}, ["roads"]),
    ({"published_before": "2015-06"}, ["budget"]),
])
def test_dates_compare_across_the_forms_publishers_use(catalog, filters, expected):
    """A bare date, a timestamp and one with an offset must sort together."""
    assert uris(catalog.datasets(limit=None, **filters)) == expected


def test_limit_caps_the_rows(catalog):
    assert len(catalog.datasets(limit=1)) == 1
    assert len(catalog.datasets(limit=None)) == 2


def test_limit_zero_gives_the_count_and_the_breakdown_only(catalog):
    page = catalog.datasets(limit=0)
    assert len(page) == 0
    assert page.total == 2
    assert page.breakdown["theme"]


def test_offset_walks_the_match(catalog):
    first = catalog.datasets(limit=1)
    second = catalog.datasets(limit=1, offset=1)
    assert first[0]["uri"] != second[0]["uri"]
    assert first.total == second.total == 2


def test_unknown_values_and_filters_are_rejected_the_same_way(catalog):
    with pytest.raises(QueryError) as info:
        catalog.datasets(theme="transprot")
    assert "transport" in str(info.value)
    with pytest.raises(QueryError):
        catalog.datasets(publisher="trafikvrket")
    with pytest.raises(QueryError) as info:
        catalog.datasets(nonsense="x")
    assert "unknown filter" in str(info.value)


def test_index_only_arguments_say_so(catalog):
    """`query=`/`sort=` are raw index expressions; a file cannot answer one."""
    for argument in ("query", "sort", "page_size"):
        with pytest.raises(QueryError) as info:
            catalog.datasets(**{argument: "x"})
        assert argument in str(info.value)


def test_a_search_carries_its_own_breakdown(catalog):
    page = catalog.datasets()
    assert page.breakdown["theme"] == [("economy_and_finance", 1), ("transport", 1)]
    assert page.breakdown["language"][0] == ("swedish", 2)
    assert page.breakdown["format"] == [("csv", 1), ("json", 1), ("xlsx", 1)]
    assert page.breakdown["publisher"][0].dataset_count == 1
    # Unlike the registry's index, a file can count keywords.
    assert ("Geodata", 1) in page.breakdown["keyword"]


def test_the_breakdown_describes_the_match_not_the_page(catalog):
    page = catalog.datasets(theme="transport")
    assert page.total == 1
    assert page.breakdown["publisher"] == [("trafikverket", 1)]
    assert page.breakdown["theme"] == [("transport", 1)]


def test_a_dataset_counts_once_per_value(catalog):
    """Two CSV distributions on one dataset is one dataset under `csv`."""
    page = catalog.datasets(publisher="trafikverket")
    assert page.total == 1
    assert page.breakdown["format"] == [("csv", 1), ("json", 1)]


def test_breakdown_limit_reports_what_it_cut(catalog):
    page = catalog.datasets(breakdown_limit=1)
    assert len(page.breakdown["theme"]) == 1
    assert page.breakdown["theme"].omitted == 1


def test_the_records_are_the_shape_the_download_writes(catalog):
    """Guards the fixture against drifting from the real record."""
    record = catalog.datasets()[0]
    assert set(record) >= set(CATALOG_RECORDS[0])
    assert isinstance(record["title"], dict)
    assert isinstance(record["publisher"]["name"], dict)


def test_a_negative_window_is_refused_not_sliced_from_the_end(catalog):
    """`limit=-1` used to return every match but the last, silently."""
    for kwargs in ({"limit": -1}, {"offset": -1}, {"limit": -5, "offset": 2}):
        with pytest.raises(QueryError) as info:
            catalog.datasets(**kwargs)
        assert "0 or more" in str(info.value)
    # The two that do mean something still do.
    assert catalog.datasets(limit=0).total == 2
    assert len(catalog.datasets(limit=None)) == 2


def test_an_offset_past_the_end_is_empty_not_an_error(catalog):
    page = catalog.datasets(limit=5, offset=99999)
    assert len(page) == 0
    assert page.total == 2
    assert page.has_more is False
