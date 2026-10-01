"""The registry's nightly link check, and what the catalogue does with it.

The verdict is the registry's and is passed through as it stands. This package
wraps what the API says; it does not re-test links or second-guess a status.
What it does decide is what to show: a broken file is marked, a working one is
not, and by default the broken ones are not there at all.
"""

from __future__ import annotations

import pytest

from conftest import CATALOG_RECORDS, write_catalog
from dataportalen import Catalog, QueryError


def with_files(record, *pairs):
    """A copy of `record` whose distributions carry the given verdicts."""
    dists = []
    for (url, reason) in pairs:
        dist = {"format": "csv", "access_url": [url], "download_url": []}
        if reason is not None:
            dist["broken"] = {"reason": reason, "checked": "2026-09-28T02:44:17"}
        dists.append(dist)
    return dict(record, distributions=dists)


ALIVE = None


@pytest.fixture
def path(tmp_path):
    return write_catalog(tmp_path, [
        with_files(CATALOG_RECORDS[0],
                   ("https://example.org/a.csv", "Not Found"),
                   ("https://example.org/b.csv", ALIVE)),
        with_files(dict(CATALOG_RECORDS[1], access_rights="public"),
                   ("https://example.org/c.csv", "Too Many Requests")),
    ])


# -- what a record carries ----------------------------------------------------


def test_a_broken_file_is_marked_and_a_working_one_is_not(path, transport):
    cat = Catalog(path, max_age=None, _transport=transport, exclude_broken=False)
    a, b = cat.datasets()[0]["distributions"]
    assert a["broken"] == {"reason": "Not Found", "checked": "2026-09-28T02:44:17"}
    assert "broken" not in b
    assert "link" not in a and "link" not in b


def test_the_reason_is_the_registrys_not_ours(path, transport):
    """`Too Many Requests` is broken because the registry said broken."""
    cat = Catalog(path, max_age=None, _transport=transport, exclude_broken=False)
    reasons = {d["broken"]["reason"] for r in cat.datasets(limit=None)
               for d in r["distributions"] if d.get("broken")}
    assert reasons == {"Not Found", "Too Many Requests"}


def test_the_record_itself_carries_no_verdict(path, transport):
    """277 datasets had a broken landing page and perfectly good files."""
    cat = Catalog(path, max_age=None, _transport=transport, exclude_broken=False)
    for record in cat.datasets(limit=None):
        assert "link" not in record
        assert "broken" not in record


# -- the default leaves broken files, and dead datasets, out -----------------


def test_broken_files_are_left_out_by_default(path, transport):
    cat = Catalog(path, max_age=None, _transport=transport)
    assert cat.datasets(limit=0).total == 1
    dists = cat.datasets()[0]["distributions"]
    assert [d["access_url"] for d in dists] == [["https://example.org/b.csv"]]
    assert "broken" not in dists[0]


def test_a_dataset_whose_every_file_is_broken_is_left_out(path, transport):
    """5,331 of them. Metadata with nothing to fetch is what dead means."""
    cat = Catalog(path, max_age=None, _transport=transport)
    assert cat.get(CATALOG_RECORDS[1]["uri"]) is None
    assert cat.get(CATALOG_RECORDS[0]["uri"]) is not None


def test_a_dataset_that_never_had_files_stays(tmp_path, transport):
    """1,647 of them: APIs and registers. Not dead, just not files."""
    record = dict(CATALOG_RECORDS[0], distributions=[])
    cat = Catalog(write_catalog(tmp_path, [record]), max_age=None,
                  _transport=transport)
    assert cat.datasets(limit=0).total == 1


def test_exclude_broken_false_keeps_everything(path, transport):
    cat = Catalog(path, max_age=None, _transport=transport, exclude_broken=False)
    assert cat.datasets(limit=0).total == 2
    assert len(cat.datasets()[0]["distributions"]) == 2


def test_link_is_not_a_filter(path, transport):
    cat = Catalog(path, max_age=None, _transport=transport)
    with pytest.raises(QueryError) as info:
        cat.datasets(link="broken")
    assert "unknown filter" in str(info.value)
    assert "link" not in cat.filters()
    assert "link" not in cat.data_services(limit=0).breakdown


# -- a file the check never saw -----------------------------------------------


def test_a_file_the_check_never_saw_is_simply_unmarked(tmp_path, transport):
    """An older file, or one written with links=False, is not 'broken'."""
    cat = Catalog(write_catalog(tmp_path), max_age=None, _transport=transport,
                  access_rights=None)
    for record in cat.datasets(limit=None):
        for dist in record["distributions"]:
            assert "broken" not in dist
    assert cat.datasets(limit=0).total == 2
