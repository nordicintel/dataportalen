"""The client works from the local catalogue unless told otherwise."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os

import pytest
from tests.test_local_catalog import RECORDS

from dataportalen import Dataportal, default_catalog_path


@pytest.fixture
def catalog_file(tmp_path):
    path = tmp_path / "catalog.jsonl"
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in RECORDS) + "\n",
        encoding="utf-8")
    return str(path)


@pytest.fixture
def dp(transport, catalog_file):
    """Local-first, with a catalogue on disk and a transport that would fail."""
    with Dataportal(transport=transport, catalog_path=catalog_file,
                    max_retries=0) as client:
        yield client


def test_searches_are_served_from_the_file(dp, transport):
    page = dp.datasets(theme="transport")
    assert [r["uri"] for r in page] == ["https://example.org/roads"]
    assert page.total == 1
    assert transport.requests == []          # nothing went over the wire


def test_results_are_dicts_with_a_total(dp):
    page = dp.datasets()
    assert page.total == 2
    assert all(isinstance(record, dict) for record in page)
    assert page.has_more is False
    assert dp.datasets(limit=1).has_more is True


def test_one_dataset_by_uri_comes_from_the_file(dp, transport):
    found = dp.dataset(uri="https://example.org/budget")
    assert found["title"] == "Kommunalt bidrag"
    assert transport.requests == []


def test_a_dataset_the_file_lacks_falls_back_to_the_registry(dp, transport):
    """A URI the copy does not have is looked up live rather than called absent."""
    transport.push({"results": 0, "offset": 0, "limit": 1,
                    "resource": {"children": []}, "facetFields": []})
    assert dp.dataset(uri="https://example.org/not-in-the-file") is None
    assert len(transport.requests) == 1


def test_values_and_counts_come_from_the_file(dp, transport):
    assert dp.values("theme") == [("economy_and_finance", 1), ("transport", 1)]
    assert dp.count_datasets(theme="transport") == 1
    assert transport.requests == []


def test_a_raw_query_or_a_facet_goes_to_the_registry(dp, transport, search_response):
    transport.push(search_response)
    dp.datasets(query="title.en:*")
    assert len(transport.requests) == 1

    transport.push(search_response)
    dp.datasets(facet_fields=["rdfType"])
    assert len(transport.requests) == 2


def test_other_entity_searches_go_to_the_registry(dp, transport, search_response):
    """The file holds datasets; catalogues and agents are not in it."""
    for method in ("distributions", "data_services", "catalogs", "agents"):
        transport.push(search_response)
        getattr(dp, method)(limit=1)
    assert len(transport.requests) == 4


def test_local_false_searches_the_registry(transport, catalog_file, search_response):
    transport.push(search_response)
    with Dataportal(transport=transport, catalog_path=catalog_file,
                    local=False) as client:
        page = client.datasets(theme="transport")
    assert transport.requests, "local=False must not read the file"
    assert all(isinstance(record, dict) for record in page)


def test_the_file_is_only_read_once(dp, transport):
    first = dp.catalog
    assert dp.datasets() is not None
    assert dp.catalog is first


def test_a_stale_copy_warns_and_is_not_refreshed(tmp_path, transport, caplog,
                                                 catalog_file):
    old = dt.datetime.now() - dt.timedelta(days=30)
    os.utime(catalog_file, (old.timestamp(), old.timestamp()))
    with caplog.at_level(logging.WARNING, logger="dataportalen"):
        with Dataportal(transport=transport, catalog_path=catalog_file) as client:
            assert client.datasets().total == 2
    assert "30 days old" in caplog.text
    assert client.catalog.stale is True
    assert transport.requests == [], "a stale copy must not trigger a download"


def test_the_default_path_is_a_cache_directory():
    path = default_catalog_path()
    assert path.endswith(os.path.join("dataportalen", "catalog.jsonl"))
    assert os.path.isabs(path)
    # Never the working directory: it must not land in someone's repository.
    assert os.path.dirname(path) != os.getcwd()
