"""The public models: attributes, `to_dict()`, printing, and the language."""

from __future__ import annotations

import copy
import dataclasses
import json

import pytest

from conftest import CATALOG_RECORDS, SERVICE_RECORDS
from dataportalen.client import _present
from dataportalen.models import (
    DataService,
    Dataset,
    Distribution,
    Facet,
    Facets,
    FacetValue,
    Keywords,
    MultilingualText,
    Publisher,
    SearchResult,
    _dataset,
    _publisher,
    _record,
)


def presented(record):
    return _present(copy.deepcopy(record))


def dump(value):
    return json.dumps(value, indent=4, ensure_ascii=False)


@pytest.fixture
def roads():
    return _dataset(presented(CATALOG_RECORDS[0]), "sv")


@pytest.fixture
def service():
    return _record(presented(SERVICE_RECORDS[0]), "sv")


# -- MultilingualText ---------------------------------------------------------


def test_text_prefers_the_catalogue_language_and_falls_back():
    both = MultilingualText({"sv": "Väg", "en": "Road"})
    assert both.text() == "Väg"
    assert both.text("en") == "Road"
    assert MultilingualText({"sv": "Väg", "en": "Road"}, "en").text() == "Road"
    assert MultilingualText({"en": "Road"}).text() == "Road"
    assert MultilingualText({"sv": "Väg"}, "en").text() == "Väg"
    assert MultilingualText({}).text() is None
    assert MultilingualText({"sv": "", "en": "Road"}).text() == "Road"


def test_text_is_a_mapping_equal_to_its_dict():
    title = MultilingualText({"sv": "Väg", "en": "Road"})
    assert title == {"sv": "Väg", "en": "Road"}
    assert title["sv"] == "Väg" and "en" in title and len(title) == 2
    assert title.to_dict() == {"sv": "Väg", "en": "Road"}
    assert title.to_dict() is not title.to_dict()
    assert not MultilingualText({})


def test_keywords_read_one_language_or_both():
    keywords = Keywords({"sv": ["väg", "trafik"], "en": ["road", "väg"]})
    assert keywords.list() == ["väg", "trafik"]
    assert keywords.list("en") == ["road", "väg"]
    assert Keywords({"en": ["road"]}).list() == ["road"]
    assert keywords.all() == ["väg", "trafik", "road"]
    assert keywords.to_dict() == {"sv": ["väg", "trafik"], "en": ["road", "väg"]}
    assert Keywords({}).list() == [] and Keywords({}).to_dict() == {}


# -- models -------------------------------------------------------------------


def test_a_dataset_reads_by_attribute(roads):
    assert isinstance(roads, Dataset)
    assert roads.title.text() == "Vägtrafiknät"
    assert roads.publisher.id == "trafikverket"
    assert isinstance(roads.publisher, Publisher)
    assert roads.publisher.dataset_count is None and roads.publisher.facets is None
    assert roads.license.id == "cc_by_4_0"
    assert roads.license.label.text() == "CC BY 4.0 (Attribution)"
    assert roads.temporal.start == "2020-01-01"
    assert roads.contact_points[0].email == "vag@example.org"
    assert all(isinstance(d, Distribution) for d in roads.distributions)
    assert [d.kind for d in roads.distributions] == ["file", "file"]
    assert roads.stale is None
    with pytest.raises(TypeError):
        roads["title"]


def test_to_dict_keeps_the_field_names_of_the_record(roads):
    record = presented(CATALOG_RECORDS[0])
    out = roads.to_dict()
    assert list(out) == [f.name for f in dataclasses.fields(Dataset)]
    for key in record:
        if key == "publisher":
            continue
        if key == "distributions":
            for mine, theirs in zip(out[key], record[key]):
                assert {k: mine[k] for k in theirs} == theirs
            continue
        assert out[key] == record[key], key
    assert out["publisher"]["id"] == record["publisher"]["id"]
    assert out["stale"] is None
    assert out["distributions"][0]["broken"] is None


def test_to_dict_is_plain_json_all_the_way_down(roads, service):
    for model in (roads, service):
        text = json.dumps(model.to_dict())
        assert json.loads(text) == model.to_dict()


@pytest.mark.parametrize("which", ["dataset", "service", "publisher", "distribution",
                                   "text", "keywords", "license", "temporal",
                                   "contact"])
def test_printing_a_model_prints_its_json(which, roads, service):
    model = {
        "dataset": roads, "service": service, "publisher": roads.publisher,
        "distribution": roads.distributions[0], "text": roads.title,
        "keywords": roads.keywords, "license": roads.license,
        "temporal": roads.temporal, "contact": roads.contact_points[0],
    }[which]
    assert str(model) == dump(model.to_dict())


def test_a_data_service_is_its_own_model(service):
    assert isinstance(service, DataService)
    assert service.type == "data_service"
    assert service.endpoint_url == "https://api.example.org/v1"
    assert list(service.to_dict()) == [f.name for f in dataclasses.fields(DataService)]


def test_the_language_reaches_every_text(roads):
    english = _dataset(presented(CATALOG_RECORDS[0]), "en")
    assert english.title.text() == "Road traffic network"
    assert english.keywords.list() == ["geodata "]
    assert english.publisher.name.text() == "Trafikverket"
    assert roads.title.text() == "Vägtrafiknät"


def test_a_record_without_a_publisher_still_has_one():
    record = presented(dict(CATALOG_RECORDS[0], publisher=None))
    dataset = _dataset(record, "sv")
    assert dataset.publisher.id is None
    assert dataset.publisher.name.text() is None
    assert dataset.publisher.identifiers == []


def test_models_do_not_share_state_with_the_record():
    record = presented(CATALOG_RECORDS[0])
    dataset = _dataset(record, "sv")
    dataset.themes.append("x")
    dataset.publisher.identifiers.append("x")
    assert record["themes"] == ["transport"]
    assert record["publisher"]["identifiers"] == []


def test_a_publisher_row_carries_counts_and_facets():
    facets = Facets({"theme": [FacetValue("transport", 2, {"sv": "Transport"})]})
    row = dict(presented(CATALOG_RECORDS[0])["publisher"],
               dataset_count=2, data_service_count=1)
    publisher = _publisher(row, "en", facets)
    assert publisher.dataset_count == 2 and publisher.data_service_count == 1
    assert publisher.facets["theme"][0].label.text() == "Transport"
    assert publisher.to_dict()["facets"] == {
        "theme": [{"value": "transport", "count": 2, "label": {"sv": "Transport"}}]}


# -- facets and SearchResult -------------------------------------------------


def test_a_facet_value_is_still_a_pair_with_a_label():
    row = FacetValue("transport", 3, {"sv": "Transport", "en": "Transport"})
    value, count = row
    assert (value, count) == ("transport", 3) == row
    assert isinstance(row.label, MultilingualText)
    assert row.to_dict() == {"value": "transport", "count": 3,
                             "label": {"sv": "Transport", "en": "Transport"}}
    assert str(row) == dump(row.to_dict())
    assert FacetValue("csv", 1).label.to_dict() == {}


def test_facets_to_dict_keeps_labels_and_counts_is_compact():
    facets = Facets({"theme": [FacetValue("transport", 3, {"sv": "Transport"}),
                               FacetValue("energy", 1, {"en": "Energy"})]}, limit=1)
    assert facets.to_dict() == {
        "theme": [{"value": "transport", "count": 3, "label": {"sv": "Transport"}}]}
    assert facets.counts() == {"theme": {"transport": 3}}
    assert facets.omitted == {"theme": 1}
    assert str(facets) == dump(facets.to_dict())
    assert str(facets["theme"]) == dump(facets["theme"].to_dict())


def test_a_facet_keeps_what_was_already_omitted():
    facets = Facets({"theme": Facet([FacetValue("a", 2), FacetValue("b", 1)], 4)},
                    limit=1)
    assert facets["theme"].omitted == 5


def test_a_search_result_holds_datasets_and_facets(roads):
    facets = Facets({"theme": [FacetValue("transport", 1)]})
    result = SearchResult([roads], facets, total=3, offset=1, limit=1)
    assert len(result) == 1 and list(result) == [roads]
    assert result.has_more
    out = result.to_dict()
    assert list(out) == ["total", "offset", "limit", "datasets", "facets",
                         "facets_omitted"]
    assert out["datasets"] == [roads.to_dict()]
    assert out["facets"] == facets.to_dict()
    assert str(result) == dump(out)
    json.dumps(out)
