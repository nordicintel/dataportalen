"""Human labels for the controlled-vocabulary URIs in DCAT-AP-SE.

The registry serves vocabulary values as bare URIs::

    http://publications.europa.eu/resource/authority/data-theme/TRAN
    http://publications.europa.eu/resource/authority/frequency/ANNUAL
    http://publications.europa.eu/resource/authority/file-type/CSV

and the upstream documentation is explicit that turning those into something
readable is the client's problem. This module is the answer: it ships a
lookup table built from DIGG's own DCAT-AP-SE templates and the authority
tables those URIs dereference to, so no network call is needed at runtime.

    >>> from dataportal_se.vocab import label
    >>> label("http://publications.europa.eu/resource/authority/data-theme/TRAN")
    'Transport'

Regenerate the table with ``python tools/build_vocabulary.py``.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, Iterator, List, Mapping, Optional, Sequence

from .rdf import DEFAULT_LANGUAGES

__all__ = ["Vocabulary", "VOCABULARY", "label", "labels", "term", "terms"]

_DATA_FILE = os.path.join(os.path.dirname(__file__), "vocabulary.json")

#: Creative Commons URLs appear with a translated deed or the legal code
#: appended. Those all name the same licence, so they are stripped before
#: lookup -- a normalisation, not a guess.
_CC_SUFFIX_RE = re.compile(r"/(?:deed|legalcode)(?:\.[A-Za-z-]+)?/?$")
#: A fragment identifier is never part of the licence's identity.
_FRAGMENT_RE = re.compile(r"#.*$")


def _variants(uri: str) -> List[str]:
    """The spellings a single vocabulary URI turns up as, most exact first."""
    seen: List[str] = []

    def add(candidate: str) -> None:
        if candidate and candidate not in seen:
            seen.append(candidate)

    add(uri)
    base = _FRAGMENT_RE.sub("", uri)
    if "creativecommons.org" in base:
        base = _CC_SUFFIX_RE.sub("/", base)
    bare = base.rstrip("/")
    for form in (base, bare, bare + "/"):
        add(form)
        if form.startswith("https://"):
            other = "http://" + form[len("https://"):]
        elif form.startswith("http://"):
            other = "https://" + form[len("http://"):]
        else:
            continue
        add(other)
    return seen


class Vocabulary:
    """A URI -> ``{language: label}`` table."""

    __slots__ = ("_labels",)

    def __init__(self, labels: Optional[Mapping[str, Mapping[str, str]]] = None) -> None:
        self._labels: Dict[str, Dict[str, str]] = {
            uri: dict(values) for uri, values in (labels or {}).items()
        }

    @classmethod
    def load(cls, path: str = _DATA_FILE) -> "Vocabulary":
        """Read the shipped table; an absent file yields an empty vocabulary."""
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return cls()
        return cls(payload.get("labels") or {})

    # -- lookup ------------------------------------------------------------

    def labels(self, uri: str) -> Dict[str, str]:
        """Every known label for ``uri``, keyed by language.

        Tries the URI as given, then the variants publishers write it in --
        ``http`` vs ``https``, with or without a trailing slash, and the
        Creative Commons ``deed``/``legalcode`` suffixes.
        """
        for candidate in _variants(uri):
            found = self._labels.get(candidate)
            if found:
                return dict(found)
        return {}

    def label(
        self,
        uri: Optional[str],
        languages: Sequence[str] = DEFAULT_LANGUAGES,
    ) -> Optional[str]:
        """The best label for ``uri``, or ``None`` if the URI is unknown.

        ``None`` is deliberate: a missing label is a fact about coverage, not
        something to paper over with a guess derived from the URI.
        """
        if not uri:
            return None
        found = self.labels(uri)
        if not found:
            return None
        for lang in languages:
            if lang in found:
                return found[lang]
        for lang in DEFAULT_LANGUAGES:
            if lang in found:
                return found[lang]
        return next(iter(found.values()), None)

    def term(
        self,
        uri: Optional[str],
        languages: Sequence[str] = DEFAULT_LANGUAGES,
    ) -> Optional[Dict[str, Optional[str]]]:
        """``{"uri": ..., "label": ...}``, or ``None`` when ``uri`` is empty.

        This is the shape every vocabulary-valued field takes in the JSON
        output: the URI is always there, the label is there when known.
        """
        if not uri:
            return None
        return {"uri": uri, "label": self.label(uri, languages)}

    def terms(
        self,
        uris: Sequence[str],
        languages: Sequence[str] = DEFAULT_LANGUAGES,
    ) -> list:
        """:meth:`term` over a list, dropping empties."""
        out = []
        for uri in uris:
            entry = self.term(uri, languages)
            if entry is not None:
                out.append(entry)
        return out

    # -- introspection -----------------------------------------------------

    def covers(self, uri: str) -> bool:
        return bool(self.labels(uri))

    def __contains__(self, uri: object) -> bool:
        return isinstance(uri, str) and self.covers(uri)

    def __len__(self) -> int:
        return len(self._labels)

    def __iter__(self) -> Iterator[str]:
        return iter(self._labels)

    def to_dict(self) -> Dict[str, Dict[str, str]]:
        return {uri: dict(values) for uri, values in self._labels.items()}

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<Vocabulary %d terms>" % len(self._labels)


#: The table shipped with the package.
VOCABULARY = Vocabulary.load()


def label(uri: Optional[str], languages: Sequence[str] = DEFAULT_LANGUAGES) -> Optional[str]:
    """Best label for a vocabulary URI, or ``None`` if unknown."""
    return VOCABULARY.label(uri, languages)


def labels(uri: str) -> Dict[str, str]:
    """Every known label for a vocabulary URI, keyed by language."""
    return VOCABULARY.labels(uri)


def term(
    uri: Optional[str], languages: Sequence[str] = DEFAULT_LANGUAGES
) -> Optional[Dict[str, Optional[str]]]:
    """``{"uri": ..., "label": ...}`` for one vocabulary URI."""
    return VOCABULARY.term(uri, languages)


def terms(uris: Sequence[str], languages: Sequence[str] = DEFAULT_LANGUAGES) -> list:
    """``{"uri": ..., "label": ...}`` for each URI in a list."""
    return VOCABULARY.terms(uris, languages)
