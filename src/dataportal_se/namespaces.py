"""RDF vocabularies used by DCAT-AP-SE and EntryStore.

Every namespace is a :class:`Namespace` instance: attribute or item access
expands a local name into a full URI.

    >>> from dataportal_se.namespaces import DCAT
    >>> DCAT.Dataset
    'http://www.w3.org/ns/dcat#Dataset'
    >>> DCAT["keyword"]
    'http://www.w3.org/ns/dcat#keyword'
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

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
]


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
