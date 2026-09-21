"""Tests that hit the real registry.

Skipped by default. Run them with::

    pytest -m network
    DATAPORTAL_LIVE=1 pytest tests/test_live.py

They are deliberately tolerant: the registry's content changes nightly, so
they assert on shape and invariants, not on specific datasets.
"""

from __future__ import annotations

import datetime as dt
import os

import pytest

from dataportal import (
    Agent,
    Catalog,
    DataService,
    Dataportal,
    Dataset,
    Distribution,
    Q,
)
from dataportal.namespaces import DCAT

pytestmark = pytest.mark.network

if not os.environ.get("DATAPORTAL_LIVE"):
    pytest.skip(
        "live registry tests; set DATAPORTAL_LIVE=1 to run", allow_module_level=True
    )


@pytest.fixture(scope="module")
def dp():
    with Dataportal() as client:
        yield client


def test_dataset_search_returns_typed_datasets(dp):
    page = dp.datasets(limit=5)
    assert page.total > 1000
    assert len(page) == 5
    for dataset in page:
        assert isinstance(dataset, Dataset)
        assert dataset.resource_uri
        assert dataset.context_id and dataset.entry_id
        assert DCAT.Dataset in dataset.types


def test_free_text_and_title_filters_narrow_the_result(dp):
    everything = dp.count(Q.rdf_type(DCAT.Dataset))
    narrowed = dp.count(Q.rdf_type(DCAT.Dataset) & Q.title("bidrag", "sv"))
    assert 0 < narrowed < everything


def test_paging_yields_distinct_entries(dp):
    seen = [d.entry_uri for d in dp.iter_datasets(limit=25, page_size=10, sort="created asc")]
    assert len(seen) == 25
    assert len(set(seen)) == 25


def test_recursive_fetch_brings_distributions_and_publisher(dp):
    hit = dp.datasets(limit=1)[0]
    full = dp.dataset(context_id=hit.context_id, entry_id=hit.entry_id)
    assert full is not None
    assert full.title
    distributions = full.distributions()
    if full.distribution_uris:
        assert distributions
        assert all(isinstance(d, Distribution) for d in distributions)


def test_lookup_by_publisher_uri_returns_an_agent(dp):
    for dataset in dp.iter_datasets(limit=10):
        if dataset.publisher_uri:
            agent = dp.agent(dataset.publisher_uri)
            if agent is not None:
                assert isinstance(agent, Agent)
                assert agent.name
                return
    pytest.skip("no resolvable publisher among the first ten datasets")


def test_lookup_many_resolves_a_batch(dp):
    uris = [d.resource_uri for d in dp.datasets(limit=5)]
    found = dp.lookup_many(uris)
    assert {e.resource_uri for e in found} <= set(uris)
    assert found


def test_catalogs_and_data_services_are_typed(dp):
    assert all(isinstance(c, Catalog) for c in dp.catalogs(limit=3))
    assert all(isinstance(s, DataService) for s in dp.data_services(limit=3))


def test_facets_report_the_known_rdf_types(dp):
    counts = dp.facet("rdfType", limit=50).as_dict()
    assert counts.get(DCAT.Dataset, 0) > 1000
    assert counts.get(DCAT.Distribution, 0) > 1000


def test_organisation_chart_is_consistent(dp):
    orgs = dp.organisations()
    summary = dp.organisation_summary()
    assert len(orgs) == summary["publishers"]
    assert orgs == sorted(orgs, key=lambda o: o.dataset_count, reverse=True)
    assert sum(o.dataset_count for o in orgs) >= summary["datasets"] * 0.5


def test_harvest_reports_name_their_context(dp):
    reports = dp.harvest_reports(limit=20)
    assert reports
    assert any(r.title for r in reports)
    assert all(r.is_latest for r in reports)
    assert any(r.main_resource_count is not None for r in reports)


def test_catalog_statistics_walk_back_in_time(dp):
    snapshots = dp.catalog_statistics(limit=3)
    assert len(snapshots) == 3
    dates = [s.date for s in snapshots]
    assert all(isinstance(d, (dt.date, dt.datetime)) for d in dates)
    assert dates == sorted(dates, reverse=True)
    assert snapshots[0].datasets_per_context


def test_context_names_map_ids_to_organisations(dp):
    names = dp.context_names()
    assert names
    assert all(key.isdigit() for key in names)


def test_entry_raw_serves_turtle(dp):
    hit = dp.datasets(limit=1)[0]
    response = dp.entry_raw(hit.context_id, hit.entry_id, format="text/turtle")
    assert response.status == 200
    assert "@prefix" in response.text


def test_unknown_entry_ids_raise_not_found(dp):
    from dataportal import HTTPError

    with pytest.raises(HTTPError):
        dp.entry_raw(999999, 999999, part="metadata")


@pytest.mark.parametrize("transport_name", ["urllib", "requests", "httpx"])
def test_every_transport_reaches_the_registry(transport_name):
    from dataportal.exceptions import MissingDependencyError
    from dataportal.transport import HttpxTransport, RequestsTransport, UrllibTransport

    factories = {
        "urllib": UrllibTransport,
        "requests": RequestsTransport,
        "httpx": HttpxTransport,
    }
    try:
        transport = factories[transport_name]()
    except MissingDependencyError:
        pytest.skip("%s is not installed" % transport_name)
    with Dataportal(transport=transport) as client:
        assert client.count(Q.rdf_type(DCAT.Dataset)) > 1000
    transport.close()


def test_the_nightly_dump_streams_lazily(dp):
    # The dump is hundreds of megabytes; read just enough to see it is RDF/XML.
    head = b""
    for chunk in dp.iter_dump(chunk_size=8192):
        head += chunk
        if len(head) > 2048:
            break
    assert head.lstrip().startswith(b"<?xml")
    assert b"rdf:RDF" in head


def test_negation_actually_excludes(dp):
    # Regression: `a AND (NOT b)` is silently empty in Lucene, so Q anchors
    # negations instead. The two counts must add up exactly.
    datasets = Q.rdf_type(DCAT.Dataset)
    total = dp.count(datasets)
    matching = dp.count(datasets & Q.title("cykel", "sv"))
    excluded = dp.count(datasets & ~Q.title("cykel", "sv"))
    assert 0 < matching < total
    assert matching + excluded == total


def test_link_check_reports_are_current(dp):
    reports = dp.link_check_reports(limit=20)
    assert reports
    for report in reports:
        assert report.checked is not None
        assert report.failed is not None
        assert 0 <= report.failed <= report.checked


def test_metadata_quality_scores_are_in_range(dp):
    scores = dp.metadata_quality(limit=20)
    assert scores
    assert all(0 <= s.percentage <= 100 for s in scores)
    assert any(s.is_total for s in dp.metadata_quality(limit=100))
