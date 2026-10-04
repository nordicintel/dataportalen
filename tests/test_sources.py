"""The source catalogues the registry harvests, and how the last harvest went.

The registry is one catalogue built from some 660. A source whose latest
harvest failed leaves its datasets as the last good harvest wrote them, so
they are marked -- not removed, because the data may be perfectly fine.
"""

from __future__ import annotations

import pytest

from conftest import CATALOG_RECORDS, SERVICE_RECORDS, write_catalog
from dataportalen import Catalog, read_catalog
from dataportalen.client import _Counter, _harvest_status

STATUS = "http://entrystore.org/terms/status"
DCT = "http://purl.org/dc/terms/"
STORE = "https://admin.dataportal.se/store"


def result(context, entry, status, modified, title="Harvest"):
    """One PipelineResult as the registry's search returns it."""
    return {
        "contextId": context, "entryId": entry,
        "metadata": {"%s/%s/resource/%s" % (STORE, context, entry): {
            DCT + "title": [{"type": "literal", "value": title}],
            DCT + "subject": [{"type": "literal", "value": "latest"}]}},
        "info": {
            "%s/%s/resource/%s" % (STORE, context, entry): {},
            "%s/%s/entry/%s" % (STORE, context, entry): {
                STATUS: [{"type": "uri",
                          "value": "http://entrystore.org/terms/" + status}],
                DCT + "modified": [{"type": "literal", "value": modified}]}},
    }


def page(children, total=None):
    return {"results": len(children) if total is None else total,
            "resource": {"children": children}}


# -- reading the registry ------------------------------------------------------


def test_the_latest_result_per_context(client, transport):
    transport.push(page([
        result("51", "9", "Success", "2026-10-04T02:23:44.100+02:00", "Trafikverket"),
        result("52", "3", "Failed", "2026-10-04T02:46:11.200+02:00"),
        result("52", "2", "Success", "2026-10-03T02:46:11.200+02:00"),
    ]))
    counter = _Counter()
    found = _harvest_status(client, counter)
    assert found == {
        "51": {"status": "success", "harvested": "2026-10-04T02:23:44",
               "title": "Trafikverket"},
        "52": {"status": "failed", "harvested": "2026-10-04T02:46:11",
               "title": "Harvest"},
    }
    assert counter.value == 1
    assert "PipelineResult" in transport.last_param("query")


def test_it_pages_until_it_has_them_all(client, transport):
    transport.push(page([result(str(n), "1", "Success", "2026-10-04T00:00:00")
                         for n in range(100)], total=101))
    transport.push(page([result("900", "1", "Failed", "2026-10-04T00:00:00")],
                        total=101))
    assert len(_harvest_status(client, _Counter())) == 101
    assert transport.last_param("offset") == "100"


def test_an_unreadable_answer_is_not_fatal(client, transport):
    """A catalogue without harvest status is still a catalogue."""
    transport.push("nope", status=404, content_type="text/plain")
    assert _harvest_status(client, _Counter()) is None


# -- on a record -------------------------------------------------------------


SOURCES = {
    "51": {"status": "failed", "harvested": "2026-10-04T02:46:11", "title": "A"},
    "52": {"status": "success", "harvested": "2026-10-04T02:23:44", "title": "B"},
    "999": {"status": "failed", "harvested": "2026-10-04T02:00:00", "title": "C"},
}


@pytest.fixture
def records():
    return [dict(CATALOG_RECORDS[0], context_id="51"),
            dict(CATALOG_RECORDS[1], context_id="52"),
            dict(SERVICE_RECORDS[0], context_id="51")]


@pytest.fixture
def catalog(tmp_path, transport, records):
    return Catalog(write_catalog(tmp_path, records, sources=SOURCES),
                   max_age=None, _transport=transport, access_rights=None)


def test_a_dataset_from_a_failed_source_says_stale(catalog):
    by_context = {r["context_id"]: r for r in catalog.datasets()}
    assert by_context["51"]["stale"] == {"reason": "harvest failed",
                                         "checked": "2026-10-04T02:46:11"}
    assert "stale" not in by_context["52"]


def test_it_is_marked_and_never_removed(catalog):
    assert catalog.datasets(limit=0).total == 2
    assert catalog.info()["stale_datasets"] == 1


def test_a_data_service_is_marked_too(catalog):
    assert "stale" in catalog.data_services()[0]


def test_read_catalog_agrees(tmp_path, records):
    path = write_catalog(tmp_path, records, sources=SOURCES)
    assert [("stale" in r) for r in read_catalog(path)] == [True, False, True]


def test_a_database_without_harvest_status_marks_nothing(tmp_path, transport):
    cat = Catalog(write_catalog(tmp_path), max_age=None, _transport=transport,
                  access_rights=None)
    assert cat.sources() == []
    assert cat.info()["stale_datasets"] == 0
    assert all("stale" not in r for r in cat.datasets(limit=None))


# -- sources() ---------------------------------------------------------------


def test_sources_lists_every_source_most_datasets_first(catalog):
    rows = catalog.sources()
    assert [row["context_id"] for row in rows] == ["51", "52", "999"]
    assert rows[0] == {"context_id": "51", "status": "failed",
                       "harvested": "2026-10-04T02:46:11", "title": "A",
                       "dataset_count": 1, "data_service_count": 1}
    # A registration that never yielded anything: listed, and empty.
    assert rows[2]["dataset_count"] == 0 and rows[2]["status"] == "failed"
