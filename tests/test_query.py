"""Solr query building and escaping."""

from __future__ import annotations

import datetime as dt

import pytest

from dataportalen import Q, escape, escape_uri, predicate_field
from dataportalen.core import QueryError
from dataportalen.rdf import DCAT, DCTERMS


def test_escape_covers_every_solr_metacharacter():
    assert escape("a:b") == r"a\:b"
    assert escape("a+b-c") == r"a\+b\-c"
    assert escape("plain") == "plain"


def test_escape_uri_escapes_every_query_metacharacter():
    # Solr treats ':' and '/' specially; '#' is harmless but escaping is safe.
    assert escape_uri("http://www.w3.org/ns/dcat#Dataset") == (
        r"http\:\/\/www.w3.org\/ns\/dcat#Dataset"
    )


def test_predicate_field_matches_the_documented_example():
    # The EntryStore docs use dcterms:subject -> 256bd150 as their example.
    assert predicate_field(DCTERMS.subject) == "metadata.predicate.literal.256bd150"
    assert predicate_field("dcterms:subject") == "metadata.predicate.literal.256bd150"
    assert predicate_field(DCTERMS.subject, "uri") == "metadata.predicate.uri.256bd150"
    assert predicate_field(DCTERMS.subject, "related.date") == (
        "related.metadata.predicate.date.256bd150"
    )


def test_predicate_field_rejects_unknown_kinds():
    with pytest.raises(QueryError):
        predicate_field(DCTERMS.subject, "nonsense")


def test_rdf_type_accepts_curies_and_full_uris():
    assert str(Q.rdf_type("dcat:Dataset")) == str(Q.rdf_type(DCAT.Dataset))
    assert "dcat" in str(Q.rdf_type(DCAT.Dataset))


def test_and_or_compose_with_parentheses():
    combined = (Q.title("a") | Q.title("b")) & Q.public()
    assert str(combined) == "(title:a OR title:b) AND public:true"


def test_a_standalone_negation_is_anchored_to_match_all():
    # Lucene returns nothing for a purely negative query.
    assert str(~Q.public()) == "*:* AND NOT public:true"


def test_negation_on_the_right_of_and_stays_bare():
    assert str(Q.title("a") & ~Q.public()) == "title:a AND NOT public:true"


def test_negation_on_the_left_of_and_is_anchored():
    # `(NOT b) AND a` silently matches nothing without the anchor.
    assert str(~Q.public() & Q.title("a")) == "(*:* AND NOT public:true) AND title:a"


def test_negation_inside_or_is_anchored():
    assert str(Q.title("a") | ~Q.title("b")) == "title:a OR (*:* AND NOT title:b)"


def test_double_negation_nests_explicitly():
    assert str(~~Q.public()) == "*:* AND NOT (*:* AND NOT public:true)"


def test_empty_is_neutral_under_composition():
    assert str(Q.empty() & Q.public()) == "public:true"
    assert str(Q.public() & Q.empty()) == "public:true"
    assert not Q.empty()


def test_any_of_builds_one_grouped_term():
    query = Q.resource("http://a/1", "http://b/2")
    assert str(query).startswith("resource:(")
    assert " OR " in str(query)


def test_range_uses_star_for_open_bounds():
    assert str(Q.modified(dt.date(2024, 1, 1))) == "modified:[2024-01-01T00:00:00Z TO *]"
    assert str(Q.created(end="NOW")) == "created:[* TO NOW]"


def test_datetime_ranges_are_normalised_to_utc():
    moment = dt.datetime(2024, 5, 1, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    assert str(Q.modified(moment)) == "modified:[2024-05-01T10:00:00Z TO *]"


def test_publisher_and_theme_target_the_uri_index():
    assert str(Q.publisher("http://example.org/org")).startswith(
        "metadata.predicate.uri."
    )
    assert str(Q.theme("http://example.org/t")).startswith("metadata.predicate.uri.")


def test_predicate_renders_booleans_and_dates_natively():
    assert str(Q.predicate("dcterms:issued", True)).endswith(":true")
    assert str(Q.predicate("dcterms:issued", dt.date(2020, 2, 3))).endswith(
        ":2020-02-03T00:00:00Z"
    )


def test_boost_and_group():
    assert str(Q.title("x").boost(10)) == "title:x^10"
    assert str(Q.title("x").group()) == "(title:x)"


def test_join_returns_the_single_part_unwrapped():
    assert str(Q.join([Q.public()])) == "public:true"
    assert str(Q.join([])) == ""
    assert str(Q.join([Q.public(), Q.title("a")], "OR")) == "public:true OR title:a"


def test_join_anchors_a_leading_negation():
    assert str(Q.join([~Q.public(), Q.title("a")])) == (
        "(*:* AND NOT public:true) AND title:a"
    )


def test_title_can_be_language_scoped():
    assert str(Q.title("bidrag", "sv")) == "title.sv:bidrag"


# -- forms the registry actually accepts -------------------------------------


def test_bare_dates_are_filled_out_for_the_index():
    """`created:[2020-01-01 TO *]` is an HTTP 400; the index wants a timestamp."""
    assert str(Q.created("2020-01-01")) == "created:[2020-01-01T00:00:00Z TO *]"
    assert str(Q.modified("2020-01")) == "modified:[2020-01-01T00:00:00Z TO *]"
    assert str(Q.created("2020", "2021")) == (
        "created:[2020-01-01T00:00:00Z TO 2021-01-01T00:00:00Z]"
    )
    assert "2024-03-04T00:00:00Z" in str(
        Q.predicate_range("http://purl.org/dc/terms/modified", "2024-03-04"))


def test_a_full_timestamp_is_left_alone():
    assert str(Q.created("2020-01-01T12:30:00Z")) == "created:[2020-01-01T12:30:00Z TO *]"
    assert str(Q.created("NOW-7DAYS")) == "created:[NOW-7DAYS TO *]"


def test_envelope_values_are_matched_in_the_index_casing():
    """The index stores `Local`, not `local`, and matches exactly."""
    assert str(Q.entry_type("local")) == "entryType:Local"
    assert str(Q.entry_type("LINK")) == "entryType:Link"
    assert str(Q.graph_type("none")) == "graphType:None"
    assert str(Q.resource_type("informationresource")) == "resourceType:InformationResource"
    # An unknown value passes through rather than being silently mangled.
    assert str(Q.entry_type("Whatever")) == "entryType:Whatever"


def test_format_checks_both_indexes():
    """Publishers state a format as a URI or as a media-type literal."""
    uri = str(Q.format("http://publications.europa.eu/resource/authority/file-type/CSV"))
    literal = str(Q.format("text/csv"))
    assert ".uri." in uri and "CSV" in uri
    assert ".literal_s." in literal
    both = str(Q.format(
        "http://publications.europa.eu/resource/authority/file-type/CSV", "text/csv"))
    assert " OR " in both and ".uri." in both and ".literal_s." in both
