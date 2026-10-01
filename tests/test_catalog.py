"""The whole-catalogue JSONL export."""

from __future__ import annotations

import pytest

from conftest import load_fixture
from dataportalen.client import CatalogSummary, _Registry, download_catalog, read_catalog


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
        str(out), limit=len(dataset_children), client=_Registry(transport=wired)
    )
    records = read_catalog(str(out))
    assert len(records) == len(dataset_children)
    for record in records:
        assert "uri" in record and "title" in record
    assert isinstance(summary, CatalogSummary)
    assert summary.datasets == len(dataset_children)


def test_summary_reports_what_it_did(wired, tmp_path, dataset_children):
    out = tmp_path / "catalog.jsonl"
    summary = download_catalog(
        str(out), limit=len(dataset_children), client=_Registry(transport=wired)
    )
    payload = summary.to_dict()
    assert payload["datasets"] == len(dataset_children)
    assert payload["bytes_written"] == out.stat().st_size
    assert payload["requests"] > 0
    assert payload["elapsed"] >= 0
    assert "CatalogSummary" in repr(summary)


def test_limit_avoids_crawling_every_distribution(transport, dataset_children, tmp_path):
    # 35k distributions must not be fetched to serve one dataset. The
    # targeted path issues a handful of lookups, not hundreds of pages.
    transport.push(_page([], 23548))
    transport.push(_page(dataset_children[:1], 23548))
    for _ in range(6):
        transport.push(_page([], 0))
    download_catalog(
        str(tmp_path / "c.jsonl"), limit=1, client=_Registry(transport=transport)
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
    download_catalog(str(out), limit=1, client=_Registry(transport=transport))
    record = read_catalog(str(out))[0]
    assert len(record["distributions"]) == 1
    assert record["distributions"][0]["uri"] == "http://example.org/dist"
    assert record["distributions"][0]["title"] == {"sv": "CSV"}
    assert record["distributions"][0]["download_url"] == ["http://example.org/f.csv"]


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
        download_catalog(str(out), limit=1, client=_Registry(transport=transport))
    record = read_catalog(str(out))[0]
    # Unresolved references drop out of the record but are warned about, so
    # a line never silently claims a dataset has no distributions.
    assert record["distributions"] == []


def test_a_supplied_client_is_left_open(wired, tmp_path, dataset_children):
    client = _Registry(transport=wired)
    download_catalog(
        str(tmp_path / "c.jsonl"), limit=len(dataset_children), client=client
    )
    assert not wired.closed


def test_language_keys_are_json_safe(wired, tmp_path, dataset_children):
    # An untagged literal must not key the object under `null`.
    out = tmp_path / "c.sqlite"
    download_catalog(
        str(out), limit=len(dataset_children), client=_Registry(transport=wired)
    )
    for record in read_catalog(str(out)):
        assert "null" not in record["title"]
        assert all(isinstance(k, str) and k for k in record["title"])


def test_the_output_directory_is_created(tmp_path, transport, search_response):
    """The default location is a cache directory that may not exist yet."""
    transport.push(search_response)          # count
    transport.push(search_response)          # datasets
    transport.push(_page([], 0))             # distributions
    transport.push(_page([], 0))             # agents
    transport.push(_page([], 0))             # contacts
    out = tmp_path / "does" / "not" / "exist" / "catalog.jsonl"
    download_catalog(str(out), limit=1,
                     client=_Registry(transport=transport))
    assert out.exists()


# -- the file holds data services too ----------------------------------------


def _entry(context, entry, uri, metadata):
    """One search hit: the envelope that points at `uri`, plus its graph."""
    return {
        "contextId": context, "entryId": entry, "rights": ["readmetadata"],
        "info": {"https://admin.dataportal.se/store/%s/entry/%s" % (context, entry): {
            "http://entrystore.org/terms/resource": [{"type": "uri", "value": uri}]}},
        "metadata": {uri: metadata},
    }


RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
DCT = "http://purl.org/dc/terms/"
DCAT_NS = "http://www.w3.org/ns/dcat#"

SERVICE = _entry("14", "9283", "http://example.org/api", {
    DCT + "title": [{"type": "literal", "lang": "sv", "value": "Utlysningar"},
                    {"type": "literal", "lang": "en", "value": "Calls"}],
    DCT + "type": [{"type": "uri",
                    "value": "http://www.wikidata.org/entity/Q749568"}],
    DCT + "publisher": [{"type": "uri", "value": "http://example.org/org"}],
    DCAT_NS + "endpointURL": [{"type": "uri", "value": "https://api.example.org/v1"}],
    DCAT_NS + "keyword": [{"type": "literal", "lang": "sv", "value": "Innovation"}],
    RDF_TYPE: [{"type": "uri", "value": DCAT_NS + "DataService"}],
})

DATASET = _entry("1", "1", "http://example.org/ds", {
    DCT + "title": [{"type": "literal", "lang": "sv", "value": "DS"}],
    DCT + "publisher": [{"type": "uri", "value": "http://example.org/org"}],
    RDF_TYPE: [{"type": "uri", "value": DCAT_NS + "Dataset"}],
})

ORG = _entry("2", "5", "http://example.org/org", {
    "http://xmlns.com/foaf/0.1/name": [
        {"type": "literal", "lang": "sv", "value": "Trafikverket"}],
    DCT + "type": [{"type": "uri", "value": "http://purl.org/adms/publishertype/"
                                            "NationalAuthority"}],
    RDF_TYPE: [{"type": "uri", "value": "http://xmlns.com/foaf/0.1/Agent"}],
})


@pytest.fixture
def full_export(transport):
    """The whole conversation a full export has, in the order it happens.

    The four counts come first, then each index crawl, and the dataset pages
    only when the write loop starts pulling them -- the crawl is a generator,
    so a dataset page is fetched as it is written.
    """
    transport.push(_page([], 1))                 # count(datasets) -> 1
    transport.push(_page([], 0))                 # count(distributions) -> 0
    transport.push(_page([], 1))                 # count(agents) -> 1
    transport.push(_page([], 0))                 # count(contact points) -> 0
    transport.push(_page([ORG], 1))              # the agent page
    transport.push(_page([], 0))                 # count(link check reports) -> 0
    transport.push(_page([DATASET], 1))          # the dataset page
    transport.push(_page([], 1))                 # count(data services) -> 1
    transport.push(_page([SERVICE], 1))          # the data service page
    return transport


def test_a_full_export_writes_both_types(full_export, tmp_path):
    out = tmp_path / "c.jsonl"
    summary = download_catalog(str(out), client=_Registry(transport=full_export))
    records = read_catalog(str(out))
    assert [r["type"] for r in records] == ["dataset", "data_service"]
    assert summary.datasets == 1
    assert summary.data_services == 1
    assert summary.to_dict()["data_services"] == 1
    assert "data services" in repr(summary)


def test_a_data_service_record_carries_what_it_has(full_export, tmp_path):
    out = tmp_path / "c.jsonl"
    download_catalog(str(out), client=_Registry(transport=full_export))
    service = read_catalog(str(out))[-1]
    assert service["title"] == {"sv": "Utlysningar", "en": "Calls"}
    assert service["service_type"] == "rest"
    assert service["endpoint_url"] == "https://api.example.org/v1"
    assert service["keywords"] == {"sv": ["Innovation"]}
    # A data service has no files, so the key is absent rather than empty.
    assert "distributions" not in service


def test_a_publisher_is_resolved_not_left_as_a_uri(full_export, tmp_path):
    """A bare publisher URI would be a dead end: nothing resolves one later."""
    out = tmp_path / "c.jsonl"
    download_catalog(str(out), client=_Registry(transport=full_export))
    dataset = read_catalog(str(out))[0]
    assert dataset["publisher"] == {
        "uri": "http://example.org/org",
        "context_id": "2", "entry_id": "5",
        "name": {"sv": "Trafikverket"},
        "type": "national_authority",
        "identifiers": [], "email": None, "homepage": None,
    }


def test_a_record_never_carries_creators(full_export, tmp_path):
    """`dcterms:creator` is read off the graph and thrown away.

    7,104 datasets named one and 6,174 of those named their own publisher
    again -- the same agent URI on 5,292 of them. The field is not a second
    publisher, so it is not in the record at all.
    """
    out = tmp_path / "c.jsonl"
    download_catalog(str(out), client=_Registry(transport=full_export))
    for record in read_catalog(str(out)):
        assert "creators" not in record
        assert "creator_uris" not in record


def test_a_limited_export_skips_data_services(wired, tmp_path, dataset_children):
    """They are the small half; a smoke test should stay small."""
    out = tmp_path / "c.jsonl"
    summary = download_catalog(str(out), limit=len(dataset_children),
                               client=_Registry(transport=wired))
    assert summary.data_services == 0
    types = {r["type"] for r in read_catalog(str(out))}
    assert types == {"dataset"}


def test_publishers_typed_as_organizations_are_indexed_too(transport, tmp_path):
    """A `foaf:Organization` publisher is still a publisher.

    Most agents are `foaf:Agent`, but 69 in the registry are
    `foaf:Organization` and 1,693 are `prov:Agent`. Crawling only `foaf:Agent`
    leaves those as bare URIs with no name.
    """
    org = _entry("2", "6", "http://example.org/org2", {
        "http://xmlns.com/foaf/0.1/name": [
            {"type": "literal", "lang": "sv", "value": "Folkhälsomyndigheten"}],
        RDF_TYPE: [{"type": "uri", "value": "http://xmlns.com/foaf/0.1/Organization"}],
    })
    dataset = _entry("1", "1", "http://example.org/ds", {
        DCT + "publisher": [{"type": "uri", "value": "http://example.org/org2"}],
        RDF_TYPE: [{"type": "uri", "value": DCAT_NS + "Dataset"}],
    })
    transport.push(_page([], 1))            # count(datasets)
    transport.push(_page([], 0))            # count(distributions)
    transport.push(_page([], 1))            # count(agents), all three types
    transport.push(_page([], 0))            # count(contact points)
    transport.push(_page([org], 1))         # the agent page
    transport.push(_page([], 0))            # count(link check reports)
    transport.push(_page([dataset], 1))     # the dataset page
    transport.push(_page([], 0))            # count(data services)

    out = tmp_path / "c.jsonl"
    download_catalog(str(out), client=_Registry(transport=transport))
    record = read_catalog(str(out))[0]
    assert record["publisher"]["name"] == {"sv": "Folkhälsomyndigheten"}


def test_the_agent_query_covers_all_three_types(transport):
    """The count is asked of one query, so a type left out is silent."""
    from dataportalen.client import _AGENT_TYPES

    assert _AGENT_TYPES == ("http://xmlns.com/foaf/0.1/Agent",
                            "http://xmlns.com/foaf/0.1/Organization",
                            "http://www.w3.org/ns/prov#Agent")


def test_an_unresolvable_publisher_keeps_its_uri_and_an_empty_name(transport,
                                                                   tmp_path):
    """A publisher the crawl never saw is still a dict, never a bare None."""
    dataset = _entry("1", "1", "http://example.org/ds", {
        DCT + "publisher": [{"type": "uri", "value": "http://example.org/gone"}],
        RDF_TYPE: [{"type": "uri", "value": DCAT_NS + "Dataset"}],
    })
    transport.push(_page([], 1))
    transport.push(_page([], 0))
    transport.push(_page([], 0))
    transport.push(_page([], 0))
    transport.push(_page([], 0))            # count(link check reports)
    transport.push(_page([dataset], 1))     # the dataset page
    transport.push(_page([], 0))            # count(data services)

    out = tmp_path / "c.jsonl"
    download_catalog(str(out), client=_Registry(transport=transport))
    record = read_catalog(str(out))[0]
    assert record["publisher"]["uri"] == "http://example.org/gone"
    assert record["publisher"]["name"] == {}
    assert record["publisher"]["type"] is None


# -- a partial download must not become the catalogue ------------------------


@pytest.mark.parametrize("name", ["partial.jsonl", "partial.jsonl.gz"])
def test_a_failed_download_leaves_no_file(transport, tmp_path, name):
    """A drop at minute six of seven must not leave an empty catalogue.

    It used to: the file was opened at the target path, so a failure left a
    0-byte file that `refresh="if_missing"` then read as a valid catalogue of
    no datasets -- forever, because a file was there.
    """

    transport.push(_page([], 5))          # count(datasets)
    for _ in range(3):
        transport.push(_page([], 0))      # the other three counts
    transport.push("boom", status=500)    # and then the dataset page dies

    out = tmp_path / name
    with pytest.raises(Exception):
        download_catalog(str(out), client=_Registry(transport=transport,
                                                    max_retries=0))
    assert not out.exists(), "a failed download must not leave the target"
    with pytest.raises(FileNotFoundError):
        read_catalog(str(out))


def test_a_failed_rebuild_leaves_the_old_catalogue_intact(transport, tmp_path):
    """The write is one transaction, which is what makes a rebuild safe.

    A full build clears `record` before writing it, so a crawl that dies
    halfway would otherwise leave an empty catalogue where a good one was.
    """
    import sqlite3

    from conftest import CATALOG_RECORDS, write_catalog

    path = write_catalog(tmp_path, [CATALOG_RECORDS[0], CATALOG_RECORDS[1]])
    transport.push(_page([], 5))          # count(datasets)
    for _ in range(3):
        transport.push(_page([], 0))      # the other three counts
    transport.push("boom", status=500)    # and then the crawl dies

    with pytest.raises(Exception):
        download_catalog(path, client=_Registry(transport=transport,
                                               max_retries=0))
    assert sqlite3.connect(path).execute(
        "SELECT count(*) FROM record").fetchone()[0] == 2


def test_a_full_build_replaces_the_rows_rather_than_merging(transport, tmp_path):
    """`refresh="always"` has to mean rebuilt, not upserted over.

    It did not: a withdrawn dataset kept its row, and so did the record shape
    it was written in -- which is exactly what a `SCHEMA_VERSION` bump tells
    people to use `refresh="always"` to be rid of.
    """
    import sqlite3

    from conftest import CATALOG_RECORDS, write_catalog

    withdrawn = dict(CATALOG_RECORDS[0], context_id="999", entry_id="9",
                     uri="https://example.org/withdrawn")
    path = write_catalog(tmp_path, [CATALOG_RECORDS[0], withdrawn])
    for _ in range(8):                    # a registry that now holds nothing
        transport.push(_page([], 0))
    download_catalog(path, client=_Registry(transport=transport))
    assert sqlite3.connect(path).execute(
        "SELECT count(*) FROM record").fetchone()[0] == 0


def test_a_refresh_merges_rather_than_replacing(transport, tmp_path):
    """The other half of the same rule: `since` must leave untouched rows be."""
    import sqlite3

    from conftest import CATALOG_RECORDS, write_catalog

    path = write_catalog(tmp_path, [CATALOG_RECORDS[0], CATALOG_RECORDS[1]])
    for _ in range(8):
        transport.push(_page([], 0))
    download_catalog(path, client=_Registry(transport=transport),
                     since="2026-09-29T00:00:00")
    assert sqlite3.connect(path).execute(
        "SELECT count(*) FROM record").fetchone()[0] == 2
