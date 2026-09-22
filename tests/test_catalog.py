"""The whole-catalogue JSONL export."""

from __future__ import annotations

import gzip
import json

import pytest
from conftest import load_fixture

from dataportalen import Dataportal, download_catalog
from dataportalen.catalog import CatalogSummary


def _page(children, total, offset=0, limit=100):
    return {
        "results": total,
        "offset": offset,
        "limit": limit,
        "resource": {"children": children},
        "facetFields": [],
    }


@pytest.fixture
def dataset_children():
    return load_fixture("search_datasets.json")["resource"]["children"]


@pytest.fixture
def wired(transport, dataset_children):
    """A transport that answers the whole limited-export conversation.

    The order matters and mirrors `download_catalog`: count datasets, fetch
    the dataset page, then resolve distributions / agents / contacts.
    """
    count = len(dataset_children)
    transport.push(_page([], count))                  # count(datasets)
    transport.push(_page(dataset_children, count))    # the dataset page
    for _ in range(6):                                # targeted lookups
        transport.push(_page([], 0))
    return transport


def test_writes_one_json_object_per_line(wired, tmp_path, dataset_children):
    out = tmp_path / "catalog.jsonl"
    summary = download_catalog(
        str(out), limit=len(dataset_children), client=Dataportal(transport=wired)
    )
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(dataset_children)
    for line in lines:
        record = json.loads(line)
        assert "uri" in record and "title" in record
    assert isinstance(summary, CatalogSummary)
    assert summary.datasets == len(dataset_children)


def test_summary_reports_what_it_did(wired, tmp_path, dataset_children):
    out = tmp_path / "catalog.jsonl"
    summary = download_catalog(
        str(out), limit=len(dataset_children), client=Dataportal(transport=wired)
    )
    payload = summary.to_dict()
    assert payload["datasets"] == len(dataset_children)
    assert payload["bytes_written"] == out.stat().st_size
    assert payload["requests"] > 0
    assert payload["elapsed"] >= 0
    assert "CatalogSummary" in repr(summary)


def test_gz_suffix_gzips_the_output(wired, tmp_path, dataset_children):
    out = tmp_path / "catalog.jsonl.gz"
    download_catalog(
        str(out), limit=len(dataset_children), client=Dataportal(transport=wired)
    )
    with gzip.open(out, "rt", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    assert len(lines) == len(dataset_children)
    assert json.loads(lines[0])["uri"]
    # Really gzip, not a text file with a misleading name.
    assert out.read_bytes()[:2] == b"\x1f\x8b"


def test_limit_avoids_crawling_every_distribution(transport, dataset_children, tmp_path):
    # 35k distributions must not be fetched to serve one dataset. The
    # targeted path issues a handful of lookups, not hundreds of pages.
    transport.push(_page([], 23548))
    transport.push(_page(dataset_children[:1], 23548))
    for _ in range(6):
        transport.push(_page([], 0))
    download_catalog(
        str(tmp_path / "c.jsonl"), limit=1, client=Dataportal(transport=transport)
    )
    assert len(transport.requests) < 10


def test_distributions_are_nested_from_the_index(transport, tmp_path):
    dataset = {
        "contextId": "1", "entryId": "1", "rights": ["readmetadata"],
        "info": {"https://admin.dataportal.se/store/1/entry/1": {
            "http://entrystore.org/terms/resource": [
                {"type": "uri", "value": "http://example.org/ds"}]}},
        "metadata": {"http://example.org/ds": {
            "http://purl.org/dc/terms/title": [{"type": "literal", "value": "DS"}],
            "http://www.w3.org/ns/dcat#distribution": [
                {"type": "uri", "value": "http://example.org/dist"}],
            "http://www.w3.org/1999/02/22-rdf-syntax-ns#type": [
                {"type": "uri", "value": "http://www.w3.org/ns/dcat#Dataset"}]}},
    }
    distribution = {
        "contextId": "1", "entryId": "2", "rights": ["readmetadata"],
        "info": {"https://admin.dataportal.se/store/1/entry/2": {
            "http://entrystore.org/terms/resource": [
                {"type": "uri", "value": "http://example.org/dist"}]}},
        "metadata": {"http://example.org/dist": {
            "http://purl.org/dc/terms/title": [{"type": "literal", "value": "CSV"}],
            "http://www.w3.org/ns/dcat#downloadURL": [
                {"type": "uri", "value": "http://example.org/f.csv"}],
            "http://www.w3.org/1999/02/22-rdf-syntax-ns#type": [
                {"type": "uri", "value": "http://www.w3.org/ns/dcat#Distribution"}]}},
    }
    transport.push(_page([], 1))
    transport.push(_page([dataset], 1))
    transport.push(_page([distribution], 1))   # distribution lookup
    transport.push(_page([], 0))               # agents
    transport.push(_page([], 0))               # contacts

    out = tmp_path / "c.jsonl"
    download_catalog(str(out), limit=1, client=Dataportal(transport=transport))
    record = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert record["distribution_uris"] == ["http://example.org/dist"]
    assert len(record["distributions"]) == 1
    assert record["distributions"][0]["title"] == "CSV"
    assert record["distributions"][0]["download_url"] == "http://example.org/f.csv"


def test_unresolvable_references_warn_rather_than_vanish(transport, tmp_path):
    dataset = {
        "contextId": "1", "entryId": "1", "rights": ["readmetadata"],
        "info": {"https://admin.dataportal.se/store/1/entry/1": {
            "http://entrystore.org/terms/resource": [
                {"type": "uri", "value": "http://example.org/ds"}]}},
        "metadata": {"http://example.org/ds": {
            "http://www.w3.org/ns/dcat#distribution": [
                {"type": "uri", "value": "http://example.org/gone"}],
            "http://www.w3.org/1999/02/22-rdf-syntax-ns#type": [
                {"type": "uri", "value": "http://www.w3.org/ns/dcat#Dataset"}]}},
    }
    transport.push(_page([], 1))
    transport.push(_page([dataset], 1))
    for _ in range(6):
        transport.push(_page([], 0))

    out = tmp_path / "c.jsonl"
    with pytest.warns(UserWarning, match="not found in the bulk crawl"):
        download_catalog(str(out), limit=1, client=Dataportal(transport=transport))
    record = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    # The reference is still reported, just not resolved.
    assert record["distribution_uris"] == ["http://example.org/gone"]
    assert record["distributions"] == []


def test_progress_is_called_for_every_dataset(wired, tmp_path, dataset_children):
    seen = []
    download_catalog(
        str(tmp_path / "c.jsonl"),
        limit=len(dataset_children),
        client=Dataportal(transport=wired),
        progress=lambda done, total: seen.append(done),
    )
    assert seen == list(range(1, len(dataset_children) + 1))


def test_client_method_delegates(wired, tmp_path, dataset_children):
    with Dataportal(transport=wired) as client:
        summary = client.download_catalog(
            str(tmp_path / "c.jsonl"), limit=len(dataset_children)
        )
    assert summary.datasets == len(dataset_children)


def test_a_supplied_client_is_left_open(wired, tmp_path, dataset_children):
    client = Dataportal(transport=wired)
    download_catalog(
        str(tmp_path / "c.jsonl"), limit=len(dataset_children), client=client
    )
    assert not wired.closed


def test_language_keys_are_json_safe(wired, tmp_path, dataset_children):
    # An untagged literal must not key the object under `null`.
    out = tmp_path / "c.jsonl"
    download_catalog(
        str(out), limit=len(dataset_children), client=Dataportal(transport=wired)
    )
    for line in out.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        assert "null" not in record["titles"]
        assert all(isinstance(k, str) and k for k in record["titles"])
