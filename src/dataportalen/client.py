"""The catalogue: one local copy of dataportal.se, searched as plain dicts.

    >>> from dataportalen import Catalog
    >>> cat = Catalog()                               # doctest: +SKIP
    >>> page = cat.datasets(theme="transport", limit=5)   # doctest: +SKIP
    >>> page.total                                    # doctest: +SKIP
    545

:class:`Catalog` is the whole public surface. :class:`_Registry` below it is
the HTTP and Solr layer, used to download the file and to fetch one entry's
raw RDF on request -- nothing else reaches the network.
"""

from __future__ import annotations

import concurrent.futures
import datetime as _dt
import gzip
import io
import json
import os
import random
import re
import threading
import time
from typing import (
    Any,
    Callable,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Type,
    Union,
)

from .core import (
    DEFAULT_USER_AGENT,
    BaseTransport,
    HTTPError,
    NotFoundError,
    ParseError,
    QueryError,
    RateLimitError,
    Response,
    ServerError,
    TransportError,
    build_url,
    default_transport,
    enable_logging,
    logger,
    progress_reporter,
)  # noqa: F401  (NotFoundError re-exported for callers catching it here)
from .models import (
    BREAKDOWN_FILTERS,
    Agent,
    Breakdown,
    ContactPoint,
    Dataset,
    Distribution,
    Entry,
    Results,
    SearchPage,
    ValueCount,
    wrap_entry,
)
from .query import SORT_MODIFIED_DESC, Q, predicate_field
from .rdf import (
    DCAT,
    FOAF,
    SUPPORTED_LANGUAGES,
    VCARD,
    publisher_for,
    resolve,
    resolve_publisher,
    slug_for,
    slugify,
)

# ==========================================================================
# client_base: The synchronous client for the Sveriges dataportal registry API.
# ==========================================================================




#: The registry that backs dataportal.se.
DEFAULT_BASE_URL = "https://admin.dataportal.se"


#: The Solr index caps a page at 100 entries.
MAX_LIMIT = 100

_RETRY_STATUSES = frozenset([408, 425, 429, 500, 502, 503, 504])

_DateLike = Union[str, _dt.date, _dt.datetime]

_BARE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BARE_MONTH = re.compile(r"^\d{4}-\d{2}$")
_BARE_YEAR = re.compile(r"^\d{4}$")


class _LRU:
    """A tiny insertion-ordered cache; avoids a functools.lru_cache on self."""

    def __init__(self, maxsize: int = 512) -> None:
        self.maxsize = maxsize
        self._data: Dict[Any, Any] = {}

    def get(self, key: Any, default: Any = None) -> Any:
        if key in self._data:
            value = self._data.pop(key)
            self._data[key] = value
            return value
        return default

    def __contains__(self, key: Any) -> bool:
        return key in self._data

    def set(self, key: Any, value: Any) -> None:
        if self.maxsize <= 0:
            return
        if key in self._data:
            self._data.pop(key)
        elif len(self._data) >= self.maxsize:
            self._data.pop(next(iter(self._data)))
        self._data[key] = value

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


def default_catalog_path() -> str:
    """Where the catalogue is kept when no path is given.

    One copy per machine rather than per project, out of the way of any
    repository: ``%LOCALAPPDATA%\\dataportalen`` on Windows, ``$XDG_CACHE_HOME``
    or ``~/.cache/dataportalen`` elsewhere.

    One file, not one per language: every record carries both languages the
    publisher supplied, so there is nothing for a second copy to differ in.
    """
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
            os.path.expanduser("~"), ".cache")
    return os.path.join(base, "dataportalen", "catalog.jsonl")


#: A copy older than this is reported as stale. Nothing is re-downloaded on
#: its own -- a six-minute download should never surprise a running program.
STALE_AFTER_DAYS = 7

#: When :class:`Catalog` is allowed to write the file. Anything that replaces
#: 58 MB on disk is a decision made when the object is built, never a side
#: effect of something that read like a search.
_REFRESH_MODES = ("if_missing", "if_stale", "always", "never")

#: Shorthands for the RDF serializations ``get(format=...)`` accepts. Anything
#: else is passed to the registry as a media type unchanged, so a format this
#: table has not heard of still works.
_RDF_FORMATS = {
    "turtle": "text/turtle",
    "ttl": "text/turtle",
    "rdf/xml": "application/rdf+xml",
    "rdfxml": "application/rdf+xml",
    "xml": "application/rdf+xml",
    "n-triples": "application/n-triples",
    "ntriples": "application/n-triples",
    "nt": "application/n-triples",
    "json-ld": "application/ld+json",
    "jsonld": "application/ld+json",
    "trig": "application/trig",
}


class _Registry:
    """The HTTP and Solr layer for ``admin.dataportal.se`` (EntryStore).

    Internal. :class:`Catalog` owns one of these and reaches the registry
    through it -- to download the catalogue, and to fetch one entry's raw RDF
    on request. Nothing else here is part of the public API; pass your own
    ``transport`` to :class:`Catalog` if you need to control the HTTP.

    The registry is read-only and unauthenticated: everything is a GET. Every
    request is logged at ``DEBUG``, retries and rate limits at ``WARNING``.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        transport: Optional[BaseTransport] = None,
        timeout: float = 30.0,
        user_agent: Optional[str] = None,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        cache_size: int = 512,
        public_only: bool = True,
        default_sort: Optional[str] = SORT_MODIFIED_DESC,
        log_level: Optional[Any] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.backoff_factor = backoff_factor
        self.public_only = public_only
        self.default_sort = default_sort
        self.user_agent = (
            user_agent or os.environ.get("DATAPORTAL_USER_AGENT") or DEFAULT_USER_AGENT
        )
        self._transport = transport if transport is not None else default_transport()
        self._owns_transport = transport is None
        self._cache = _LRU(cache_size)
        if log_level is not None:
            enable_logging(log_level)
        logger.debug("client ready: %s (transport=%s)",
                     self.base_url, type(self._transport).__name__)

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        if self._owns_transport:
            self._transport.close()

    def __enter__(self) -> "_Registry":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<_Registry %s>" % self.base_url

    # -- plumbing ----------------------------------------------------------

    def _headers(self, accept: Optional[str]) -> Dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
        }
        if accept:
            headers["Accept"] = accept
        return headers

    @staticmethod
    def _raise_for_status(response: Response) -> None:
        if 200 <= response.status < 300:
            return
        body = response.text
        if response.status == 404:
            raise NotFoundError("not found", status=404, url=response.url, body=body,
                                headers=response.headers)
        if response.status == 429:
            retry_after = response.headers.get("retry-after")
            try:
                parsed = float(retry_after) if retry_after else None
            except ValueError:
                parsed = None
            raise RateLimitError("rate limited", status=429, url=response.url, body=body,
                                 headers=response.headers, retry_after=parsed)
        if response.status >= 500:
            raise ServerError("server error", status=response.status, url=response.url,
                              body=body, headers=response.headers)
        raise HTTPError("request failed", status=response.status, url=response.url,
                        body=body, headers=response.headers)

    def _sleep_for(self, attempt: int, retry_after: Optional[float]) -> None:
        if retry_after is not None:
            time.sleep(min(retry_after, 60.0))
            return
        delay = self.backoff_factor * (2 ** attempt)
        time.sleep(min(delay + random.uniform(0, delay * 0.1), 30.0))

    def _request(
        self,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        *,
        method: str = "GET",
        accept: Optional[str] = None,
        absolute_url: Optional[str] = None,
    ) -> Response:
        """Issue one request against the registry, with retries.

        Returns the raw :class:`~dataportalen.core.Response`; use this for
        endpoints the typed helpers do not cover.
        """
        url = absolute_url or build_url(self.base_url, path, params)
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            started = time.time()
            try:
                response = self._transport.request(
                    method, url, headers=self._headers(accept), timeout=self.timeout
                )
            except TransportError as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    logger.error("%s %s failed after %d attempts: %s",
                                 method, url, attempt + 1, exc)
                    raise
                logger.warning("%s %s failed (%s); retrying (%d/%d)",
                               method, url, exc, attempt + 1, self.max_retries)
                self._sleep_for(attempt, None)
                continue
            elapsed = time.time() - started
            logger.debug("%s %s -> %d  %.2fs  %d bytes",
                         method, url, response.status, elapsed, len(response.content))
            if response.status in _RETRY_STATUSES and attempt < self.max_retries:
                retry_after = response.headers.get("retry-after")
                try:
                    parsed = float(retry_after) if retry_after else None
                except ValueError:
                    parsed = None
                logger.warning("%s returned %d; retrying (%d/%d)%s",
                               url, response.status, attempt + 1, self.max_retries,
                               " after %ss" % parsed if parsed else "")
                self._sleep_for(attempt, parsed)
                continue
            self._raise_for_status(response)
            return response
        raise last_exc or TransportError("request to %s failed" % url)

    def _get_json(self, path: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        """GET a JSON endpoint and decode it."""
        return self._request(path, params, accept="application/json").json()

    # -- search ------------------------------------------------------------

    def _finalize_query(self, query: Union[str, Q, None]) -> str:
        parts: List[Q] = []
        if query is None or (isinstance(query, str) and not query.strip()):
            base = Q.empty()
        elif isinstance(query, Q):
            base = query
        else:
            base = Q.raw(query)
        if base:
            parts.append(base)
        if self.public_only:
            parts.append(Q.public(True))
        combined = Q.join(parts, "AND")
        return str(combined) if combined else "*:*"

    def _search_raw(
        self,
        query: Union[str, Q, None] = None,
        *,
        limit: int = 50,
        offset: int = 0,
        sort: Optional[str] = ...,  # type: ignore[assignment]
        facet_fields: Optional[Sequence[str]] = None,
        filter_query: Optional[Union[str, Q, Sequence[Union[str, Q]]]] = None,
        facet_min_count: Optional[int] = None,
        facet_limit: Optional[int] = None,
        facet_matches: Optional[str] = None,
        facet_missing: Optional[bool] = None,
        rdf_format: Optional[str] = None,
        lang: Optional[str] = None,
        extra_params: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run a Solr search and return the decoded JSON body unchanged."""
        if sort is ...:
            sort = self.default_sort
        params: Dict[str, Any] = {
            "type": "solr",
            "query": self._finalize_query(query),
            "limit": max(1, min(int(limit), MAX_LIMIT)),
            "offset": max(0, int(offset)),
        }
        if sort:
            params["sort"] = sort
        if facet_fields:
            params["facetFields"] = ",".join(facet_fields)
        if filter_query is not None:
            if isinstance(filter_query, (str, Q)):
                params["filterQuery"] = str(filter_query)
            else:
                params["filterQuery"] = ",".join(str(f) for f in filter_query)
        if facet_min_count is not None:
            params["facetMinCount"] = int(facet_min_count)
        if facet_limit is not None:
            params["facetLimit"] = int(facet_limit)
        if facet_matches is not None:
            params["facetMatches"] = facet_matches
        if facet_missing is not None:
            params["facetMissing"] = facet_missing
        if rdf_format is not None:
            params["rdfFormat"] = rdf_format
        if lang is not None:
            params["lang"] = lang
        if extra_params:
            params.update(extra_params)
        data = self._request("/store/search", params, accept="application/json").json()
        if not isinstance(data, dict):
            raise ParseError("unexpected search response: %r" % (type(data),))
        return data

    def _search(
        self,
        query: Union[str, Q, None] = None,
        *,
        model: Optional[Type[Entry]] = None,
        limit: int = 50,
        offset: int = 0,
        sort: Optional[str] = ...,  # type: ignore[assignment]
        **kwargs: Any,
    ) -> SearchPage:
        """Run a Solr search and return a :class:`~dataportalen.models.SearchPage`.

        ``model`` forces every hit into one class; by default each hit is typed
        from its own ``rdf:type``.
        """
        data = self._search_raw(query, limit=limit, offset=offset, sort=sort, **kwargs)
        return self._page_from_json(
            data,
            model=model,
            params=dict(kwargs, query=query, model=model, limit=limit, sort=sort),
        )

    def _page_from_json(
        self,
        data: Mapping[str, Any],
        *,
        model: Optional[Type[Entry]] = None,
        params: Optional[Mapping[str, Any]] = None,
    ) -> SearchPage:
        children = ((data.get("resource") or {}).get("children")) or []
        entries = [
            wrap_entry(child, client=self, default=model)
            for child in children
        ]
        return SearchPage(
            entries=entries,
            total=int(data.get("results", len(entries)) or 0),
            offset=int(data.get("offset", 0) or 0),
            limit=int(data.get("limit", len(entries)) or 0),
            raw=data,
            client=self,
            params=params,
        )

    def _count(self, query: Union[str, Q, None] = None, **kwargs: Any) -> int:
        """Estimated number of matches (Solr's count, see the docs' caveat)."""
        data = self._search_raw(query, limit=1, offset=0, sort=None, **kwargs)
        return int(data.get("results", 0) or 0)

    # -- single entries ----------------------------------------------------

    def _entry_raw(
        self,
        context_id: Union[str, int],
        entry_id: Union[str, int],
        *,
        part: str = "metadata",
        recursive: Union[bool, str] = False,
        format: Optional[str] = None,
    ) -> Response:
        """Fetch one entry in any serialization the server offers.

        ``part`` selects which graph to read: ``"metadata"`` (the DCAT
        description -- what you almost always want), ``"entry"`` (the
        EntryStore envelope: creator, timestamps, resource URI) or
        ``"resource"``.

        ``recursive=True`` (or ``"dcat"``) pulls related entities --
        distributions, the publisher, contact points -- into the same
        response. It applies to the metadata part.

        ``format`` is a media type such as ``text/turtle``,
        ``application/ld+json`` or ``application/rdf+json``; without it the
        server returns ``application/rdf+xml``.
        """
        if part not in ("metadata", "entry", "resource"):
            raise ValueError("part must be 'metadata', 'entry' or 'resource'")
        params: Dict[str, Any] = {}
        if recursive:
            params["recursive"] = "dcat" if recursive is True else recursive
        if format:
            params["format"] = format
        path = "/store/%s/%s/%s" % (context_id, part, entry_id)
        return self._request(path, params, accept=format)

    def _lookup_many(
        self,
        uris: Sequence[str],
        *,
        model: Optional[Type[Entry]] = None,
        use_cache: bool = True,
        batch_size: int = 20,
    ) -> List[Entry]:
        """Resolve several resource URIs, batching them into few requests.

        Results come back in the order of ``uris``; URIs with no matching
        entry are simply absent.
        """
        wanted = [u for u in dict.fromkeys(uris) if u]
        found: Dict[str, Entry] = {}
        pending: List[str] = []
        for uri in wanted:
            cached = self._cache.get(uri) if use_cache else None
            if cached is not None:
                found[uri] = cached
            else:
                pending.append(uri)
        for start in range(0, len(pending), batch_size):
            batch = pending[start:start + batch_size]
            page = self._search(
                Q.resource(*batch), limit=min(MAX_LIMIT, max(len(batch), 1)),
                sort=None, model=model,
            )
            for entry in page.entries:
                uri = entry.resource_uri
                if uri:
                    found[uri] = entry
                    if use_cache:
                        self._cache.set(uri, entry)
        out = [found[u] for u in wanted if u in found]
        return [e.as_(model) for e in out] if model else out


def _flatten(value: Any, resolver: Any, what: str = "value") -> List[str]:
    """Resolve one-or-many short names to the union of their URIs."""
    out: List[str] = []
    for item in _as_list(value):
        for uri in (resolver(item, what) if what != "value" else resolver(item)):
            if uri not in out:
                out.append(uri)
    return out


def _one(value: Any, resolver: Any, what: str) -> str:
    found = resolver(value, what)
    return found[0]


def _as_results(page: SearchPage, breakdown: Optional[Breakdown] = None) -> Results:
    """A page of models as the dicts every caller wanted anyway."""
    return Results(
        [entry.to_dict() for entry in page],
        total=page.total,
        offset=page.offset,
        limit=page.limit,
        facets=page.facets,
        breakdown=breakdown,
    )


def local_breakdown(
    records: Sequence[Dict[str, Any]], limit: Optional[int] = None
) -> Breakdown:
    """A breakdown counted over records in hand -- one pass, every filter."""
    tallies: Dict[str, Dict[str, int]] = {name: {} for name in BREAKDOWN_FILTERS}
    for record in records:
        for name, counts in tallies.items():
            for value in set(_local_values(record, name)):
                counts[value] = counts.get(value, 0) + 1
    return Breakdown({
        name: [ValueCount(value, count) for value, count in
               sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))]
        for name, counts in tallies.items()
    }, limit=limit)


def _any_uri(predicate: str, uris: List[str], kind: str = "uri") -> Q:
    """Match a predicate against any of several object URIs.

    ``kind="related.uri"`` looks in the graphs of related entries instead of
    this one's -- how a dataset is matched on something that lives on its
    publisher.
    """

    field = predicate_field(predicate, kind)
    if len(uris) == 1:
        return Q.term(field, uris[0])
    return Q.any_of(field, uris)


def _date(value: Any) -> Optional[str]:
    """Accept "2024-01-01", a date or a datetime; emit what Solr needs.

    A bare date used to produce an HTTP 400, which is the whole reason this
    exists: the obvious input has to be the working one.
    """
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, _dt.date):
        return value.strftime("%Y-%m-%dT00:00:00Z")
    text = str(value).strip()
    if text in ("*", "NOW") or text.startswith("NOW"):
        return text
    if _BARE_DATE.match(text):
        return text + "T00:00:00Z"
    if _BARE_MONTH.match(text):
        return text + "-01T00:00:00Z"
    if _BARE_YEAR.match(text):
        return text + "-01-01T00:00:00Z"
    return text


def _as_list(value: Any) -> List[str]:
    if isinstance(value, (str, bytes)):
        return [value if isinstance(value, str) else value.decode()]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(v) for v in value]
    return [str(value)]


# ==========================================================================
# catalog: Download the whole catalogue to a local JSONL file.
# ==========================================================================




#: Entries per request. The registry caps this at 100.
PAGE_SIZE = 100

#: The sort concurrent pages tile the corpus with. It must be a key with no
#: ties, so a page boundary always falls in the same place: `modified desc`
#: (the search default) shifts under the nightly re-harvest, and `created asc`
#: ties whenever datasets are harvested in the same instant. Walking 2,000
#: datasets returns 2,000 distinct entries under either `created asc` or
#: `uri asc` today, so the ties do no harm in practice -- `uri asc` is unique
#: per entry and removes the possibility.
STABLE_SORT = "uri asc"


class CatalogSummary:
    """What a :func:`download_catalog` run produced."""

    __slots__ = ("path", "datasets", "distributions", "bytes_written", "elapsed", "requests")

    def __init__(
        self,
        path: str,
        datasets: int,
        distributions: int,
        bytes_written: int,
        elapsed: float,
        requests: int,
    ) -> None:
        self.path = path
        self.datasets = datasets
        self.distributions = distributions
        self.bytes_written = bytes_written
        self.elapsed = elapsed
        self.requests = requests

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "datasets": self.datasets,
            "distributions": self.distributions,
            "bytes_written": self.bytes_written,
            "elapsed": round(self.elapsed, 1),
            "requests": self.requests,
        }

    def __repr__(self) -> str:
        return "<CatalogSummary %s: %d datasets, %d distributions, %.1f MiB in %.0fs>" % (
            os.path.basename(self.path),
            self.datasets,
            self.distributions,
            self.bytes_written / (1 << 20),
            self.elapsed,
        )


class _Counter:
    """A thread-safe request tally."""

    def __init__(self) -> None:
        self._value = 0
        self._lock = threading.Lock()

    def add(self, n: int = 1) -> None:
        with self._lock:
            self._value += n

    @property
    def value(self) -> int:
        return self._value


def _open_output(path: str):
    """Open ``path`` for writing text, gzipping when the name says so.

    Creates the directory if it is missing: the default location is a cache
    directory that may not exist yet, and six minutes of downloading must not
    end in a FileNotFoundError.
    """
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    if path.endswith(".gz"):
        return gzip.open(path, "wt", encoding="utf-8", newline="\n")
    return io.open(path, "w", encoding="utf-8", newline="\n")


def _pages(total: int, page_size: int = PAGE_SIZE) -> List[int]:
    """Offsets covering ``total`` entries."""
    return list(range(0, total, page_size))


def _crawl(
    client: Any,
    query: Q,
    model: Optional[type],
    total: int,
    workers: int,
    counter: _Counter,
    on_page: Optional[Callable[[int], None]] = None,
) -> Iterator[Entry]:
    """Fetch every page of a query concurrently, yielding entries.

    Pages are independent because the offsets are known up front and the sort
    is stable, so they can be fetched in parallel and stitched back together.
    """
    offsets = _pages(total)
    if not offsets:
        return

    def fetch(offset: int) -> List[Entry]:
        page = client._search(
            query, model=model, limit=PAGE_SIZE, offset=offset, sort=STABLE_SORT
        )
        counter.add()
        return list(page.entries)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for entries in pool.map(fetch, offsets):
            if on_page is not None:
                on_page(len(entries))
            for entry in entries:
                yield entry


def _index_by_uri(entries: Iterator[Entry]) -> Dict[str, Dict[str, Any]]:
    """``{resource URI: to_dict()}`` for a crawled entity type."""
    out: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        uri = entry.resource_uri
        if uri:
            out[uri] = entry.to_dict()
    return out


def _take(entries, count):
    """The first ``count`` entries of a lazy crawl."""
    for index, entry in enumerate(entries):
        if index >= count:
            return
        yield entry


def _bulk_indexes(client, workers, counter):
    """One crawl per referenced type -- the right trade for a full export."""
    distribution_total = client._count(Q.rdf_type(DCAT.Distribution))
    agent_total = client._count(Q.rdf_type(FOAF.Agent))
    contact_query = Q.rdf_type(
        VCARD.Organization, VCARD.Organisation, VCARD.Individual, VCARD.Kind
    )
    contact_total = client._count(contact_query)
    counter.add(3)

    logger.info("indexing %s distributions, %s agents, %s contact points "
                "(%d requests, this is the slow part)",
                f"{distribution_total:,}", f"{agent_total:,}", f"{contact_total:,}",
                sum(len(_pages(n)) for n in
                    (distribution_total, agent_total, contact_total)))

    started = time.time()
    distributions = _index_by_uri(_crawl(
        client, Q.rdf_type(DCAT.Distribution), Distribution,
        distribution_total, workers, counter))
    logger.info("  distributions indexed: %s (%.0fs)",
                f"{len(distributions):,}", time.time() - started)

    started = time.time()
    agents = _index_by_uri(_crawl(
        client, Q.rdf_type(FOAF.Agent), Agent, agent_total, workers, counter))
    logger.info("  agents indexed: %s (%.0fs)",
                f"{len(agents):,}", time.time() - started)

    started = time.time()
    contacts = _index_by_uri(_crawl(
        client, contact_query, ContactPoint, contact_total, workers, counter))
    logger.info("  contact points indexed: %s (%.0fs)",
                f"{len(contacts):,}", time.time() - started)
    return distributions, agents, contacts


def _targeted_indexes(client, datasets, workers, counter):
    """Resolve only the URIs this batch of datasets actually references."""
    distribution_uris = {}
    agent_uris = {}
    contact_uris = {}
    for dataset in datasets:
        for uri in dataset.distribution_uris:
            distribution_uris.setdefault(uri, None)
        if dataset.publisher_uri:
            agent_uris.setdefault(dataset.publisher_uri, None)
        for uri in dataset.contact_point_uris:
            contact_uris.setdefault(uri, None)

    def resolve(uris, model):
        if not uris:
            return {}
        found = client._lookup_many(list(uris), model=model)
        counter.add(max(1, (len(uris) + 19) // 20))
        return {e.resource_uri: e.to_dict() for e in found if e.resource_uri}

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, 3)) as pool:
        futures = [
            pool.submit(resolve, distribution_uris, Distribution),
            pool.submit(resolve, agent_uris, Agent),
            pool.submit(resolve, contact_uris, ContactPoint),
        ]
        return tuple(future.result() for future in futures)


def download_catalog(
    path: str,
    *,
    workers: int = 8,
    limit: Optional[int] = None,
    progress: Any = "auto",
    client: Any = None,
    base_url: Optional[str] = None,
) -> CatalogSummary:
    """Download every dataset to ``path`` as JSONL and return a summary.

    Each line is one dataset in the same shape as
    :meth:`~dataportalen.models.Dataset.to_dict`, with its distributions,
    publisher and contact points resolved and nested -- so a line stands on
    its own with no further lookups.

    :param path: where to write; a ``.gz`` suffix gzips the output.
    :param workers: parallel requests. The registry tolerates 8 comfortably.
    :param limit: stop after this many datasets, for smoke tests.
    :param progress: ``"auto"`` (the default) draws a live progress line on
        stderr when it is a terminal, and otherwise logs progress periodically
        -- a five-minute run should never look like a hang. ``None`` is
        silent; a callable is invoked as ``progress(done, total)``.
    :param client: an existing ``_Registry`` to reuse.
    :param base_url: registry root, when not passing ``client``.
    """
    owned = client is None
    if owned:
        kwargs = {}
        if base_url:
            kwargs["base_url"] = base_url
        client = _Registry(**kwargs)

    started_all = time.time()
    report = progress_reporter(progress, "datasets")
    counter = _Counter()
    written = 0
    bytes_written = 0
    distributions_written = 0

    try:
        dataset_total = client._count(Q.rdf_type(DCAT.Dataset))
        counter.add()
        if limit is not None:
            dataset_total = min(dataset_total, limit)
        logger.info("exporting %s datasets to %s", f"{dataset_total:,}", path)

        datasets = _crawl(
            client, Q.rdf_type(DCAT.Dataset), Dataset, dataset_total, workers, counter
        )
        missing: Dict[str, None] = {}

        if limit is None:
            # Full export: every distribution is needed anyway, so one bulk
            # crawl per referenced type beats resolving dataset by dataset.
            indexes = _bulk_indexes(client, workers, counter)
        else:
            # Partial export: crawling 35k distributions to serve a few
            # hundred datasets would dwarf the actual work, so resolve only
            # what this batch references.
            datasets = list(_take(datasets, limit))
            indexes = _targeted_indexes(client, datasets, workers, counter)

        distributions, agents, contacts = indexes

        with _open_output(path) as handle:
            for dataset in datasets:
                record, used = _assemble(
                    dataset, distributions, agents, contacts, missing
                )
                line = json.dumps(record, ensure_ascii=False) + "\n"
                handle.write(line)
                written += 1
                bytes_written += len(line.encode("utf-8"))
                distributions_written += used
                if report is not None:
                    report(written, dataset_total)

        if missing:
            # Anything the crawl did not cover -- a distribution in a
            # non-public context, say. Reported, never silently dropped.
            _report_missing(missing)


    finally:
        if owned:
            client.close()

    logger.info("wrote %s: %s datasets, %s distributions, %.1f MiB in %.0fs "
                "(%d requests)",
                path, f"{written:,}", f"{distributions_written:,}",
                bytes_written / (1 << 20), time.time() - started_all, counter.value)
    return CatalogSummary(
        path=path,
        datasets=written,
        distributions=distributions_written,
        bytes_written=bytes_written,
        elapsed=time.time() - started_all,
        requests=counter.value,
    )


def _assemble(
    dataset: Dataset,
    distributions: Dict[str, Dict[str, Any]],
    agents: Dict[str, Dict[str, Any]],
    contacts: Dict[str, Dict[str, Any]],
    missing: Dict[str, None],
) -> Tuple[Dict[str, Any], int]:
    """One dataset dict with its references spliced in from the indexes."""
    # `distributions=False`: a search hit has none inline, and we are about to
    # supply better ones from the index.
    record = dataset.to_dict(distributions=False)

    nested = []
    for uri in dataset.distribution_uris:
        found = distributions.get(uri)
        if found is not None:
            nested.append(found)
        else:
            missing.setdefault(uri, None)
    record["distributions"] = nested

    publisher_uri = dataset.publisher_uri
    if publisher_uri:
        found = agents.get(publisher_uri)
        if found is not None:
            record["publisher"] = found
        elif record.get("publisher") is None:
            record["publisher"] = {"uri": publisher_uri, "name": {}}

    if not record.get("contact_points"):
        resolved = [contacts[u] for u in dataset.contact_point_uris if u in contacts]
        if resolved:
            record["contact_points"] = resolved

    return record, len(nested)


def _report_missing(missing: Dict[str, None]) -> None:
    import warnings

    sample = list(missing)[:3]
    warnings.warn(
        "%d referenced URIs were not found in the bulk crawl and are listed by "
        "URI only (e.g. %s)" % (len(missing), ", ".join(sample)),
        stacklevel=2,
    )


__all__ = [
    "Catalog",
    "default_catalog_path",
    "DEFAULT_BASE_URL",
]


# --- the catalogue on disk ---------------------------------------------------


class Catalog:
    """Sweden's open-data catalogue, downloaded once and searched locally.

    The registry answers about two requests a second and caps a page at 100
    entries, so reading all of it takes minutes. This downloads it once and
    every search after that is local::

        from dataportalen import Catalog

        cat = Catalog()                                  # downloads on first use
        page = cat.datasets(theme="transport", format="csv")
        page.total                                       # 72
        page.breakdown["publisher"]                      # who publishes them
        for dataset in page:
            print(dataset["title"]["sv"], dataset["distributions"])

    Everything that creates or replaces the file is an argument here, so
    nothing downloads 58 MB behind a call that looked like a search.

    :param path: the JSONL file; a ``.gz`` suffix is read and written as gzip.
        Defaults to :func:`default_catalog_path`.
    :param refresh: when to download.

        ``"if_missing"`` (the default)
            download only when the file is not there. An old file is used and
            reported, never replaced behind your back.
        ``"if_stale"``
            also download when it is older than ``stale_after`` days.
        ``"always"``
            download now, whatever is there.
        ``"never"``
            never download; a missing file raises
            :class:`FileNotFoundError`. For a program that must not reach the
            network.

    :param stale_after: days before a copy counts as stale. The registry
        re-harvests nightly, so a week is already behind.
    :param progress: ``"auto"`` draws a progress line on a terminal and logs
        periodically otherwise -- a five-minute download should never look
        like a hang. ``None`` is silent; a callable gets ``(done, total)``.
    :param workers: parallel requests while downloading. Eight is comfortable.
    :param base_url: registry root, to point at another EntryStore.
    :param transport: your own :class:`~dataportalen.core.BaseTransport`.
    """

    def __init__(
        self,
        path: Optional[str] = None,
        *,
        refresh: str = "if_missing",
        stale_after: int = STALE_AFTER_DAYS,
        progress: Any = "auto",
        workers: int = 8,
        base_url: str = DEFAULT_BASE_URL,
        transport: Optional[BaseTransport] = None,
    ) -> None:
        if refresh not in _REFRESH_MODES:
            raise QueryError(
                "refresh must be one of %s; got %r"
                % (", ".join(map(repr, _REFRESH_MODES)), refresh))
        self.path = path or default_catalog_path()
        self.stale_after = int(stale_after)
        self._progress = progress
        self._workers = workers
        self._registry = _Registry(base_url, transport=transport)
        self._owns_registry = True
        self._by_uri: Optional[Dict[str, Dict[str, Any]]] = None
        self._seen: Dict[str, set] = {}
        self._records: List[Dict[str, Any]] = []
        self._load(refresh)

    # -- the file ----------------------------------------------------------

    def _load(self, refresh: str) -> None:
        """Read the file, downloading first if this mode says to."""
        exists = os.path.exists(self.path)
        if refresh == "always" or not exists:
            if refresh == "never":
                raise FileNotFoundError(
                    "%s does not exist and refresh=\"never\" forbids downloading "
                    "it; use refresh=\"if_missing\" or point path= at a copy"
                    % self.path)
            self._download()
            return
        self._read()
        if refresh == "if_stale" and self.stale:
            logger.info("%s is %d days old; refreshing", self.path, self.age_days)
            self._download()
        elif self.stale:
            # Never on its own: a program that answered in a second yesterday
            # must not block for six minutes today. The age is reported so the
            # decision stays yours.
            logger.warning(
                "%s is %d days old; the registry re-harvests nightly. Build "
                "the Catalog with refresh=\"if_stale\" for current data.",
                self.path, self.age_days)

    def _download(self) -> None:
        download_catalog(self.path, progress=self._progress,
                         workers=self._workers, client=self._registry)
        self._read()

    def _read(self) -> None:
        self._records = _read_jsonl(self.path)
        self._by_uri = None
        self._seen = {}
        logger.info("read %s records from %s", f"{len(self._records):,}", self.path)

    @property
    def downloaded(self) -> Optional[_dt.datetime]:
        """When the file was written, which is how old the data is."""
        try:
            return _dt.datetime.fromtimestamp(os.path.getmtime(self.path))
        except OSError:                                   # pragma: no cover
            return None

    @property
    def age_days(self) -> Optional[int]:
        when = self.downloaded
        return None if when is None else (_dt.datetime.now() - when).days

    @property
    def stale(self) -> bool:
        age = self.age_days
        return age is not None and age >= self.stale_after

    def info(self) -> Dict[str, Any]:
        """What this copy is and how old::

            {"path": "...\\catalog.jsonl",
             "downloaded": "2026-09-30T08:12:41",
             "age_days": 0, "stale": False, "bytes": 61203344,
             "datasets": 23575, "data_services": 599, "publishers": 365}
        """
        when = self.downloaded
        try:
            size = os.path.getsize(self.path)
        except OSError:                                   # pragma: no cover
            size = 0
        return {
            "path": self.path,
            "downloaded": when.isoformat() if when else None,
            "age_days": self.age_days,
            "stale": self.stale,
            "bytes": size,
            "datasets": len(self._records),
            "publishers": len({
                (r.get("publisher") or {}).get("uri")
                for r in self._records if (r.get("publisher") or {}).get("uri")
            }),
        }

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Release the HTTP connection. Never required -- nothing leaks."""
        if self._owns_registry:
            self._registry.close()

    def __enter__(self) -> "Catalog":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return iter(self._records)

    def __repr__(self) -> str:                            # pragma: no cover
        return "<Catalog %s: %d datasets>" % (self.path, len(self._records))

    # -- searching ---------------------------------------------------------

    def _observed(self, filter: str) -> Optional[set]:
        """Every value this file actually holds for one filter.

        What makes "the breakdown reports it, so you can filter on it" true
        even where the vocabulary table has no entry for the value.
        """
        if filter not in BREAKDOWN_FILTERS:
            return None
        if filter not in self._seen:
            self._seen[filter] = {
                value for record in self._records
                for value in _local_values(record, filter)
            }
        return self._seen[filter]

    def _matching(self, records, filters):
        tests = [_local_test(name, value, self._observed(name))
                 for name, value in filters.items()]
        return [r for r in records if all(test(r) for test in tests)]

    def datasets(
        self,
        *,
        limit: Optional[int] = 50,
        offset: int = 0,
        breakdown_limit: Optional[int] = None,
        **filters: Any,
    ) -> Results:
        """Search datasets. A list of dicts that knows its own total.

            >>> page = cat.datasets(theme="transport")     # doctest: +SKIP
            >>> page.total, page[0]["title"]["sv"]         # doctest: +SKIP
            (545, 'Farthinder')

        ``limit`` caps the rows you hold -- ``None`` for every match, ``0`` for
        the count and the breakdown alone. ``breakdown_limit`` caps each list
        in the breakdown, and what it cuts is counted in
        :attr:`~dataportalen.Breakdown.omitted`.
        """
        for unsupported in ("sort", "page_size", "query"):
            if unsupported in filters:
                raise QueryError(
                    "%r is a search-index argument; a local catalogue matches "
                    "every record at once" % unsupported)
        found = self._matching(self._records, filters)
        breakdown = local_breakdown(found, limit=breakdown_limit)
        window = found[offset:] if limit is None else found[offset:offset + limit]
        return Results(window, total=len(found), offset=offset, limit=limit,
                       breakdown=breakdown)

    def get(self, uri: str, format: str = "dict") -> Any:
        """One record by its URI, or ``None`` if this copy has no such thing.

            >>> cat.get("https://data.svk.se/dataset/c3c2...")   # doctest: +SKIP
            {'uri': 'https://data.svk.se/dataset/c3c2...', 'title': {...}, ...}

        ``format="dict"`` is local and immediate. Any other format is fetched
        from the registry as RDF -- ``"turtle"``, ``"rdf/xml"``, ``"n-triples"``
        or a media type -- and returned as text. That is the only request this
        class makes outside a download.

        Four of the 23,575 dataset URIs are shared by two records, because the
        same dataset was harvested into two catalogues; the first is returned.
        """
        if format == "dict":
            if self._by_uri is None:
                self._by_uri = {}
                for record in self._records:
                    key = record.get("uri")
                    if key and key not in self._by_uri:
                        self._by_uri[key] = record
            return self._by_uri.get(uri)

        record = self.get(uri)
        if record is None:
            return None
        return self._registry._entry_raw(
            record["context_id"], record["entry_id"],
            format=_RDF_FORMATS.get(format, format),
        ).text


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    opener = gzip.open if path.endswith(".gz") else io.open
    with opener(path, "rt", encoding="utf-8") as handle:    # type: ignore[operator]
        return [json.loads(line) for line in handle if line.strip()]


#: How each filter reads out of a record. A dataset's format lives on its
#: distributions, its publisher is only there as a URI, and the rest are
#: plain fields.
def _local_values(record: Dict[str, Any], filter: str) -> List[str]:
    if filter in ("theme", "themes"):
        return list(record.get("themes") or [])
    if filter == "language":
        # Only the two the filter supports: the rest is a long tail of
        # corpora languages, still present on the record itself.
        return [lang for lang in (record.get("languages") or [])
                if lang in SUPPORTED_LANGUAGES]
    if filter == "place":
        return list(record.get("spatial") or [])
    if filter == "keyword":
        keywords = record.get("keywords") or []
        if isinstance(keywords, dict):                      # {sv: [...], en: [...]}
            return [k for values in keywords.values() for k in values]
        return list(keywords)
    if filter == "format":
        return [d["format"] for d in (record.get("distributions") or [])
                if d.get("format")]
    if filter == "updated":
        value = record.get("accrual_periodicity")
        return [value] if value else []
    if filter in ("license", "access_rights"):
        value = record.get(filter)
        return [value] if value else []
    if filter == "publisher":
        publisher = record.get("publisher") or {}
        name = publisher_for(publisher.get("uri"))
        return [name] if name else []
    if filter == "publisher_type":
        kind = (record.get("publisher") or {}).get("type")
        return [kind] if kind else []
    raise QueryError(
        "cannot count values for %r; try one of: %s"
        % (filter, ", ".join(BREAKDOWN_FILTERS)))


def _iso_stamp(value: Any) -> str:
    """A date or timestamp as a comparable ``YYYY-MM-DDTHH:MM:SS`` string.

    Publishers write all four of ``2020-03-04``, ``...T09:00:00``,
    ``...T09:00:00+01:00`` and ``...T09:00:00.123456``; truncating to seconds
    lets them sort against each other and against a filter's bound.
    """
    if not value:
        return ""
    text = str(value)
    if len(text) == 10:
        text += "T00:00:00"
    return text[:19]


def _local_slugs(value: Any, name: str, observed: Optional[Any] = None) -> set:
    """The short names a filter value stands for, in a local catalogue.

    A vocabulary term resolves as usual. A value that is not in the table but
    *is* in the file resolves to itself: the breakdown reports whatever the
    data contains -- ``parquet``, a bare GeoNames id -- and everything it
    reports has to be usable as a filter. Only a value that is neither known
    nor present is an error, and then the suggestions come from the file.
    """
    slugs = {slugify(value)}
    try:
        slugs.update(slug_for(uri) for uri in resolve(value, name))
    except QueryError:
        if observed is None or not (slugs & set(observed)):
            raise
    return slugs


def _local_test(name: str, value: Any, observed: Optional[Any] = None) -> Any:
    """One filter as a predicate over a record."""
    if name in ("text", "title", "description"):
        needle = str(value).lower()

        def text_test(record: Dict[str, Any]) -> bool:
            if name == "text":
                parts = [record.get("title"), record.get("description")]
                parts.extend(_local_values(record, "keyword"))
            else:
                parts = [record.get(name)]
            for part in parts:
                if isinstance(part, dict):                  # a localized field
                    part = " ".join(str(v) for v in part.values())
                if part and needle in str(part).lower():
                    return True
            return False

        return text_test

    if name in ("uri", "catalog"):
        key = "uri" if name == "uri" else "context_id"
        wanted = {str(v) for v in _as_list(value)}
        return lambda record: str(record.get(key)) in wanted

    if name.endswith("_after") or name.endswith("_before"):
        field = {"updated": "modified", "published": "issued"}.get(
            name.rsplit("_", 1)[0])
        if field is None:
            raise QueryError("unknown filter %r" % (name,))
        bound = _iso_stamp(_date(value))
        after = name.endswith("_after")

        def date_test(record):
            stamp = _iso_stamp(record.get(field))
            if not stamp:
                return False
            return stamp >= bound if after else stamp <= bound

        return date_test

    if name == "keyword":
        # The index matches a keyword on substrings, so this does too.
        wanted = [str(v).lower() for v in _as_list(value)]
        return lambda record: all(
            any(w in k.lower() for k in _local_values(record, "keyword"))
            for w in wanted)

    if name == "publisher":
        wanted = set()
        for item in _as_list(value):
            resolve_publisher(item)                        # raises with a hint
            wanted.add(slugify(item))
        return lambda record: bool(
            wanted & set(_local_values(record, "publisher")))

    if name in ("theme", "format", "license", "access_rights", "updated",
                "language", "place", "publisher_type"):
        wanted = set()
        for item in _as_list(value):
            wanted.update(_local_slugs(item, name, observed))
        return lambda record: bool(wanted & set(_local_values(record, name)))

    raise QueryError(
        "unknown filter %r for a local catalogue; supported: access_rights, "
        "catalog, description, format, keyword, language, license, place, "
        "published_after, published_before, publisher, text, theme, title, "
        "updated, updated_after, updated_before, uri" % (name,))
