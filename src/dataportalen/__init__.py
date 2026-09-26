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

    from dataportalen import Dataportal, AsyncDataportal

    with Dataportal() as dp:
        page = dp.datasets(title="bidrag", limit=10)
        print(page.total)
        for dataset in page:
            print(dataset.title, dataset.publisher_uri)

See https://docs.dataportal.se/registry/api/ for the upstream documentation.
"""

from __future__ import annotations

from ._log import enable_logging, logger
from ._version import __version__ as _version
from .aio import AsyncDataportal
from .catalog import CatalogSummary, download_catalog
from .client import DEFAULT_BASE_URL, DUMP_URL, MAX_LIMIT, Dataportal
from .exceptions import (
    DataportalError,
    HTTPError,
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
    SearchPage,
    Standard,
    register_model,
    wrap_entry,
)
from .models import (
    PeriodOfTime as Temporal,
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
    VCARD,
    Types,
    expand,
    shorten,
)
from .query import Q, escape, escape_uri, predicate_field
from .rdf import BNode, Graph, Literal, Node, Resource, URIRef
from .terms import known_publishers, known_values, slug_for, slugify
from .transport import (
    AsyncHttpxTransport,
    BaseTransport,
    HttpxTransport,
    RequestsTransport,
    Response,
    UrllibTransport,
)
from .vocab import VOCABULARY, Vocabulary, label, labels, term, terms

#: Defined in _version.py so packaging and the User-Agent cannot drift.
__version__ = _version

__all__ = [
    "__version__",
    # clients
    "Dataportal",
    "AsyncDataportal",
    "DEFAULT_BASE_URL",
    "DUMP_URL",
    "MAX_LIMIT",
    # whole-catalogue export
    "download_catalog",
    "CatalogSummary",
    # logging
    "enable_logging",
    "logger",
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
    # short values
    "known_values",
    "known_publishers",
    "slug_for",
    "slugify",
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
    "AsyncHttpxTransport",
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
]
