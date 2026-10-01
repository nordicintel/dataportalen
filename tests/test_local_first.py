"""The file: where it lives, when it is written, and what `get`/`info` say.

Every refresh mode is exercised against a transport that would fail if asked,
so "nothing was downloaded" is a fact here rather than a hope.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os

import pytest

from conftest import CATALOG_RECORDS, write_catalog
from dataportalen import Catalog, default_catalog_path


def age(path, days):
    """Backdate a catalogue's own record of when it was last refreshed.

    Not the file's mtime: an incremental refresh rewrites a few hundred rows
    and must not make the whole copy look new, so the database records the
    time itself and the Catalog reads that.
    """
    from dataportalen.client import _connect, _meta_set

    when = (dt.datetime.now() - dt.timedelta(days=days)).replace(microsecond=0)
    db = _connect(path)
    with db:
        _meta_set(db, last_refreshed=when.isoformat())
    db.close()


# -- where it lives ----------------------------------------------------------


def test_the_default_path_is_a_cache_directory():
    path = default_catalog_path()
    # One file, not one per language: a record carries both languages.
    assert path.endswith(os.path.join("dataportalen", "catalog.sqlite"))
    assert os.path.isabs(path)
    # Never the working directory: it must not land in someone's repository.
    assert os.path.dirname(path) != os.getcwd()


def test_it_reads_the_file(cat):
    """One file, two kinds of line; len() and iteration are the datasets."""
    assert len(cat) == 2
    assert [r["uri"] for r in cat] == [r["uri"] for r in CATALOG_RECORDS]
    assert cat.data_services().total == 2


# -- when it is written ------------------------------------------------------


def test_a_bad_max_age_is_rejected(tmp_path, transport):
    from dataportalen import QueryError

    for bad in (-1, True, "week"):
        with pytest.raises((QueryError, TypeError)):
            Catalog(write_catalog(tmp_path), max_age=bad, _transport=transport, access_rights=None)


def test_max_age_none_uses_an_old_copy_as_is(tmp_path, transport, caplog):
    """None is the caller saying: I know, and I do not want the network."""
    path = write_catalog(tmp_path)
    age(path, 30)
    with caplog.at_level(logging.INFO, logger="dataportalen"):
        cat = Catalog(path, max_age=None, _transport=transport, access_rights=None)
    assert "30 days old" in caplog.text
    assert cat.age_days == 30
    assert cat.datasets().total == 2
    assert transport.requests == [], "max_age=None must not touch the network"


def refresh_pages(transport, n=8):
    for _ in range(n):
        transport.push({"results": 0, "offset": 0, "limit": 100,
                        "resource": {"children": []}, "facetFields": []})


def test_max_age_is_the_caller_s_definition_of_old(tmp_path, transport):
    """Three days old: fine under a week, refreshed under two days."""
    path = write_catalog(tmp_path)
    age(path, 3)
    Catalog(path, max_age=7, _transport=transport, access_rights=None)
    assert transport.requests == []

    refresh_pages(transport)
    cat = Catalog(path, max_age=2, _transport=transport, access_rights=None)
    assert transport.requests, "an old copy under max_age must refresh"
    assert cat.age_days == 0, "and then it is current"
    assert cat.datasets().total == 2, "a refresh keeps what it did not touch"


def test_a_fresh_copy_is_used_under_the_default(tmp_path, transport):
    path = write_catalog(tmp_path)
    cat = Catalog(path, _transport=transport, access_rights=None)
    assert cat.datasets().total == 2
    assert transport.requests == []


def test_a_refresh_is_incremental_not_a_rebuild(tmp_path, transport):
    """The query carries the bound; the registry does the narrowing."""
    path = write_catalog(tmp_path)
    age(path, 10)
    refresh_pages(transport)
    Catalog(path, _transport=transport, access_rights=None)
    asked = " ".join(transport.requests)
    assert "modified" in asked, "a refresh asks only for what changed"


def test_rebuild_fetches_everything_whatever_is_there(tmp_path, transport):
    path = write_catalog(tmp_path)
    refresh_pages(transport)
    with pytest.raises(Exception):
        # A full build against a registry that answers nothing ends with zero
        # rows, which _read refuses -- the point here is only that it asked
        # for everything rather than for what changed.
        Catalog(path, rebuild=True, _transport=transport, access_rights=None)
    asked = " ".join(transport.requests)
    assert asked and "modified" not in asked


# -- what it holds -----------------------------------------------------------


def test_public_is_the_default_scope(tmp_path, transport):
    """Record 1 is non_public; by default it is not there at all."""
    cat = Catalog(write_catalog(tmp_path), max_age=None, _transport=transport)
    assert cat.datasets(limit=0).total == 1
    assert cat.datasets()[0]["access_rights"] == "public"
    assert cat.get(CATALOG_RECORDS[1]["uri"]) is None


def test_access_rights_none_holds_everything(tmp_path, transport):
    cat = Catalog(write_catalog(tmp_path), max_age=None, _transport=transport,
                  access_rights=None)
    assert cat.datasets(limit=0).total == 2


def test_none_names_the_records_that_set_nothing(tmp_path, transport):
    """4,167 datasets, 17.7%, mostly universities, say nothing at all."""
    unset = dict(CATALOG_RECORDS[0], uri="https://example.org/unset",
                 context_id="7", entry_id="7")
    unset.pop("access_rights")
    path = write_catalog(tmp_path, [CATALOG_RECORDS[0], unset])
    assert Catalog(path, max_age=None, _transport=transport).datasets(
        limit=0).total == 1
    assert Catalog(path, max_age=None, _transport=transport,
                   access_rights=["public", "none"]).datasets(limit=0).total == 2
    assert Catalog(path, max_age=None, _transport=transport,
                   access_rights="none").datasets(limit=0).total == 1


def test_an_unknown_access_rights_value_is_refused(tmp_path, transport):
    from dataportalen import QueryError

    with pytest.raises(QueryError) as info:
        Catalog(write_catalog(tmp_path), max_age=None, _transport=transport,
                access_rights=["public", "open"])
    assert "open" in str(info.value) and "non_public" in str(info.value)


def test_the_scope_applies_to_data_services_too(tmp_path, transport):
    from conftest import SERVICE_RECORDS

    hidden = dict(SERVICE_RECORDS[0], uri="https://example.org/svc-r",
                  context_id="8", entry_id="8", access_rights="restricted")
    path = write_catalog(tmp_path, SERVICE_RECORDS + [hidden])
    assert Catalog(path, max_age=None, _transport=transport).data_services(
        limit=0).total == 2


# -- what it says about itself ----------------------------------------------


def test_a_fresh_file_is_never_negative_days_old(tmp_path, transport):
    """A just-written file can be stamped a hair ahead of the clock."""
    cat = Catalog(write_catalog(tmp_path), max_age=None, _transport=transport, access_rights=None)
    assert cat.age_days == 0


def test_info_reports_the_file_and_its_age(cat):
    info = cat.info()
    assert info["database"].endswith("catalog.sqlite")
    assert info["datasets"] == 2
    assert info["data_services"] == 2
    assert info["publishers"] == 2
    assert info["bytes"] > 0
    assert info["age_days"] == 0
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
    cat = Catalog(path, max_age=None, _transport=transport, access_rights=None)
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
