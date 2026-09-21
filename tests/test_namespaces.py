"""Namespace expansion, including the str-method collisions it must survive."""

from __future__ import annotations

import pytest

from dataportal.namespaces import (
    DCAT,
    DCTERMS,
    FOAF,
    NAMESPACES,
    Namespace,
    Types,
    expand,
    shorten,
)


def test_attribute_access_expands_a_term():
    assert DCAT.Dataset == "http://www.w3.org/ns/dcat#Dataset"
    assert DCAT["keyword"] == "http://www.w3.org/ns/dcat#keyword"
    assert DCAT("keyword") == "http://www.w3.org/ns/dcat#keyword"


@pytest.mark.parametrize("local", ["title", "format", "type", "index", "count", "split"])
def test_terms_that_collide_with_str_methods_still_expand(local):
    # DCTERMS.title must be the predicate, not str.title.
    assert isinstance(DCTERMS[local], str)
    assert DCTERMS[local] == "http://purl.org/dc/terms/" + local
    assert getattr(DCTERMS, local) == DCTERMS[local]


def test_a_namespace_is_still_usable_as_a_plain_uri():
    assert str(DCAT) == "http://www.w3.org/ns/dcat#"
    assert "http://www.w3.org/ns/dcat#x".startswith(DCAT)
    assert len(DCAT) == len("http://www.w3.org/ns/dcat#")
    assert DCAT[0:4] == "http"


def test_expand_handles_curies_full_uris_and_unknown_prefixes():
    assert expand("dcat:Dataset") == DCAT.Dataset
    assert expand("dct:title") == DCTERMS.title
    assert expand("http://example.org/x") == "http://example.org/x"
    assert expand("nosuch:Term") == "nosuch:Term"
    assert expand("bare") == "bare"


def test_shorten_prefers_the_longest_matching_namespace():
    assert shorten(DCAT.Dataset) == "dcat:Dataset"
    assert shorten("http://entrystore.org/terms/pipelineresult#merge") == "pipeline:merge"
    assert shorten("http://example.org/x") == "http://example.org/x"


def test_types_are_plain_uri_strings():
    assert Types.DATASET == DCAT.Dataset
    assert Types.AGENT == FOAF.Agent
    assert Types.CATALOG_STATISTICS.endswith("#CatalogStatistics")


def test_every_registered_namespace_is_a_namespace():
    assert all(isinstance(ns, Namespace) for ns in NAMESPACES.values())
