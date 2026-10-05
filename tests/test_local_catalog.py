"""Searching the catalogue: the filters, the values and the facets.

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
                   _transport=transport)


def uris(results):
    return [r.uri.rsplit("/", 1)[-1] for r in results]


@pytest.mark.parametrize("filters,expected", [
    ({"theme": "transport"}, ["roads"]),
    ({"theme": ["transport", "economy_and_finance"]}, ["roads", "budget"]),
    ({"format": "csv"}, ["roads"]),
    ({"format": "xlsx"}, ["budget"]),
    ({"access_rights": "public"}, ["roads"]),
    ({"access_rights": "non_public"}, ["budget"]),
    ({"access_rights": ["public", "non_public"]}, ["roads", "budget"]),
    ({"access_rights": "none"}, []),
    ({"accrual_periodicity": "monthly"}, ["budget"]),
    ({"publisher": "trafikverket"}, ["roads"]),
    ({"publisher_type": "national_authority"}, ["roads", "budget"]),
    ({"query": "cykel"}, ["roads"]),
    ({"query": "bidrag"}, ["budget"]),
    ({"keyword": "geodata"}, ["roads"]),
    ({"theme": "transport", "format": "csv"}, ["roads"]),
    ({"theme": "transport", "format": "xlsx"}, []),
])
def test_every_filter_matches_what_it_says(catalog, filters, expected):
    assert uris(catalog.search(**filters)) == expected


def test_text_searches_both_languages(catalog):
    """`query=` reads the whole language map, not one key of it."""
    assert uris(catalog.search(query="Road traffic")) == ["roads"]
    assert uris(catalog.search(query="Vägtrafiknät")) == ["roads"]


@pytest.mark.parametrize("filters,expected", [
    ({"modified_after": "2024-01-01"}, ["roads"]),
    ({"modified_after": "2018-12-31"}, ["roads", "budget"]),
    ({"modified_after": "2024-05-06T08:00:00+02:00"}, ["roads"]),
    ({"modified_after": "2024-05-06T09:30:00.123456"}, []),
    ({"modified_after": "2024"}, ["roads"]),
])
def test_dates_compare_across_the_forms_publishers_use(catalog, filters, expected):
    """A bare date, a timestamp and one with an offset must sort together."""
    assert uris(catalog.datasets(**filters)) == expected
    assert uris(catalog.search(**filters)) == expected


def test_modified_after_falls_back_to_issued(tmp_path, transport):
    """A dataset that never says when it changed is dated by its issue."""
    undated = dict(CATALOG_RECORDS[1], modified=None, issued="2023-02-01")
    catalog = Catalog(write_catalog(tmp_path, [CATALOG_RECORDS[0], undated]),
                      max_age=None, _transport=transport)
    assert uris(catalog.datasets(modified_after="2022-12-31")) == \
        ["roads", "budget"]
    assert uris(catalog.datasets(modified_after="2023-06-01")) == ["roads"]


@pytest.mark.parametrize("filter,value,names", [
    ("license", "cc_by_4_0", "license"),
    ("language", "en", "languages"),
    ("updated", "monthly", "accrual_periodicity"),
    ("issued_after", "2020", "modified_after"),
    ("issued_before", "2015-06", "modified_after"),
    ("modified_before", "2020-01-01", "modified_after"),
])
def test_a_removed_filter_says_what_replaced_it(catalog, filter, value, names):
    """Each is refused by name rather than as a typo."""
    with pytest.raises(QueryError) as info:
        catalog.search(**{filter: value})
    message = str(info.value)
    assert filter in message and names in message
    assert "unknown filter" not in message


def test_limit_caps_the_rows(catalog):
    assert len(catalog.search(limit=1)) == 1
    assert len(catalog.search(limit=1).datasets) == 1
    assert len(catalog.search(limit=None)) == 2
    assert len(catalog.search()) == 2
    assert len(catalog.datasets()) == 2


def test_limit_zero_gives_the_count_and_the_facets_only(catalog):
    page = catalog.search(limit=0)
    assert len(page) == 0
    assert page.total == 2
    assert page.facets["theme"]


def test_offset_walks_the_match(catalog):
    first = catalog.search(limit=1)
    second = catalog.search(limit=1, offset=1)
    assert first.datasets[0].uri != second.datasets[0].uri
    assert first.has_more and not second.has_more
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
    """`sort=` and `page_size=` belong to an index; a file answers at once."""
    for argument in ("sort", "page_size"):
        with pytest.raises(QueryError) as info:
            catalog.datasets(**{argument: "x"})
        assert argument in str(info.value)


def test_query_is_a_phrase_not_a_bag_of_words(catalog):
    """The rename did not change the matching.

    "traffic Road" is not in the title "Road traffic network", so it matches
    nothing -- splitting a query into terms that must all appear would make
    `air quality` 481 datasets instead of 15 on the real catalogue.
    """
    assert uris(catalog.search(query="Road traffic")) == ["roads"]
    assert uris(catalog.search(query="traffic Road")) == []
    assert uris(catalog.search(query="ROAD TRAFFIC")) == ["roads"]


def test_query_none_is_no_query(catalog):
    assert catalog.search(query=None).total == catalog.search().total == 2


def test_text_is_refused_and_the_error_names_query(catalog):
    for search in (catalog.search, catalog.datasets, catalog.data_services):
        with pytest.raises(QueryError) as info:
            search(text="cykel")
        assert "query" in str(info.value)


def test_query_is_search_s_first_argument_and_nobody_else_s(catalog):
    """search() is the question, so the phrase comes first; datasets() is
    the list, so it takes no phrase at all."""
    assert uris(catalog.search("cykel")) == uris(catalog.search(query="cykel"))
    with pytest.raises(TypeError):
        catalog.datasets("cykel")
    with pytest.raises(QueryError) as info:
        catalog.datasets(query="cykel")
    assert "search(query=...)" in str(info.value)


def test_a_search_carries_its_own_facets(catalog):
    page = catalog.search()
    assert page.facets["theme"] == [("economy_and_finance", 1), ("transport", 1)]
    assert page.facets["accrual_periodicity"] == [("annual", 1), ("monthly", 1)]
    assert page.facets["accrual_periodicity"][0].label
    assert page.facets["access_rights"] == [("non_public", 1), ("public", 1)]
    assert "language" not in page.facets and "license" not in page.facets
    assert page.facets["format"] == [("csv", 1), ("json", 1), ("xlsx", 1)]
    assert page.facets["publisher"][0].count == 1
    # Unlike the registry's index, a file can count keywords.
    assert ("Geodata", 1) in page.facets["keyword"]


def test_the_facets_describe_the_match_not_the_page(catalog):
    page = catalog.search(theme="transport")
    assert page.total == 1
    assert page.facets["publisher"] == [("trafikverket", 1)]
    assert page.facets["theme"] == [("transport", 1)]


def test_a_facet_value_is_still_a_pair(catalog):
    """`count` shadows tuple.count on purpose; the pair must still be a pair."""
    row = catalog.facets()["theme"][0]
    value, count = row
    assert row == (value, count)
    assert (row.value, row.count) == (value, count)
    from dataportalen.models import MultilingualText
    assert isinstance(row.label, MultilingualText) and row.label.text()


def test_the_old_names_are_gone(catalog):
    page = catalog.search()
    assert not hasattr(page, "breakdown")
    assert not hasattr(page, "facet"), "the pre-0.7 server-facet lookup"
    assert not hasattr(catalog, "filters")
    with pytest.raises(QueryError) as info:
        catalog.search(breakdown_limit=3)
    assert "unknown filter" in str(info.value)


def test_asking_for_a_facet_that_is_not_there_names_the_ones_that_are(catalog):
    with pytest.raises(QueryError) as info:
        catalog.search().facets["service_type"]
    assert "theme" in str(info.value)


def test_a_dataset_counts_once_per_value(catalog):
    """Two CSV distributions on one dataset is one dataset under `csv`."""
    page = catalog.search(publisher="trafikverket")
    assert page.total == 1
    assert page.facets["format"] == [("csv", 1), ("json", 1)]


def test_facet_limit_reports_what_it_cut(catalog):
    page = catalog.search(facet_limit=1)
    assert len(page.facets["theme"]) == 1
    assert page.facets["theme"].omitted == 1


def test_the_records_are_the_shape_the_download_writes(catalog):
    """Guards the fixture against drifting from the real record."""
    from dataportalen.models import Dataset, MultilingualText

    dataset = catalog.datasets()[0]
    assert isinstance(dataset, Dataset)
    record = dataset.to_dict()
    assert set(record) >= set(CATALOG_RECORDS[0])
    assert isinstance(record["title"], dict)
    assert isinstance(record["publisher"]["name"], dict)
    assert isinstance(dataset.title, MultilingualText)
    assert dataset.title == CATALOG_RECORDS[0]["title"]


def test_a_negative_window_is_refused_not_sliced_from_the_end(catalog):
    """`limit=-1` used to return every match but the last, silently."""
    for kwargs in ({"limit": -1}, {"offset": -1}, {"limit": -5, "offset": 2}):
        with pytest.raises(QueryError) as info:
            catalog.search(**kwargs)
        assert "0 or more" in str(info.value)
    # The two that do mean something still do.
    assert catalog.search(limit=0).total == 2
    assert len(catalog.search(limit=None)) == 2


def test_an_offset_past_the_end_is_empty_not_an_error(catalog):
    page = catalog.search(limit=5, offset=99999)
    assert len(page) == 0
    assert page.total == 2
    assert page.has_more is False
