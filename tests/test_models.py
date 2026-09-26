"""Typed entry models, exercised against recorded registry responses."""

from __future__ import annotations

import datetime as dt

import pytest

from conftest import load_fixture
from dataportalen import (
    Agent,
    CatalogStatistics,
    ContactPoint,
    Dataset,
    Distribution,
    Entry,
)
from dataportalen.models import wrap_entry
from dataportalen.namespaces import DCAT, DCTERMS


@pytest.fixture
def dataset_hit():
    return load_fixture("search_datasets.json")["resource"]["children"][0]


@pytest.fixture
def recursive_dataset():
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


def test_wrap_entry_types_a_hit_from_its_rdf_type(dataset_hit):
    entry = wrap_entry(dataset_hit)
    assert isinstance(entry, Dataset)


def test_envelope_fields_come_from_the_info_graph(dataset_hit):
    entry = wrap_entry(dataset_hit)
    assert entry.context_id and entry.entry_id
    assert entry.entry_uri.endswith("/entry/%s" % entry.entry_id)
    assert entry.resource_uri and entry.resource_uri != entry.entry_uri
    assert isinstance(entry.modified, (dt.date, dt.datetime))
    assert entry.is_public
    assert entry.entry_type


def test_dataset_reads_core_dcat_fields(recursive_dataset):
    dataset = recursive_dataset
    assert dataset.title
    assert dataset.description
    assert dataset.publisher_uri.startswith("http")
    assert dataset.license
    assert dataset.keywords
    assert dataset.theme_uris
    assert dataset.landing_page
    assert dataset.access_rights


def test_recursive_graph_resolves_to_the_dataset_not_a_distribution(recursive_dataset):
    # The graph also describes distributions and the publisher; the entry must
    # still be about the dataset.
    assert DCAT.Dataset in recursive_dataset.types


def test_distributions_come_from_the_graph_without_extra_requests(recursive_dataset):
    distributions = recursive_dataset.distributions()
    assert distributions
    assert all(isinstance(d, Distribution) for d in distributions)
    assert len(distributions) == len(recursive_dataset.distribution_uris)
    first = distributions[0]
    assert first.download_url or first.access_url
    assert first.format or first.media_type


def test_contact_points_are_typed_and_normalise_mailto(recursive_dataset):
    contacts = recursive_dataset.contact_points
    assert contacts
    contact = contacts[0]
    assert isinstance(contact, ContactPoint)
    assert contact.name
    assert contact.email and not contact.email.startswith("mailto:")


def test_fetch_contact_points_does_not_duplicate_inline_ones(recursive_dataset):
    # Everything is already in the graph, so no client is needed.
    assert len(recursive_dataset.fetch_contact_points()) == len(
        recursive_dataset.contact_point_uris
    )


def test_keywords_prefer_the_configured_language():
    entry = wrap_entry(
        {
            "metadata": {
                "http://x/1": {
                    DCAT.keyword: [
                        {"type": "literal", "value": "cykel", "lang": "sv"},
                        {"type": "literal", "value": "bike", "lang": "en"},
                    ],
                    "http://www.w3.org/1999/02/22-rdf-syntax-ns#type": [
                        {"type": "uri", "value": DCAT.Dataset}
                    ],
                }
            }
        },
        default=Dataset,
        languages=("en", "sv"),
    )
    assert entry.keywords == ["bike"]
    assert entry.keywords_by_language == {"sv": ["cykel"], "en": ["bike"]}


def test_period_of_time_keeps_bare_years():
    entry = wrap_entry(
        {
            "metadata": {
                "http://x/1": {
                    DCTERMS.temporal: [{"type": "bnode", "value": "_:t"}],
                    "http://www.w3.org/1999/02/22-rdf-syntax-ns#type": [
                        {"type": "uri", "value": DCAT.Dataset}
                    ],
                },
                "_:t": {
                    "http://schema.org/startDate": [{"type": "literal", "value": "2019"}],
                    DCAT.endDate: [
                        {
                            "type": "literal",
                            "value": "2024-12-31",
                            "datatype": "http://www.w3.org/2001/XMLSchema#date",
                        }
                    ],
                },
            }
        },
        default=Dataset,
    )
    period = entry.temporal
    assert period.start == "2019"
    assert period.start_value == "2019"
    assert period.end == dt.date(2024, 12, 31)


def test_agent_reads_name_identifiers_and_homepage(recursive_dataset):
    publisher_uri = recursive_dataset.publisher_uri
    agent = Agent.from_resource(recursive_dataset.metadata.resource(publisher_uri))
    assert agent.name
    assert agent.identifiers
    assert agent.homepage
    assert agent.agent_type


def test_catalog_statistics_maps_contexts_to_counts():
    hit = load_fixture("catalog_statistics.json")["resource"]["children"][0]
    stats = wrap_entry(hit, default=CatalogStatistics)
    assert isinstance(stats.date, (dt.date, dt.datetime))
    assert stats.dataset_count and stats.dataset_count > 0
    per_context = stats.datasets_per_context
    assert per_context
    assert all(key.isdigit() for key in per_context)
    assert all(isinstance(v, int) for v in per_context.values())


def test_as_and_with_languages_preserve_the_envelope(dataset_hit):
    entry = wrap_entry(dataset_hit)
    plain = entry.as_(Entry)
    assert plain.context_id == entry.context_id
    assert plain.entry_id == entry.entry_id
    english = entry.with_languages(["en", "sv"])
    assert english.languages == ("en", "sv")
    assert english.context_id == entry.context_id


def test_following_references_without_a_client_is_a_clear_error(dataset_hit):
    entry = wrap_entry(dataset_hit)
    with pytest.raises(RuntimeError, match="without a client"):
        entry.as_(Dataset).publisher()


def test_raw_json_round_trips_a_search_hit(dataset_hit):
    assert wrap_entry(dataset_hit).raw_json() == dataset_hit


def test_raw_json_reconstructs_when_there_was_no_raw_payload(recursive_dataset):
    payload = recursive_dataset.raw_json()
    assert payload["contextId"] == "547"
    assert payload["metadata"]
    assert payload["info"]


def test_link_check_report_counts_links():
    from dataportalen import LinkCheckReport

    hit = load_fixture("link_check_report.json")["resource"]["children"][0]
    report = wrap_entry(hit)
    assert isinstance(report, LinkCheckReport)
    assert report.checked is not None and report.checked >= 0
    assert report.failed is not None
    assert report.succeeded == report.checked - report.failed
    assert isinstance(report.run_at, (dt.date, dt.datetime))


def test_metadata_quality_reads_the_mqa_score():
    from dataportalen import MetadataQuality

    children = load_fixture("metadata_quality.json")["resource"]["children"]
    scores = [wrap_entry(hit) for hit in children]
    assert all(isinstance(s, MetadataQuality) for s in scores)
    assert all(s.score is not None for s in scores)
    assert all(0 <= s.percentage <= 100 for s in scores)
    assert all(isinstance(s.is_total, bool) for s in scores)
