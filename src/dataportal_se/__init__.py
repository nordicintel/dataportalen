"""A Python wrapper for the Sveriges dataportal registry API.

``admin.dataportal.se`` runs EntryScape Registry on top of EntryStore and
serves every dataset visible on dataportal.se, described with DCAT-AP-SE
2.0.0. This package wraps its three shapes of access:

* **Search** -- a Solr index over all entries (:meth:`Dataportal.search`,
  :meth:`Dataportal.datasets`, ...).
* **Single entries** -- by registry id or by the publisher's own URI
  (:meth:`Dataportal.entry`, :meth:`Dataportal.lookup`).
* **Operational data** -- harvest reports, nightly statistics and the full
  RDF dump.

Quick start::

    from dataportal_se import Dataportal

    with Dataportal() as dp:
        page = dp.datasets(title="bidrag", limit=10)
        print(page.total)
        for dataset in page:
            print(dataset.title, dataset.publisher_uri)

See https://docs.dataportal.se/registry/api/ for the upstream documentation.
"""

from __future__ import annotations

from .client import DEFAULT_BASE_URL, DUMP_URL, MAX_LIMIT, Dataportal
from .exceptions import (
    DataportalError,
    HTTPError,
    MissingDependencyError,
    NotFoundError,
    ParseError,
    QueryError,
    RateLimitError,
    ServerError,
    TimeoutError,
    TransportError,
)
from .models import (
    Agent,
    Catalog,
    CatalogStatistics,
    Checksum,
    ContactPoint,
    DataService,
    Dataset,
    DatasetSeries,
    Distribution,
    Entry,
    Facet,
    FacetValue,
    LinkCheckReport,
    MetadataQuality,
    OrganisationStats,
    PeriodOfTime,
    PeriodOfTime as Temporal,
    SearchPage,
    Standard,
    register_model,
    wrap_entry,
)
from .namespaces import (
    ADMS,
    DCAT,
    DCATAP,
    DCTERMS,
    ES,
    ESCAPE,
    FOAF,
    OWL,
    PROV,
    RDF,
    SKOS,
    Types,
    VCARD,
    expand,
    shorten,
)
from .query import Q, escape, escape_uri, predicate_field
from .vocab import VOCABULARY, Vocabulary, label, labels, term, terms
from .rdf import BNode, Graph, Literal, Node, Resource, URIRef
from .transport import (
    BaseTransport,
    HttpxTransport,
    RequestsTransport,
    Response,
    UrllibTransport,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # client
    "Dataportal",
    "DEFAULT_BASE_URL",
    "DUMP_URL",
    "MAX_LIMIT",
    # query
    "Q",
    "escape",
    # vocabulary labels
    "Vocabulary",
    "VOCABULARY",
    "label",
    "labels",
    "term",
    "terms",
    "escape_uri",
    "predicate_field",
    # models
    "Entry",
    "Dataset",
    "DatasetSeries",
    "Distribution",
    "DataService",
    "Catalog",
    "Agent",
    "ContactPoint",
    "Standard",
    "LinkCheckReport",
    "MetadataQuality",
    "CatalogStatistics",
    "OrganisationStats",
    "PeriodOfTime",
    "Temporal",
    "Checksum",
    "SearchPage",
    "Facet",
    "FacetValue",
    "wrap_entry",
    "register_model",
    # rdf
    "Graph",
    "Resource",
    "Node",
    "Literal",
    "URIRef",
    "BNode",
    # namespaces
    "Types",
    "expand",
    "shorten",
    "RDF",
    "DCAT",
    "DCATAP",
    "DCTERMS",
    "FOAF",
    "VCARD",
    "SKOS",
    "ADMS",
    "OWL",
    "PROV",
    "ES",
    "ESCAPE",
    # transport
    "BaseTransport",
    "UrllibTransport",
    "RequestsTransport",
    "HttpxTransport",
    "Response",
    # exceptions
    "DataportalError",
    "TransportError",
    "TimeoutError",
    "HTTPError",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
    "ParseError",
    "QueryError",
    "MissingDependencyError",
]


def __getattr__(name: str):
    """Expose the async client lazily so httpx stays optional."""
    if name in ("AsyncDataportal", "AsyncHttpxTransport"):
        from . import aio

        return getattr(aio, name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
