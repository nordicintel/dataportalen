"""The registry's nightly link check, carried into the catalogue.

The verdict is the registry's and is passed through as it stands. This package
wraps what the API says; it does not re-test links or second-guess a status.
"""

from __future__ import annotations

import pytest

from conftest import CATALOG_RECORDS, write_catalog
from dataportalen import Catalog, QueryError


def with_links(record, *pairs):
    """A copy of `record` whose distributions carry the given verdicts."""
    dists = []
    for (url, status, message) in pairs:
        dists.append({"format": "csv", "access_url": [url], "download_url": [],
                      "link": {"status": status, "message": message,
                               "checked": "2026-09-28T02:44:17", "attempts": 1}})
    return dict(record, distributions=dists)


@pytest.fixture
def catalog(tmp_path, transport):
    records = [
        with_links(CATALOG_RECORDS[0],
                   ("https://example.org/a.csv", "broken", "Not Found"),
                   ("https://example.org/b.csv", "success", "OK")),
        with_links(CATALOG_RECORDS[1],
                   ("https://example.org/c.csv", "broken", "Too Many Requests")),
    ]
    return Catalog(write_catalog(tmp_path, records), max_age=None,
                   _transport=transport)


# -- the verdict is on the record -------------------------------------------


def test_each_file_carries_the_registrys_verdict(catalog):
    dist = catalog.datasets()[0]["distributions"][0]
    assert dist["link"] == {"status": "broken", "message": "Not Found",
                            "checked": "2026-09-28T02:44:17", "attempts": 1}


def test_the_message_is_passed_through_not_interpreted(catalog):
    """`Too Many Requests` is broken because the registry says broken."""
    statuses = [(d["link"]["status"], d["link"]["message"])
                for r in catalog.datasets(limit=None)
                for d in r["distributions"]]
    assert ("broken", "Too Many Requests") in statuses
    assert ("broken", "Not Found") in statuses


# -- link is an ordinary filter ---------------------------------------------


def test_link_filters_like_any_other(catalog):
    assert catalog.datasets(link="broken", limit=0).total == 2
    assert catalog.datasets(link="success", limit=0).total == 1


def test_link_is_in_the_breakdown_with_counts(catalog):
    counts = dict(catalog.datasets(limit=0).breakdown["link"])
    assert counts == {"broken": 2, "success": 1}


def test_a_link_value_round_trips_like_every_other(catalog):
    for value, count in catalog.filters()["link"]:
        assert catalog.datasets(link=value, limit=0).total == count


def test_link_takes_a_list(catalog):
    assert catalog.datasets(link=["broken", "success"], limit=0).total == 2


def test_an_unknown_link_value_is_refused(catalog):
    with pytest.raises(QueryError) as info:
        catalog.datasets(link="dead")
    assert "success" in str(info.value)


def test_data_services_take_link_too(cat):
    assert "link" in cat.data_services(limit=0).breakdown


# -- excluding them at init --------------------------------------------------


def test_exclude_broken_drops_the_broken_files(tmp_path, transport):
    records = [
        with_links(CATALOG_RECORDS[0],
                   ("https://example.org/a.csv", "broken", "Not Found"),
                   ("https://example.org/b.csv", "success", "OK")),
    ]
    path = write_catalog(tmp_path, records)

    kept = Catalog(path, max_age=None, _transport=transport)
    assert len(kept.datasets()[0]["distributions"]) == 2

    pruned = Catalog(path, max_age=None, _transport=transport,
                     exclude_broken=True)
    dists = pruned.datasets()[0]["distributions"]
    assert len(dists) == 1
    assert dists[0]["link"]["status"] == "success"


def test_a_dataset_whose_files_all_break_keeps_its_metadata(tmp_path, transport):
    """It is still a dataset; it just has nothing you can fetch."""
    records = [with_links(CATALOG_RECORDS[0],
                          ("https://example.org/a.csv", "broken", "Gone"))]
    cat = Catalog(write_catalog(tmp_path, records), max_age=None,
                  _transport=transport, exclude_broken=True)
    record = cat.datasets()[0]
    assert record["distributions"] == []
    assert record["title"]


def test_excluding_is_off_by_default(tmp_path, transport):
    records = [with_links(CATALOG_RECORDS[0],
                          ("https://example.org/a.csv", "broken", "Gone"))]
    cat = Catalog(write_catalog(tmp_path, records), max_age=None,
                  _transport=transport)
    assert len(cat.datasets()[0]["distributions"]) == 1


# -- a record with no check at all -------------------------------------------


def test_a_file_the_check_never_saw_has_no_link_key(tmp_path, transport):
    """An older file, or one written with links=False."""
    cat = Catalog(write_catalog(tmp_path), max_age=None, _transport=transport)
    dist = cat.datasets()[0]["distributions"][0]
    assert dist.get("link") is None
    assert cat.datasets(limit=0).breakdown["link"] == []
    assert cat.datasets(link="broken", limit=0).total == 0
