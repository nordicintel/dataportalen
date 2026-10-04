"""Sweden's open-data catalogue as plain Python dictionaries.

The registry at ``dataportal.se`` describes its datasets in RDF, where every
value is a web address. This downloads the catalogue once and serves searches
from that copy, in short lowercase names::

    from dataportalen import Catalog, text

    cat = Catalog()
    page = cat.datasets(theme="transport", format="csv")
    print(page.total)                    # 72
    print(page.facets["publisher"])      # who publishes them
    for dataset in page:
        print(text(dataset["title"]), dataset["distributions"])

The first use downloads about 64 MB, once; every search after that is local
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
from .live import LiveCatalog
from .models import Facet, Facets, FacetValue, Results, text
from .records import (
    ContactRecord,
    DataServiceRecord,
    DatasetRecord,
    DistributionRecord,
    KeywordMap,
    LanguageMap,
    LicenseRecord,
    LinkMark,
    Publisher,
    PublisherDetail,
    PublisherRecord,
    TemporalRecord,
)

__version__ = _version

__all__ = [
    # the catalogue
    "Catalog",
    "default_catalog_path",
    "read_catalog",
    # the same searches, asked of the registry itself
    "LiveCatalog",
    # reading a record
    "text",
    # what a search gives you
    "Results",
    "Facets",
    "Facet",
    "FacetValue",
    # the shapes of the dicts, for editors and type checkers
    "DatasetRecord",
    "DataServiceRecord",
    "DistributionRecord",
    "PublisherRecord",
    "Publisher",
    "PublisherDetail",
    "LicenseRecord",
    "ContactRecord",
    "TemporalRecord",
    "LinkMark",
    "LanguageMap",
    "KeywordMap",
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
