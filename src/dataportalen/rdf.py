"""RDF in, plain values out.

Everything that knows about RDF lives here: the namespaces and CURIE handling,
the RDF/JSON graph parser, the bundled label table, and the short-name layer
that turns a vocabulary URI into ``"transport"`` and back.
"""

from __future__ import annotations

import datetime as _dt
import difflib
import json
import os
import re
import unicodedata
from typing import (
    Any,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

from .core import ParseError, QueryError

# ==========================================================================
# namespaces: RDF vocabularies used by DCAT-AP-SE and EntryStore.
# ==========================================================================




class Namespace(str):
    """A namespace URI that expands local names on attribute/item access.

    It is a :class:`str`, so it can be passed anywhere a URI is expected, but
    *every* non-underscore attribute expands into a term rather than doing
    what :class:`str` would do -- ``DCTERMS.title`` is the title predicate,
    not ``str.title``. Call :func:`str` on a namespace to get a plain string
    with the usual string methods back.
    """

    __slots__ = ()

    def __new__(cls, uri: str) -> "Namespace":
        return super().__new__(cls, uri)

    def __getattribute__(self, name: str) -> Any:
        # Local names like `title`, `format`, `index` and `type` collide with
        # str's methods; term expansion must win, or DCTERMS.title silently
        # becomes a bound method.
        if name.startswith("_"):
            return str.__getattribute__(self, name)
        return str.__str__(self) + name

    def __getitem__(self, name: Any) -> Any:  # type: ignore[override]
        if isinstance(name, slice):
            return str.__getitem__(self, name)
        return str.__str__(self) + name

    def __call__(self, name: str) -> str:
        return str.__str__(self) + name

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "Namespace(%s)" % (str.__repr__(self),)


RDF = Namespace("http://www.w3.org/1999/02/22-rdf-syntax-ns#")
RDFS = Namespace("http://www.w3.org/2000/01/rdf-schema#")
OWL = Namespace("http://www.w3.org/2002/07/owl#")
XSD = Namespace("http://www.w3.org/2001/XMLSchema#")

DCTERMS = Namespace("http://purl.org/dc/terms/")
DC = Namespace("http://purl.org/dc/elements/1.1/")
DCAT = Namespace("http://www.w3.org/ns/dcat#")
DCATAP = Namespace("http://data.europa.eu/r5r/")
DQV = Namespace("http://www.w3.org/ns/dqv#")
ADMS = Namespace("http://www.w3.org/ns/adms#")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")
VCARD = Namespace("http://www.w3.org/2006/vcard/ns#")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
SCHEMA = Namespace("http://schema.org/")
PROV = Namespace("http://www.w3.org/ns/prov#")
SPDX = Namespace("http://spdx.org/rdf/terms#")
TIME = Namespace("http://www.w3.org/2006/time#")
LOCN = Namespace("http://www.w3.org/ns/locn#")
ODRS = Namespace("http://schema.theodi.org/odrs#")
PROF = Namespace("http://www.w3.org/ns/dx/prof/")

#: EntryStore core terms (entry graph: metadata, resource, relation, ...).
ES = Namespace("http://entrystore.org/terms/")
#: Alias kept for readability at call sites.
ESTERMS = ES
#: EntryScape-specific terms (ServiceDistribution, MQA, LinkCheckReport, ...).
ESCAPE = Namespace("http://entryscape.com/terms/")
#: Nightly catalog statistics properties.
STATS = Namespace("http://entrystore.org/terms/statistics#")

NAMESPACES: Dict[str, Namespace] = {
    "rdf": RDF,
    "rdfs": RDFS,
    "owl": OWL,
    "xsd": XSD,
    "dcterms": DCTERMS,
    "dct": DCTERMS,
    "dc": DC,
    "dcat": DCAT,
    "dcatap": DCATAP,
    "dqv": DQV,
    "adms": ADMS,
    "foaf": FOAF,
    "vcard": VCARD,
    "skos": SKOS,
    "schema": SCHEMA,
    "prov": PROV,
    "spdx": SPDX,
    "time": TIME,
    "locn": LOCN,
    "odrs": ODRS,
    "prof": PROF,
    "es": ES,
    "escape": ESCAPE,
    "stats": STATS,
}


def expand(term: str) -> str:
    """Expand ``prefix:local`` into a full URI; pass full URIs through unchanged.

    >>> expand("dcat:Dataset")
    'http://www.w3.org/ns/dcat#Dataset'
    >>> expand("http://example.com/x")
    'http://example.com/x'
    """
    if "://" in term:
        return term
    prefix, sep, local = term.partition(":")
    if not sep:
        return term
    ns = NAMESPACES.get(prefix)
    return ns + local if ns is not None else term


def shorten(uri: str) -> str:
    """Return ``prefix:local`` for a known namespace, otherwise the URI itself."""
    best: Optional[Tuple[str, Namespace]] = None
    for prefix, ns in NAMESPACES.items():
        if uri.startswith(ns) and (best is None or len(ns) > len(best[1])):
            best = (prefix, ns)
    if best is None:
        return uri
    return f"{best[0]}:{uri[len(best[1]):]}"


# --- Well-known class URIs, handy for search filters -------------------------

class Types:
    """Frequently searched ``rdfType`` values."""

    DATASET = DCAT.Dataset
    DATASET_SERIES = DCAT.DatasetSeries
    DISTRIBUTION = DCAT.Distribution
    DATA_SERVICE = DCAT.DataService
    CATALOG = DCAT.Catalog
    AGENT = FOAF.Agent
    ORGANIZATION = FOAF.Organization
    VCARD_ORGANIZATION = VCARD.Organization
    VCARD_INDIVIDUAL = VCARD.Individual
    STANDARD = DCTERMS.Standard
    CATALOG_STATISTICS = STATS.CatalogStatistics
    LINK_CHECK_REPORT = ESCAPE.LinkCheckReport
    MQA = ESCAPE.MQA
    MQA_TOTAL = ESCAPE.MQATotal
    INDEPENDENT_DATA_SERVICE = ESCAPE.IndependentDataService
    CATALOG_CONTEXT = ESCAPE.CatalogContext
    PROFILE = PROF.Profile


# ==========================================================================
# rdf_graph: A small, dependency-free RDF/JSON model.
# ==========================================================================




#: Language codes tried, in order, when picking a single localized value.
DEFAULT_LANGUAGES: Tuple[str, ...] = ("sv", "en")

_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

_INT_TYPES = frozenset(
    [
        "integer", "int", "long", "short", "byte", "nonNegativeInteger",
        "positiveInteger", "negativeInteger", "nonPositiveInteger",
        "unsignedInt", "unsignedLong", "unsignedShort", "unsignedByte",
    ]
)


def _parse_datetime(value: str) -> Optional[_dt.datetime]:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Python < 3.11 cannot parse fractional seconds of arbitrary length.
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        return _dt.datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            return _dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_xsd(value: str, datatype: Optional[str]) -> Any:
    """Convert a literal's lexical form to a Python object using its datatype.

    Unknown or absent datatypes, and values that do not parse, are returned
    as the original :class:`str` -- this never raises.
    """
    if not datatype:
        return value
    local = datatype[len(XSD):] if datatype.startswith(XSD) else datatype
    try:
        if local in _INT_TYPES:
            return int(value)
        if local in ("decimal", "float", "double"):
            return float(value)
        if local == "boolean":
            low = value.strip().lower()
            if low in ("true", "1"):
                return True
            if low in ("false", "0"):
                return False
            return value
        if local == "date":
            match = _DATE_RE.match(value.strip())
            if match:
                return _dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            parsed = _parse_datetime(value)
            return parsed.date() if parsed else value
        if local in ("dateTime", "dateTimeStamp"):
            return _parse_datetime(value) or value
        if local == "gYear":
            return int(value.strip()[:4])
    except (TypeError, ValueError):
        return value
    return value


class Node:
    """Base class for RDF terms."""

    __slots__ = ()

    #: ``True`` for :class:`URIRef`.
    is_uri = False
    #: ``True`` for :class:`BNode`.
    is_bnode = False
    #: ``True`` for :class:`Literal`.
    is_literal = False

    @property
    def value(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def to_json(self) -> Dict[str, str]:  # pragma: no cover - overridden
        raise NotImplementedError


class URIRef(Node):
    """A resource identified by a URI."""

    __slots__ = ("_value",)
    is_uri = True

    def __init__(self, value: str) -> None:
        self._value = value

    @property
    def value(self) -> str:
        return self._value

    @property
    def uri(self) -> str:
        """Alias for :attr:`value`; reads better when following references."""
        return self._value

    def curie(self) -> str:
        """``prefix:local`` when the namespace is known, else the full URI."""
        return shorten(self._value)

    def to_json(self) -> Dict[str, str]:
        return {"type": "uri", "value": self._value}

    def __str__(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "URIRef(%r)" % (self._value,)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, URIRef):
            return self._value == other._value
        if isinstance(other, str):
            return self._value == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(("uri", self._value))


class BNode(Node):
    """A blank node; its identifier is only meaningful inside one graph."""

    __slots__ = ("_value",)
    is_bnode = True

    def __init__(self, value: str) -> None:
        self._value = value

    @property
    def value(self) -> str:
        return self._value

    def to_json(self) -> Dict[str, str]:
        return {"type": "bnode", "value": self._value}

    def __str__(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "BNode(%r)" % (self._value,)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, BNode):
            return self._value == other._value
        if isinstance(other, str):
            return self._value == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(("bnode", self._value))


class Literal(Node):
    """A literal value, optionally with a language tag or a datatype."""

    __slots__ = ("_value", "lang", "datatype")
    is_literal = True

    def __init__(
        self,
        value: str,
        lang: Optional[str] = None,
        datatype: Optional[str] = None,
    ) -> None:
        self._value = value
        self.lang = lang
        self.datatype = datatype

    @property
    def value(self) -> str:
        return self._value

    def to_python(self) -> Any:
        """The value converted according to :attr:`datatype` (never raises)."""
        return parse_xsd(self._value, self.datatype)

    def to_json(self) -> Dict[str, str]:
        out: Dict[str, str] = {"type": "literal", "value": self._value}
        if self.lang:
            out["lang"] = self.lang
        if self.datatype:
            out["datatype"] = self.datatype
        return out

    def __str__(self) -> str:
        return self._value

    def __repr__(self) -> str:
        extra = ""
        if self.lang:
            extra += ", lang=%r" % (self.lang,)
        if self.datatype:
            extra += ", datatype=%r" % (shorten(self.datatype),)
        return "Literal(%r%s)" % (self._value, extra)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Literal):
            return (self._value, self.lang, self.datatype) == (
                other._value,
                other.lang,
                other.datatype,
            )
        if isinstance(other, str):
            return self._value == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(("literal", self._value, self.lang, self.datatype))


def node_from_json(obj: Mapping[str, Any]) -> Node:
    """Build a :class:`Node` from one RDF/JSON object term."""
    try:
        kind = obj["type"]
        value = obj["value"]
    except (KeyError, TypeError) as exc:  # pragma: no cover - malformed input
        raise ParseError("not a valid RDF/JSON node: %r" % (obj,)) from exc
    if kind == "uri":
        return URIRef(value)
    if kind == "bnode":
        return BNode(value)
    if kind == "literal":
        return Literal(value, obj.get("lang") or obj.get("xml:lang"), obj.get("datatype"))
    raise ParseError("unknown RDF/JSON node type %r" % (kind,))


class Graph:
    """An immutable-ish view over an RDF/JSON document.

    ``Graph`` is intentionally thin: it indexes subjects and predicates and
    hands out :class:`Node` objects, leaving inference and reasoning out.
    """

    __slots__ = ("_data",)

    def __init__(
        self,
        data: Optional[Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]]] = None,
    ) -> None:
        self._data: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        if data:
            for subject, predicates in data.items():
                bucket = self._data.setdefault(subject, {})
                for predicate, objects in (predicates or {}).items():
                    bucket.setdefault(predicate, []).extend(dict(o) for o in objects)

    # -- construction ------------------------------------------------------

    @classmethod
    def from_json(cls, data: Optional[Mapping[str, Any]]) -> "Graph":
        return cls(data)

    def to_json(self) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        """The underlying RDF/JSON document (a deep copy)."""
        return {
            s: {p: [dict(o) for o in objs] for p, objs in preds.items()}
            for s, preds in self._data.items()
        }

    def merged(self, other: "Graph") -> "Graph":
        """A new graph containing the triples of both graphs."""
        out = Graph(self._data)
        for subject, predicates in other._data.items():
            bucket = out._data.setdefault(subject, {})
            for predicate, objects in predicates.items():
                existing = bucket.setdefault(predicate, [])
                for obj in objects:
                    if obj not in existing:
                        existing.append(dict(obj))
        return out

    # -- inspection --------------------------------------------------------

    def subjects(self) -> List[str]:
        return list(self._data)

    def named_subjects(self) -> List[str]:
        """Subjects that are URIs (blank nodes excluded)."""
        return [s for s in self._data if not s.startswith("_:")]

    def predicates(self, subject: str) -> List[str]:
        return list(self._data.get(subject, {}))

    def objects(self, subject: str, predicate: str) -> List[Node]:
        raw = self._data.get(subject, {}).get(expand(predicate), [])
        return [node_from_json(o) for o in raw]

    def subjects_of_type(self, rdf_type: str) -> List[str]:
        """Subjects carrying the given ``rdf:type``."""
        wanted = expand(rdf_type)
        return [
            subject
            for subject in self._data
            if any(o.get("value") == wanted for o in self._data[subject].get(RDF.type, []))
        ]

    def resource(self, subject: str) -> "Resource":
        return Resource(self, subject)

    def __contains__(self, subject: object) -> bool:
        return subject in self._data

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        """Number of triples."""
        return sum(len(objs) for preds in self._data.values() for objs in preds.values())

    def __bool__(self) -> bool:
        return bool(self._data)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<Graph subjects=%d triples=%d>" % (len(self._data), len(self))


def _pick_language(literals: Sequence[Literal], languages: Sequence[str]) -> Optional[Literal]:
    """Pick the best literal: first matching language, then untagged, then any."""
    if not literals:
        return None
    by_lang: Dict[Optional[str], Literal] = {}
    for lit in literals:
        by_lang.setdefault(lit.lang, lit)
    for lang in languages:
        if lang in by_lang:
            return by_lang[lang]
        # Accept regional variants such as "sv-SE" for "sv".
        for tag, lit in by_lang.items():
            if tag and tag.lower().split("-")[0] == lang.lower():
                return lit
    if None in by_lang:
        return by_lang[None]
    return literals[0]


class Resource:
    """Language-aware accessor for one subject inside a :class:`Graph`."""

    __slots__ = ("graph", "subject", "languages")

    def __init__(
        self,
        graph: Graph,
        subject: str,
        languages: Sequence[str] = DEFAULT_LANGUAGES,
    ) -> None:
        self.graph = graph
        self.subject = subject
        self.languages = tuple(languages)

    # -- identity ----------------------------------------------------------

    @property
    def uri(self) -> str:
        """The subject URI (or blank-node label)."""
        return self.subject

    @property
    def is_bnode(self) -> bool:
        return self.subject.startswith("_:")

    @property
    def types(self) -> List[str]:
        """``rdf:type`` values of this subject."""
        return self.uris(RDF.type)

    def is_a(self, rdf_type: str) -> bool:
        return expand(rdf_type) in self.types

    def with_languages(self, languages: Sequence[str]) -> "Resource":
        return Resource(self.graph, self.subject, languages)

    # -- raw access --------------------------------------------------------

    def predicates(self) -> List[str]:
        return self.graph.predicates(self.subject)

    def objects(self, predicate: str) -> List[Node]:
        """All object nodes for ``predicate`` (accepts ``prefix:local``)."""
        return self.graph.objects(self.subject, predicate)

    def first(self, predicate: str) -> Optional[Node]:
        objects = self.objects(predicate)
        return objects[0] if objects else None

    # -- literals ----------------------------------------------------------

    def literals(self, predicate: str) -> List[Literal]:
        return [n for n in self.objects(predicate) if isinstance(n, Literal)]

    def value(self, predicate: str, languages: Optional[Sequence[str]] = None) -> Optional[str]:
        """One literal value, honouring the language preference."""
        chosen = _pick_language(self.literals(predicate), languages or self.languages)
        return chosen.value if chosen else None

    def values(self, predicate: str, lang: Optional[str] = None) -> List[str]:
        """All literal values, optionally restricted to one language tag."""
        literals = self.literals(predicate)
        if lang is not None:
            wanted = lang.lower()
            literals = [
                lit for lit in literals
                if (lit.lang or "").lower().split("-")[0] == wanted
            ]
        return [lit.value for lit in literals]

    def localized(self, predicate: str) -> Dict[Optional[str], str]:
        """``{language tag or None: value}`` for a predicate."""
        out: Dict[Optional[str], str] = {}
        for lit in self.literals(predicate):
            out.setdefault(lit.lang, lit.value)
        return out

    def python(self, predicate: str) -> Any:
        """The first literal converted via its ``xsd`` datatype."""
        literals = self.literals(predicate)
        return literals[0].to_python() if literals else None

    def date(self, predicate: str) -> Optional[Union[_dt.date, _dt.datetime]]:
        """The first literal parsed as a date or datetime, when possible.

        Publishers routinely omit the ``xsd`` datatype, so the lexical form
        decides: a value with a time part becomes a
        :class:`~datetime.datetime`, otherwise a :class:`~datetime.date`.
        """
        for lit in self.literals(predicate):
            parsed = lit.to_python()
            if isinstance(parsed, (_dt.date, _dt.datetime)):
                return parsed
            guess = XSD.dateTime if "T" in lit.value else XSD.date
            parsed = parse_xsd(lit.value, guess)
            if isinstance(parsed, (_dt.date, _dt.datetime)):
                return parsed
        return None

    def integer(self, predicate: str) -> Optional[int]:
        value = self.python(predicate)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if value is None:
            return None
        try:
            return int(str(value))
        except (TypeError, ValueError):
            return None

    def boolean(self, predicate: str) -> Optional[bool]:
        value = self.python(predicate)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("true", "1"):
                return True
            if low in ("false", "0"):
                return False
        return None

    # -- references --------------------------------------------------------

    def uris(self, predicate: str) -> List[str]:
        """All object URIs for ``predicate``."""
        return [n.value for n in self.objects(predicate) if isinstance(n, URIRef)]

    def uri_of(self, predicate: str) -> Optional[str]:
        uris = self.uris(predicate)
        return uris[0] if uris else None

    def refs(self, predicate: str) -> List["Resource"]:
        """Follow ``predicate`` to subjects described in the same graph.

        URIs and blank nodes that are not described here are skipped -- use
        :meth:`uris` to see every reference, resolved or not.
        """
        out: List[Resource] = []
        for node in self.objects(predicate):
            if isinstance(node, Literal):
                continue
            if node.value in self.graph:
                out.append(Resource(self.graph, node.value, self.languages))
        return out

    def ref(self, predicate: str) -> Optional["Resource"]:
        refs = self.refs(predicate)
        return refs[0] if refs else None

    def to_dict(self, shorten_keys: bool = True) -> Dict[str, List[Any]]:
        """A plain dict of this subject, for printing and debugging."""
        out: Dict[str, List[Any]] = {}
        for predicate in self.predicates():
            key = shorten(predicate) if shorten_keys else predicate
            out[key] = [
                n.to_python() if isinstance(n, Literal) else n.value
                for n in self.objects(predicate)
            ]
        return out

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<Resource %s>" % (self.subject,)


# ==========================================================================
# vocab: Human labels for the controlled-vocabulary URIs in DCAT-AP-SE.
# ==========================================================================




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


# ==========================================================================
# terms: Short, readable values instead of URIs.
# ==========================================================================




_VOCABULARY_FILE = os.path.join(os.path.dirname(__file__), "vocabulary.json")
_ORGANISATIONS_FILE = os.path.join(os.path.dirname(__file__), "organisations.json")
#: Short names for publishers, kept by hand: `scb` for the 50-character slug.
_ALIASES_FILE = os.path.join(os.path.dirname(__file__), "aliases.json")

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


#: ISO 639-3 to 639-1, for the languages that have a two-letter code. The
#: registry names a language by its EU authority URI, whose tail is the
#: three-letter code (`.../language/SWE`); a record carries the two-letter
#: one where it exists (`sv`) and the three-letter one otherwise (`fit`,
#: Tornedalen Finnish, has no other). A publisher who wrote the ISO 639-1
#: URI directly (`id.loc.gov/vocabulary/iso639-1/sv`, 183 datasets) lands
#: on the same code instead of beside it as a second value.
_ISO_639_1 = {
    "afr": "af", "amh": "am", "ara": "ar", "aze": "az", "bak": "ba",
    "bel": "be", "ben": "bn", "bod": "bo", "bos": "bs", "bre": "br",
    "bul": "bg", "cat": "ca", "ces": "cs", "chv": "cv", "cos": "co",
    "cym": "cy", "dan": "da", "deu": "de", "dzo": "dz", "ell": "el",
    "eng": "en", "est": "et", "eus": "eu", "fao": "fo", "fas": "fa",
    "fij": "fj", "fin": "fi", "fra": "fr", "fry": "fy", "gla": "gd",
    "gle": "ga", "glg": "gl", "guj": "gu", "hau": "ha", "hbs": "sh",
    "heb": "he", "hin": "hi", "hrv": "hr", "hun": "hu", "hye": "hy",
    "ibo": "ig", "ind": "id", "isl": "is", "ita": "it", "jpn": "ja",
    "kal": "kl", "kan": "kn", "kat": "ka", "kaz": "kk", "khm": "km",
    "kin": "rw", "kir": "ky", "kor": "ko", "kur": "ku", "lao": "lo",
    "lat": "la", "lav": "lv", "lit": "lt", "ltz": "lb", "mal": "ml",
    "mar": "mr", "mkd": "mk", "mlt": "mt", "mon": "mn", "mri": "mi",
    "msa": "ms", "mya": "my", "nep": "ne", "nld": "nl", "nno": "nn",
    "nob": "nb", "nor": "no", "oci": "oc", "pan": "pa", "pol": "pl",
    "por": "pt", "pus": "ps", "ron": "ro", "rus": "ru", "sin": "si",
    "slk": "sk", "slv": "sl", "sme": "se", "smo": "sm", "som": "so",
    "spa": "es", "sqi": "sq", "srd": "sc", "srp": "sr", "swa": "sw",
    "swe": "sv", "tam": "ta", "tat": "tt", "tel": "te", "tgk": "tg",
    "tgl": "tl", "tha": "th", "tir": "ti", "ton": "to", "tuk": "tk",
    "tur": "tr", "uig": "ug", "ukr": "uk", "urd": "ur", "uzb": "uz",
    "vie": "vi", "xho": "xh", "yid": "yi", "yor": "yo", "zho": "zh",
    "zul": "zu",
}

_LANGUAGE_PREFIXES = ("http://publications.europa.eu/resource/authority/language/",
                      "https://publications.europa.eu/resource/authority/language/",
                      "http://id.loc.gov/vocabulary/iso639-1/",
                      "https://id.loc.gov/vocabulary/iso639-1/",
                      "http://id.loc.gov/vocabulary/iso639-2/",
                      "http://id.loc.gov/vocabulary/iso639-3/")


def language_code(uri: Optional[str]) -> Optional[str]:
    """The ISO code a language URI stands for, or ``None`` if it is not one.

    >>> language_code("http://publications.europa.eu/resource/authority/language/SWE")
    'sv'
    >>> language_code("http://id.loc.gov/vocabulary/iso639-1/sv")
    'sv'
    >>> language_code("http://publications.europa.eu/resource/authority/language/FIT")
    'fit'
    """
    if not uri or not uri.startswith(_LANGUAGE_PREFIXES):
        return None
    tail = uri.rstrip("/").rsplit("/", 1)[-1].lower()
    if not tail.isalpha():
        return None
    return tail if len(tail) == 2 else _ISO_639_1.get(tail, tail)


#: Terms whose short name is fixed rather than derived from the label. DIGG's
#: licence categories were `nolicense` and `otherlicense` -- their URI tails
#: -- before they had a label at all, and a label must not rename a value
#: people filter on and have written down.
_FIXED_SLUGS = {
    "https://dataportal.se/concepts/licensecategories/nolicense": "nolicense",
    "https://dataportal.se/concepts/licensecategories/otherlicense": "otherlicense",
}


def _build_terms() -> Tuple[Dict[str, List[str]], Dict[str, str]]:
    """``(slug -> [uri], uri -> slug)`` for every labelled vocabulary term."""
    labels = _load(_VOCABULARY_FILE, "labels")
    by_slug: Dict[str, List[str]] = {}
    by_uri: Dict[str, str] = {}
    for uri, translations in labels.items():
        label = translations.get("en") or translations.get("sv")
        slug = _FIXED_SLUGS.get(uri) or (slugify(label) if label else "")
        if not slug:
            continue
        code = language_code(uri)
        if code:
            # A language's short name is its ISO code, not its English name:
            # `sv`, not `swedish`. The name still resolves, as an alias.
            by_uri[uri] = code
            by_slug.setdefault(code, []).append(uri)
            by_slug.setdefault(slug, []).append(uri)
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


def _build_publishers() -> Tuple[Dict[str, List[str]], Dict[str, str]]:
    """``(slug -> [uri], uri -> slug)`` for publishers.

    A publisher is listed under its slugged name and, where it has one, its
    organisation number. The reverse map prefers the name, because that is
    what a caller wants printed back at them.
    """
    organisations = _load(_ORGANISATIONS_FILE, "organisations")
    by_slug: Dict[str, List[str]] = {}
    by_uri: Dict[str, str] = {}
    for slug, record in organisations.items():
        uri = record.get("uri")
        if not uri:
            continue
        by_slug.setdefault(slug, []).append(uri)
        if uri not in by_uri or slug.strip("se0123456789"):
            by_uri[uri] = slug
    return by_slug, by_uri


def _build_aliases() -> Dict[str, str]:
    """``alias -> publisher slug``, both slugified so lookups match input."""
    return {slugify(alias): slugify(target)
            for alias, target in _load(_ALIASES_FILE, "aliases").items()
            if alias and target}


_BY_SLUG, _BY_URI = _build_terms()
_PUBLISHERS, _PUBLISHER_BY_URI = _build_publishers()
_ALIASES = _build_aliases()


def publisher_for(uri: Optional[str]) -> Optional[str]:
    """The name to pass as ``publisher=`` for a publisher's URI.

    >>> publisher_for("http://dataportal.se/organisation/SE2021006297")
    'trafikverket'
    """
    if not uri:
        return None
    return _PUBLISHER_BY_URI.get(uri) or _PUBLISHER_BY_URI.get(uri.rstrip("/"))


def slug_for(uri: Optional[str]) -> Optional[str]:
    """The short name for a URI, for output.

    Falls back to the URI's last path segment when the term is unlabelled
    upstream, so the result is always a short string and never a URI.
    """
    if not uri:
        return None
    for form in _variants(uri):
        # The table holds one spelling of a licence; a publisher writes any
        # of several. `.../by/4.0/deed.sv` is CC BY 4.0, and reading it as
        # the tail `deed_sv` put 2,578 distributions under a licence that
        # does not exist.
        known = _BY_URI.get(form)
        if known:
            return known
    code = language_code(uri)
    if code:
        return code
    tail = uri.rstrip("/").rsplit("/", 1)[-1].rsplit("#", 1)[-1]
    return slugify(tail) or None


def label_for(slug: Optional[str]) -> Dict[str, str]:
    """The human label for a short name, as ``{"sv": ..., "en": ...}``.

    The reverse of :func:`slug_for`, for putting a readable name next to a
    facet's value. A slug usually stands for several URIs (the same
    concept written four ways), and they agree on the label, so the first one
    with any label wins. ``{}`` where the vocabulary has none -- a keyword or
    a publisher is its own label and needs no lookup.
    """
    if not slug:
        return {}
    for uri in _BY_SLUG.get(slug, ()):
        found = VOCABULARY.labels(uri)
        if found:
            return {key: value for key, value in found.items()
                    if key in DEFAULT_LANGUAGES}
    return {}


def _suggest(value: str, candidates: Sequence[str], what: str) -> QueryError:
    """The error for an unknown value, naming the likeliest few alternatives.

    The cutoff is deliberately strict: three plausible names help, whereas a
    long tail of weak matches ("landskrona" for "transprot") is just noise.
    """
    close = difflib.get_close_matches(value, candidates, n=3, cutoff=0.7)
    if not close:
        close = sorted(c for c in candidates if value in c)[:3]
    hint = (" Did you mean: %s?" % ", ".join(close)) if close else ""
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
    """The URIs for a publisher named by slug, alias or organisation number.

    >>> resolve_publisher("trafikverket")          # doctest: +SKIP
    ['http://dataportal.se/organisation/SE2021006297']
    >>> resolve_publisher("scb") == resolve_publisher(
    ...     "statistikmyndigheten_scb_statistiska_centralbyran")
    True
    """
    if not isinstance(value, str) or not value.strip():
        raise QueryError("publisher must be a non-empty string, got %r" % (value,))
    slug = slugify(value)
    slug = _ALIASES.get(slug, slug)
    found = _PUBLISHERS.get(slug)
    if not found:
        raise _suggest(slug, list(_PUBLISHERS) + list(_ALIASES), "publisher")
    return list(found)


#: Which vocabularies back each filter, as URI prefixes. Lets a caller ask
#: "what can `theme=` be?" instead of searching one flat list of 918 names.
FILTER_VOCABULARIES: Dict[str, Tuple[str, ...]] = {
    "theme": ("http://publications.europa.eu/resource/authority/data-theme/",
              "http://inspire.ec.europa.eu/theme/",
              "http://inspire.ec.europa.eu/metadata-codelist/TopicCategory/"),
    "format": ("http://publications.europa.eu/resource/authority/file-type/",
               "application/", "text/", "image/", "video/", "audio/",
               "multipart/", "message/", "model/", "chemical/", "font/"),
    "license": ("http://creativecommons.org/", "https://creativecommons.org/",
                "http://publications.europa.eu/resource/authority/licence/",
                "https://dataportal.se/concepts/licensecategories/",
                "http://opendefinition.org/", "https://opendefinition.org/"),
    "access_rights": (
        "http://publications.europa.eu/resource/authority/access-right/",),
    "updated": ("http://publications.europa.eu/resource/authority/frequency/",
                "http://purl.org/cld/freq/"),
    "language": ("http://publications.europa.eu/resource/authority/language/",
                 "http://id.loc.gov/vocabulary/iso639-1/",
                 "http://lexvo.org/id/iso639-3/"),
    "publisher_type": ("http://purl.org/adms/publishertype/",),
    "place": ("http://sws.geonames.org/", "https://sws.geonames.org/",
              "http://publications.europa.eu/resource/authority/place/",
              "http://publications.europa.eu/resource/authority/country/"),
}



def known_values(filter: Optional[str] = None, prefix: str = "") -> List[str]:
    """The short values a filter accepts, or every value this package knows.

    >>> known_values("access_rights")
    ['non_public', 'public', 'restricted']
    >>> known_values("theme", "trans")
    ['transport', 'transport_networks', 'transportation']

    With no argument it lists everything, which is long -- a filter name is
    usually what you want. :meth:`~dataportalen.Dataportal.values` gives the
    same list for the live registry, with a dataset count against each.
    """
    if filter is not None and filter not in FILTER_VOCABULARIES:
        raise _suggest(str(filter), sorted(FILTER_VOCABULARIES), "filter")
    slug = slugify(prefix) if prefix else ""
    if filter is None:
        found = set(_BY_SLUG)
    else:
        wanted = FILTER_VOCABULARIES[filter]
        found = {
            name for name, uris in _BY_SLUG.items()
            if any(uri.startswith(wanted) for uri in uris)
        }
        if filter == "language":
            # The codes, not the English names that resolve as aliases.
            found = {name for name in found
                     if any(language_code(uri) == name for uri in _BY_SLUG[name])}
    return sorted(s for s in found if not slug or slug in s)


def known_publishers(prefix: str = "") -> List[str]:
    """Every publisher name this package can resolve, aliases included."""
    slug = slugify(prefix) if prefix else ""
    names = set(_PUBLISHERS) | set(_ALIASES)
    return sorted(s for s in names if not slug or slug in s)


__all__ = [
    "Namespace",
    "RDF",
    "RDFS",
    "OWL",
    "XSD",
    "DCTERMS",
    "DC",
    "DCAT",
    "DCATAP",
    "DQV",
    "ADMS",
    "FOAF",
    "VCARD",
    "SKOS",
    "SCHEMA",
    "PROV",
    "SPDX",
    "TIME",
    "LOCN",
    "ODRS",
    "ES",
    "ESTERMS",
    "ESCAPE",
    "STATS",
    "PROF",
    "NAMESPACES",
    "expand",
    "shorten",
    "Types",
    "Node",
    "Literal",
    "URIRef",
    "BNode",
    "Graph",
    "Resource",
    "DEFAULT_LANGUAGES",
    "parse_xsd",
    "node_from_json",
    "Vocabulary",
    "VOCABULARY",
    "label",
    "labels",
    "term",
    "terms",
    "slugify",
    "slug_for",
    "language_code",
    "publisher_for",
    "resolve",
    "resolve_publisher",
    "known_values",
    "label_for",
    "FILTER_VOCABULARIES",
    "known_publishers",
]
