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

from dataportalen import (
    Agent,
    Catalog,
    Dataportal,
    DataService,
    Dataset,
    Distribution,
    Q,
)
from dataportalen.rdf import DCAT

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
    from dataportalen import HTTPError

    with pytest.raises(HTTPError):
        dp.entry_raw(999999, 999999, part="metadata")


@pytest.mark.parametrize("transport_name", ["urllib", "requests", "httpx"])
def test_every_transport_reaches_the_registry(transport_name):
    """All three work, with nothing optional to install."""
    from dataportalen.core import HttpxTransport, RequestsTransport, UrllibTransport

    factories = {
        "urllib": UrllibTransport,
        "requests": RequestsTransport,
        "httpx": HttpxTransport,
    }
    transport = factories[transport_name]()
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


def test_catalog_export_round_trips(dp, tmp_path):
    """A small real export: every line self-contained and correct."""
    import json

    from dataportalen import download_catalog

    out = tmp_path / "catalog.jsonl"
    summary = download_catalog(str(out), limit=200, client=dp)
    assert summary.datasets == 200
    assert summary.bytes_written == out.stat().st_size

    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 200
    assert all(r["uri"] for r in records)
    # Default language: one string per field, not a map to unpack.
    assert all(isinstance(r["title"], str) for r in records)
    assert all(isinstance(r["keywords"], list) for r in records)
    # Controlled values are short names, never URIs or localized labels.
    themes = {t for r in records for t in r["themes"]}
    assert themes
    for theme in themes:
        assert isinstance(theme, str) and "://" not in theme
        assert theme == theme.lower()
    # "transport" not "TRAN", and not "Regeringen och den offentliga sektorn".
    assert not any(t.startswith("regeringen") for t in themes)


def test_catalog_export_gzips(dp, tmp_path):
    import gzip
    import json

    from dataportalen import download_catalog

    out = tmp_path / "catalog.jsonl.gz"
    download_catalog(str(out), limit=25, client=dp)
    assert out.read_bytes()[:2] == b"\x1f\x8b"
    with gzip.open(out, "rt", encoding="utf-8") as handle:
        assert len([json.loads(line) for line in handle]) == 25


def test_vocabulary_labels_resolve_on_live_data(dp):
    """The label table must actually hit what publishers use."""
    total = labelled = 0
    for dataset in dp.iter_datasets(limit=200):
        doc = dataset.to_dict(distributions=False)
        for field in ("themes", "languages"):
            for value in doc[field]:
                total += 1
                labelled += 1 if value and "://" not in value else 0
        for field in ("access_rights", "accrual_periodicity"):
            value = doc[field]
            if value:
                total += 1
                labelled += 1 if "://" not in value else 0
    assert total > 100
    # Every one of these resolves to a short name; a bare URI leaking through
    # means the shipped table drifted from what the registry serves.
    assert labelled == total


# -- the forms callers actually type -----------------------------------------


def test_query_helpers_are_accepted_by_the_registry(dp):
    """Every Q helper below once produced an HTTP 400 or silently zero hits."""
    base = Q.rdf_type(DCAT.Dataset)
    assert dp.count(base & Q.created("2020-01-01")) > 0
    assert dp.count(base & Q.modified("2020")) > 0
    assert dp.count(base & Q.predicate_range(
        "http://purl.org/dc/terms/modified", "2024-01-01")) > 0
    assert dp.count(Q.entry_type("local")) > 0
    assert dp.count(Q.graph_type("none")) > 0
    assert dp.count(Q.resource_type("informationresource")) > 0
    assert dp.count(base & Q.format("text/csv")) > 0


def test_every_filter_matches_something(dp):
    """A filter that matches nothing is indistinguishable from a broken one."""
    cases = {
        "text": "cykel", "title": "bidrag", "keyword": "geodata",
        "publisher": "trafikverket", "theme": "transport", "format": "csv",
        "license": "cc_by_4_0", "access_rights": "public", "updated": "annual",
        "language": "swedish", "place": "kingdom_of_sweden", "catalog": 50,
        "updated_after": "2024-01-01", "published_after": "2020",
    }
    for name, value in cases.items():
        assert dp.datasets(limit=1, **{name: value}).total > 0, name


def test_organisations_lead_into_a_search(dp):
    orgs = dp.organisations()
    assert len(orgs) > 300
    filterable = [o for o in orgs if o.publisher]
    assert len(filterable) >= len(orgs) - 5
    assert dp.datasets(publisher=filterable[0].publisher, limit=1).total > 0


def test_language_shapes_the_output(dp):
    uri = next(iter(dp.datasets(limit=1))).resource_uri
    swedish = dp.dataset(uri=uri).to_dict()
    assert isinstance(swedish["title"], str)
    with Dataportal(language="all") as every:
        assert isinstance(every.dataset(uri=uri).to_dict()["title"], dict)
