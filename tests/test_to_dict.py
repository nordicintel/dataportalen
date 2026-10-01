"""The JSON/dict output layer.

The point of the package is that callers never have to touch RDF, so these
tests hold the output to that standard: everything `json.dumps`-able, no
bare vocabulary URIs without a place for their label, dates as strings.
"""

from __future__ import annotations

import json

import pytest

from conftest import load_fixture
from dataportalen.models import Dataset, Distribution, wrap_entry


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
    assert json.loads(encoded)["title"] == dataset.to_dict()["title"]


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
    for key in ("access_rights", "accrual_periodicity"):
        if out[key] is not None:
            assert isinstance(out[key], str), key
            assert "://" not in out[key], key
    for language in out["languages"]:
        assert isinstance(language, str) and "://" not in language
        assert len(language) in (2, 3) and language.islower(), language


def test_a_licence_is_a_dict_you_can_read_and_filter_on(dataset):
    """Nobody knows what cc_by_nc_sa_4_0 permits without the page."""
    out = dataset.to_dict()
    lic = out["license"]
    assert set(lic) == {"id", "label", "uri"}
    assert lic["id"] and "://" not in lic["id"]
    assert lic["uri"].startswith("http")
    assert isinstance(lic["label"], dict)
    for dist in out["distributions"]:
        if dist["license"] is not None:
            assert set(dist["license"]) == {"id", "label", "uri"}


def test_ids_are_on_the_record_and_nowhere_below_it(dataset):
    """context/entry id fetch a record; a nested object is not fetched."""
    out = dataset.to_dict()
    assert out["context_id"] and out["entry_id"]
    for nested in [out["publisher"]] + out["distributions"] + out["contact_points"]:
        assert "context_id" not in nested and "entry_id" not in nested


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
    # One field per URL: 99.6% of distributions have exactly one.
    assert out["access_url"] is None or isinstance(out["access_url"], str)
    assert out["download_url"] is None or isinstance(out["download_url"], str)
    assert "access_urls" not in out and "download_urls" not in out


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
    page = client._search()
    out = page.to_dict()
    json.dumps(out, ensure_ascii=False)
    assert out["total"] == search_response["results"]
    assert out["count"] == len(page)
    assert len(out["results"]) == len(page)
    assert json.loads(page.to_json())["total"] == out["total"]


def test_entry_base_dict_works_for_untyped_entries():
    entry = wrap_entry({"metadata": {"http://x/1": {
        "http://purl.org/dc/terms/title": [{"type": "literal", "value": "T"}],
    }}})
    out = entry.to_dict()
    json.dumps(out)
    # An untagged literal folds into Swedish -- see models._fold.
    assert out["title"] == {"sv": "T"}
    assert out["uri"] == "http://x/1"


def test_one_key_per_concept(dataset):
    """No scalar-plus-plural pairs: every concept has exactly one key."""
    out = dataset.to_dict()
    for gone in ("titles", "descriptions", "keywords_by_language",
                 "distribution_uris"):
        assert gone not in out, gone
    assert isinstance(out["title"], dict)
    assert isinstance(out["keywords"], dict)
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
    # These went the same way: ~97% empty, by the rule the docstring states.
    for gone in ("applicable_legislation", "documentation", "conforms_to"):
        assert gone not in out, gone


def test_the_dropped_fields_are_still_on_the_model(dataset):
    """Dropping them from the dict must not lose the data."""
    assert dataset.version is None or isinstance(dataset.version, str)
    assert isinstance(dataset.provenance, list)
    assert isinstance(dataset.hvd_categories, list)
    assert isinstance(dataset.distribution_uris, list)


def test_short_names_are_never_translated(dataset):
    """Controlled values are one fixed English short name, whatever the prose."""
    out = dataset.to_dict()
    assert out["access_rights"] == "public"
    assert out["accrual_periodicity"] == "annual"


def test_every_localized_field_is_a_two_language_map(dataset):
    """There is no language setting: a record carries what the publisher wrote."""
    out = dataset.to_dict()
    assert out["title"] == {"sv": dataset.titles["sv"]}
    assert isinstance(out["description"], dict)
    assert isinstance(out["keywords"], dict)
    assert isinstance(out["publisher"]["name"], dict)


def test_only_sv_and_en_are_ever_keys(dataset):
    out = dataset.to_dict()
    for field in ("title", "description", "keywords"):
        assert set(out[field]) <= {"sv", "en"}, field


def test_an_untranslated_field_has_no_en_key(dataset):
    """Absence says "not translated"; None would say "translated to nothing"."""
    out = dataset.to_dict()
    assert "sv" in out["title"]
    assert "en" not in out["title"]


def test_a_foreign_tag_folds_into_swedish():
    entry = wrap_entry({"metadata": {"http://x/1": {
        "http://purl.org/dc/terms/title": [
            {"type": "literal", "lang": "no", "value": "Norsk"},
        ],
        "http://purl.org/dc/terms/description": [
            {"type": "literal", "lang": "und", "value": "Otaggad"},
            {"type": "literal", "lang": "en", "value": "English"},
        ],
    }}})
    out = entry.to_dict()
    assert out["title"] == {"sv": "Norsk"}
    assert out["description"] == {"sv": "Otaggad", "en": "English"}


def test_a_regional_tag_folds_to_its_base_language():
    entry = wrap_entry({"metadata": {"http://x/1": {
        "http://purl.org/dc/terms/title": [
            {"type": "literal", "lang": "en-GB", "value": "Colour"},
        ],
    }}})
    assert entry.to_dict()["title"] == {"en": "Colour"}


# -- reading a language map --------------------------------------------------


def test_text_prefers_swedish_and_falls_back():
    from dataportalen import text

    assert text({"sv": "Vägtrafiknät", "en": "Road"}) == "Vägtrafiknät"
    assert text({"en": "Road"}) == "Road"
    assert text({"sv": "Vägtrafiknät", "en": "Road"}, "en") == "Road"
    assert text({"sv": "Vägtrafiknät"}, "en") == "Vägtrafiknät"


def test_text_is_safe_on_anything_a_record_holds():
    """Ten percent of datasets have no Swedish title; this must not raise."""
    from dataportalen import text

    assert text({}) is None
    assert text(None) is None
    assert text("already a string") == "already a string"


def test_text_on_a_real_record(dataset):
    from dataportalen import text

    assert text(dataset.to_dict()["title"]) == dataset.titles["sv"]


# -- a file's size, where the publisher states one ----------------------------


def _distribution(*sizes, datatype=None):
    from dataportalen.models import Distribution

    literals = [dict({"type": "literal", "value": s},
                     **({"datatype": datatype} if datatype else {})) for s in sizes]
    metadata = {
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#type": [
            {"type": "uri", "value": "http://www.w3.org/ns/dcat#Distribution"}],
        "http://www.w3.org/ns/dcat#accessURL": [
            {"type": "uri", "value": "https://example.org/f.csv"}],
    }
    if literals:
        metadata["http://www.w3.org/ns/dcat#byteSize"] = literals
    return Distribution.from_json({
        "contextId": "1", "entryId": "2",
        "info": {"https://admin.dataportal.se/store/1/entry/2": {
            "http://entrystore.org/terms/resource": [
                {"type": "uri", "value": "https://example.org/dist"}]}},
        "metadata": {"https://example.org/dist": metadata},
    }).to_dict()


@pytest.mark.parametrize("written,size", [
    ("200704", 200704),
    ("5 420 000", 5420000),           # 11 of the registry's 485 are like this
    ("1\u00a0744\u00a0896", 1744896),  # ...or with no-break spaces
    ("39000.0", 39000),               # 3 are typed as decimals
    ("0", 0),                         # the publisher's claim, passed through
])
def test_byte_size_reads_what_publishers_write(written, size):
    assert _distribution(written)["byte_size"] == size


def test_a_file_with_no_stated_size_has_no_byte_size_key():
    """1.4% of files state one. The key is there when known and absent
    otherwise, the rule `broken` follows -- not a null on the other 98.6%."""
    assert "byte_size" not in _distribution()


@pytest.mark.parametrize("written", ["2022-02-09", "about 3 MB", "", "1.5", "-4"])
def test_an_unreadable_size_is_not_a_size(written):
    assert "byte_size" not in _distribution(written)


def test_the_first_readable_size_wins():
    assert _distribution("unknown", "1024")["byte_size"] == 1024


# -- an empty language map is {} everywhere -----------------------------------


def test_a_record_with_no_keywords_has_an_empty_dict_not_a_list():
    """`keywords` was [] on 1,324 records and a {sv, en} map on the rest, so
    record["keywords"].get("sv") raised on 5.5% of the registry."""
    from dataportalen.models import DataService

    for model, rdf_type in ((Dataset, "Dataset"), (DataService, "DataService")):
        record = model.from_json({
            "contextId": "1", "entryId": "1",
            "info": {"https://admin.dataportal.se/store/1/entry/1": {
                "http://entrystore.org/terms/resource": [
                    {"type": "uri", "value": "https://example.org/x"}]}},
            "metadata": {"https://example.org/x": {
                "http://www.w3.org/1999/02/22-rdf-syntax-ns#type": [
                    {"type": "uri", "value": "http://www.w3.org/ns/dcat#" + rdf_type}]}},
        }).to_dict()
        assert record["keywords"] == {}
        assert record["keywords"].get("sv") is None
