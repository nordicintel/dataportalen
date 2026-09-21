"""Vocabulary label lookup."""

from __future__ import annotations

import json

import pytest

from dataportal_se.vocab import VOCABULARY, Vocabulary

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
