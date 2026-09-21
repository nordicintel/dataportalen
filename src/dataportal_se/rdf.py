"""A small, dependency-free RDF/JSON model.

The registry returns metadata as `RDF/JSON <https://www.w3.org/TR/rdf-json/>`_::

    {"<subject>": {"<predicate>": [{"type": "literal", "value": "...",
                                    "lang": "sv", "datatype": "..."}]}}

:class:`Graph` wraps that structure and :class:`Resource` gives ergonomic,
language-aware access to a single subject within it.
"""

from __future__ import annotations

import datetime as _dt
import re
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

from .exceptions import ParseError
from .namespaces import RDF, XSD, expand, shorten

__all__ = [
    "Node",
    "Literal",
    "URIRef",
    "BNode",
    "Graph",
    "Resource",
    "DEFAULT_LANGUAGES",
    "parse_xsd",
    "node_from_json",
]

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

    def triples(self) -> Iterator[Tuple[str, str, Node]]:
        for subject, predicates in self._data.items():
            for predicate, objects in predicates.items():
                for obj in objects:
                    yield subject, predicate, node_from_json(obj)

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

    def resources(self) -> Iterator["Resource"]:
        for subject in self._data:
            yield Resource(self, subject)

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

    def pythons(self, predicate: str) -> List[Any]:
        return [lit.to_python() for lit in self.literals(predicate)]

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
