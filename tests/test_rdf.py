"""The RDF/JSON graph model."""

from __future__ import annotations

import datetime as dt

import pytest

from dataportal import BNode, Graph, Literal, URIRef
from dataportal.exceptions import ParseError
from dataportal.namespaces import DCAT, DCTERMS, RDF, XSD
from dataportal.rdf import parse_xsd

DOC = {
    "http://example.org/d1": {
        DCTERMS.title: [
            {"type": "literal", "value": "Hej", "lang": "sv"},
            {"type": "literal", "value": "Hello", "lang": "en"},
        ],
        DCTERMS.issued: [
            {"type": "literal", "value": "2020-03-04", "datatype": XSD.date}
        ],
        DCAT.keyword: [
            {"type": "literal", "value": "cykel", "lang": "sv"},
            {"type": "literal", "value": "bike", "lang": "en"},
        ],
        DCAT.distribution: [{"type": "uri", "value": "http://example.org/dist1"}],
        DCTERMS.temporal: [{"type": "bnode", "value": "_:b0"}],
        RDF.type: [{"type": "uri", "value": DCAT.Dataset}],
    },
    "http://example.org/dist1": {
        DCTERMS.title: [{"type": "literal", "value": "CSV"}],
        DCAT.byteSize: [
            {"type": "literal", "value": "1234", "datatype": XSD.integer}
        ],
        RDF.type: [{"type": "uri", "value": DCAT.Distribution}],
    },
    "_:b0": {
        DCAT.startDate: [{"type": "literal", "value": "2019-01-01", "datatype": XSD.date}],
    },
}


@pytest.fixture
def graph() -> Graph:
    return Graph(DOC)


def test_graph_counts_triples_not_subjects(graph):
    assert len(graph.subjects()) == 3
    assert len(graph) == 12
    assert bool(graph)
    assert not bool(Graph())


def test_named_subjects_excludes_blank_nodes(graph):
    assert "_:b0" not in graph.named_subjects()
    assert len(graph.named_subjects()) == 2


def test_subjects_of_type(graph):
    assert graph.subjects_of_type(DCAT.Dataset) == ["http://example.org/d1"]
    assert graph.subjects_of_type("dcat:Distribution") == ["http://example.org/dist1"]


def test_value_follows_the_language_preference(graph):
    resource = graph.resource("http://example.org/d1")
    assert resource.value(DCTERMS.title) == "Hej"
    assert resource.value(DCTERMS.title, ["en"]) == "Hello"
    assert resource.with_languages(["en", "sv"]).value(DCTERMS.title) == "Hello"


def test_value_accepts_regional_variants():
    graph = Graph({"s": {DCTERMS.title: [{"type": "literal", "value": "x", "lang": "sv-SE"}]}})
    assert graph.resource("s").value(DCTERMS.title) == "x"


def test_value_falls_back_to_untagged_then_any():
    untagged = Graph({"s": {DCTERMS.title: [{"type": "literal", "value": "plain"}]}})
    assert untagged.resource("s").value(DCTERMS.title) == "plain"
    other = Graph({"s": {DCTERMS.title: [{"type": "literal", "value": "x", "lang": "de"}]}})
    assert other.resource("s").value(DCTERMS.title) == "x"


def test_values_can_be_filtered_by_language(graph):
    resource = graph.resource("http://example.org/d1")
    assert resource.values(DCAT.keyword) == ["cykel", "bike"]
    assert resource.values(DCAT.keyword, "en") == ["bike"]


def test_localized_maps_tags_to_values(graph):
    assert graph.resource("http://example.org/d1").localized(DCTERMS.title) == {
        "sv": "Hej", "en": "Hello",
    }


def test_datatypes_become_python_objects(graph):
    resource = graph.resource("http://example.org/d1")
    assert resource.python(DCTERMS.issued) == dt.date(2020, 3, 4)
    assert graph.resource("http://example.org/dist1").integer(DCAT.byteSize) == 1234


def test_refs_follow_only_described_subjects(graph):
    resource = graph.resource("http://example.org/d1")
    assert [r.uri for r in resource.refs(DCAT.distribution)] == ["http://example.org/dist1"]
    assert resource.ref(DCTERMS.temporal).uri == "_:b0"
    # A URI that nothing in this graph describes is reported but not followed.
    lone = Graph({"s": {DCAT.distribution: [{"type": "uri", "value": "http://x/1"}]}})
    assert lone.resource("s").uris(DCAT.distribution) == ["http://x/1"]
    assert lone.resource("s").refs(DCAT.distribution) == []


def test_curie_shortening_on_uri_nodes(graph):
    node = graph.objects("http://example.org/d1", RDF.type)[0]
    assert isinstance(node, URIRef)
    assert node.curie() == "dcat:Dataset"


def test_predicates_accept_curies(graph):
    assert graph.resource("http://example.org/d1").value("dcterms:title") == "Hej"


def test_merged_is_a_union_without_duplicates(graph):
    merged = graph.merged(Graph(DOC))
    assert len(merged) == len(graph)
    extra = Graph({"http://example.org/d1": {DCTERMS.creator: [{"type": "uri", "value": "http://c"}]}})
    assert len(graph.merged(extra)) == len(graph) + 1


def test_to_json_round_trips(graph):
    assert Graph(graph.to_json()).to_json() == graph.to_json()


def test_to_json_is_a_copy(graph):
    snapshot = graph.to_json()
    snapshot["http://example.org/d1"][DCTERMS.title].append({"type": "literal", "value": "X"})
    assert len(graph.resource("http://example.org/d1").literals(DCTERMS.title)) == 2


def test_node_equality_and_hashing():
    assert URIRef("http://a") == "http://a"
    assert Literal("x", "sv") != Literal("x", "en")
    assert BNode("_:1") == BNode("_:1")
    assert len({URIRef("http://a"), URIRef("http://a")}) == 1


def test_malformed_nodes_raise_parse_error():
    with pytest.raises(ParseError):
        Graph({"s": {"p": [{"type": "nonsense", "value": "x"}]}}).objects("s", "p")


@pytest.mark.parametrize(
    "value,datatype,expected",
    [
        ("42", XSD.integer, 42),
        ("4.5", XSD.double, 4.5),
        ("true", XSD.boolean, True),
        ("false", XSD.boolean, False),
        ("2020-01-02", XSD.date, dt.date(2020, 1, 2)),
        ("2019", XSD.gYear, 2019),
        ("not a number", XSD.integer, "not a number"),
        ("x", None, "x"),
    ],
)
def test_parse_xsd(value, datatype, expected):
    assert parse_xsd(value, datatype) == expected


def test_parse_xsd_handles_offsets_and_fractional_seconds():
    parsed = parse_xsd("2026-09-21T04:17:34.948+02:00", XSD.dateTime)
    assert isinstance(parsed, dt.datetime)
    assert parsed.year == 2026 and parsed.utcoffset() == dt.timedelta(hours=2)


def test_date_accessor_tolerates_missing_datatypes():
    graph = Graph({"s": {DCTERMS.issued: [{"type": "literal", "value": "2020-01-02"}]}})
    assert graph.resource("s").date(DCTERMS.issued) == dt.date(2020, 1, 2)
