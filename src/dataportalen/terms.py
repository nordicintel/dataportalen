"""Short, readable values instead of URIs.

The registry speaks in URIs::

    http://publications.europa.eu/resource/authority/data-theme/TRAN
    http://purl.org/adms/publishertype/LocalAuthority
    http://creativecommons.org/licenses/by/4.0/

Nobody wants to type or read those, so this package doesn't use them. Every
controlled value is a short lowercase name derived from its official English
label::

    transport          local_authority          cc_by_4_0

That is the only form. Filters take it, output returns it, and there is one
spelling per concept -- not a code, a URI and a label to choose between.

    >>> from dataportalen.terms import slug_for, resolve
    >>> slug_for("http://publications.europa.eu/resource/authority/data-theme/TRAN")
    'transport'
    >>> resolve("transport")
    ['http://publications.europa.eu/resource/authority/data-theme/TRAN']

Unknown values raise with the closest matches, so a typo tells you what you
meant rather than silently returning nothing.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import unicodedata
from typing import Dict, List, Optional, Sequence, Tuple

from .exceptions import QueryError

__all__ = [
    "slugify",
    "slug_for",
    "resolve",
    "resolve_publisher",
    "known_values",
    "known_publishers",
]

_VOCABULARY_FILE = os.path.join(os.path.dirname(__file__), "vocabulary.json")
_ORGANISATIONS_FILE = os.path.join(os.path.dirname(__file__), "organisations.json")

_PARENTHETICAL = re.compile(r"\([^)]*\)")
#: Labels for file and media types carry the extension in a parenthetical,
#: e.g. "Microsoft Excel XML (.xlsx)". The extension is what a person types
#: for a format, so it is indexed as an alias for the term.
_EXTENSION = re.compile(r"\(\s*\.([A-Za-z0-9]{1,8})\s*\)")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_UNDERSCORES = re.compile(r"_+")


def slugify(text: str) -> str:
    """A short lowercase name from a label.

    >>> slugify("Local authority")
    'local_authority'
    >>> slugify("CC BY 4.0 (Attribution)")
    'cc_by_4_0'
    >>> slugify("Ekonomi och finans")
    'ekonomi_och_finans'
    """
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _PARENTHETICAL.sub(" ", text.lower())
    text = _NON_ALNUM.sub("_", text)
    return _UNDERSCORES.sub("_", text).strip("_")


def _load(path: str, key: str) -> Dict:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle).get(key) or {}
    except (OSError, ValueError):  # pragma: no cover - shipped with the package
        return {}


def _build_terms() -> Tuple[Dict[str, List[str]], Dict[str, str]]:
    """``(slug -> [uri], uri -> slug)`` for every labelled vocabulary term."""
    labels = _load(_VOCABULARY_FILE, "labels")
    by_slug: Dict[str, List[str]] = {}
    by_uri: Dict[str, str] = {}
    for uri, translations in labels.items():
        label = translations.get("en") or translations.get("sv")
        slug = slugify(label) if label else ""
        if not slug:
            continue
        by_uri[uri] = slug
        # Several vocabularies name the same concept -- file-type/PDF and
        # application/pdf both slug to "pdf". Keep them all so a filter on
        # "pdf" matches whichever one a publisher happened to use.
        by_slug.setdefault(slug, []).append(uri)
        # "Microsoft Excel XML (.xlsx)" is findable as xlsx, because that is
        # what anyone filtering by format will actually type.
        extension = _EXTENSION.search(label)
        if extension:
            alias = slugify(extension.group(1))
            if alias and alias != slug:
                by_slug.setdefault(alias, []).append(uri)
    return by_slug, by_uri


def _build_publishers() -> Dict[str, List[str]]:
    """``slug -> [uri]`` for publishers, by name and by organisation number."""
    organisations = _load(_ORGANISATIONS_FILE, "organisations")
    out: Dict[str, List[str]] = {}
    for slug, record in organisations.items():
        uri = record.get("uri")
        if uri:
            out.setdefault(slug, []).append(uri)
    return out


_BY_SLUG, _BY_URI = _build_terms()
_PUBLISHERS = _build_publishers()


def slug_for(uri: Optional[str]) -> Optional[str]:
    """The short name for a URI, for output.

    Falls back to the URI's last path segment when the term is unlabelled
    upstream, so the result is always a short string and never a URI.
    """
    if not uri:
        return None
    known = _BY_URI.get(uri) or _BY_URI.get(uri.rstrip("/"))
    if known:
        return known
    tail = uri.rstrip("/").rsplit("/", 1)[-1].rsplit("#", 1)[-1]
    return slugify(tail) or None


def _suggest(value: str, candidates: Sequence[str], what: str) -> QueryError:
    close = difflib.get_close_matches(value, candidates, n=5, cutoff=0.5)
    if not close:
        close = [c for c in candidates if value in c][:5]
    hint = ("  Did you mean: %s?" % ", ".join(close)) if close else ""
    return QueryError("unknown %s %r.%s" % (what, value, hint))


def resolve(value: str, what: str = "value") -> List[str]:
    """The URIs a short value stands for.

    :raises QueryError: if the value is not a known term, listing near misses.
    """
    if not isinstance(value, str) or not value.strip():
        raise QueryError("%s must be a non-empty string, got %r" % (what, value))
    slug = slugify(value)
    found = _BY_SLUG.get(slug)
    if not found:
        raise _suggest(slug, list(_BY_SLUG), what)
    return list(found)


def resolve_publisher(value: str) -> List[str]:
    """The URIs for a publisher named by slug or organisation number.

    >>> resolve_publisher("trafikverket")          # doctest: +SKIP
    ['http://dataportal.se/organisation/SE2021006297']
    """
    if not isinstance(value, str) or not value.strip():
        raise QueryError("publisher must be a non-empty string, got %r" % (value,))
    slug = slugify(value)
    found = _PUBLISHERS.get(slug)
    if not found:
        raise _suggest(slug, list(_PUBLISHERS), "publisher")
    return list(found)


def known_values(prefix: str = "") -> List[str]:
    """Every short value this package knows, optionally filtered by prefix.

    Handy at a prompt when you cannot remember a spelling::

        known_values("trans")   -> ['transport', 'transport_networks', ...]
    """
    slug = slugify(prefix) if prefix else ""
    return sorted(s for s in _BY_SLUG if not slug or slug in s)


def known_publishers(prefix: str = "") -> List[str]:
    """Every publisher name this package can resolve, optionally filtered."""
    slug = slugify(prefix) if prefix else ""
    return sorted(s for s in _PUBLISHERS if not slug or slug in s)
