"""The synchronous client for the Sveriges dataportal registry API.

    >>> from dataportalen import Dataportal
    >>> dp = Dataportal()
    >>> page = dp.datasets(title="bidrag", limit=5)
    >>> page.total                                    # doctest: +SKIP
    122
    >>> for dataset in dp.iter_datasets(publisher="http://dataportal.se/organisation/SE2021005router"):
    ...     print(dataset.title)                      # doctest: +SKIP
"""

from __future__ import annotations

import datetime as _dt
import os
import random
import re
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

from ._log import enable_logging, logger
from .exceptions import (
    HTTPError,
    NotFoundError,
    ParseError,
    RateLimitError,
    ServerError,
    TransportError,
)  # noqa: F401  (NotFoundError re-exported for callers catching it here)
from .models import (
    Agent,
    Catalog,
    CatalogStatistics,
    DataService,
    Dataset,
    DatasetSeries,
    Distribution,
    Entry,
    Facet,
    LinkCheckReport,
    MetadataQuality,
    OrganisationStats,
    SearchPage,
    Standard,
    wrap_entry,
)
from .namespaces import DCAT, DCTERMS, FOAF, Types
from .query import Q, SORT_MODIFIED_DESC
from .terms import resolve, resolve_publisher
from .rdf import DEFAULT_LANGUAGES, Graph
from .transport import (
    DEFAULT_USER_AGENT,
    BaseTransport,
    Response,
    build_url,
    default_transport,
)

__all__ = ["Dataportal", "DEFAULT_BASE_URL", "DUMP_URL"]

#: The registry that backs dataportal.se.
DEFAULT_BASE_URL = "https://admin.dataportal.se"

#: Nightly RDF/XML dump of every dataset, per DCAT-AP-SE 2.0.0.
DUMP_URL = "https://admin.dataportal.se/all.rdf"

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


class Dataportal:
    """Client for ``admin.dataportal.se`` (EntryStore Registry).

    The registry is read-only and unauthenticated: everything here is a GET.

    :param base_url: registry root; override to point at another EntryStore.
    :param languages: preferred language order for localized values.
    :param public_only: add ``public:true`` to every search (the default, and
        what the public API effectively serves).
    :param transport: an explicit :class:`~dataportalen.transport.BaseTransport`;
        ``requests`` by default. Pass ``HttpxTransport()`` for HTTP/2.
    :param log_level: convenience -- ``"INFO"`` or ``"DEBUG"`` starts printing
        this package's log records to stderr. Leave it ``None`` and configure
        the ``dataportalen`` logger yourself if your application already has
        logging set up.

    Every request is logged at ``DEBUG``, retries and rate limits at
    ``WARNING``, so a slow or failing run explains itself::

        dp = Dataportal(log_level="DEBUG")
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        transport: Optional[BaseTransport] = None,
        timeout: float = 30.0,
        languages: Sequence[str] = DEFAULT_LANGUAGES,
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
        self.languages = tuple(languages)
        self.max_retries = max(0, int(max_retries))
        self.backoff_factor = backoff_factor
        self.public_only = public_only
        self.default_sort = default_sort
        self.user_agent = user_agent or os.environ.get("DATAPORTAL_USER_AGENT") or DEFAULT_USER_AGENT
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

    def __enter__(self) -> "Dataportal":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<Dataportal %s>" % self.base_url

    def clear_cache(self) -> None:
        """Drop memoized URI lookups."""
        self._cache.clear()

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

    def request(
        self,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        *,
        method: str = "GET",
        accept: Optional[str] = None,
        absolute_url: Optional[str] = None,
    ) -> Response:
        """Issue one request against the registry, with retries.

        Returns the raw :class:`~dataportalen.transport.Response`; use this for
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

    def get_json(self, path: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        """GET a JSON endpoint and decode it."""
        return self.request(path, params, accept="application/json").json()

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

    def search_raw(
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
        data = self.request("/store/search", params, accept="application/json").json()
        if not isinstance(data, dict):
            raise ParseError("unexpected search response: %r" % (type(data),))
        return data

    def search(
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
        data = self.search_raw(query, limit=limit, offset=offset, sort=sort, **kwargs)
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
            wrap_entry(child, client=self, languages=self.languages, default=model)
            for child in children
        ]
        facets = [Facet.from_json(f) for f in (data.get("facetFields") or [])]
        return SearchPage(
            entries=entries,
            total=int(data.get("results", len(entries)) or 0),
            offset=int(data.get("offset", 0) or 0),
            limit=int(data.get("limit", len(entries)) or 0),
            facets=facets,
            raw=data,
            client=self,
            params=params,
        )

    def iter_search(
        self,
        query: Union[str, Q, None] = None,
        *,
        model: Optional[Type[Entry]] = None,
        limit: Optional[int] = None,
        page_size: int = MAX_LIMIT,
        offset: int = 0,
        sort: Optional[str] = ...,  # type: ignore[assignment]
        **kwargs: Any,
    ) -> Iterator[Entry]:
        """Iterate over every hit, fetching pages as needed.

        ``limit`` caps the total number of entries yielded (``None`` = all);
        ``page_size`` controls the request size.

        Paging deep into a large result set is inherently racy -- the index
        changes nightly -- so sort by something stable (the default
        ``modified desc`` is not) if exactness matters.
        """
        if sort is ...:
            sort = self.default_sort
        yielded = 0
        current = max(0, int(offset))
        while True:
            want = min(page_size, MAX_LIMIT)
            if limit is not None:
                want = min(want, limit - yielded)
                if want <= 0:
                    return
            page = self.search(
                query, model=model, limit=want, offset=current, sort=sort, **kwargs
            )
            if not page.entries:
                return
            for entry in page.entries:
                yield entry
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            current += len(page.entries)
            if current >= page.total:
                return

    def count(self, query: Union[str, Q, None] = None, **kwargs: Any) -> int:
        """Estimated number of matches (Solr's count, see the docs' caveat)."""
        data = self.search_raw(query, limit=1, offset=0, sort=None, **kwargs)
        return int(data.get("results", 0) or 0)

    def facet(
        self,
        field: str,
        query: Union[str, Q, None] = None,
        *,
        limit: int = 100,
        min_count: int = 1,
        matches: Optional[str] = None,
        **kwargs: Any,
    ) -> Facet:
        """Count distinct values of one indexed field."""
        page = self.search(
            query,
            limit=1,
            sort=None,
            facet_fields=[field],
            facet_limit=limit,
            facet_min_count=min_count,
            facet_matches=matches,
            **kwargs,
        )
        return page.facet(field) or Facet(field, [])

    # -- single entries ----------------------------------------------------

    def entry_raw(
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
        return self.request(path, params, accept=format)

    def metadata_graph(
        self,
        context_id: Union[str, int],
        entry_id: Union[str, int],
        *,
        recursive: Union[bool, str] = True,
    ) -> Graph:
        """The metadata graph of one entry, as RDF/JSON."""
        response = self.entry_raw(
            context_id, entry_id, part="metadata", recursive=recursive,
            format="application/rdf+json",
        )
        return Graph(response.json())

    def entry_graph(self, context_id: Union[str, int], entry_id: Union[str, int]) -> Graph:
        """The EntryStore envelope graph (creator, created/modified, resource URI)."""
        response = self.entry_raw(
            context_id, entry_id, part="entry", format="application/rdf+json"
        )
        return Graph(response.json())

    def entry(
        self,
        context_id: Union[str, int],
        entry_id: Union[str, int],
        *,
        recursive: Union[bool, str] = True,
        with_info: bool = True,
        model: Optional[Type[Entry]] = None,
        info: Optional[Graph] = None,
        rights: Sequence[str] = (),
    ) -> Entry:
        """Fetch one entry by its registry ids, as a typed model.

        Metadata and the EntryStore envelope live behind separate URLs, so
        ``with_info=True`` (the default) costs a second request. Pass
        ``with_info=False`` -- or an ``info`` graph you already hold, e.g.
        from a search hit -- to make do with one.
        """
        metadata = self.metadata_graph(context_id, entry_id, recursive=recursive)
        if info is None and with_info:
            info = self.entry_graph(context_id, entry_id)
        payload: Dict[str, Any] = {
            "metadata": metadata.to_json(),
            "contextId": str(context_id),
            "entryId": str(entry_id),
        }
        if info is not None:
            payload["info"] = info.to_json()
        if rights:
            payload["rights"] = list(rights)
        return wrap_entry(payload, client=self, languages=self.languages, default=model)

    def lookup(
        self,
        uri: str,
        *,
        model: Optional[Type[Entry]] = None,
        use_cache: bool = True,
    ) -> Optional[Entry]:
        """Find a managed entry by its resource URI.

        This is how you go from a URI found inside someone's metadata -- a
        publisher, a distribution, a data service -- to the entry describing
        it, as the registry documentation describes.
        """
        if use_cache:
            cached = self._cache.get(uri)
            if cached is not None:
                return cached.as_(model) if model else cached
        page = self.search(Q.resource(uri), limit=1, sort=None, model=model)
        found = page.entries[0] if page.entries else None
        if found is not None and use_cache:
            self._cache.set(uri, found)
        return found

    def lookup_many(
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
            page = self.search(
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

    # -- typed searches ----------------------------------------------------

    def _entity_query(
        self,
        rdf_type: Optional[str],
        *,
        text: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        keyword: Optional[Union[str, Sequence[str]]] = None,
        publisher: Optional[Union[str, Sequence[str]]] = None,
        theme: Optional[Union[str, Sequence[str]]] = None,
        format: Optional[Union[str, Sequence[str]]] = None,
        license: Optional[Union[str, Sequence[str]]] = None,
        access_rights: Optional[str] = None,
        updated: Optional[str] = None,
        language: Optional[Union[str, Sequence[str]]] = None,
        place: Optional[Union[str, Sequence[str]]] = None,
        updated_after: Optional[_DateLike] = None,
        updated_before: Optional[_DateLike] = None,
        published_after: Optional[_DateLike] = None,
        published_before: Optional[_DateLike] = None,
        harvested_after: Optional[_DateLike] = None,
        harvested_before: Optional[_DateLike] = None,
        catalog: Optional[Union[str, int, Sequence[Union[str, int]]]] = None,
        uri: Optional[Union[str, Sequence[str]]] = None,
        query: Union[str, Q, None] = None,
    ) -> Q:
        """Build the Solr query for a set of keyword filters.

        Controlled values are short names (``theme="transport"``), never URIs;
        see :mod:`dataportalen.terms`. Dates accept ``"2024-01-01"``, a
        ``date`` or a ``datetime``.
        """
        parts: List[Q] = []
        if rdf_type:
            parts.append(Q.rdf_type(rdf_type))
        if query is not None:
            parts.append(query if isinstance(query, Q) else Q.raw(query))
        if text:
            parts.append(Q.text(text))
        if title:
            parts.append(Q.title(title))
        if description:
            parts.append(Q.description(description))
        if keyword:
            words = [keyword] if isinstance(keyword, str) else list(keyword)
            parts.append(Q.join([Q.tag(w) for w in words], "AND"))

        # Short names -> the URIs publishers actually used.
        if publisher:
            parts.append(Q.publisher(*_flatten(publisher, resolve_publisher)))
        if theme:
            parts.append(Q.theme(*_flatten(theme, resolve, "theme")))
        if format:
            parts.append(_format_query(_flatten(format, resolve, "format")))
        if license:
            parts.append(Q.license(*_flatten(license, resolve, "license")))
        if access_rights:
            parts.append(Q.predicate(
                DCTERMS.accessRights,
                _one(access_rights, resolve, "access_rights"), kind="uri"))
        if updated:
            parts.append(Q.accrual_periodicity(*_flatten(updated, resolve, "updated")))
        if language:
            parts.append(_any_uri(
                DCTERMS.language, _flatten(language, resolve, "language")))
        if place:
            parts.append(_any_uri(DCTERMS.spatial, _flatten(place, resolve, "place")))

        # Dates. `updated`/`published` are the publisher's own; `harvested` is
        # this registry's bookkeeping, which changes nightly for everything.
        if updated_after is not None or updated_before is not None:
            parts.append(Q.predicate_range(
                DCTERMS.modified, _date(updated_after), _date(updated_before)))
        if published_after is not None or published_before is not None:
            parts.append(Q.predicate_range(
                DCTERMS.issued, _date(published_after), _date(published_before)))
        if harvested_after is not None or harvested_before is not None:
            parts.append(Q.modified(_date(harvested_after), _date(harvested_before)))

        if catalog is not None:
            parts.append(Q.context(*[self.context_uri(c) for c in _as_list(catalog)]))
        if uri:
            parts.append(Q.resource(*_as_list(uri)))
        return Q.join(parts, "AND")

    def context_uri(self, context_id: Union[str, int]) -> str:
        """The resource URI of a context, as indexed in the ``context`` field."""
        text = str(context_id)
        if "://" in text:
            return text
        return "%s/store/%s" % (self.base_url, text)

    def datasets(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        sort: Optional[str] = ...,  # type: ignore[assignment]
        facet_fields: Optional[Sequence[str]] = None,
        **filters: Any,
    ) -> SearchPage:
        """Search datasets (``dcat:Dataset``); returns one page."""
        return self.search(
            self._entity_query(DCAT.Dataset, **filters),
            model=Dataset,
            limit=limit,
            offset=offset,
            sort=sort,
            facet_fields=facet_fields,
        )

    def iter_datasets(
        self,
        *,
        limit: Optional[int] = None,
        page_size: int = MAX_LIMIT,
        sort: Optional[str] = ...,  # type: ignore[assignment]
        **filters: Any,
    ) -> Iterator[Dataset]:
        """Iterate over matching datasets across pages."""
        return self.iter_search(  # type: ignore[return-value]
            self._entity_query(DCAT.Dataset, **filters),
            model=Dataset,
            limit=limit,
            page_size=page_size,
            sort=sort,
        )

    def dataset(
        self,
        uri: Optional[str] = None,
        *,
        context_id: Optional[Union[str, int]] = None,
        entry_id: Optional[Union[str, int]] = None,
        recursive: bool = True,
    ) -> Optional[Dataset]:
        """Fetch a single dataset, by its own URI or by registry ids.

        With ``recursive`` (the default for the id form) the distributions,
        publisher and contact points come along in the same request.
        """
        if uri:
            found = self.lookup(uri, model=Dataset)
            if found is None:
                return None
            if recursive and found.context_id and found.entry_id:
                # The search hit already carries the envelope; reuse it so the
                # recursive fetch costs exactly one extra request.
                return self.entry(
                    found.context_id, found.entry_id, recursive=True,
                    model=Dataset, info=found.info, rights=found.rights,
                )  # type: ignore[return-value]
            return found  # type: ignore[return-value]
        if context_id is None or entry_id is None:
            raise TypeError("pass either uri= or both context_id= and entry_id=")
        return self.entry(context_id, entry_id, recursive=recursive, model=Dataset)  # type: ignore[return-value]

    def datasets_in_context(
        self,
        context_id: Union[str, int],
        *,
        limit: Optional[int] = None,
        page_size: int = MAX_LIMIT,
    ) -> Iterator[Dataset]:
        """Every dataset harvested into one catalog context."""
        return self.iter_datasets(context=context_id, limit=limit, page_size=page_size)

    def distributions(self, *, limit: int = 50, offset: int = 0, **filters: Any) -> SearchPage:
        """Search distributions (``dcat:Distribution``)."""
        return self.search(
            self._entity_query(DCAT.Distribution, **filters),
            model=Distribution, limit=limit, offset=offset,
        )

    def iter_distributions(self, *, limit: Optional[int] = None, **filters: Any) -> Iterator[Distribution]:
        return self.iter_search(  # type: ignore[return-value]
            self._entity_query(DCAT.Distribution, **filters), model=Distribution, limit=limit
        )

    def data_services(self, *, limit: int = 50, offset: int = 0, **filters: Any) -> SearchPage:
        """Search data services (``dcat:DataService``)."""
        return self.search(
            self._entity_query(DCAT.DataService, **filters),
            model=DataService, limit=limit, offset=offset,
        )

    def iter_data_services(self, *, limit: Optional[int] = None, **filters: Any) -> Iterator[DataService]:
        return self.iter_search(  # type: ignore[return-value]
            self._entity_query(DCAT.DataService, **filters), model=DataService, limit=limit
        )

    def dataset_series(self, *, limit: int = 50, offset: int = 0, **filters: Any) -> SearchPage:
        """Search dataset series (``dcat:DatasetSeries``)."""
        return self.search(
            self._entity_query(DCAT.DatasetSeries, **filters),
            model=DatasetSeries, limit=limit, offset=offset,
        )

    def catalogs(self, *, limit: int = 50, offset: int = 0, **filters: Any) -> SearchPage:
        """Search catalogs (``dcat:Catalog``) -- one per harvested source."""
        return self.search(
            self._entity_query(DCAT.Catalog, **filters),
            model=Catalog, limit=limit, offset=offset,
        )

    def iter_catalogs(self, *, limit: Optional[int] = None, **filters: Any) -> Iterator[Catalog]:
        return self.iter_search(  # type: ignore[return-value]
            self._entity_query(DCAT.Catalog, **filters), model=Catalog, limit=limit
        )

    def agents(self, *, limit: int = 50, offset: int = 0, **filters: Any) -> SearchPage:
        """Search agents (``foaf:Agent``) -- publishers and creators."""
        return self.search(
            self._entity_query(FOAF.Agent, **filters),
            model=Agent, limit=limit, offset=offset,
        )

    def iter_agents(self, *, limit: Optional[int] = None, **filters: Any) -> Iterator[Agent]:
        return self.iter_search(  # type: ignore[return-value]
            self._entity_query(FOAF.Agent, **filters), model=Agent, limit=limit
        )

    def agent(self, uri: str) -> Optional[Agent]:
        """Look up one agent (e.g. a dataset's ``dcterms:publisher``)."""
        return self.lookup(uri, model=Agent)  # type: ignore[return-value]

    def standards(self, *, limit: int = 50, offset: int = 0, **filters: Any) -> SearchPage:
        """Search standards/specifications (``dcterms:Standard``)."""
        return self.search(
            self._entity_query(DCTERMS.Standard, **filters),
            model=Standard, limit=limit, offset=offset,
        )

    # -- registry-wide statistics -----------------------------------------

    def organisations(self) -> List[OrganisationStats]:
        """Dataset counts per publishing organisation (``/charts/orgData.json``).

        Sorted by dataset count, descending, as the endpoint returns them.
        """
        data = self.get_json("/charts/orgData.json")
        labels = data.get("labels") or []
        values = data.get("values") or []
        series = (data.get("series") or [[]])[0]
        out: List[OrganisationStats] = []
        for index, uri in enumerate(values):
            name = labels[index] if index < len(labels) else ""
            count = int(series[index]) if index < len(series) else 0
            out.append(OrganisationStats(uri, name, count))
        return out

    def organisation_summary(self) -> Dict[str, int]:
        """Registry totals that accompany the organisation chart."""
        data = self.get_json("/charts/orgData.json")
        return {
            "datasets": int(data.get("datasetCount", 0) or 0),
            "independent_data_services": int(data.get("independentDataserviceCount", 0) or 0),
            "publishers": int(data.get("publisherCount", 0) or 0),
        }

    def link_check_reports(
        self,
        *,
        limit: int = MAX_LIMIT,
        failing_only: bool = False,
        context: Optional[Union[str, int]] = None,
    ) -> List[LinkCheckReport]:
        """The nightly link check, one report per catalog.

        ``failing_only`` keeps the catalogs where at least one distribution
        URL did not answer.
        """
        query = Q.rdf_type(Types.LINK_CHECK_REPORT)
        if context is not None:
            query = query & Q.context(self.context_uri(context))
        reports = [
            entry
            for entry in self.iter_search(query, model=LinkCheckReport, limit=limit, sort=None)
        ]
        if failing_only:
            reports = [r for r in reports if (r.failed or 0) > 0]  # type: ignore[union-attr]
        return reports  # type: ignore[return-value]

    def metadata_quality(
        self,
        *,
        limit: int = MAX_LIMIT,
        include_total: bool = True,
    ) -> List[MetadataQuality]:
        """DCAT-AP metadata quality (MQA) scores, one per catalog.

        With ``include_total`` the repository-wide ``MQATotal`` entry is
        included; it is the one whose ``is_total`` is ``True``.
        """
        types = [Types.MQA, Types.MQA_TOTAL] if include_total else [Types.MQA]
        return list(  # type: ignore[return-value]
            self.iter_search(
                Q.rdf_type(*types), model=MetadataQuality, limit=limit, sort=None
            )
        )

    def catalog_statistics(self, *, limit: int = 1, offset: int = 0) -> List[CatalogStatistics]:
        """Nightly registry-wide snapshots, newest first.

        Raise ``limit`` to walk back through the historical series.
        """
        page = self.search(
            Q.rdf_type(Types.CATALOG_STATISTICS),
            model=CatalogStatistics,
            limit=limit,
            offset=offset,
            sort=SORT_MODIFIED_DESC,
        )
        return list(page.entries)  # type: ignore[arg-type]

    def context_names(self, *, limit: Optional[int] = None) -> Dict[str, str]:
        """Map ``contextId`` to the catalog's title.

        Each harvested organisation gets one context, so this is how a
        ``contextId`` -- on any entry, or in
        :attr:`~dataportalen.models.CatalogStatistics.datasets_per_context` --
        becomes a readable name. For the publishing organisation itself,
        follow a catalog's ``publisher_uri`` to an
        :class:`~dataportalen.models.Agent`.
        """
        out: Dict[str, str] = {}
        for catalog in self.iter_catalogs(limit=limit):
            if catalog.context_id and catalog.title:
                out[str(catalog.context_id)] = catalog.title
        return out

    def context_publishers(self, *, limit: Optional[int] = None) -> Dict[str, str]:
        """Map ``contextId`` to the publishing organisation's URI."""
        out: Dict[str, str] = {}
        for catalog in self.iter_catalogs(limit=limit):
            if catalog.context_id and catalog.publisher_uri:
                out[str(catalog.context_id)] = catalog.publisher_uri
        return out

    def datasets_per_organisation(self, *, day: int = 0) -> List[Tuple[str, str, int]]:
        """``(contextId, organisation name, dataset count)`` for one day.

        ``day=0`` is the newest snapshot, ``day=1`` the one before it, and so
        on. Contexts with no known name keep an empty name.
        """
        stats = self.catalog_statistics(limit=1, offset=day)
        if not stats:
            return []
        names = self.context_names()
        counts = stats[0].datasets_per_context
        rows = [(ctx, names.get(ctx, ""), count) for ctx, count in counts.items()]
        rows.sort(key=lambda row: row[2], reverse=True)
        return rows

    # -- the whole catalogue -----------------------------------------------

    def download_catalog(
        self,
        path: str,
        *,
        workers: int = 8,
        limit: Optional[int] = None,
        progress: Any = "auto",
    ) -> Any:
        """Download every dataset to ``path`` as JSONL; returns a summary.

        One self-contained JSON object per line, distributions, publisher and
        contact points nested. See :func:`dataportalen.download_catalog`.
        """
        from .catalog import download_catalog as _download

        return _download(
            path, workers=workers, limit=limit, progress=progress, client=self
        )

    # -- the nightly dump --------------------------------------------------

    def iter_dump(self, *, chunk_size: int = 1 << 20, url: str = DUMP_URL) -> Iterator[bytes]:
        """Stream the nightly RDF/XML dump of every dataset.

        The dump is the only place where related entities (distributions,
        publishers, contacts) arrive alongside their datasets in one document.
        It is large -- hundreds of megabytes -- so this never buffers it.
        """
        status, headers, chunks = self._transport.stream(
            "GET", url, headers=self._headers("application/rdf+xml"), timeout=self.timeout,
            chunk_size=chunk_size,
        )
        if not 200 <= status < 300:
            body = b"".join(chunks)[:2048].decode("utf-8", "replace")
            raise HTTPError("dump download failed", status=status, url=url, body=body,
                            headers=headers)
        return chunks

    def download_dump(
        self,
        destination: str,
        *,
        chunk_size: int = 1 << 20,
        url: str = DUMP_URL,
        progress: Optional[Callable[[int], None]] = None,
    ) -> str:
        """Save the nightly dump to ``destination``; returns the path.

        ``progress`` is called with the cumulative byte count after each chunk.
        """
        total = 0
        with open(destination, "wb") as handle:
            for chunk in self.iter_dump(chunk_size=chunk_size, url=url):
                handle.write(chunk)
                total += len(chunk)
                if progress is not None:
                    progress(total)
        return destination


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


def _format_query(values: List[str]) -> Q:
    """Match dcterms:format, which publishers write both ways.

    Only 122 datasets state a file-type URI; 16,430 state a media type as a
    plain literal. Checking one index finds almost nothing, so check both.
    """
    from .query import predicate_field

    uri_field = predicate_field(DCTERMS.format, "uri")
    literal_field = predicate_field(DCTERMS.format, "literal_s")
    parts: List[Q] = []
    for value in values:
        if "://" in value:
            parts.append(Q.term(uri_field, value))
        else:
            parts.append(Q.term(literal_field, value))
    return Q.join(parts, "OR")


def _any_uri(predicate: str, uris: List[str]) -> Q:
    """Match a predicate against any of several object URIs."""
    from .query import predicate_field

    field = predicate_field(predicate, "uri")
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
