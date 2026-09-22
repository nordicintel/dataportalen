"""The JSON/dict output layer.

The point of the package is that callers never have to touch RDF, so these
tests hold the output to that standard: everything `json.dumps`-able, no
bare vocabulary URIs without a place for their label, dates as strings.
"""

from __future__ import annotations

import json

import pytest
from conftest import load_fixture

from dataportalen import Dataset, Distribution
from dataportalen.models import wrap_entry


@pytest.fixture
def dataset():
    return wrap_entry(
        {
            "metadata": load_fixture("dataset_recursive.json"),
            "info": load_fixture("dataset_entry.json"),
            "contextId": "547",
            "entryId": "28672",
            "rights": ["readmetadata", "readresource"],
        },
        default=Dataset,
    )


@pytest.fixture
def search_hit():
    return wrap_entry(load_fixture("search_datasets.json")["resource"]["children"][0])


def test_dataset_dict_is_json_serializable(dataset):
    encoded = json.dumps(dataset.to_dict(), ensure_ascii=False)
    assert json.loads(encoded)["title"] == dataset.title


def test_to_json_returns_a_string(dataset):
    assert json.loads(dataset.to_json())["uri"] == dataset.resource_uri


def test_dataset_dict_carries_the_core_fields(dataset):
    out = dataset.to_dict()
    assert out["uri"] == dataset.resource_uri
    assert out["context_id"] == "547"
    assert out["entry_id"] == "28672"
    assert out["title"]
    assert out["description"]
    assert out["keywords"]
    assert out["landing_page"]


def test_vocabulary_fields_are_uri_label_pairs(dataset):
    out = dataset.to_dict()
    for theme in out["themes"]:
        assert set(theme) == {"uri", "label"}
        assert theme["uri"].startswith("http")
    for key in ("license", "access_rights", "accrual_periodicity"):
        if out[key] is not None:
            assert set(out[key]) == {"uri", "label"}


def test_dates_are_iso_strings_not_objects(dataset):
    out = dataset.to_dict()
    for key in ("issued", "modified"):
        assert out[key] is None or isinstance(out[key], str)
    if out["temporal"]:
        for key in ("start", "end"):
            assert out["temporal"][key] is None or isinstance(out["temporal"][key], str)


def test_publisher_is_resolved_from_the_graph_when_present(dataset):
    publisher = dataset.to_dict()["publisher"]
    assert publisher["uri"].startswith("http")
    # The recursive fixture describes the agent, so the name comes for free.
    assert publisher["name"]


def test_publisher_on_a_search_hit_is_uri_only(search_hit):
    publisher = search_hit.as_(Dataset).to_dict()["publisher"]
    assert publisher is None or publisher["uri"].startswith("http")
    if publisher is not None and publisher.get("name") is None:
        assert set(publisher) == {"uri", "name"}


def test_distributions_are_nested_after_a_recursive_fetch(dataset):
    out = dataset.to_dict()
    assert out["distributions"]
    assert len(out["distributions"]) == len(out["distribution_uris"])
    first = out["distributions"][0]
    assert first["download_url"] or first["access_url"]
    assert set(first["format"]) == {"uri", "label"} if first["format"] else True


def test_distributions_can_be_left_out(dataset):
    out = dataset.to_dict(distributions=False)
    assert "distributions" not in out
    assert out["distribution_uris"]


def test_search_hit_has_no_nested_distributions_only_uris(search_hit):
    out = search_hit.as_(Dataset).to_dict()
    assert out["distributions"] == []
    assert isinstance(out["distribution_uris"], list)


def test_contact_points_are_flat_dicts(dataset):
    contacts = dataset.to_dict()["contact_points"]
    assert contacts
    assert set(contacts[0]) == {"uri", "name", "email", "telephone", "url"}
    assert not (contacts[0]["email"] or "").startswith("mailto:")


def test_distribution_dict_shape(dataset):
    distribution = dataset.distributions()[0]
    assert isinstance(distribution, Distribution)
    out = distribution.to_dict()
    json.dumps(out)
    for key in ("uri", "title", "access_url", "download_url", "byte_size", "checksum"):
        assert key in out


def test_raw_json_still_exposes_the_registry_payload(search_hit):
    raw = search_hit.raw_json()
    assert "metadata" in raw and "info" in raw


def test_to_rdf_returns_the_graph(dataset):
    graph = dataset.to_rdf()
    assert isinstance(graph, dict)
    assert dataset.resource_uri in graph


def test_to_rdf_dict_uses_curie_keys(dataset):
    out = dataset.to_rdf_dict()
    assert any(key.startswith("dcterms:") or key.startswith("dcat:") for key in out)


def test_search_page_serializes_whole(client, transport, search_response):
    transport.push(search_response)
    page = client.search()
    out = page.to_dict()
    json.dumps(out, ensure_ascii=False)
    assert out["total"] == search_response["results"]
    assert out["count"] == len(page)
    assert len(out["results"]) == len(page)
    assert isinstance(out["facets"], dict)
    assert json.loads(page.to_json())["total"] == out["total"]


def test_entry_base_dict_works_for_untyped_entries():
    entry = wrap_entry({"metadata": {"http://x/1": {
        "http://purl.org/dc/terms/title": [{"type": "literal", "value": "T"}],
    }}})
    out = entry.to_dict()
    json.dumps(out)
    assert out["title"] == "T"
    assert out["uri"] == "http://x/1"
