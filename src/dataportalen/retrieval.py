"""What a distribution is: a file, an API, a web page.

DCAT-AP-SE only says that ``accessURL`` is "a web address for the
distribution". It can be a file, a web page or an API, and one distribution in
fourteen has a ``downloadURL``. So what a distribution *is* has to be read out
of everything it carries, strongest signal first:

1. a geodata format -- a map service or a geo file;
2. the shape of the address -- a rowstore, a CKAN resource, Huwise, PxWeb,
   Kolada, a DOI;
3. ``downloadURL``, which the profile says is always a file -- unless the
   format says ``html`` and the address has no file extension, because
   publishers do point it at web pages;
4. the format, for an ``accessURL``;
5. an access service, with nothing else to go on.

Nothing here makes a request. It reads metadata, so it is a statement about
what the publisher described, not about what the server returns.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

__all__ = ["KINDS", "classify"]


#: Every value :func:`classify` returns, and what it means.
KINDS: Dict[str, str] = {
    "file": "a file to download: CSV, Excel, JSON, PDF, a ZIP",
    "rowstore": "EntryScape's rowstore: a table behind an API",
    "ckan": "a CKAN resource or its API",
    "huwise": "a Huwise (Opendatasoft) dataset: records and exports",
    "pxweb": "a PxWeb statistical table",
    "kolada": "a Kolada key figure",
    "doi": "a DOI, resolving to a research-data landing page",
    "geodata": "a map service or a geo file",
    "api": "an API described by a data service",
    "web_page": "a web page; the data is behind it, not at it",
    "unknown": "the metadata does not say",
}

#: The format slugs that mean geodata, as a service or as a file.
_GEO_FORMATS = frozenset([
    "wms_service", "wfs_service", "wmts_service", "wcs_service",
    "geopackage", "geojson", "geo_jsonld", "gml_xml", "gpx",
    "x_shapefile", "shapefile", "shape", "vnd_google_earth_kml_xml",
])

_FILE_EXTENSION = re.compile(
    r"\.(csv|tsv|xlsx|xls|ods|json|zip|xml|parquet|txt|pdf|px|rdf|ttl)$",
    re.IGNORECASE)
_GEO_EXTENSION = re.compile(r"\.(geojson|gpkg|kml|kmz|shp|gml)$", re.IGNORECASE)
_GEO_ADDRESS = re.compile(
    r"service=(wfs|wms|wmts|wcs)|/(wfs|wms|wmts|wcs)(\?|/|$)|wmsserver"
    r"|/ogc/features|/arcgis/rest/services|/featureserver|/mapserver")
_PXWEB_ADDRESS = re.compile(r"pxweb|/api/v[12]/(sv|en)/")


def _by_address(url: str) -> Optional[str]:
    """The kind an address gives away by its shape, or ``None``."""
    lowered = url.lower()
    parsed = urlparse(lowered)
    host = parsed.hostname or ""
    if "/rowstore/dataset/" in lowered:
        return "rowstore"
    if re.search(r"/dataset/[^/]+/resource/", lowered) or "/api/3/action/" in lowered:
        return "ckan"
    if ("/explore/dataset/" in lowered or "/api/explore/" in lowered
            or "/api/records/" in lowered):
        return "huwise"
    if _GEO_ADDRESS.search(lowered) or _GEO_EXTENSION.search(parsed.path):
        return "geodata"
    if host == "api.scb.se" or _PXWEB_ADDRESS.search(lowered):
        return "pxweb"
    if host == "kolada.se" or host.endswith(".kolada.se"):
        return "kolada"
    if host in ("doi.org", "dx.doi.org"):
        return "doi"
    return None


def _first(value: Any) -> str:
    """A URL field as one string, whichever shape it was stored in."""
    if isinstance(value, str):
        return value
    for item in value or ():
        if item:
            return str(item)
    return ""


def _signals(dist: Dict[str, Any]) -> Tuple[str, str, str]:
    return (dist.get("format") or "", _first(dist.get("download_url")),
            _first(dist.get("access_url")))


def classify(dist: Dict[str, Any]) -> str:
    """The kind of one distribution record. One of :data:`KINDS`."""
    fmt, download, access = _signals(dist)

    if fmt in _GEO_FORMATS:
        return "geodata"

    html = fmt == "html"
    if download:
        shape = _by_address(download)
        if shape in ("geodata", "rowstore", "huwise"):
            return shape
        # The profile says downloadURL is a file. Where the publisher also
        # says the format is HTML and the address has no file extension, the
        # format is the one telling the truth.
        if html and not _FILE_EXTENSION.search(urlparse(download).path):
            return "web_page"
        return "file"

    if access:
        shape = _by_address(access)
        if shape:
            return shape
        if html:
            return "web_page"
        if fmt or _FILE_EXTENSION.search(urlparse(access).path):
            return "file"

    if dist.get("access_service_uris"):
        return "api"
    return "unknown"
