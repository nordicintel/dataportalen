"""What a distribution is, read out of its metadata.

`accessURL` can be a file, an API or a web page, and most distributions have
nothing else. `kind` says which, without a request being made.
"""

from __future__ import annotations

import pytest

from conftest import CATALOG_RECORDS, write_catalog
from dataportalen import Catalog, LiveCatalog, QueryError, read_catalog
from dataportalen.retrieval import KINDS, classify


def dist(format=None, access=None, download=None, services=()):
    return {"format": format, "access_url": access, "download_url": download,
            "access_service_uris": list(services)}


@pytest.mark.parametrize("record, kind", [
    (dist("csv", "https://example.org/files/a"), "file"),
    (dist(None, "https://example.org/files/a.xlsx"), "file"),
    (dist("csv", download="https://example.org/a"), "file"),
    (dist("pdf", "https://example.org/report"), "file"),
    (dist("csv", "https://x.entryscape.net/rowstore/dataset/abc"), "rowstore"),
    (dist("csv", download="https://x.entryscape.net/rowstore/dataset/abc/csv"),
     "rowstore"),
    (dist(None, "https://ckan.example.org/dataset/roads/resource/1"), "ckan"),
    (dist("json", "https://opendata.umea.se/api/explore/v2.1/catalog/datasets/x"),
     "huwise"),
    (dist("json", "https://api.scb.se/OV0104/v1/doris/sv/ssd/BE/BE0101"), "pxweb"),
    (dist("json", "https://statistik.csn.se/PXWeb/api/v1/sv/CSNstat"), "pxweb"),
    (dist("json", "https://api.kolada.se/v2/kpi/N00945"), "kolada"),
    (dist(None, "https://doi.org/10.5878/abc"), "doi"),
    (dist("wms_service", "https://example.org/maps"), "geodata"),
    (dist("geopackage", download="https://example.org/a.gpkg"), "geodata"),
    (dist(None, "https://example.org/arcgis/rest/services/x/MapServer"), "geodata"),
    (dist(None, "https://example.org/ows?service=WFS&request=GetCapabilities"),
     "geodata"),
    (dist("html", "https://example.org/about-the-data"), "web_page"),
    (dist(None, "https://example.org/api", services=["https://example.org/svc"]),
     "api"),
    (dist(None, "https://example.org/somewhere"), "unknown"),
    (dist(), "unknown"),
])
def test_each_kind(record, kind):
    assert classify(record) == kind
    assert kind in KINDS


def test_a_download_url_that_says_html_is_a_web_page():
    """SMHI points downloadURL at web pages and says so in the format. The
    profile calls downloadURL a file; the format is the one telling the truth."""
    assert classify(dist("html", download="https://www.smhi.se/data/x")) == "web_page"
    assert classify(dist("html", download="https://example.org/x.csv")) == "file"


def test_a_url_stored_as_a_list_is_read():
    assert classify(dist("json", access=["https://api.scb.se/OV0104/v1/x"])) == "pxweb"


def test_nothing_is_fetched(transport):
    classify(dist("csv", "https://example.org/a.csv"))
    assert transport.requests == []


# -- on a record -------------------------------------------------------------


def with_dists(record, *dists):
    full = []
    for d in dists:
        base = dict(record["distributions"][0])
        base.update(d)
        full.append(base)
    return dict(record, distributions=full)


@pytest.fixture
def catalog(tmp_path, transport):
    records = [
        with_dists(CATALOG_RECORDS[0],
                   dist("csv", "https://example.org/files/roads.csv"),
                   dist("json", "https://api.scb.se/OV0104/v1/doris/sv/ssd/x")),
        with_dists(CATALOG_RECORDS[1], dist("html", "https://example.org/budget")),
    ]
    return Catalog(write_catalog(tmp_path, records), max_age=None,
                   _transport=transport, access_rights=None)


def test_every_distribution_carries_its_kind(catalog):
    kinds = [[d["kind"] for d in r["distributions"]] for r in catalog.datasets()]
    assert sorted(kinds) == [["file", "pxweb"], ["web_page"]]


def test_read_catalog_carries_it_too(tmp_path):
    for record in read_catalog(write_catalog(tmp_path)):
        for d in record.get("distributions") or []:
            assert d["kind"] in KINDS


def test_kind_is_a_filter_on_any_distribution(catalog):
    assert catalog.datasets(kind="pxweb", limit=0).total == 1
    assert catalog.datasets(kind="web_page", limit=0).total == 1
    assert catalog.datasets(kind=["pxweb", "web_page"], limit=0).total == 2
    assert catalog.datasets(kind="geodata", limit=0).total == 0


def test_kind_is_a_facet_and_its_values_feed_back_in(catalog):
    rows = catalog.facets()["kind"]
    assert sorted((row.value, row.count) for row in rows) == [
        ("file", 1), ("pxweb", 1), ("web_page", 1)]
    for row in rows:
        assert catalog.datasets(kind=row.value, limit=0).total == row.count


def test_an_unknown_kind_is_an_error(catalog):
    with pytest.raises(QueryError) as info:
        catalog.datasets(kind="fil")
    assert "unknown kind" in str(info.value) and "file" in str(info.value)


def test_the_live_catalogue_says_why_it_cannot_filter_on_kind(transport):
    with LiveCatalog(_transport=transport) as live:
        with pytest.raises(QueryError) as info:
            live.datasets(kind="file")
    assert "use Catalog" in str(info.value)
