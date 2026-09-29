"""The catalogue on disk: same filters, same values, no network."""

from __future__ import annotations

import gzip
import json

import pytest

from dataportalen import LocalCatalog, QueryError

RECORDS = [
    {
        "uri": "https://example.org/roads",
        "context_id": "50",
        "title": "Vägtrafiknät",
        "description": "Nationellt vägnät med cykelvägar",
        "keywords": ["vägnät", "Geodata"],
        "themes": ["transport"],
        "license": "cc_by_4_0",
        "access_rights": "public",
        "accrual_periodicity": "annual",
        "languages": ["swedish"],
        "spatial": ["kingdom_of_sweden"],
        "issued": "2020-03-04",
        "modified": "2024-05-06T09:00:00+02:00",
        "publisher": {"uri": "http://dataportal.se/organisation/SE2021006297",
                      "name": "Trafikverket"},
        "distributions": [{"format": "csv"}, {"format": "json"}],
    },
    {
        "uri": "https://example.org/budget",
        "context_id": "51",
        "title": "Kommunalt bidrag",
        "description": "Utbetalda bidrag per kommun",
        "keywords": ["ekonomi"],
        "themes": ["economy_and_finance"],
        "license": "cc0_1_0",
        "access_rights": "non_public",
        "accrual_periodicity": "monthly",
        "languages": ["swedish", "english"],
        "spatial": [],
        "issued": "2014-01-01",
        "modified": "2019-01-01",
        "publisher": {"uri": "http://dataportal.se/organisation/SE2021005521",
                      "name": "Försäkringskassan"},
        "distributions": [{"format": "xlsx"}],
    },
]


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / "catalog.jsonl"
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in RECORDS) + "\n",
        encoding="utf-8")
    return LocalCatalog(str(path), download=False)


def test_it_reads_the_file(catalog):
    assert len(catalog) == 2
    assert [r["uri"] for r in catalog] == [r["uri"] for r in RECORDS]
    assert catalog.downloaded is not None


def test_gzip_is_read_transparently(tmp_path):
    path = tmp_path / "catalog.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(RECORDS[0]) + "\n")
    assert len(LocalCatalog(str(path), download=False)) == 1


def test_a_missing_file_is_not_silently_downloaded(tmp_path):
    with pytest.raises(FileNotFoundError):
        LocalCatalog(str(tmp_path / "nope.jsonl"), download=False)


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
    ({"catalog": 51}, ["budget"]),
    ({"uri": "https://example.org/budget"}, ["budget"]),
    ({"text": "cykel"}, ["roads"]),
    ({"text": "bidrag"}, ["budget"]),
    ({"title": "bidrag"}, ["budget"]),
    ({"description": "vägnät"}, ["roads"]),
    ({"keyword": "geodata"}, ["roads"]),
    ({"theme": "transport", "format": "csv"}, ["roads"]),
    ({"theme": "transport", "format": "xlsx"}, []),
])
def test_filters_match_what_the_client_would(catalog, filters, expected):
    found = [r["uri"].rsplit("/", 1)[-1] for r in catalog.datasets(**filters)]
    assert found == expected


@pytest.mark.parametrize("filters,expected", [
    ({"updated_after": "2024-01-01"}, ["roads"]),
    ({"updated_before": "2020-01-01"}, ["budget"]),
    ({"published_after": "2020"}, ["roads"]),
    ({"published_before": "2015-06"}, ["budget"]),
])
def test_dates_compare_across_the_forms_publishers_use(catalog, filters, expected):
    """A bare date, a timestamp and one with an offset must sort together."""
    found = [r["uri"].rsplit("/", 1)[-1] for r in catalog.datasets(**filters)]
    assert found == expected


def test_limit_caps_the_result(catalog):
    assert len(catalog.datasets(limit=1)) == 1
    assert len(catalog.datasets()) == 2


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
    for argument in ("query", "offset", "sort"):
        with pytest.raises(QueryError) as info:
            catalog.datasets(**{argument: "x"})
        assert argument in str(info.value)


def test_a_local_search_is_broken_down_too(catalog):
    """Same shape as the live one, counted over the file."""
    page = catalog.datasets()
    assert page.breakdown["theme"] == [("economy_and_finance", 1), ("transport", 1)]
    assert page.breakdown["language"][0] == ("swedish", 2)
    assert page.breakdown["format"] == [("csv", 1), ("json", 1), ("xlsx", 1)]
    assert page.breakdown["publisher"][0].dataset_count == 1
    # Unlike the registry, a file can count keywords.
    assert ("Geodata", 1) in page.breakdown["keyword"]


def test_the_breakdown_describes_the_match_not_the_page(catalog):
    page = catalog.datasets(theme="transport")
    assert page.total == 1
    assert page.breakdown["publisher"] == [("trafikverket", 1)]
    assert page.breakdown["theme"] == [("transport", 1)]


def test_a_dataset_counts_once_per_value(catalog):
    """Two CSV distributions on one dataset is one dataset under `csv`."""
    page = catalog.datasets(uri="https://example.org/roads")
    assert page.breakdown["format"] == [("csv", 1), ("json", 1)]
