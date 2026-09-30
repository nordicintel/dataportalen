"""The file: where it lives, when it is written, and what `get`/`info` say.

Every refresh mode is exercised against a transport that would fail if asked,
so "nothing was downloaded" is a fact here rather than a hope.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
import os

import pytest

from conftest import CATALOG_RECORDS, write_catalog
from dataportalen import Catalog, default_catalog_path


def age(path, days):
    when = (dt.datetime.now() - dt.timedelta(days=days)).timestamp()
    os.utime(path, (when, when))


# -- where it lives ----------------------------------------------------------


def test_the_default_path_is_a_cache_directory():
    path = default_catalog_path()
    # One file, not one per language: a record carries both languages.
    assert path.endswith(os.path.join("dataportalen", "catalog.jsonl"))
    assert os.path.isabs(path)
    # Never the working directory: it must not land in someone's repository.
    assert os.path.dirname(path) != os.getcwd()


def test_it_reads_the_file(cat):
    """One file, two kinds of line; len() and iteration are the datasets."""
    assert len(cat) == 2
    assert [r["uri"] for r in cat] == [r["uri"] for r in CATALOG_RECORDS]
    assert cat.data_services().total == 2


def test_gzip_is_read_transparently(tmp_path, transport):
    path = tmp_path / "catalog.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(CATALOG_RECORDS[0]) + "\n")
    assert len(Catalog(str(path), refresh="never", transport=transport)) == 1


# -- when it is written ------------------------------------------------------


def test_refresh_never_will_not_download_a_missing_file(tmp_path, transport):
    with pytest.raises(FileNotFoundError) as info:
        Catalog(str(tmp_path / "nope.jsonl"), refresh="never", transport=transport)
    assert "never" in str(info.value)
    assert transport.requests == []


def test_an_unknown_refresh_mode_is_rejected(tmp_path, transport):
    from dataportalen import QueryError

    with pytest.raises(QueryError) as info:
        Catalog(write_catalog(tmp_path), refresh="sometimes", transport=transport)
    assert "if_missing" in str(info.value)


def test_a_stale_copy_warns_and_is_not_refreshed(tmp_path, transport, caplog):
    """A program that answered in a second yesterday must not block today."""
    path = write_catalog(tmp_path)
    age(path, 30)
    with caplog.at_level(logging.WARNING, logger="dataportalen"):
        cat = Catalog(path, refresh="if_missing", transport=transport)
    assert "30 days old" in caplog.text
    assert cat.stale is True
    assert cat.datasets().total == 2
    assert transport.requests == [], "a stale copy must not trigger a download"


def test_stale_after_is_the_caller_s_definition_of_old(tmp_path, transport):
    path = write_catalog(tmp_path)
    age(path, 3)
    assert Catalog(path, refresh="never", transport=transport,
                   stale_after=7).stale is False
    assert Catalog(path, refresh="never", transport=transport,
                   stale_after=2).stale is True


def test_a_fresh_copy_is_used_under_if_stale(tmp_path, transport):
    path = write_catalog(tmp_path)
    cat = Catalog(path, refresh="if_stale", transport=transport)
    assert cat.datasets().total == 2
    assert transport.requests == []


# -- what it says about itself ----------------------------------------------


def test_a_fresh_file_is_never_negative_days_old(tmp_path, transport):
    """A just-written file can be stamped a hair ahead of the clock."""
    cat = Catalog(write_catalog(tmp_path), refresh="never", transport=transport)
    assert cat.age_days == 0
    assert cat.stale is False


def test_info_reports_the_file_and_its_age(cat):
    info = cat.info()
    assert info["path"].endswith("catalog.jsonl")
    assert info["datasets"] == 2
    assert info["data_services"] == 2
    assert info["publishers"] == 2
    assert info["bytes"] > 0
    assert info["age_days"] == 0
    assert info["stale"] is False
    assert info["downloaded"] is not None
    json.dumps(info)                       # it is a plain dict, all the way down


# -- get ---------------------------------------------------------------------


def test_get_returns_the_record_for_a_uri(cat, transport):
    found = cat.get("https://example.org/budget")
    assert found["title"] == {"sv": "Kommunalt bidrag"}
    assert transport.requests == []


def test_get_returns_none_for_a_uri_the_copy_lacks(cat, transport):
    assert cat.get("https://example.org/not-in-the-file") is None
    assert transport.requests == [], "an absent URI is not worth a request"


def test_get_returns_the_first_of_two_records_sharing_a_uri(tmp_path, transport):
    """Four real datasets are harvested into two catalogues under one URI."""
    twin = dict(CATALOG_RECORDS[0], context_id="99", entry_id="9")
    path = write_catalog(tmp_path, [CATALOG_RECORDS[0], twin])
    cat = Catalog(path, refresh="never", transport=transport)
    assert cat.get(CATALOG_RECORDS[0]["uri"])["context_id"] == "50"


def test_get_in_another_format_asks_the_registry(cat, transport):
    transport.push("<http://x> <http://y> <http://z> .", content_type="text/turtle")
    text = cat.get("https://example.org/budget", format="turtle")
    assert "<http://x>" in text
    assert len(transport.requests) == 1
    assert "text/turtle" in transport.requests[-1] or transport.requests[-1]


def test_get_in_another_format_is_none_for_an_unknown_uri(cat, transport):
    assert cat.get("https://example.org/nope", format="turtle") is None
    assert transport.requests == []
