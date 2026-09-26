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
    assert json.loads(encoded)["title"] == dataset.titles


def test_to_json_returns_a_string(dataset):
    assert json.loads(dataset.to_json())["uri"] == dataset.resource_uri


def test_dataset_dict_carries_the_core_fields(dataset):
    out = dataset.to_dict()
    assert out["uri"] == dataset.resource_uri
    assert out["context_id"] == "547"
    assert out["entry_id"] == "28672"
    assert out["title"]
    assert isinstance(out["title"], dict)
    assert out["description"]
    assert out["keywords"]
    assert out["landing_page"]


def test_controlled_values_are_short_names_not_uris(dataset):
    """No URIs and no {uri,label} objects -- just the short name."""
    out = dataset.to_dict()
    for theme in out["themes"]:
        assert isinstance(theme, str)
        assert "://" not in theme
        assert theme == theme.lower()
    for key in ("license", "access_rights", "accrual_periodicity"):
        if out[key] is not None:
            assert isinstance(out[key], str), key
            assert "://" not in out[key], key
    for language in out["languages"]:
        assert isinstance(language, str) and "://" not in language


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
    # distribution_uris is gone: the nested objects carry the URIs.
    assert "distribution_uris" not in out
    assert len(out["distributions"]) == len(dataset.distribution_uris)
    first = out["distributions"][0]
    assert first["download_url"] or first["access_url"]
    if first["format"]:
        assert isinstance(first["format"], str)
        assert "://" not in first["format"]


def test_distributions_can_be_left_out(dataset):
    out = dataset.to_dict(distributions=False)
    assert "distributions" not in out
    # Still reachable from the model, just not in the dict.
    assert dataset.distribution_uris


def test_search_hit_has_no_nested_distributions_only_uris(search_hit):
    out = search_hit.as_(Dataset).to_dict()
    assert out["distributions"] == []
    assert isinstance(search_hit.as_(Dataset).distribution_uris, list)


def test_contact_points_are_flat_dicts(dataset):
    contacts = dataset.to_dict()["contact_points"]
    assert contacts
    assert set(contacts[0]) == {"uri", "name", "email"}
    assert not (contacts[0]["email"] or "").startswith("mailto:")


def test_distribution_dict_shape(dataset):
    distribution = dataset.distributions()[0]
    assert isinstance(distribution, Distribution)
    out = distribution.to_dict()
    json.dumps(out)
    for key in ("uri", "title", "access_url", "download_url", "format"):
        assert key in out
    # Near-always-empty fields are not carried in the dict.
    for key in ("byte_size", "checksum", "media_type", "rights"):
        assert key not in out
    # access_url is the complete list, not a scalar plus a plural.
    assert isinstance(out["access_url"], list)
    assert "access_urls" not in out


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
    assert out["title"] == {"und": "T"}
    assert out["uri"] == "http://x/1"


def test_one_key_per_concept(dataset):
    """No scalar-plus-plural pairs: every concept has exactly one key."""
    out = dataset.to_dict()
    for gone in ("titles", "descriptions", "keywords_by_language",
                 "distribution_uris"):
        assert gone not in out, gone
    for langmap in ("title", "description", "keywords"):
        assert isinstance(out[langmap], dict), langmap
    for dist in out["distributions"]:
        for gone in ("titles", "access_urls", "download_urls"):
            assert gone not in dist, gone
    assert isinstance(out["publisher"]["name"], dict)
    assert "names" not in out["publisher"]


def test_near_empty_fields_are_dropped(dataset):
    """Fields empty for ~97%+ of the registry do not bloat every record."""
    out = dataset.to_dict()
    for gone in ("version", "provenance", "subjects", "hvd_categories",
                 "source_uris", "in_series_uris", "is_part_of_uris",
                 "temporal_resolution", "spatial_resolution_in_meters"):
        assert gone not in out, gone
    # The ones worth keeping are still there.
    for kept in ("applicable_legislation", "documentation", "conforms_to"):
        assert kept in out, kept


def test_the_dropped_fields_are_still_on_the_model(dataset):
    """Dropping them from the dict must not lose the data."""
    assert dataset.version is None or isinstance(dataset.version, str)
    assert isinstance(dataset.provenance, list)
    assert isinstance(dataset.hvd_categories, list)
    assert isinstance(dataset.distribution_uris, list)


def test_short_names_ignore_the_language_preference(dataset):
    """Controlled values are one fixed name; only authored text is localized."""
    swedish_first = dataset.with_languages(["sv", "en"]).to_dict()
    assert swedish_first["access_rights"] == "public"
    assert swedish_first["accrual_periodicity"] == "annual"
    # Publisher-authored text still follows the preference.
    assert "sv" in swedish_first["title"]
