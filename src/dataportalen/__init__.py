"""Sweden's open-data catalogue as plain Python dictionaries.

The registry at ``dataportal.se`` describes its datasets in RDF, where every
value is a web address. This downloads the catalogue once and serves searches
from that copy, in short lowercase names::

    from dataportalen import Catalog

    cat = Catalog()
    page = cat.datasets(theme="transport", format="csv")
    print(page.total)                    # 72
    print(page.breakdown["publisher"])   # who publishes them
    for dataset in page:
        print(dataset["title"]["sv"], dataset["distributions"])

The first use downloads about 58 MB, once; every search after that is local
and immediate. Text comes back as ``{"sv": ..., "en": ...}`` -- both languages
when the publisher wrote both -- and everything from a controlled vocabulary
as one short English word you can filter on.

See https://docs.dataportal.se/registry/api/ for the upstream documentation.
"""

from __future__ import annotations

from .client import Catalog, default_catalog_path, read_catalog
from .core import (
    DataportalError,
    HTTPError,
    NotFoundError,
    ParseError,
    QueryError,
    RateLimitError,
    ServerError,
    TimeoutError,
    TransportError,
    enable_logging,
    logger,
)
from .core import __version__ as _version
from .models import Breakdown, Results, ValueCount, ValueList, text

__version__ = _version

__all__ = [
    # the catalogue
    "Catalog",
    "default_catalog_path",
    "read_catalog",
    # reading a record
    "text",
    # what a search gives you
    "Results",
    "Breakdown",
    "ValueList",
    "ValueCount",
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
    "__version__",
]
