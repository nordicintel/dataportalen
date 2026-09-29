"""Sweden's open-data catalogue as plain Python dictionaries.

The registry at ``dataportal.se`` describes its datasets in RDF, where every
value is a web address. This package downloads the catalogue once and serves
searches from that copy, in short lowercase names::

    from dataportalen import Dataportal

    with Dataportal() as dp:
        page = dp.datasets(theme="transport", format="csv")
        print(page.total)                    # 72
        print(page.breakdown["publisher"])   # who publishes them
        for dataset in page:
            print(dataset["title"], dataset["distributions"])

The first search downloads about 58 MB, once; every search after that is
local and immediate. The registry itself is asked only for what the file
does not hold: single entries as RDF, link checks, quality scores, nightly
statistics.

See https://docs.dataportal.se/registry/api/ for the upstream documentation.
"""

from __future__ import annotations

from .client import (
    DEFAULT_BASE_URL,
    CatalogSummary,
    Dataportal,
    LocalCatalog,
    default_catalog_path,
    download_catalog,
)
from .core import (
    BaseTransport,
    DataportalError,
    HTTPError,
    NotFoundError,
    ParseError,
    QueryError,
    RateLimitError,
    RequestsTransport,
    Response,
    ServerError,
    TimeoutError,
    TransportError,
    enable_logging,
    logger,
)
from .core import __version__ as _version
from .models import (
    Breakdown,
    Dataset,
    Entry,
    Publisher,
    Results,
    ValueCount,
    ValueList,
)
from .query import Q
from .rdf import Graph, known_publishers, known_values, slug_for

__version__ = _version

__all__ = [
    # the client
    "Dataportal",
    "LocalCatalog",
    "download_catalog",
    "CatalogSummary",
    "default_catalog_path",
    "DEFAULT_BASE_URL",
    # what a search gives you
    "Results",
    "Breakdown",
    "ValueList",
    "ValueCount",
    "Publisher",
    # short values
    "known_values",
    "known_publishers",
    "slug_for",
    # errors
    "DataportalError",
    "TransportError",
    "TimeoutError",
    "HTTPError",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
    "ParseError",
    "QueryError",
    # logging
    "logger",
    "enable_logging",
    # the escape hatch: raw index queries and the RDF underneath
    "Q",
    "Entry",
    "Dataset",
    "Graph",
    # transport, for a custom HTTP stack
    "BaseTransport",
    "RequestsTransport",
    "Response",
    "__version__",
]
