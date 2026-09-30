"""The catalogue database: what it keys on, and what a refresh replaces.

The whole reason for a database rather than a file is that bringing a copy up to
date should cost a few hundred rows rather than a rebuild. These hold that.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3

import pytest

from conftest import CATALOG_RECORDS, write_catalog
from dataportalen import Catalog, ParseError, read_catalog
from dataportalen.client import SCHEMA_VERSION, _connect, _meta_get, _meta_set, _write_records


def rows_of(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in db.execute("SELECT * FROM record")]
    finally:
        db.close()


# -- what a row is keyed on --------------------------------------------------


def test_two_records_sharing_a_uri_both_survive(tmp_path, transport):
    """Four datasets in the corpus share a URI with another.

    Keying on `uri` merged those pairs and lost four records. The registry
    identifies an entry by context and entry id, so that is the key.
    """
    twin = dict(CATALOG_RECORDS[0], context_id="99", entry_id="9")
    path = write_catalog(tmp_path, [CATALOG_RECORDS[0], twin])
    assert len(rows_of(path)) == 2
    cat = Catalog(path, refresh="never", transport=transport)
    assert len(cat) == 2
    # get() by that URI still answers with one of them, deterministically.
    assert cat.get(CATALOG_RECORDS[0]["uri"])["context_id"] == "50"


def test_rewriting_an_entry_replaces_its_own_row(tmp_path, transport):
    path = write_catalog(tmp_path, [CATALOG_RECORDS[0]])
    changed = dict(CATALOG_RECORDS[0], title={"sv": "Nytt namn"})
    db = _connect(path)
    with db:
        _write_records(db, [("dataset", "2026-10-01T00:00:00", changed)])
    db.close()

    rows = rows_of(path)
    assert len(rows) == 1, "an update must not append a second row"
    assert json.loads(rows[0]["doc"])["title"] == {"sv": "Nytt namn"}
    assert rows[0]["harvested"] == "2026-10-01T00:00:00"


def test_a_new_entry_appends(tmp_path, transport):
    path = write_catalog(tmp_path, [CATALOG_RECORDS[0]])
    fresh = dict(CATALOG_RECORDS[1], context_id="77", entry_id="7")
    db = _connect(path)
    with db:
        _write_records(db, [("dataset", None, fresh)])
    db.close()
    assert len(rows_of(path)) == 2


def test_the_uri_is_indexed_for_lookup(tmp_path):
    path = write_catalog(tmp_path)
    db = sqlite3.connect(path)
    try:
        names = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        db.close()
    assert "record_uri" in names
    # Nothing queries `harvested` locally -- the registry filters by it.
    assert "record_harvested" not in names


# -- the meta table ----------------------------------------------------------


def test_info_reports_when_it_was_built_and_last_caught_up(cat):
    info = cat.info()
    assert info["first_retrieved"] == "2026-09-30T00:00:00"
    assert info["last_refreshed"] == "2026-09-30T00:00:00"
    json.dumps(info)


def test_age_comes_from_the_database_not_the_files_mtime(tmp_path, transport):
    """An incremental refresh touches the file for a few hundred rows.

    If age came from the mtime, a refresh of 630 datasets would make the whole
    copy look newly built.
    """
    path = write_catalog(tmp_path)
    old = (dt.datetime.now() - dt.timedelta(days=30)).replace(microsecond=0)
    db = _connect(path)
    with db:
        _meta_set(db, last_refreshed=old.isoformat())
    db.close()

    cat = Catalog(path, refresh="never", transport=transport)
    assert cat.age_days == 30
    assert cat.stale is True
    assert cat.last_refreshed == old.isoformat()


def test_a_database_from_another_schema_is_refused(tmp_path, transport):
    path = write_catalog(tmp_path)
    db = _connect(path)
    with db:
        _meta_set(db, schema="99")
    db.close()
    with pytest.raises(ParseError) as info:
        Catalog(path, refresh="never", transport=transport)
    assert "schema" in str(info.value)
    assert SCHEMA_VERSION in str(info.value)


def test_an_empty_database_is_not_a_catalogue(tmp_path, transport):
    path = str(tmp_path / "empty.sqlite")
    db = _connect(path)
    db.close()
    with pytest.raises(ParseError):
        Catalog(path, refresh="never", transport=transport)


# -- read_catalog ------------------------------------------------------------


def test_read_catalog_gives_every_record(tmp_path):
    path = write_catalog(tmp_path)
    records = read_catalog(path)
    assert len(records) == len(CATALOG_RECORDS) + 2
    assert {r["type"] for r in records} == {"dataset", "data_service"}


# -- the timestamp a refresh asks from --------------------------------------


def test_the_refresh_bound_is_the_registrys_stamp_not_the_publishers():
    """A publisher can leave `modified` empty or set it to 2100."""
    from dataportalen.client import _solr_stamp

    assert _solr_stamp("2026-09-30") == "2026-09-30T00:00:00Z"
    assert _solr_stamp("2026-09-30T12:00:00") == "2026-09-30T12:00:00Z"
    assert _solr_stamp("2026-09-30T12:00:00.123456") == "2026-09-30T12:00:00Z"
    assert _solr_stamp("2026-09-30T12:00:00+02:00") == "2026-09-30T12:00:00Z"
    assert _solr_stamp(dt.datetime(2026, 9, 30, 12)) == "2026-09-30T12:00:00Z"


def test_an_unusable_refresh_bound_is_refused():
    from dataportalen import QueryError
    from dataportalen.client import _solr_stamp

    for value in (None, "", "   "):
        with pytest.raises(QueryError):
            _solr_stamp(value)


def test_a_refresh_asks_only_for_what_changed(tmp_path, transport, caplog):
    """The query carries the bound, so the registry does the narrowing."""
    import logging

    from dataportalen.client import _Registry, download_catalog

    path = write_catalog(tmp_path)
    for _ in range(8):
        transport.push({"results": 0, "offset": 0, "limit": 100,
                        "resource": {"children": []}, "facetFields": []})
    with caplog.at_level(logging.INFO, logger="dataportalen"):
        download_catalog(path, client=_Registry(transport=transport),
                         progress=None, since="2026-09-29T00:00:00")
    assert "refreshing" in caplog.text
    asked = " ".join(transport.requests)
    assert "modified" in asked, "the bound has to reach the registry"
    assert "2026-09-29T00%3A00%3A00Z" in asked or "2026-09-29" in asked


def test_a_refresh_keeps_the_rows_it_did_not_ask_about(tmp_path, transport):
    path = write_catalog(tmp_path)
    before = len(rows_of(path))
    from dataportalen.client import _Registry, download_catalog

    for _ in range(8):
        transport.push({"results": 0, "offset": 0, "limit": 100,
                        "resource": {"children": []}, "facetFields": []})
    download_catalog(path, client=_Registry(transport=transport), progress=None,
                     since="2026-09-29T00:00:00")
    assert len(rows_of(path)) == before, "a refresh must not drop untouched rows"
    assert _meta_get(_connect(path), "first_retrieved") == "2026-09-30T00:00:00"
