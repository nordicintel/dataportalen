"""Composable Solr query building for the EntryStore search endpoint.

The registry exposes a Solr index; queries are strings in Lucene syntax with
some awkward escaping rules (every colon inside a URI must be backslashed).
:class:`Q` hides that:

    >>> from dataportalen import Q
    >>> from dataportalen.namespaces import DCAT
    >>> str(Q.rdf_type(DCAT.Dataset) & Q.public())
    'rdfType:http\\\\:\\\\/\\\\/www.w3.org\\\\/ns\\\\/dcat#Dataset AND public:true'

Operators: ``&`` (AND), ``|`` (OR), ``~`` (NOT). Free-form fragments always
remain available through :meth:`Q.raw`.

Negation needs care in Lucene: a purely negative query matches nothing, and
``a AND (NOT b)`` is silently empty. :class:`Q` tracks which fragments are
negative and anchors them to ``*:*`` wherever a positive clause is required,
so ``~Q.language("eng")`` and ``Q.title("x") & ~Q.language("eng")`` both do
what they look like they do.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Any, Iterable, List, Optional, Sequence, Union

from .exceptions import QueryError
from .namespaces import expand

__all__ = ["Q", "escape", "escape_uri", "predicate_field", "SORT_MODIFIED_DESC"]

#: Characters that carry meaning in the Solr/Lucene query parser.
_SPECIAL = set('+-!(){}[]^"~*?:\\/&|;')

SORT_MODIFIED_DESC = "modified desc"

_DateLike = Union[str, _dt.date, _dt.datetime]


def escape(value: str) -> str:
    """Backslash-escape every character special to the Solr query parser."""
    out: List[str] = []
    for char in value:
        if char in _SPECIAL:
            out.append("\\")
        out.append(char)
    return "".join(out)


def escape_uri(uri: str) -> str:
    """Escape a URI for use as a Solr term.

    The registry documentation only mentions the colon, but ``#``, ``/`` and
    friends are equally special, so everything gets escaped.
    """
    return escape(uri)


def predicate_field(predicate: str, kind: str = "literal") -> str:
    """Build a ``metadata.predicate.<kind>.<md5-8>`` field name.

    EntryStore indexes predicate/object pairs under a field whose name embeds
    the first 8 hex characters of the MD5 of the predicate URI.

    ``kind`` is one of ``literal``, ``literal_s``, ``literal_t``, ``uri``,
    ``date``, ``integer`` or ``decimal``. Prefix ``related.`` is added by
    passing ``kind="related.<kind>"``.

    >>> predicate_field("http://purl.org/dc/terms/subject")
    'metadata.predicate.literal.256bd150'
    """
    allowed = {"literal", "literal_s", "literal_t", "uri", "date", "integer", "decimal"}
    related = False
    if kind.startswith("related."):
        related = True
        kind = kind[len("related."):]
    if kind not in allowed:
        raise QueryError(
            "unknown predicate index kind %r (expected one of %s)"
            % (kind, ", ".join(sorted(allowed)))
        )
    digest = hashlib.md5(expand(predicate).encode("utf-8")).hexdigest()[:8]
    prefix = "related.metadata.predicate" if related else "metadata.predicate"
    return "%s.%s.%s" % (prefix, kind, digest)


def _fmt_date(value: _DateLike) -> str:
    if isinstance(value, _dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, _dt.date):
        return value.strftime("%Y-%m-%dT00:00:00Z")
    return str(value)


class Q:
    """An immutable Solr query fragment.

    Instances are combined with ``&``, ``|`` and ``~``; parentheses are added
    automatically where precedence requires them.
    """

    __slots__ = ("_expr", "_atomic", "_negative")

    def __init__(self, expr: str, atomic: bool = True, negative: bool = False) -> None:
        self._expr = expr
        self._atomic = atomic
        #: A purely negative fragment ("NOT x"). Lucene cannot run one on its
        #: own and silently returns nothing for ``a AND (NOT b)``, so these
        #: need special handling wherever a positive clause is expected.
        self._negative = negative

    # -- combinators -------------------------------------------------------

    def _wrapped(self) -> str:
        """This fragment where a positive, self-contained clause is required."""
        if self._negative:
            return "(*:* AND %s)" % (self._expr,)
        return self._expr if self._atomic else "(%s)" % (self._expr,)

    def _standalone(self) -> str:
        """This fragment as a complete query."""
        if self._negative:
            return "*:* AND %s" % (self._expr,)
        return self._expr

    def __and__(self, other: "Q") -> "Q":
        if not isinstance(other, Q):
            return NotImplemented
        if not self._expr:
            return other
        if not other._expr:
            return self
        # `a AND NOT b` is valid and efficient; only a *leading* negation has
        # to be anchored to a positive clause.
        right = other._expr if other._negative else other._wrapped()
        return Q("%s AND %s" % (self._wrapped(), right), atomic=False)

    def __or__(self, other: "Q") -> "Q":
        if not isinstance(other, Q):
            return NotImplemented
        if not self._expr:
            return other
        if not other._expr:
            return self
        return Q("%s OR %s" % (self._wrapped(), other._wrapped()), atomic=False)

    def __invert__(self) -> "Q":
        return Q("NOT %s" % (self._wrapped(),), atomic=True, negative=True)

    def boost(self, factor: float) -> "Q":
        """Raise this term's relevance, e.g. ``Q.title('x').boost(10)``."""
        return Q("%s^%s" % (self._wrapped(), factor), atomic=True)

    def group(self) -> "Q":
        """Force surrounding parentheses."""
        return Q("(%s)" % (self._standalone(),), atomic=True)

    # -- rendering ---------------------------------------------------------

    def __str__(self) -> str:
        """The fragment as a query you can send as-is."""
        return self._standalone()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "Q(%r)" % (self._standalone(),)

    def __bool__(self) -> bool:
        return bool(self._expr)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Q):
            return self._standalone() == other._standalone()
        if isinstance(other, str):
            return self._standalone() == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._standalone())

    # -- generic constructors ---------------------------------------------

    @classmethod
    def raw(cls, expr: str) -> "Q":
        """Use a hand-written Solr fragment verbatim (no escaping applied)."""
        return cls(expr, atomic=False)

    @classmethod
    def empty(cls) -> "Q":
        """A neutral fragment; combining it with anything returns the other."""
        return cls("", atomic=True)

    @classmethod
    def all(cls) -> "Q":
        """Match everything."""
        return cls("*:*", atomic=True)

    @classmethod
    def term(cls, field: str, value: Any, escape_value: bool = True) -> "Q":
        """``field:value`` with escaping."""
        text = value if isinstance(value, str) else str(value)
        return cls("%s:%s" % (field, escape(text) if escape_value else text))

    @classmethod
    def any_of(cls, field: str, values: Iterable[Any], escape_values: bool = True) -> "Q":
        """``field:(a OR b OR c)`` -- one round trip instead of many."""
        items = [v if isinstance(v, str) else str(v) for v in values]
        if not items:
            return cls.empty()
        rendered = " OR ".join(escape(v) if escape_values else v for v in items)
        return cls("%s:(%s)" % (field, rendered))

    @classmethod
    def range(
        cls,
        field: str,
        start: Optional[Any] = None,
        end: Optional[Any] = None,
    ) -> "Q":
        """``field:[start TO end]``; ``None`` becomes the open bound ``*``."""
        low = "*" if start is None else (_fmt_date(start) if isinstance(start, (_dt.date, _dt.datetime)) else str(start))
        high = "*" if end is None else (_fmt_date(end) if isinstance(end, (_dt.date, _dt.datetime)) else str(end))
        return cls("%s:[%s TO %s]" % (field, low, high))

    @classmethod
    def text(cls, value: str) -> "Q":
        """Free-text search against the catch-all ``all`` field."""
        return cls(escape(value), atomic=True)

    @classmethod
    def join(cls, parts: Sequence["Q"], operator: str = "AND") -> "Q":
        """Combine several fragments with one operator (``AND`` or ``OR``)."""
        usable = [p for p in parts if p]
        if not usable:
            return cls.empty()
        combined = usable[0]
        use_and = operator.strip().upper() != "OR"
        for part in usable[1:]:
            combined = combined & part if use_and else combined | part
        return combined

    # -- indexed fields ----------------------------------------------------

    @classmethod
    def rdf_type(cls, *types: str) -> "Q":
        """Filter on ``rdfType``; accepts CURIEs such as ``dcat:Dataset``."""
        expanded = [expand(t) for t in types]
        if len(expanded) == 1:
            return cls("rdfType:%s" % escape_uri(expanded[0]))
        return cls.any_of("rdfType", expanded)

    @classmethod
    def public(cls, value: bool = True) -> "Q":
        """Entries whose metadata is readable by the guest user."""
        return cls("public:%s" % ("true" if value else "false"))

    @classmethod
    def uri(cls, *uris: str) -> "Q":
        """Match on the entry URI."""
        return cls.any_of("uri", uris) if len(uris) != 1 else cls("uri:%s" % escape_uri(uris[0]))

    @classmethod
    def resource(cls, *uris: str) -> "Q":
        """Match on the resource URI -- how you look up a dataset by its own URI."""
        if len(uris) == 1:
            return cls("resource:%s" % escape_uri(uris[0]))
        return cls.any_of("resource", uris)

    @classmethod
    def context(cls, *context_uris: str) -> "Q":
        """Match on the surrounding context's resource URI."""
        if len(context_uris) == 1:
            return cls("context:%s" % escape_uri(context_uris[0]))
        return cls.any_of("context", context_uris)

    @classmethod
    def context_name(cls, name: str) -> "Q":
        return cls("contextname:%s" % escape(name))

    @classmethod
    def title(cls, value: str, lang: Optional[str] = None) -> "Q":
        """Match titles; ``lang`` narrows to e.g. ``title.sv``."""
        field = "title.%s" % lang if lang else "title"
        return cls("%s:%s" % (field, escape(value)))

    @classmethod
    def description(cls, value: str) -> "Q":
        return cls("description:%s" % escape(value))

    @classmethod
    def tag(cls, value: str, lang: Optional[str] = None) -> "Q":
        """Match a literal keyword (``dcat:keyword``, ``dcterms:subject``, ...)."""
        field = "tag.literal.%s" % lang if lang else "tag.literal"
        return cls("%s:%s" % (field, escape(value)))

    @classmethod
    def tag_uri(cls, *uris: str) -> "Q":
        """Match a keyword given as a URI (e.g. a EuroVoc concept)."""
        if len(uris) == 1:
            return cls("tag.uri:%s" % escape_uri(uris[0]))
        return cls.any_of("tag.uri", uris)

    @classmethod
    def language(cls, code: str) -> "Q":
        """Match the resource language (``dcterms:language``)."""
        return cls("lang:%s" % escape(code))

    @classmethod
    def creator(cls, uri: str) -> "Q":
        return cls("creator:%s" % escape_uri(uri))

    @classmethod
    def contributor(cls, uri: str) -> "Q":
        return cls("contributors:%s" % escape_uri(uri))

    @classmethod
    def lists(cls, uri: str) -> "Q":
        """Entries belonging to the list with the given resource URI."""
        return cls("lists:%s" % escape_uri(uri))

    @classmethod
    def graph_type(cls, value: str) -> "Q":
        """``Context``, ``List``, ``User``, ``Pipeline``, ``None``, ..."""
        return cls("graphType:%s" % escape(value))

    @classmethod
    def entry_type(cls, value: str) -> "Q":
        """``Local``, ``Link``, ``LinkReference`` or ``Reference``."""
        return cls("entryType:%s" % escape(value))

    @classmethod
    def resource_type(cls, value: str) -> "Q":
        """``InformationResource``, ``NamedResource``, ``Unknown``, ..."""
        return cls("resourceType:%s" % escape(value))

    @classmethod
    def profile(cls, uri: str) -> "Q":
        return cls("profile:%s" % escape_uri(uri))

    @classmethod
    def created(cls, start: Optional[_DateLike] = None, end: Optional[_DateLike] = None) -> "Q":
        return cls.range("created", start, end)

    @classmethod
    def modified(cls, start: Optional[_DateLike] = None, end: Optional[_DateLike] = None) -> "Q":
        return cls.range("modified", start, end)

    @classmethod
    def object_literal(cls, value: str) -> "Q":
        """Any string literal anywhere in the metadata."""
        return cls("metadata.object.literal:%s" % escape(value))

    @classmethod
    def object_uri(cls, uri: str) -> "Q":
        """Any object URI anywhere in the metadata (e.g. the publisher)."""
        return cls("metadata.object.uri:%s" % escape_uri(uri))

    # -- predicate/object pairs -------------------------------------------

    @classmethod
    def predicate(
        cls,
        predicate: str,
        value: Any,
        kind: str = "literal",
    ) -> "Q":
        """Match an exact predicate/object pair.

        ``kind`` selects the index: ``literal`` (default, n-gram),
        ``literal_s`` (exact string), ``uri``, ``date``, ``integer`` or
        ``decimal``. Use ``related.<kind>`` for the related-property index.

        >>> str(Q.predicate("dcterms:publisher", "http://example.org/a", kind="uri"))
        'metadata.predicate.uri.9259d4c1:http\\\\:\\\\/\\\\/example.org\\\\/a'
        """
        field = predicate_field(predicate, kind)
        if kind.endswith("uri"):
            return cls("%s:%s" % (field, escape_uri(str(value))))
        if isinstance(value, bool):
            return cls("%s:%s" % (field, "true" if value else "false"))
        if isinstance(value, (_dt.date, _dt.datetime)):
            return cls("%s:%s" % (field, _fmt_date(value)))
        return cls("%s:%s" % (field, escape(str(value))))

    @classmethod
    def predicate_range(
        cls,
        predicate: str,
        start: Optional[Any] = None,
        end: Optional[Any] = None,
        kind: str = "date",
    ) -> "Q":
        """Range query over a predicate's ``date``/``integer``/``decimal`` index."""
        return cls.range(predicate_field(predicate, kind), start, end)

    @classmethod
    def predicate_exists(cls, predicate: str, kind: str = "literal") -> "Q":
        """Entries that have the predicate at all."""
        return cls("%s:[* TO *]" % predicate_field(predicate, kind), atomic=True)

    # -- DCAT-AP-SE conveniences ------------------------------------------

    @classmethod
    def publisher(cls, *uris: str) -> "Q":
        """Datasets published by the given agent URI(s)."""
        field = predicate_field("http://purl.org/dc/terms/publisher", "uri")
        if len(uris) == 1:
            return cls("%s:%s" % (field, escape_uri(uris[0])))
        return cls.any_of(field, uris)

    @classmethod
    def theme(cls, *uris: str) -> "Q":
        """Datasets in the given ``dcat:theme`` (data theme URI)."""
        field = predicate_field("http://www.w3.org/ns/dcat#theme", "uri")
        if len(uris) == 1:
            return cls("%s:%s" % (field, escape_uri(uris[0])))
        return cls.any_of(field, uris)

    @classmethod
    def license(cls, *uris: str) -> "Q":
        field = predicate_field("http://purl.org/dc/terms/license", "uri")
        if len(uris) == 1:
            return cls("%s:%s" % (field, escape_uri(uris[0])))
        return cls.any_of(field, uris)

    @classmethod
    def format(cls, *uris: str) -> "Q":
        """Distributions with the given ``dcterms:format`` URI(s)."""
        field = predicate_field("http://purl.org/dc/terms/format", "uri")
        if len(uris) == 1:
            return cls("%s:%s" % (field, escape_uri(uris[0])))
        return cls.any_of(field, uris)

    @classmethod
    def media_type(cls, *uris: str) -> "Q":
        field = predicate_field("http://www.w3.org/ns/dcat#mediaType", "uri")
        if len(uris) == 1:
            return cls("%s:%s" % (field, escape_uri(uris[0])))
        return cls.any_of(field, uris)

    @classmethod
    def accrual_periodicity(cls, *uris: str) -> "Q":
        field = predicate_field("http://purl.org/dc/terms/accrualPeriodicity", "uri")
        if len(uris) == 1:
            return cls("%s:%s" % (field, escape_uri(uris[0])))
        return cls.any_of(field, uris)

    @classmethod
    def hvd_category(cls, *uris: str) -> "Q":
        """High-value dataset category (``dcatap:hvdCategory``)."""
        field = predicate_field("http://data.europa.eu/r5r/hvdCategory", "uri")
        if len(uris) == 1:
            return cls("%s:%s" % (field, escape_uri(uris[0])))
        return cls.any_of(field, uris)
