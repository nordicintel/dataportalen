"""The RDF layer: namespaces, graph parsing, and the label table."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from dataportalen.core import ParseError
from dataportalen.rdf import (
    DCAT,
    DCTERMS,
    FOAF,
    NAMESPACES,
    RDF,
    VOCABULARY,
    XSD,
    BNode,
    Graph,
    Literal,
    Namespace,
    Types,
    URIRef,
    Vocabulary,
    expand,
    parse_xsd,
    shorten,
)

# ==========================================================================
# The RDF/JSON graph model.
# ==========================================================================




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


# ==========================================================================
# Namespace expansion, including the str-method collisions it must survive.
# ==========================================================================




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
    assert shorten("http://entryscape.com/terms/MQA") == "escape:MQA"
    assert shorten("http://example.org/x") == "http://example.org/x"


def test_types_are_plain_uri_strings():
    assert Types.DATASET == DCAT.Dataset
    assert Types.AGENT == FOAF.Agent
    assert Types.CATALOG_STATISTICS.endswith("#CatalogStatistics")


def test_every_registered_namespace_is_a_namespace():
    assert all(isinstance(ns, Namespace) for ns in NAMESPACES.values())


# ==========================================================================
# Vocabulary label lookup.
# ==========================================================================




THEME_TRAN = "http://publications.europa.eu/resource/authority/data-theme/TRAN"


@pytest.fixture
def vocabulary() -> Vocabulary:
    return Vocabulary({
        THEME_TRAN: {"sv": "Transport", "en": "Transport"},
        "http://example.org/sv-only": {"sv": "Bara svenska"},
        "http://example.org/trailing/": {"en": "Trailing slash"},
    })


def test_label_follows_the_language_preference(vocabulary):
    assert vocabulary.label(THEME_TRAN, ["en"]) == "Transport"
    assert vocabulary.label(THEME_TRAN, ["sv"]) == "Transport"


def test_label_falls_back_when_the_language_is_missing(vocabulary):
    assert vocabulary.label("http://example.org/sv-only", ["en"]) == "Bara svenska"


def test_unknown_uris_report_none_rather_than_guessing(vocabulary):
    # A guess derived from the URI would look like data; None is honest.
    assert vocabulary.label("http://example.org/authority/file-type/CSV") is None
    assert vocabulary.label(None) is None
    assert vocabulary.label("") is None


def test_term_shape_is_stable(vocabulary):
    assert vocabulary.term(THEME_TRAN, ["en"]) == {"uri": THEME_TRAN, "label": "Transport"}
    assert vocabulary.term("http://example.org/nope") == {
        "uri": "http://example.org/nope", "label": None,
    }
    assert vocabulary.term(None) is None


def test_terms_maps_a_list_and_drops_empties(vocabulary):
    out = vocabulary.terms([THEME_TRAN, "", "http://example.org/nope"], ["en"])
    assert [t["uri"] for t in out] == [THEME_TRAN, "http://example.org/nope"]


def test_trailing_slashes_are_tolerated(vocabulary):
    assert vocabulary.label("http://example.org/trailing") == "Trailing slash"


def test_membership_and_size(vocabulary):
    assert THEME_TRAN in vocabulary
    assert "http://example.org/nope" not in vocabulary
    assert len(vocabulary) == 3


def test_a_missing_data_file_yields_an_empty_vocabulary(tmp_path):
    empty = Vocabulary.load(str(tmp_path / "does-not-exist.json"))
    assert len(empty) == 0
    assert empty.label(THEME_TRAN) is None


def test_a_corrupt_data_file_does_not_raise(tmp_path):
    path = tmp_path / "vocabulary.json"
    path.write_text("{not json", encoding="utf-8")
    assert len(Vocabulary.load(str(path))) == 0


def test_round_trips_through_json(vocabulary):
    restored = Vocabulary(json.loads(json.dumps(vocabulary.to_dict())))
    assert restored.label(THEME_TRAN, ["en"]) == "Transport"


# --- the table actually shipped ---------------------------------------------


def test_the_shipped_table_is_populated():
    assert len(VOCABULARY) > 200, (
        "vocabulary.json looks empty or missing; "
        "regenerate it with `python tools/build_vocabulary.py`"
    )


@pytest.mark.parametrize(
    "uri",
    [
        "http://publications.europa.eu/resource/authority/data-theme/TRAN",
        "http://publications.europa.eu/resource/authority/data-theme/GOVE",
        "http://publications.europa.eu/resource/authority/frequency/ANNUAL",
        "http://publications.europa.eu/resource/authority/access-right/PUBLIC",
        "http://publications.europa.eu/resource/authority/file-type/CSV",
        "http://publications.europa.eu/resource/authority/language/SWE",
    ],
)
def test_common_vocabulary_uris_have_labels(uri):
    label = VOCABULARY.label(uri)
    assert label, "%s has no label" % uri


def test_labels_exist_in_both_languages_for_the_data_themes():
    themes = [
        uri for uri in VOCABULARY
        if uri.startswith("http://publications.europa.eu/resource/authority/data-theme/")
    ]
    assert len(themes) >= 13
    for uri in themes:
        found = VOCABULARY.labels(uri)
        assert "sv" in found and "en" in found, uri


# -- discovering values offline ----------------------------------------------


def test_known_values_can_be_scoped_to_one_filter():
    """A flat list of 900+ names does not answer "what can theme= be?"."""
    from dataportalen.rdf import known_values

    themes = known_values("theme")
    assert "transport" in themes
    assert "csv" not in themes
    assert len(themes) < len(known_values())

    assert known_values("access_rights") == ["non_public", "public", "restricted"]
    assert known_values("theme", "trans") == [
        "transport", "transport_networks", "transportation"]


def test_known_values_rejects_an_unknown_filter():
    from dataportalen import QueryError
    from dataportalen.rdf import known_values

    with pytest.raises(QueryError) as info:
        known_values("themes")
    assert "theme" in str(info.value)


def test_an_alias_is_the_publisher_it_names():
    """`scb` is what a person types; the 50-character slug is what is stored."""
    from dataportalen.rdf import known_publishers, publisher_for, resolve_publisher

    long = "statistikmyndigheten_scb_statistiska_centralbyran"
    assert resolve_publisher("scb") == resolve_publisher(long)
    # The facets and the records keep the canonical slug; the alias is
    # input only.
    assert publisher_for(resolve_publisher("scb")[0]) == long
    assert "scb" in known_publishers("scb")


def test_every_alias_names_a_publisher_the_package_can_resolve():
    """The file is kept by hand; a typo in it must fail here, not in a search."""
    from dataportalen.rdf import _ALIASES, _PUBLISHERS

    assert _ALIASES, "publishers.json shipped without aliases"
    for alias, target in _ALIASES.items():
        assert target in _PUBLISHERS, "%s -> %s is not a publisher" % (alias, target)
        assert alias not in _PUBLISHERS, "%s shadows a real publisher" % alias


def test_a_publisher_has_at_most_one_alias_and_no_two_share_one():
    """One short name per publisher, unique: an alias names exactly one."""
    import json

    from dataportalen.rdf import _ALIASES, _PUBLISHERS_FILE, alias_for

    with open(_PUBLISHERS_FILE, encoding="utf-8") as handle:
        table = json.load(handle)["publishers"]
    given = [row["alias"] for row in table.values() if row.get("alias")]
    assert len(given) == len(set(given)) == len(_ALIASES)
    targets = list(_ALIASES.values())
    assert len(targets) == len(set(targets))
    for alias, target in _ALIASES.items():
        assert alias_for(target) == alias


def test_a_colliding_alias_is_left_out_not_chosen(monkeypatch, tmp_path):
    """Two claims on one alias, or an alias that is an id: neither wins."""
    import json

    from dataportalen import rdf

    path = tmp_path / "publishers.json"
    path.write_text(json.dumps({"publishers": {
        "a_myndighet": {"uri": "http://dataportal.se/organisation/SE1111111111",
                        "alias": "am"},
        "a_museum": {"uri": "http://example.org/museum", "alias": "am"},
        "b_verket": {"uri": "http://example.org/b", "alias": "a_museum"},
    }}), encoding="utf-8")
    monkeypatch.setattr(rdf, "_PUBLISHERS_FILE", str(path))
    by_slug, by_uri, aliases = rdf._build_publishers()
    assert aliases == {}
    assert by_slug["se1111111111"] == ["http://dataportal.se/organisation/SE1111111111"]


def test_an_organisation_number_is_read_from_the_uri():
    from dataportalen.rdf import publisher_for, resolve_publisher

    assert resolve_publisher("se2021006297") == resolve_publisher("trafikverket")
    assert publisher_for(resolve_publisher("SE2021006297")[0]) == "trafikverket"


def test_publisher_for_is_the_reverse_of_the_filter_value():
    from dataportalen.rdf import known_publishers, publisher_for, resolve_publisher

    uri = resolve_publisher("trafikverket")[0]
    assert publisher_for(uri) == "trafikverket"
    assert publisher_for("https://example.org/nobody") is None
    assert "trafikverket" in known_publishers("trafik")
