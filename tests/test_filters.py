"""What you can filter by: `filters()`, the labels, and the honest refusals.

Every filter in here was measured against the whole corpus before it was
allowed to exist -- and the four a data service refuses were measured too.
"""

from __future__ import annotations

import json

import pytest

from conftest import CATALOG_RECORDS, write_catalog
from dataportalen import Catalog, QueryError
from dataportalen.models import DATA_SERVICE_FILTERS, DATASET_FILTERS


@pytest.fixture
def catalog(tmp_path, transport):
    return Catalog(write_catalog(tmp_path), max_age=None,
                   _transport=transport)


# -- filters() ---------------------------------------------------------------


def test_facets_lists_every_dataset_filter(catalog):
    found = catalog.facets()
    assert list(found) == list(DATASET_FILTERS)


def test_facets_agree_with_a_search_s_facets(catalog):
    """The same structure, so browsing and reading a result are one thing."""
    assert catalog.facets().to_dict() == \
        catalog.search(limit=0).facets.to_dict()


def test_facets_values_are_what_you_feed_back_in(catalog):
    value, count = catalog.facets()["theme"][0]
    assert catalog.search(theme=value).total == count


def test_facets_limit_reports_what_it_cut(catalog):
    found = catalog.facets(limit=1)
    assert len(found["theme"]) == 1
    assert found.omitted["theme"] == 1


def test_facets_is_json_serializable(catalog):
    json.dumps(catalog.facets().to_dict())


# -- labels ------------------------------------------------------------------


def test_a_controlled_value_carries_its_vocabulary_label(catalog):
    rows = {row.value: row.label for row in catalog.facets()["access_rights"]}
    assert rows["public"] == {"en": "Public", "sv": "Publik"}
    assert rows["non_public"] == {"en": "Non-public", "sv": "Ej offentlig"}


def test_a_publisher_carries_the_name_from_the_records(catalog):
    """The vocabulary has no entry for an organisation; the file does."""
    rows = {row.value: row.label for row in catalog.facets()["publisher"]}
    assert rows["trafikverket"] == {"sv": "Trafikverket"}


def test_a_keyword_is_its_own_label(catalog):
    for row in catalog.facets()["keyword"]:
        assert row.label == {}


def test_a_keyword_never_wears_a_vocabulary_term_s_label(tmp_path, transport):
    """`german` showed "tyska" and `transport` the theme's label: 45 keywords
    in the registry share a name with a term of some other vocabulary."""
    record = dict(CATALOG_RECORDS[0], keywords={"sv": ["transport", "csv", "sv"]})
    cat = Catalog(write_catalog(tmp_path, [record]), max_age=None,
                  _transport=transport)
    facets = cat.facets()
    assert {row.value: row.label for row in facets["keyword"]} == {
        "transport": {}, "csv": {}, "sv": {}}
    assert facets["theme"][0].label, "the theme still has its own"


def test_a_value_count_still_unpacks_and_compares_as_a_pair(catalog):
    """The label rides alongside the tuple, it is not part of it."""
    row = next(r for r in catalog.facets()["access_rights"]
               if r.value == "public")
    value, count = row
    assert (value, count) == row == ("public", 1)
    assert row.label


# -- data services take the same signature ----------------------------------


def test_data_services_filter_like_datasets(catalog):
    found = catalog.data_services(service_type="rest")
    assert [r.uri for r in found] == ["https://api.example.org/v1"]
    assert isinstance(found, list)


@pytest.mark.parametrize("filters,expected", [
    ({"publisher": "trafikverket"}, ["https://api.example.org/v1"]),
    ({"publisher_type": "national_authority"},
     ["https://api.example.org/v1", "https://geodata.example.org/wms"]),
    ({"service_type": "view_service"}, ["https://geodata.example.org/wms"]),
    ({"theme": "transport"}, ["https://geodata.example.org/wms"]),
    ({"access_rights": "public"},
     ["https://api.example.org/v1", "https://geodata.example.org/wms"]),
    ({"access_rights": "none"}, []),
    ({"keyword": "geodata"}, ["https://geodata.example.org/wms"]),
])
def test_the_six_that_apply_to_a_data_service(catalog, filters, expected):
    assert [r.uri for r in catalog.data_services(**filters)] == expected


def test_data_service_filters_have_only_the_keys_they_have(catalog):
    assert "format" not in DATA_SERVICE_FILTERS
    assert "accrual_periodicity" not in DATA_SERVICE_FILTERS
    assert "kind" not in DATA_SERVICE_FILTERS
    assert "service_type" not in catalog.facets(), "the facets are datasets'"


@pytest.mark.parametrize("filter,value", [
    ("format", "csv"),          # a data service has no distributions
    ("kind", "file"),           # so no kind of distribution either
    ("accrual_periodicity", "annual"),  # nor an accrual periodicity
    ("modified_after", "2024-01-01"),  # modified is on 7.5% of them
])
def test_a_filter_that_cannot_apply_is_refused_not_silently_empty(
        catalog, filter, value):
    """Zero results would read as "no CSV APIs" rather than "wrong question"."""
    with pytest.raises(QueryError) as info:
        catalog.data_services(**{filter: value})
    message = str(info.value)
    assert filter in message
    assert "service_type" in message, "the error names the ones that do work"


@pytest.mark.parametrize("filter,value,names", [
    ("updated", "annual", "accrual_periodicity"),
    ("license", "cc0_1_0", "license.id"),
    ("language", "sv", "languages"),
    ("issued_after", "2020", "modified_after"),
    ("issued_before", "2020", "modified_after"),
    ("modified_before", "2020", "modified_after"),
])
def test_a_removed_filter_names_its_replacement(catalog, filter, value, names):
    """Code written against 0.12 is told what changed, not that it typed
    something wrong -- on every call that took the filter."""
    for call in (catalog.datasets, catalog.data_services, catalog.search):
        with pytest.raises(QueryError) as info:
            call(**{filter: value})
        assert names in str(info.value), call


def test_updated_is_accrual_periodicity_with_the_same_values(catalog):
    with pytest.raises(QueryError) as info:
        catalog.datasets(updated="annual")
    assert "accrual_periodicity='annual'" in str(info.value)
    assert [r.uri for r in catalog.datasets(accrual_periodicity="annual")] == \
        ["https://example.org/roads"]


def test_a_data_service_takes_no_query(catalog):
    """`query` belongs to search(), which answers datasets."""
    with pytest.raises(QueryError) as info:
        catalog.data_services(query="WMS")
    assert "query" in str(info.value)


def test_a_dataset_still_takes_all_of_its_own(catalog):
    for name, value in (("format", "csv"), ("accrual_periodicity", "annual"),
                        ("access_rights", "none")):
        catalog.search(limit=0, **{name: value})
        catalog.datasets(**{name: value})


def test_place_is_not_a_filter_but_spatial_is_still_on_the_record(catalog):
    """19% set it and two thirds of those say 'Sweden'; the rest restate the
    publisher. Not demonstrated, so dropped -- the field itself stays."""
    with pytest.raises(QueryError) as info:
        catalog.datasets(place="kingdom_of_sweden")
    assert "unknown filter" in str(info.value)
    assert "place" not in catalog.facets()
    assert catalog.datasets()[0].spatial == ["kingdom_of_sweden"]


def test_the_date_filters_are_named_after_the_fields_they_read(catalog):
    for old in ("updated_after", "updated_before", "published_after",
                "published_before", "issued_after", "issued_before",
                "modified_before"):
        with pytest.raises(QueryError) as info:
            catalog.datasets(**{old: "2020-01-01"})
        assert "modified_after" in str(info.value)


# -- aliases -----------------------------------------------------------------


def test_an_alias_filters_like_the_publisher_it_names(tmp_path, transport):
    from dataportalen.rdf import resolve_publisher

    scb = dict(CATALOG_RECORDS[0], publisher={
        "uri": resolve_publisher("scb")[0],
        "name": {"sv": "Statistikmyndigheten SCB"}, "type": "national_authority"})
    catalog = Catalog(write_catalog(tmp_path, [scb, CATALOG_RECORDS[1]]),
                      max_age=None, _transport=transport)
    long = "statistikmyndigheten_scb_statistiska_centralbyran"
    assert catalog.search(publisher="scb", limit=0).total == 1
    by_alias = catalog.search(publisher="scb", limit=0).total
    assert by_alias == catalog.search(publisher=long, limit=0).total
    values = {row.value for row in catalog.facets()["publisher"]}
    assert long in values and "scb" not in values, "canonical slug in output"


# -- creator is not a filter any more ----------------------------------------


def test_creator_is_not_a_filter(catalog):
    """It was, and it told the truth; it just never told anyone anything.

    7,104 datasets named a creator and 6,174 named their own publisher again.
    A name that is no filter anywhere is reported as unknown rather than as
    inapplicable, which is what it now is.
    """
    with pytest.raises(QueryError) as info:
        catalog.datasets(creator="trafikverket")
    assert "unknown filter" in str(info.value)
    assert "creator" not in catalog.facets()
    assert "creator" not in DATA_SERVICE_FILTERS
    with pytest.raises(QueryError) as info:
        catalog.data_services(creator="trafikverket")
    assert "unknown filter" in str(info.value)


# -- a facet value must filter to exactly its own count ------------------


def test_a_value_filters_to_exactly_the_count_it_claims(tmp_path, transport):
    """`json` also resolves to application/json+zip, whose slug is different.

    Expanding a local value through the vocabulary made `format="json"` match
    datasets the facet counted under `json_in_a_zip` -- 54 of them in the
    real corpus -- so the counts and the searches disagreed.
    """
    plain = dict(CATALOG_RECORDS[0], uri="https://example.org/a",
                 distributions=[{"format": "json"}])
    zipped = dict(CATALOG_RECORDS[1], uri="https://example.org/b",
                  distributions=[{"format": "json_in_a_zip"}])
    catalog = Catalog(write_catalog(tmp_path, [plain, zipped]), max_age=None,
                      _transport=transport)

    counts = dict((value, count) for value, count in catalog.facets()["format"])
    assert counts == {"json": 1, "json_in_a_zip": 1}
    for value, count in counts.items():
        assert catalog.search(limit=0, format=value).total == count, value


def test_an_alias_still_resolves_when_the_file_has_no_such_slug(tmp_path,
                                                                transport):
    """`xlsx` has to keep finding what is stored as microsoft_excel_xml.

    That is what the 597 xlsx datasets in the real corpus are filed under, and
    nobody filtering by format types the long name.
    """
    record = dict(CATALOG_RECORDS[0],
                  distributions=[{"format": "microsoft_excel_xml"}])
    catalog = Catalog(write_catalog(tmp_path, [record]), max_age=None,
                      _transport=transport)
    assert catalog.search(limit=0, format="xlsx").total == 1


def test_every_facet_value_round_trips(catalog):
    """The contract the docs state: a value goes straight back in."""
    options = catalog.facets()
    for name in options:
        for value, count in options[name]:
            assert catalog.search(limit=0, **{name: value}).total == count, (
                "%s=%r claimed %d" % (name, value, count))


def test_data_services_refuse_a_window_and_point_at_search(catalog):
    """A complete list takes no window at all, negative or not."""
    for kwargs in ({"limit": -1}, {"limit": 5}, {"offset": 1},
                   {"facet_limit": 1}):
        for listing in (catalog.data_services, catalog.datasets):
            with pytest.raises(QueryError) as info:
                listing(**kwargs)
            assert "search(" in str(info.value)
