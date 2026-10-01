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
                   _transport=transport, access_rights=None)


# -- filters() ---------------------------------------------------------------


def test_filters_lists_every_dataset_filter(catalog):
    found = catalog.filters()
    assert list(found) == list(DATASET_FILTERS)


def test_filters_agrees_with_a_search_breakdown(catalog):
    """The same structure, so browsing and reading a result are one thing."""
    assert catalog.filters().to_dict() == \
        catalog.datasets(limit=0).breakdown.to_dict()


def test_filters_values_are_what_you_feed_back_in(catalog):
    value, count = catalog.filters()["theme"][0]
    assert catalog.datasets(theme=value).total == count


def test_filters_limit_reports_what_it_cut(catalog):
    found = catalog.filters(limit=1)
    assert len(found["theme"]) == 1
    assert found.omitted["theme"] == 1


def test_filters_is_json_serializable(catalog):
    json.dumps(catalog.filters().to_dict())


# -- labels ------------------------------------------------------------------


def test_a_controlled_value_carries_its_vocabulary_label(catalog):
    rows = {row.value: row.label for row in catalog.filters()["access_rights"]}
    assert rows["public"] == {"en": "Public", "sv": "Publik"}
    assert rows["non_public"] == {"en": "Non-public", "sv": "Ej offentlig"}


def test_a_publisher_carries_the_name_from_the_records(catalog):
    """The vocabulary has no entry for an organisation; the file does."""
    rows = {row.value: row.label for row in catalog.filters()["publisher"]}
    assert rows["trafikverket"] == {"sv": "Trafikverket"}


def test_a_keyword_is_its_own_label(catalog):
    for row in catalog.filters()["keyword"]:
        assert row.label == {}


def test_a_value_count_still_unpacks_and_compares_as_a_pair(catalog):
    """The label rides alongside the tuple, it is not part of it."""
    row = next(r for r in catalog.filters()["access_rights"]
               if r.value == "public")
    value, count = row
    assert (value, count) == row == ("public", 1)
    assert row.label


# -- data services take the same signature ----------------------------------


def test_data_services_search_like_datasets(catalog):
    page = catalog.data_services(service_type="rest")
    assert [r["uri"] for r in page] == ["https://api.example.org/v1"]
    assert page.total == 1


@pytest.mark.parametrize("filters,expected", [
    ({"publisher": "trafikverket"}, ["https://api.example.org/v1"]),
    ({"publisher_type": "national_authority"},
     ["https://api.example.org/v1", "https://geodata.example.org/wms"]),
    ({"service_type": "view_service"}, ["https://geodata.example.org/wms"]),
    ({"theme": "transport"}, ["https://geodata.example.org/wms"]),
    ({"license": "cc0_1_0"}, ["https://api.example.org/v1"]),
    ({"access_rights": "public"},
     ["https://api.example.org/v1", "https://geodata.example.org/wms"]),
    ({"keyword": "geodata"}, ["https://geodata.example.org/wms"]),
    ({"text": "WMS"}, ["https://geodata.example.org/wms"]),
])
def test_the_seven_that_apply_to_a_data_service(catalog, filters, expected):
    assert [r["uri"] for r in catalog.data_services(limit=None, **filters)] == expected


def test_a_data_service_breakdown_has_only_the_keys_it_has(catalog):
    found = catalog.data_services(limit=0).breakdown
    assert list(found) == list(DATA_SERVICE_FILTERS)
    assert "format" not in found
    assert "updated" not in found


@pytest.mark.parametrize("filter,value", [
    ("format", "csv"),          # a data service has no distributions
    ("updated", "annual"),      # nor an accrual periodicity
    ("language", "sv"),          # one single value across all 599
    ("modified_after", "2024-01-01"),  # modified is on 7.5% of them
    ("issued_after", "2020"),          # issued on 0.8%
])
def test_a_filter_that_cannot_apply_is_refused_not_silently_empty(
        catalog, filter, value):
    """Zero results would read as "no CSV APIs" rather than "wrong question"."""
    with pytest.raises(QueryError) as info:
        catalog.data_services(**{filter: value})
    message = str(info.value)
    assert filter in message
    assert "service_type" in message, "the error names the ones that do work"


def test_a_dataset_still_takes_all_of_its_own(catalog):
    for name in ("format", "updated", "language"):
        catalog.datasets(limit=0, **{name: {
            "format": "csv", "updated": "annual", "language": "sv"}[name]})


def test_place_is_not_a_filter_but_spatial_is_still_on_the_record(catalog):
    """19% set it and two thirds of those say 'Sweden'; the rest restate the
    publisher. Not demonstrated, so dropped -- the field itself stays."""
    with pytest.raises(QueryError) as info:
        catalog.datasets(place="kingdom_of_sweden")
    assert "unknown filter" in str(info.value)
    assert "place" not in catalog.filters()
    assert catalog.datasets()[0]["spatial"] == ["kingdom_of_sweden"]


def test_the_date_filters_are_named_after_the_fields_they_read(catalog):
    for old in ("updated_after", "updated_before", "published_after",
                "published_before"):
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
                      max_age=None, _transport=transport, access_rights=None)
    long = "statistikmyndigheten_scb_statistiska_centralbyran"
    assert catalog.datasets(publisher="scb", limit=0).total == 1
    by_alias = catalog.datasets(publisher="scb", limit=0).total
    assert by_alias == catalog.datasets(publisher=long, limit=0).total
    values = {row.value for row in catalog.filters()["publisher"]}
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
    assert "creator" not in catalog.filters()
    assert "creator" not in catalog.data_services(limit=0).breakdown


# -- a breakdown value must filter to exactly its own count ------------------


def test_a_value_filters_to_exactly_the_count_it_claims(tmp_path, transport):
    """`json` also resolves to application/json+zip, whose slug is different.

    Expanding a local value through the vocabulary made `format="json"` match
    datasets the breakdown counted under `json_in_a_zip` -- 54 of them in the
    real corpus -- so the counts and the searches disagreed.
    """
    plain = dict(CATALOG_RECORDS[0], uri="https://example.org/a",
                 distributions=[{"format": "json"}])
    zipped = dict(CATALOG_RECORDS[1], uri="https://example.org/b",
                  distributions=[{"format": "json_in_a_zip"}])
    catalog = Catalog(write_catalog(tmp_path, [plain, zipped]), max_age=None,
                      _transport=transport, access_rights=None)

    counts = dict((value, count) for value, count in catalog.filters()["format"])
    assert counts == {"json": 1, "json_in_a_zip": 1}
    for value, count in counts.items():
        assert catalog.datasets(limit=0, format=value).total == count, value


def test_an_alias_still_resolves_when_the_file_has_no_such_slug(tmp_path,
                                                                transport):
    """`xlsx` has to keep finding what is stored as microsoft_excel_xml.

    That is what the 597 xlsx datasets in the real corpus are filed under, and
    nobody filtering by format types the long name.
    """
    record = dict(CATALOG_RECORDS[0],
                  distributions=[{"format": "microsoft_excel_xml"}])
    catalog = Catalog(write_catalog(tmp_path, [record]), max_age=None,
                      _transport=transport, access_rights=None)
    assert catalog.datasets(limit=0, format="xlsx").total == 1


def test_every_breakdown_value_round_trips(catalog):
    """The contract the docs state: a value goes straight back in."""
    options = catalog.filters()
    for name in options:
        for value, count in options[name]:
            assert catalog.datasets(limit=0, **{name: value}).total == count, (
                "%s=%r claimed %d" % (name, value, count))


def test_data_services_refuse_a_negative_window_too(catalog):
    with pytest.raises(QueryError):
        catalog.data_services(limit=-1)
