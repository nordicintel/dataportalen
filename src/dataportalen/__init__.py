"""Sweden's open-data catalogue as Python objects, not RDF.

The registry at ``dataportal.se`` describes its datasets in RDF, where every
value is a web address. This downloads the catalogue once and answers from
that copy, in short lowercase names::

    from dataportalen import Catalog

    catalog = Catalog()
    result = catalog.search(theme="transport", format="csv")
    print(result.total)                      # 72
    print(result.facets["publisher"])        # who publishes them
    for dataset in result.datasets:
        print(dataset.title.text(), dataset.publisher.id)

The first use downloads about 95 MB, once; every call after that is local
and immediate. What comes back is a :class:`Dataset`, :class:`DataService`,
:class:`Publisher` or :class:`Distribution` -- or, with ``as_dict=True``,
the same as plain dicts. Text is a :class:`MultilingualText` that keeps both
languages, and everything from a controlled vocabulary is one short English
word you can filter on.

See https://docs.dataportal.se/registry/api/ for the upstream documentation.
"""

from __future__ import annotations

from .client import Catalog, default_catalog_path, read_catalog
from .core import (
    DataportalError,
    DataportalWarning,
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
from .models import (
    Contact,
    DataService,
    Dataset,
    Distribution,
    Facet,
    Facets,
    FacetValue,
    Keywords,
    License,
    LinkMark,
    MultilingualText,
    Publisher,
    SearchResult,
    Temporal,
)

__version__ = _version

__all__ = [
    # the catalogue
    "Catalog",
    "default_catalog_path",
    "read_catalog",
    # what it hands back
    "Dataset",
    "DataService",
    "Distribution",
    "Publisher",
    "SearchResult",
    "MultilingualText",
    "Keywords",
    "License",
    "Contact",
    "Temporal",
    "LinkMark",
    "Facets",
    "Facet",
    "FacetValue",
    # errors and warnings
    "DataportalError",
    "TransportError",
    "TimeoutError",
    "HTTPError",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
    "ParseError",
    "QueryError",
    "DataportalWarning",
    # logging
    "logger",
    "enable_logging",
    "__version__",
]
