"""search() is the question, datasets()/data_services()/publishers() the lists.

Over the conftest catalogue: two datasets (one public, one non_public) and
two data services. Nothing here reaches the network.
"""

from __future__ import annotations

import copy
import json

import pytest

from conftest import CATALOG_RECORDS, SERVICE_RECORDS, write_catalog
from dataportalen import (
    Catalog,
    DataService,
    Dataset,
    Facets,
    Publisher,
    QueryError,
    SearchResult,
)
from dataportalen.models import DATASET_FILTERS


def _catalog(tmp_path, transport, records, **kwargs):
    return Catalog(write_catalog(tmp_path, records), max_age=None,
                   _transport=transport, **kwargs)


def _dataset(uri, **fields):
    """A copy of the road dataset under another URI, with fields replaced."""
    record = copy.deepcopy(CATALOG_RECORDS[0])
    record.update(uri=uri, entry_id=uri.rsplit("/", 1)[-1], **fields)
    return record


# -- the window ----------------------------------------------------------------


def test_search_with_no_window_returns_every_match(cat):
    result = cat.search()
    assert isinstance(result, SearchResult)
    assert result.total == len(result) == len(cat) == 2
    assert result.limit is None and result.offset == 0
    assert not result.has_more
    assert [d.uri for d in result] == [d.uri for d in cat]


def test_limit_and_offset_window_the_matches(cat):
    every = [d.uri for d in cat.search().datasets]
    first = cat.search(limit=1)
    assert [d.uri for d in first.datasets] == every[:1]
    assert first.total == 2 and first.has_more
    second = cat.search(limit=1, offset=1)
    assert [d.uri for d in second.datasets] == every[1:2]
    assert not second.has_more
    assert cat.search(offset=1).datasets[0].uri == every[1]
    assert cat.search(offset=5).datasets == []


def test_limit_zero_is_the_count_and_the_facets_alone(cat):
    result = cat.search(limit=0)
    assert result.datasets == [] and result.total == 2
    assert result.has_more
    assert result.facets.counts() == cat.search().facets.counts()


@pytest.mark.parametrize("window", [{"limit": -1}, {"offset": -1},
                                    {"limit": True}, {"offset": True}])
def test_a_negative_window_is_refused(cat, window):
    with pytest.raises(QueryError, match="0 or more"):
        cat.search(**window)


def test_facets_count_every_match_whatever_the_window(cat):
    expected = cat.search().facets.to_dict()
    for window in ({"limit": 0}, {"limit": 1}, {"limit": 1, "offset": 1},
                   {"offset": 2}):
        assert cat.search(**window).facets.to_dict() == expected, window
    assert cat.facets().to_dict() == expected


def test_facet_limit_caps_each_facet_and_counts_what_it_cut(cat):
    result = cat.search(facet_limit=1)
    assert len(result.facets["theme"]) == 1
    assert result.facets.omitted["theme"] == 1
    out = result.to_dict()
    assert out["facets_omitted"] == result.facets.omitted
    assert len(out["facets"]["theme"]) == 1
    assert cat.search().to_dict()["facets_omitted"] == {}
    assert cat.facets(1).omitted == result.facets.omitted


# -- the lists take no window ------------------------------------------------


@pytest.mark.parametrize("method", ["datasets", "data_services", "publishers"])
@pytest.mark.parametrize("argument", ["limit", "offset", "facet_limit", "query"])
def test_the_lists_refuse_a_window_and_a_query(cat, method, argument):
    value = "väg" if argument == "query" else 1
    with pytest.raises(QueryError) as caught:
        getattr(cat, method)(**{argument: value})
    message = str(caught.value)
    assert "%s() returns every match and takes no %s" % (method, argument) in message
    assert "search(%s=...) does" % argument in message


def test_the_lists_are_complete(cat):
    assert [d.uri for d in cat.datasets()] == [d.uri for d in cat.search().datasets]
    assert all(isinstance(d, Dataset) for d in cat.datasets())
    services = cat.data_services()
    assert {s.uri for s in services} == {r["uri"] for r in SERVICE_RECORDS}
    assert all(isinstance(s, DataService) for s in services)
    assert all(isinstance(p, Publisher) for p in cat.publishers())


def test_search_returns_datasets_only(cat):
    assert all(isinstance(d, Dataset) for d in cat.search().datasets)
    assert {d.type for d in cat.search().datasets} == {"dataset"}
    # "Utlysningar" is a data service's title, and nothing else's.
    assert cat.search("utlysningar").total == 0
    assert len(cat.data_services(keyword="innovation")) == 1


def test_facet_keys_are_the_dataset_filters(cat):
    assert tuple(cat.search().facets) == DATASET_FILTERS
    assert tuple(cat.facets()) == DATASET_FILTERS
    for gone in ("license", "language", "updated"):
        assert gone not in cat.facets()


# -- as_dict -------------------------------------------------------------------


def test_as_dict_is_to_dict_and_dumps(cat):
    uri = CATALOG_RECORDS[0]["uri"]
    service = SERVICE_RECORDS[0]["uri"]
    pid = cat.publishers()[0].id
    pairs = [
        (cat.search(as_dict=True), cat.search().to_dict()),
        (cat.search(limit=1, theme="transport", as_dict=True),
         cat.search(limit=1, theme="transport").to_dict()),
        (cat.datasets(as_dict=True), [d.to_dict() for d in cat.datasets()]),
        (cat.data_services(as_dict=True),
         [s.to_dict() for s in cat.data_services()]),
        (cat.publishers(as_dict=True), [p.to_dict() for p in cat.publishers()]),
        (cat.publisher(pid, as_dict=True), cat.publisher(pid).to_dict()),
        (cat.facets(as_dict=True), cat.facets().to_dict()),
        (cat.get(uri, as_dict=True), cat.get(uri).to_dict()),
        (cat.get(service, as_dict=True), cat.get(service).to_dict()),
    ]
    for got, expected in pairs:
        assert got == expected
        assert json.loads(json.dumps(got, ensure_ascii=False)) == got
    assert cat.get("https://example.org/nothing", as_dict=True) is None


def test_str_of_a_model_is_its_json(cat):
    dataset = cat.datasets()[0]
    assert str(dataset) == json.dumps(dataset.to_dict(), indent=4,
                                      ensure_ascii=False)
    assert isinstance(cat.facets(), Facets)


# -- language ----------------------------------------------------------------


def test_the_catalogue_language_is_what_text_picks(tmp_path, transport):
    uri = CATALOG_RECORDS[0]["uri"]
    with _catalog(tmp_path, transport, None) as sv:
        dataset = sv.get(uri)
        assert dataset.title.text() == "Vägtrafiknät"
        assert dataset.keywords.list() == ["vägnät", "Geodata"]
    with _catalog(tmp_path, transport, None, language="en") as en:
        dataset = en.get(uri)
        assert dataset.title.text() == "Road traffic network"
        assert dataset.title.text("sv") == "Vägtrafiknät"
        assert dataset.keywords.list() == ["geodata "]
        # Swedish only: falls back rather than answering None.
        assert dataset.description.text() == "Nationellt vägnät med cykelvägar"
        assert en.search(limit=1).datasets[0].title.text() == "Road traffic network"


def test_an_unknown_language_is_refused(tmp_path, transport):
    with pytest.raises(QueryError, match="language"):
        _catalog(tmp_path, transport, None, language="de")


# -- scope and filters ---------------------------------------------------------


def test_the_default_scope_holds_every_access_right(cat):
    assert len(cat) == 2
    assert {d.access_rights for d in cat} == {"public", "non_public"}
    assert cat.search(access_rights="public").total == 1
    assert cat.search(access_rights="non_public").total == 1
    assert cat.info()["datasets"] == 2
    assert "access_rights" not in cat.info()


def test_access_rights_is_no_constructor_argument(tmp_path, transport):
    with pytest.raises(TypeError):
        _catalog(tmp_path, transport, None, access_rights="public")


def test_access_rights_none_matches_records_that_set_none(tmp_path, transport):
    records = CATALOG_RECORDS + [_dataset("https://example.org/unset",
                                          access_rights=None)]
    with _catalog(tmp_path, transport, records) as catalog:
        assert [d.uri for d in catalog.search(access_rights="none")] == [
            "https://example.org/unset"]
        assert catalog.search(access_rights=["none", "public"]).total == 2
        assert len(catalog.datasets(access_rights="none")) == 1


def test_accrual_periodicity_filters(cat):
    assert [d.uri for d in cat.search(accrual_periodicity="annual")] == [
        CATALOG_RECORDS[0]["uri"]]
    assert [d.uri for d in cat.datasets(accrual_periodicity="monthly")] == [
        CATALOG_RECORDS[1]["uri"]]
    assert cat.search(accrual_periodicity=["annual", "monthly"]).total == 2
    assert cat.facets().counts()["accrual_periodicity"] == {"annual": 1,
                                                             "monthly": 1}


def test_modified_after_falls_back_to_issued(tmp_path, transport):
    records = CATALOG_RECORDS + [
        _dataset("https://example.org/new", issued="2025-06-01", modified=None),
        # modified wins over issued when both are set
        _dataset("https://example.org/old", issued="2025-06-01",
                 modified="2010-01-01"),
    ]
    with _catalog(tmp_path, transport, records) as catalog:
        found = catalog.search(modified_after="2025-01-01")
        assert [d.uri for d in found] == ["https://example.org/new"]
        assert [d.uri for d in catalog.datasets(modified_after="2024")] == [
            CATALOG_RECORDS[0]["uri"], "https://example.org/new"]


def test_modified_after_is_for_datasets_only(cat):
    with pytest.raises(QueryError):
        cat.data_services(modified_after="2020")


@pytest.mark.parametrize("name,value,replacement", [
    ("updated", "annual", "accrual_periodicity='annual'"),
    ("license", "cc0_1_0", "dataset.license.id"),
    ("language", "sv", "dataset.languages"),
    ("issued_after", "2020", "modified_after"),
    ("issued_before", "2020", "modified_after"),
    ("modified_before", "2020", "modified_after"),
    ("text", "väg", "search(query='väg')"),
])
def test_every_removed_filter_names_its_replacement(cat, name, value,
                                                    replacement):
    for call in (cat.search, cat.datasets, cat.data_services, cat.publishers):
        with pytest.raises(QueryError) as caught:
            call(**{name: value})
        assert replacement in str(caught.value), (call.__name__, caught.value)
