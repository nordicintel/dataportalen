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
import difflib
import json
import os
import random
import re
import sqlite3
import threading
import time
from http import HTTPStatus
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Type,
    Union,
)
from urllib.parse import quote as _quote

from .core import (
    DEFAULT_USER_AGENT,
    BaseTransport,
    DataportalError,
    HTTPError,
    NotFoundError,
    ParseError,
    QueryError,
    RateLimitError,
    Response,
    ServerError,
    TransportError,
    __version__,
    build_url,
    default_transport,
    enable_logging,
    logger,
    progress_reporter,
)  # noqa: F401  (NotFoundError re-exported for callers catching it here)
from .models import (
    _EMPTY_AGENT,
    DATA_SERVICE_FILTERS,
    DATASET_FILTERS,
    Agent,
    ContactPoint,
    DataService,
    Dataset,
    Distribution,
    Entry,
    Facets,
    FacetValue,
    Results,
    SearchPage,
    _iso,
    wrap_entry,
)
from .query import SORT_MODIFIED_DESC, Q
from .rdf import (
    DCAT,
    DCTERMS,
    FOAF,
    PROV,
    VCARD,
    aliases_for,
    label_for,
    publisher_for,
    resolve,
    resolve_publisher,
    slug_for,
    slugify,
)
from .records import (
    DataServiceRecord,
    DatasetRecord,
    Publisher,
    PublisherDetail,
)
from .retrieval import KINDS, classify

# ==========================================================================
# client_base: The synchronous client for the Sveriges dataportal registry API.
# ==========================================================================




#: The registry that backs dataportal.se.
DEFAULT_BASE_URL = "https://admin.dataportal.se"


#: The Solr index caps a page at 100 entries.
MAX_LIMIT = 100

#: The longest request sent. The registry refuses one near 8,190 characters.
MAX_URL = 8000

_RETRY_STATUSES = frozenset([408, 425, 429, 500, 502, 503, 504])

_DateLike = Union[str, _dt.date, _dt.datetime]

_BARE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BARE_MONTH = re.compile(r"^\d{4}-\d{2}$")
_BARE_YEAR = re.compile(r"^\d{4}$")


class _LRU:
    """A tiny insertion-ordered cache; avoids a functools.lru_cache on self.

    Locked, because a partial export resolves distributions, agents and
    contact points on three threads at once and they share one of these. The
    eviction was the race: between ``next(iter(self._data))`` picking the
    oldest key and ``pop`` removing it, another thread could pop the same
    one, and the second pop raises KeyError.
    """

    def __init__(self, maxsize: int = 512) -> None:
        self.maxsize = maxsize
        self._data: Dict[Any, Any] = {}
        self._lock = threading.Lock()

    def get(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            if key in self._data:
                value = self._data.pop(key)
                self._data[key] = value
                return value
            return default

    def __contains__(self, key: Any) -> bool:
        with self._lock:
            return key in self._data

    def set(self, key: Any, value: Any) -> None:
        if self.maxsize <= 0:
            return
        with self._lock:
            if key in self._data:
                self._data.pop(key)
            elif len(self._data) >= self.maxsize:
                self._data.pop(next(iter(self._data)))
            self._data[key] = value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
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
    return os.path.join(base, "dataportalen", "catalog.sqlite")


#: How old a copy may be before :class:`Catalog` brings it up to date. A
#: refresh is incremental -- a day's churn is about 630 datasets, under a
#: minute -- which is why it can happen on its own where a rebuild could not.
DEFAULT_MAX_AGE = 7

#: Parallel requests while downloading. Measured against the real registry:
#: one thread gets 0.84 requests/second, two get 2.26, and four and eight get
#: the same 2.26 -- the registry is the limit, not the client. Two is the
#: whole gain; a full build is 6 minutes with it and 17 without.
_WORKERS = 2

#: The date filters, which only datasets support: `modified` is on 89.1% of
#: them and `issued` on 42.1%, against 7.5% and 0.8% of the 599 data services.
_DATE_FILTERS = ("modified_after", "modified_before",
                 "issued_after", "issued_before")

#: Every name that is a filter for something. A name outside this set is a
#: typo, not a question the wrong kind of record cannot answer.
_EVERY_FILTER = frozenset(
    DATASET_FILTERS + DATA_SERVICE_FILTERS + _DATE_FILTERS + ("query",))

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
        if len(url) > MAX_URL:
            # The registry answers HTTP 414 somewhere near 8,190 characters.
            # A search naming a few hundred values gets there -- every
            # national authority is 170 publisher URIs, 12,703 characters --
            # and "URI Too Long" says nothing about which argument did it.
            raise QueryError(
                "this search needs a %s-character request and the registry "
                "stops at about 8,190; ask for fewer values at once"
                % f"{len(url):,}")
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
        # Quoted for defence in depth. Both ids come from our own records,
        # and every one in the corpus is numeric or 32 hex characters -- but
        # `Catalog(path=...)` accepts any file, and a `../` in a hand-edited
        # context_id would otherwise redirect the one outbound request.
        path = "/store/%s/%s/%s" % (
            _quote(str(context_id), safe=""), part, _quote(str(entry_id), safe=""))
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
            # A full page, and the next one if there is one: a URI can belong
            # to two entries, so 20 URIs are not 20 hits. Asking for exactly
            # len(batch) returned the first 20 of 22 for one batch of the 365
            # publisher URIs, and the agent that fell off the end was the one
            # nine of Svenska kraftnät's ten datasets name.
            offset = 0
            while True:
                page = self._search(
                    Q.resource(*batch), limit=MAX_LIMIT, offset=offset,
                    sort=STABLE_SORT, model=model,
                )
                for entry in page.entries:
                    uri = entry.resource_uri
                    if uri:
                        found[uri] = entry
                        if use_cache:
                            self._cache.set(uri, entry)
                offset += len(page.entries)
                if len(page.entries) < MAX_LIMIT or offset >= page.total:
                    break
        out = [found[u] for u in wanted if u in found]
        return [e.as_(model) for e in out] if model else out

def local_facets(
    records: Sequence[Dict[str, Any]],
    limit: Optional[int] = None,
    filters: Sequence[str] = DATASET_FILTERS,
    names: Optional[Mapping[str, Dict[str, Any]]] = None,
) -> Facets:
    """Facets counted over records in hand -- one pass, every filter.

    ``names`` labels the publisher facet: ``{id: name}``, from the Catalog's
    publishers, so a facet row and :meth:`Catalog.publisher` never disagree
    about what an organisation is called. Without it the name is the first
    one seen on a record, which for 2 publishers is not the same thing.

    ``filters`` is the set that applies to these records, so the facets of a
    data service search carry the keys it has rather than empty lists for
    the ones it does not.
    """
    tallies: Dict[str, Dict[str, int]] = {name: {} for name in filters}
    collect = names is None
    names = {} if collect else names
    spellings: Dict[str, Dict[str, int]] = {}
    for record in records:
        for name, counts in tallies.items():
            if name == "keyword":
                # One keyword, however it is spelt: `Kommun` and `kommun` are
                # a count of one each for the same thing, and a dataset that
                # carries both spellings (395 do) still counts once.
                seen: Dict[str, set] = {}
                for keyword in _local_values(record, "keyword"):
                    seen.setdefault(_fold_keyword(keyword), set()).add(keyword.strip())
                for key, forms in seen.items():
                    counts[key] = counts.get(key, 0) + 1
                    tally = spellings.setdefault(key, {})
                    for form in forms:
                        tally[form] = tally.get(form, 0) + 1
                continue
            for value in set(_local_values(record, name)):
                counts[value] = counts.get(value, 0) + 1
        if collect:
            _collect_names(record, names)

    def rows(name: str, counts: Dict[str, int]) -> List[FacetValue]:
        if name == "keyword":
            # Shown as the publisher wrote it: the commonest spelling, and for
            # the 479 groups where that is a tie, the first alphabetically.
            # Showing the folded form would have rewritten 60% of all values
            # into spellings no record carries.
            shown = [(min(spellings[key], key=lambda f: (-spellings[key][f], f)), n)
                     for key, n in counts.items()]
            return [FacetValue(value, n, {})
                    for value, n in sorted(shown, key=lambda pair: (-pair[1], pair[0]))]
        ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        if name == "publisher":
            return [FacetValue(value, n, names.get(value) or {}) for value, n in ordered]
        if name == "kind":
            # The package's own values, not vocabulary terms: `file` and `api`
            # must not borrow the label of a term that shares the name.
            return [FacetValue(value, n, {}) for value, n in ordered]
        # A label comes from the vocabulary only for a vocabulary filter.
        # Looked up by bare value across every filter, 45 keywords wore the
        # label of a term that happened to share their name.
        return [FacetValue(value, n, label_for(value)) for value, n in ordered]

    return Facets({name: rows(name, counts) for name, counts in tallies.items()},
                  limit=limit)


def _fold_keyword(value: Any) -> str:
    """What two spellings of one keyword have in common.

    Strip and casefold, nothing more: 23,373 exact values become 21,359. Not
    `slugify`, which the vocabulary filters use and which would also merge 169
    groups that are different words.
    """
    return str(value).strip().casefold()


def _org_slug(org: Optional[Dict[str, Any]]) -> Optional[str]:
    """The filter value for one publisher.

    The package's URI table first, because those slugs are stable across
    releases. Failing that, the organisation's own name from the record --
    without which 13 of the 365 publishers would have no filter value at all,
    since they mint URIs the table never saw
    (``fohm-app.folkhalsomyndigheten.se/...``, ``myndighetsregistret.scb.se/...``).
    """
    if not org:
        return None
    known = publisher_for(org.get("uri"))
    if known:
        return known
    name = org.get("name")
    if isinstance(name, dict):
        name = name.get("sv") or name.get("en")
    return (slugify(name) or None) if name else None


def _collect_names(record: Dict[str, Any], out: Dict[str, Dict[str, Any]]) -> None:
    """Remember the readable name of each publisher seen.

    The vocabulary has no entry for an organisation, so its label comes from
    the records -- the same place the slug came from.
    """
    org = record.get("publisher")
    slug = _org_slug(org)
    if slug and slug not in out and isinstance((org or {}).get("name"), dict):
        out[slug] = org["name"]

def _date(value: Any) -> Optional[str]:
    """Accept "2024-01-01", a date or a datetime; emit the full timestamp Solr needs.

    Solr rejects a bare date with HTTP 400, so the obvious input is widened
    here rather than refused.
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


def _require_values(value: Any, name: str) -> List[str]:
    """The values of a filter, refusing the ones that quietly mean everything.

    `keyword=[]` or `theme=None` is almost always a tag list that came out
    empty by accident. Treating it as "no condition" would return the whole
    corpus; treating it as "no match" would return nothing. Either answer
    hides the mistake, so it is an error instead.
    """
    if value is None:
        raise QueryError(
            "%s needs a value; got None. Leave the filter out to match "
            "everything." % (name,))
    values = _as_list(value)
    if not values or all(v is None or not str(v).strip() for v in values):
        raise QueryError(
            "%s needs a value; got %r. Leave the filter out to match "
            "everything." % (name, value))
    return values


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

    __slots__ = ("path", "datasets", "data_services", "distributions",
                 "bytes_written", "elapsed", "requests")

    def __init__(
        self,
        path: str,
        datasets: int,
        distributions: int,
        bytes_written: int,
        elapsed: float,
        requests: int,
        data_services: int = 0,
    ) -> None:
        self.path = path
        self.datasets = datasets
        self.data_services = data_services
        self.distributions = distributions
        self.bytes_written = bytes_written
        self.elapsed = elapsed
        self.requests = requests

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "datasets": self.datasets,
            "data_services": self.data_services,
            "distributions": self.distributions,
            "bytes_written": self.bytes_written,
            "elapsed": round(self.elapsed, 1),
            "requests": self.requests,
        }

    def __repr__(self) -> str:
        return ("<CatalogSummary %s: %d datasets, %d data services, "
                "%d distributions, %.1f MiB in %.0fs>") % (
            os.path.basename(self.path),
            self.datasets,
            self.data_services,
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


#: The database layout. `harvested` is the registry's own timestamp for the
#: entry, not the publisher's `modified` -- it is what "changed since we last
#: looked" means, and the only thing an incremental refresh can trust.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS record (
    context_id TEXT NOT NULL,
    entry_id   TEXT NOT NULL,
    uri        TEXT,
    type       TEXT NOT NULL,
    harvested  TEXT,
    doc        TEXT NOT NULL,
    PRIMARY KEY (context_id, entry_id)
);
CREATE INDEX IF NOT EXISTS record_uri ON record(uri);
CREATE INDEX IF NOT EXISTS record_type ON record(type);
-- No index on `harvested`: the registry filters by it, we never do.
"""

#: Bumped when the layout changes in a way an older file cannot satisfy --
#: including a change to the record shape, since `doc` holds the record as it
#: was written. Version 2 dropped `creators`; version 3 is the 0.10.0 record
#: shape (licence dicts, ISO language codes, no ids below the top level);
#: version 4 adds `byte_size` where a file states one and makes an empty
#: `keywords` a dict.
SCHEMA_VERSION = "4"


def _connect(path: str) -> Any:
    """Open the catalogue database, creating the file and layout if needed."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(_SCHEMA)
    return db


def _meta_get(db: Any, key: str) -> Optional[str]:
    row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _meta_set(db: Any, **values: Any) -> None:
    db.executemany(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [(k, None if v is None else str(v)) for k, v in values.items()])


#: One upsert, keyed on the registry's own identity for an entry.
_UPSERT = (
    "INSERT INTO record (context_id, entry_id, uri, type, harvested, doc) "
    "VALUES (?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(context_id, entry_id) DO UPDATE SET "
    "uri = excluded.uri, type = excluded.type, "
    "harvested = excluded.harvested, doc = excluded.doc"
)


def _write_records(db: Any, rows: Iterable[Dict[str, Any]],
                   kinds: Any = None, harvested: Any = None) -> int:
    """Insert or replace records. Returns how many were written.

    The upsert is the whole reason this is a database: a changed dataset
    overwrites its own row and a new one appends itself, with no index to
    maintain and no file to rewrite.

    ``rows`` yields ``(kind, harvested, record)``. The key is the record's
    ``context_id``/``entry_id`` -- not its ``uri``, because four datasets in
    the corpus share a URI with another and keying on that would merge them.
    """
    written = 0
    batch = []
    for kind, stamp, record in rows:
        batch.append((
            str(record.get("context_id")), str(record.get("entry_id")),
            record.get("uri"), kind, stamp,
            json.dumps(record, ensure_ascii=False, separators=(",", ":"))))
        if len(batch) >= 1000:
            db.executemany(_UPSERT, batch)
            written += len(batch)
            batch = []
    if batch:
        db.executemany(_UPSERT, batch)
        written += len(batch)
    return written


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


#: Publishers are not all typed the same way: most are `foaf:Agent` (5,846),
#: but 69 are `foaf:Organization` and 1,693 are `prov:Agent`. The crawl asks
#: for all three. It once had to, because the Organization entries carried a
#: third of the creator references; creators are gone, but narrowing this to
#: `foaf:Agent` is a separate question -- it would have to be measured against
#: the publisher URIs alone, and silently losing a publisher costs more than
#: the 18 extra pages of an 856-request build.
_AGENT_TYPES = (FOAF.Agent, FOAF.Organization, PROV.Agent)


#: The nightly link check. Each catalogue publishes a report entry whose
#: metadata is a set of counters and whose *resource* is the detail: one JSON
#: object per link, naming the URL, the entry it belongs to, the verdict and
#: when it was reached. 159 of them, ~29 MiB, about ten seconds -- against a
#: 691-request crawl that is noise, and it is the only place the per-link
#: result exists.
LINK_CHECK_TYPE = "http://entryscape.com/terms/LinkCheckReport"


def _link_checks(client, counter):
    """``{url: check}`` from every catalogue's latest link-check report.

    A catalogue keeps about three days of reports; only the newest is read.
    The verdict is the registry's, passed through as it stands -- ``status``
    is ``success``, ``broken`` or ``excluded``, and ``message`` is whatever
    reason it gave, which is often none.
    """
    query = Q.rdf_type(LINK_CHECK_TYPE) & Q.public()
    total = client._count(query)
    counter.add()

    newest: Dict[str, Tuple[str, str]] = {}
    for offset in _pages(total):
        raw = client._search_raw(query, limit=PAGE_SIZE, offset=offset,
                                 sort=STABLE_SORT)
        counter.add()
        for child in (raw.get("resource") or {}).get("children") or []:
            graphs = list((child.get("metadata") or {}).values())
            if not graphs:
                continue
            created = (graphs[0].get(DCTERMS.created) or [{}])[0].get("value", "")
            context = child.get("contextId")
            if context and (context not in newest or created > newest[context][0]):
                newest[context] = (created, child.get("entryId"))

    checks: Dict[str, Dict[str, Any]] = {}
    for context, (_, entry_id) in sorted(newest.items()):
        try:
            response = client._request(
                "/store/%s/resource/%s" % (_quote(str(context), safe=""),
                                           _quote(str(entry_id), safe="")),
                {}, accept="application/json")
            records = response.json()
        except (DataportalError, ValueError):             # pragma: no cover
            # One unreadable report must not cost the whole download.
            logger.warning("link-check report for context %s is unreadable",
                           context)
            continue
        counter.add()
        if not isinstance(records, list):                 # pragma: no cover
            continue
        for record in records:
            url = record.get("uri")
            if not url:
                continue
            # A URL checked twice keeps the later verdict.
            seen = checks.get(url)
            if seen is None or (record.get("checkedAt") or "") >= (
                    seen.get("checkedAt") or ""):
                checks[url] = record
    logger.info("link checks: %s URLs over %s catalogues",
                f"{len(checks):,}", f"{len(newest):,}")
    return checks


def _urls_of(dist: Dict[str, Any]) -> List[str]:
    """Every URL a distribution names, whichever shape the field is in."""
    out: List[str] = []
    for key in ("download_url", "access_url"):
        value = dist.get(key)
        if isinstance(value, str):
            out.append(value)
        elif value:
            out.extend(v for v in value if v)
    return out


def _attach_links(record: Dict[str, Any], checks: Dict[str, Dict[str, Any]]) -> None:
    """Mark each file the registry's nightly check found broken.

    A broken file carries ``{"reason": ..., "checked": ...}`` under ``broken``;
    a working one carries nothing. The 18,360 successes and 5,021 the check
    skipped were a ``link`` dict on every file for the one in three that
    matters, and a landing-page verdict on the dataset that was ``None`` on
    10,331 of them -- 277 datasets had a broken landing page and perfectly
    good files, which is not what "dead" means.

    Only files. The registry checks ``accessURL``, ``downloadURL``,
    ``landingPage``, ``foaf:page``, ``conformsTo`` and ``endpointDescription``
    -- never ``endpointURL`` -- so whether a data service's API answers is
    something it does not know, and this does not pretend to.
    """
    if not checks:
        return
    for dist in record.get("distributions") or []:
        for url in _urls_of(dist):
            check = checks.get(url)
            if check and check.get("status") == "broken":
                dist["broken"] = {
                    "reason": check.get("statusMessage") or None,
                    "checked": (check.get("checkedAt") or "")[:19] or None,
                }
                break


def _bulk_indexes(client, workers, counter):
    """One crawl per referenced type -- the right trade for a full export."""
    distribution_total = client._count(Q.rdf_type(DCAT.Distribution))
    agent_query = Q.rdf_type(*_AGENT_TYPES)
    agent_total = client._count(agent_query)
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
        client, agent_query, Agent, agent_total, workers, counter))
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
        # A data service has no distributions, and no such attribute.
        for uri in getattr(dataset, "distribution_uris", ()):
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
    limit: Optional[int] = None,
    client: Any = None,
    links: bool = True,
    since: Optional[str] = None,
) -> CatalogSummary:
    """Download the catalogue to ``path`` as JSONL and return a summary.

    One JSON object per line: every ``dcat:Dataset`` and every
    ``dcat:DataService``, each carrying a ``type`` that says which. A line
    stands on its own -- distributions, publisher and contact points are
    resolved and nested, so nothing needs a further lookup.

    :param path: the SQLite database to fill.
    :param limit: stop after this many datasets, for smoke tests. Data services
        are skipped entirely when it is set -- they are the small half, and a
        smoke test should stay small.
    :param client: an existing ``_Registry`` to reuse.
    :param links: also read the registry's nightly link check and mark every
        file it found broken. 159 requests, about ten seconds. Skipped for a
        partial export, where the reports would dwarf the work.
    :param since: only fetch entries the registry has touched since this
        timestamp, and leave the rest of the database alone. This is what makes
        a refresh cheap: a day's churn is about 630 datasets -- seven pages
        instead of 236. It cannot see deletions, so a row whose dataset has
        been withdrawn stays until a full rebuild.
    """
    owned = client is None
    if owned:
        client = _Registry()
    workers = _WORKERS

    started_all = time.time()
    # A live line on a terminal, a log line every so often otherwise: a
    # five-minute run must never look like a hang, and nobody needs silence.
    report = progress_reporter("datasets")
    counter = _Counter()
    written = 0
    services_written = 0
    bytes_written = 0
    distributions_written = 0

    # A refresh only asks for what the registry has touched since we looked.
    # It is the registry's own timestamp, not the publisher's `modified`:
    # a publisher can leave that empty or set it to 2100, and neither tells
    # us whether the entry changed.
    scope = Q.rdf_type(DCAT.Dataset)
    if since:
        scope = scope & Q.raw("modified:[%s TO *]" % _solr_stamp(since))

    try:
        dataset_total = client._count(scope)
        counter.add()
        if limit is not None:
            dataset_total = min(dataset_total, limit)
        logger.info("%s %s datasets to %s",
                    "refreshing" if since else "exporting",
                    f"{dataset_total:,}", path)

        datasets = _crawl(client, scope, Dataset, dataset_total, workers, counter)
        missing: Dict[str, None] = {}

        if limit is None and not since:
            # Full export: every distribution is needed anyway, so one bulk
            # crawl per referenced type beats resolving dataset by dataset.
            indexes = _bulk_indexes(client, workers, counter)
        else:
            # A refresh or a partial export touches a few hundred datasets, and
            # crawling 35k distributions to serve them would dwarf the work.
            datasets = list(_take(datasets, limit) if limit else datasets)
            indexes = _targeted_indexes(client, datasets, workers, counter)
            if limit is not None:
                links = False

        distributions, agents, contacts = indexes
        checks = _link_checks(client, counter) if links else {}

        def rows():
            nonlocal written, services_written, distributions_written, bytes_written
            for dataset in datasets:
                record, used = _assemble(
                    dataset, distributions, agents, contacts, missing)
                _attach_links(record, checks)
                written += 1
                distributions_written += used
                bytes_written += len(json.dumps(record, ensure_ascii=False).encode())
                if report is not None:
                    report(written, dataset_total)
                yield "dataset", _iso(dataset.modified), record

            if limit is None:
                # 599 services in six pages: the one real DCAT class the
                # database would otherwise lack, for ~1% of the dataset crawl.
                for entry, record in _service_records(
                        client, workers, counter, agents, contacts, missing,
                        since=since):
                    services_written += 1
                    bytes_written += len(json.dumps(record, ensure_ascii=False).encode())
                    yield "data_service", _iso(entry.modified), record

        db = _connect(path)
        try:
            with db:
                if since is None:
                    # A full build is a rebuild, not an upsert over whatever is
                    # already there. Without this, a dataset the registry has
                    # withdrawn keeps its row forever -- and so does the shape
                    # it was written in, which is what `SCHEMA_VERSION` tells
                    # people to use `refresh="always"` to be rid of. It is in
                    # the same transaction as the write, so a download that
                    # dies leaves the old catalogue untouched.
                    db.execute("DELETE FROM record")
                _write_records(db, rows())
                now = _dt.datetime.now().replace(microsecond=0).isoformat()
                _meta_set(db, schema=SCHEMA_VERSION, package=__version__,
                          last_refreshed=now)
                if not _meta_get(db, "first_retrieved"):
                    _meta_set(db, first_retrieved=now)
        finally:
            db.close()

        if missing:
            # Anything the crawl did not cover -- a distribution in a
            # non-public context, say. Reported, never silently dropped.
            _report_missing(missing)

    finally:
        if owned:
            client.close()

    logger.info("wrote %s: %s datasets, %s data services, %s distributions, "
                "%.1f MiB in %.0fs (%d requests)",
                path, f"{written:,}", f"{services_written:,}",
                f"{distributions_written:,}",
                bytes_written / (1 << 20), time.time() - started_all, counter.value)
    try:
        bytes_written = os.path.getsize(path)
    except OSError:                                       # pragma: no cover
        pass
    return CatalogSummary(
        path=path,
        datasets=written,
        data_services=services_written,
        distributions=distributions_written,
        bytes_written=bytes_written,
        elapsed=time.time() - started_all,
        requests=counter.value,
    )


def _service_records(client, workers, counter, agents, contacts, missing,
                     since=None):
    """Every ``dcat:DataService``, assembled like a dataset.

    Yields ``(entry, record)`` so the caller can store the registry's own
    timestamp alongside the record, which is what a refresh filters on.
    """
    scope = Q.rdf_type(DCAT.DataService)
    if since:
        scope = scope & Q.raw("modified:[%s TO *]" % _solr_stamp(since))
    total = client._count(scope)
    counter.add()
    logger.info("%s %s data services", "refreshing" if since else "exporting",
                f"{total:,}")
    for service in _crawl(client, scope, DataService, total, workers, counter):
        record, _ = _assemble(service, {}, agents, contacts, missing,
                              with_distributions=False)
        yield service, record


def _assemble(
    entry: Any,
    distribution_index: Dict[str, Dict[str, Any]],
    agents: Dict[str, Dict[str, Any]],
    contacts: Dict[str, Dict[str, Any]],
    missing: Dict[str, None],
    *,
    with_distributions: bool = True,
) -> Tuple[Dict[str, Any], int]:
    """One dataset or data service, with its references spliced in.

    A search hit carries no nested distributions, publisher or contacts -- only
    the URIs -- so they come from the indexes the crawl already built rather
    than a request each.
    """
    # `distributions=False` on a dataset: a search hit has none inline, and we
    # are about to supply better ones from the index. A data service has no
    # distributions at all, and its to_dict takes no such argument.
    record = (entry.to_dict(distributions=False) if with_distributions
              else entry.to_dict())

    nested = []
    if with_distributions:
        for uri in entry.distribution_uris:
            found = distribution_index.get(uri)
            if found is not None:
                nested.append(found)
            else:
                missing.setdefault(uri, None)
        record["distributions"] = nested

    publisher_uri = entry.publisher_uri
    if publisher_uri:
        found = agents.get(publisher_uri)
        if found is not None:
            record["publisher"] = found
        elif record.get("publisher") is None:
            record["publisher"] = dict(_EMPTY_AGENT, uri=publisher_uri)

    if not record.get("contact_points"):
        resolved = []
        for uri in entry.contact_point_uris:
            found = contacts.get(uri)
            if found is None:
                # Reported like an unresolved distribution rather than
                # dropped: a record that names a contact and shows none
                # should not do so in silence.
                missing.setdefault(uri, None)
            else:
                resolved.append(found)
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


#: What ``access_rights=`` may name. ``none`` is the record that sets nothing.
_ACCESS_VALUES = ("public", "non_public", "restricted", "none")


def _access_scope(value: Any) -> Optional[frozenset]:
    """The access_rights values a Catalog holds, or ``None`` for all of them."""
    if value is None:
        return None
    if isinstance(value, str):
        value = (value,)
    try:
        wanted = frozenset(str(v).strip().lower() for v in value)
    except TypeError:
        raise QueryError("access_rights must be a list of values or None; got %r"
                         % (value,))
    unknown = wanted - set(_ACCESS_VALUES)
    if unknown or not wanted:
        raise QueryError(
            "access_rights takes %s; got %r"
            % (", ".join(_ACCESS_VALUES), sorted(unknown)[0] if unknown else value))
    return wanted


class Catalog:
    """Sweden's open-data catalogue, downloaded once and searched locally.

    The registry answers about two requests a second and caps a page at 100
    entries, so reading all of it takes minutes. This downloads it once and
    every search after that is local::

        from dataportalen import Catalog, text

        cat = Catalog()                                  # downloads on first use
        page = cat.datasets(theme="transport", format="csv")
        page.total                                       # 72
        page.facets["publisher"]                      # who publishes them
        for dataset in page:
            print(text(dataset["title"]), dataset["distributions"])

    Everything that creates or replaces the database is an argument here, so
    nothing downloads 94 MB behind a call that looked like a search.

    :param database: the SQLite file. Defaults to :func:`default_catalog_path`.
    :param max_age: days. A copy older than this -- or missing -- is brought
        up to date when the object is built: a download if there is nothing,
        otherwise an incremental refresh of what the registry has touched
        since, under a minute. ``None`` means use whatever is there and only
        download if there is nothing.
    :param rebuild: fetch everything again now, whatever is there. What a
        schema change asks for, and the only thing that drops a dataset the
        registry has since withdrawn.
    :param exclude_broken: drop every dead file, and any dataset whose every
        file is dead. Dead means the registry's nightly link check got an HTTP
        error for it -- Not Found, Forbidden, Internal Server Error -- or
        found its host is not in DNS: 903 of 35,148 files, 127 public
        datasets. The 10,858 files its checker could
        not get through to (no answer, connection reset, timeout, Too Many
        Requests) are not dead;
        they stay and carry ``unverified: {"reason", "checked"}``. A dataset
        that never had files (1,647: APIs, registers) stays too. ``False``
        keeps everything and marks each dead file with ``broken``, same two
        keys.
    :param access_rights: which ``access_rights`` values the catalogue holds.
        ``"public"``, ``"non_public"``, ``"restricted"``, and ``"none"`` for
        the 4,167 datasets -- 17.7%, mostly universities -- that set nothing.
        ``None`` holds everything.
    """

    def __init__(
        self,
        database: Optional[str] = None,
        *,
        max_age: Optional[int] = DEFAULT_MAX_AGE,
        rebuild: bool = False,
        exclude_broken: bool = True,
        access_rights: Optional[Sequence[str]] = ("public",),
        _transport: Optional[BaseTransport] = None,
    ) -> None:
        if max_age is not None and (isinstance(max_age, bool) or max_age < 0):
            raise QueryError("max_age must be a number of days, or None; got %r"
                             % (max_age,))
        self.database = database or default_catalog_path()
        self.max_age = max_age
        self.exclude_broken = bool(exclude_broken)
        self.access_rights = _access_scope(access_rights)
        self._registry = _Registry(transport=_transport)
        self._owns_registry = True
        self._by_uri: Optional[Dict[str, Dict[str, Any]]] = None
        self._publisher_rows: Optional[List[Dict[str, Any]]] = None
        self._seen: Dict[str, set] = {}
        self._records: List[Dict[str, Any]] = []
        self._services: List[Dict[str, Any]] = []
        self._first_retrieved: Optional[str] = None
        self._last_refreshed: Optional[str] = None
        self._excluded: Dict[str, int] = {}
        self._load(rebuild)

    # -- the file ----------------------------------------------------------

    def _load(self, rebuild: bool) -> None:
        """Read the database, bringing it up to date first if asked to."""
        if rebuild or not os.path.exists(self.database):
            self._download(full=True)
            return
        self._read()
        age = self.age_days
        if self.max_age is not None and age is not None and age >= self.max_age:
            # Incremental: only what the registry has touched since we looked.
            # A day of churn is about 630 datasets, seven pages -- so keeping a
            # copy current costs seconds rather than the minutes a rebuild does.
            logger.info("%s is %d days old; refreshing what changed",
                        self.database, age)
            self._download(full=False)
        elif age:
            logger.info("%s is %d days old and max_age is %s; used as is",
                        self.database, age, self.max_age)

    def _download(self, full: bool = True) -> None:
        """Fill the database, or bring it up to date.

        ``full=False`` asks the registry only for entries it has touched since
        our last refresh, and every row it returns replaces its own. It cannot
        see a deletion: a dataset withdrawn from the registry keeps its row
        until a full rebuild, which ``rebuild=True`` does.
        """
        since = None if full else self.last_refreshed
        download_catalog(self.database, client=self._registry, since=since)
        self._read()

    def _read(self) -> None:
        """Load the database and split its rows by type."""
        db = _connect(self.database)
        try:
            version = _meta_get(db, "schema")
            if version is not None and version != SCHEMA_VERSION:
                raise ParseError(
                    "%s was written with catalogue schema %s and this is "
                    "version %s. Build the Catalog with rebuild=True."
                    % (self.database, version, SCHEMA_VERSION))
            self._first_retrieved = _meta_get(db, "first_retrieved")
            self._last_refreshed = _meta_get(db, "last_refreshed")
            every = [_present(json.loads(row["doc"]))
                     for row in db.execute("SELECT doc FROM record")]
        finally:
            db.close()
        if not every:
            # A catalogue with nothing in it is not a catalogue. Writes are
            # atomic now, so this means the file was emptied by something
            # else -- and reading it as "0 datasets, every search answers
            # nothing" would hide that for as long as the file sat there.
            raise ParseError(
                "%s is empty, so it is not a usable catalogue. Delete it, or "
                "build the Catalog with rebuild=True to fetch a fresh one."
                % self.database)
        self._publishers = _Publishers(             # before scope, on purpose
            (record["publisher"], 1) for record in every)
        every = self._scope(every)
        self._records = [r for r in every if r.get("type", "dataset") == "dataset"]
        self._services = [r for r in every if r.get("type") == "data_service"]
        self._by_uri = None
        self._seen = {}
        self._publisher_rows = None
        logger.info("read %s datasets and %s data services from %s",
                    f"{len(self._records):,}", f"{len(self._services):,}", self.database)

    def _scope(self, every: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """What this Catalog holds of what the database holds.

        The database is the whole registry; ``access_rights`` and
        ``exclude_broken`` narrow it when the object is built, so every
        search and every count after that already agrees with them.
        """
        excluded = {"access_rights": 0, "dead_distributions": 0,
                    "dead_datasets": 0}
        self._excluded = excluded
        if self.access_rights is not None:
            before = len(every)
            every = [r for r in every
                     if (r.get("access_rights") or "none") in self.access_rights]
            excluded["access_rights"] = before - len(every)
            if before - len(every):
                logger.info("left out %s records outside access_rights=%s",
                            f"{before - len(every):,}",
                            "/".join(sorted(self.access_rights)))
        if self.exclude_broken:
            # `broken` here already means dead: _present moved the files the
            # registry merely could not reach to `unverified`, and those stay.
            kept, files, dead = [], 0, 0
            for record in every:
                dists = record.get("distributions")
                if dists:
                    alive = [d for d in dists if not d.get("broken")]
                    files += len(dists) - len(alive)
                    if not alive:
                        dead += 1
                        continue
                    record["distributions"] = alive
                kept.append(record)
            every = kept
            excluded["dead_distributions"] = files
            excluded["dead_datasets"] = dead
            if files:
                logger.info("left out %s dead files and the %s datasets that "
                            "had nothing else", f"{files:,}", f"{dead:,}")
        return every

    @property
    def first_retrieved(self) -> Optional[str]:
        """When this copy was first built, as the database recorded it."""
        return self._first_retrieved

    @property
    def last_refreshed(self) -> Optional[str]:
        """When it was last brought up to date. What a refresh asks from."""
        return self._last_refreshed

    @property
    def downloaded(self) -> Optional[_dt.datetime]:
        """When the copy was last refreshed, which is how old the data is.

        The database's own record of it, not the file's mtime: an incremental
        refresh touches the file for a few hundred rows, and that must not make
        the whole copy look new.
        """
        stamp = self._last_refreshed
        if stamp:
            try:
                return _dt.datetime.fromisoformat(stamp)
            except ValueError:                            # pragma: no cover
                pass
        try:
            return _dt.datetime.fromtimestamp(os.path.getmtime(self.database))
        except OSError:                                   # pragma: no cover
            return None

    @property
    def age_days(self) -> Optional[int]:
        """How many days old the file is, or ``None`` if it is not there.

        Clamped at zero: a file written a moment ago can carry a timestamp a
        fraction of a second ahead of the clock, and a negative timedelta
        floors to -1 day -- so a fresh download would report being written
        tomorrow.
        """
        when = self.downloaded
        if when is None:
            return None
        return max(0, (_dt.datetime.now() - when).days)

    def info(self) -> Dict[str, Any]:
        """What this copy is and how old::

            {"database": "...\\catalog.sqlite",
             "first_retrieved": "2026-09-30T17:57:14",
             "last_refreshed": "2026-10-01T06:38:13",
             "downloaded": "2026-10-01T06:38:13",
             "age_days": 0, "bytes": 98725888,
             "datasets": 23582, "data_services": 599, "publishers": 356,
             "excluded": {"access_rights": 0, "dead_distributions": 0,
                          "dead_datasets": 0},
             "unverified_distributions": 10858}

        ``excluded`` is what ``access_rights`` and ``exclude_broken`` left out
        when this object was built: records outside the access scope, dead
        distributions, and the datasets that had nothing but dead ones.
        ``unverified_distributions`` counts what is held but that the
        registry's checker could not reach.
        """
        when = self.downloaded
        try:
            size = os.path.getsize(self.database)
        except OSError:                                   # pragma: no cover
            size = 0
        return {
            "database": self.database,
            "first_retrieved": self._first_retrieved,
            "last_refreshed": self._last_refreshed,
            "downloaded": when.isoformat() if when else None,
            "age_days": self.age_days,
            "bytes": size,
            "datasets": len(self._records),
            "data_services": len(self._services),
            # The length of publishers(): organisations, not URIs. Eight of
            # them mint two URIs each (Folkhälsomyndigheten, SLU, Malmö
            # Museer...), and counting URIs would claim 365 next to a list of
            # 356.
            "publishers": len(self._rows()),
            "excluded": dict(self._excluded),
            "unverified_distributions": sum(
                1 for record in self._records
                for dist in record.get("distributions") or ()
                if "unverified" in dist),
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
        return "<Catalog %s: %d datasets, %d data services>" % (
            self.database, len(self._records), len(self._services))

    # -- searching ---------------------------------------------------------

    def _observed(self, filter: str, kind: str = "dataset") -> Optional[set]:
        """Every value this file actually holds for one filter.

        What makes "a facet reports it, so you can filter on it" true
        even where the vocabulary table has no entry for the value. Kept per
        kind of record, because a data service's values are its own.
        """
        if filter not in DATASET_FILTERS and filter not in DATA_SERVICE_FILTERS:
            return None
        if filter == "keyword":
            # Across both kinds, folded. A keyword has no vocabulary to vouch
            # for it, so "known" has to mean "somewhere in this Catalog":
            # checked per kind, data_services(keyword="kommun") would raise
            # for the commonest keyword there is, because no service has it.
            key = ("*", "keyword")
            if key not in self._seen:
                self._seen[key] = {
                    _fold_keyword(value)
                    for record in self._records + self._services
                    for value in _local_values(record, "keyword")
                }
            return self._seen[key]
        key = (kind, filter)
        if key not in self._seen:
            records = self._services if kind == "data_service" else self._records
            self._seen[key] = {
                value for record in records
                for value in _local_values(record, filter)
            }
        return self._seen[key]

    @staticmethod
    def _window(limit, offset):
        """Reject a window that Python would happily slice from the wrong end.

        ``limit=-1`` sliced as ``found[0:-1]`` and quietly returned every match
        but the last; ``offset=-1`` returned nothing at all. Both are garbage
        in, but both looked like an answer, which is the worse failure -- a
        caller whose arithmetic went negative got a plausible result instead of
        a complaint.
        """
        if limit is not None and limit < 0:
            raise QueryError("limit must be 0 or more, or None for every "
                             "match; got %r" % (limit,))
        if offset < 0:
            raise QueryError("offset must be 0 or more; got %r" % (offset,))

    def _check(self, filters, allowed, what):
        """Refuse a filter this kind of record cannot answer, and say which can.

        Silently matching nothing would be worse: a data service has no
        `format` because it has no distributions, and a search that returned
        zero would read as "no CSV APIs" rather than "wrong question".

        A name that is not a filter anywhere falls through to
        :func:`_local_test`, which reports it as unknown rather than as
        inapplicable -- a typo and a wrong question are different mistakes.
        """
        for name in filters:
            if name in allowed or name not in _EVERY_FILTER:
                continue
            raise QueryError(
                "%s have no %r. Available: %s"
                % (what, name, ", ".join(allowed)))

    def _matching(self, records, filters, kind="dataset"):
        tests = []
        for name, value in filters.items():
            if name == "publisher":
                # Resolved by the Catalog, which knows its publishers; the
                # other filters need only the vocabulary and the record.
                wanted = {self._publishers.resolve(item)
                          for item in _require_values(value, name)}
                tests.append(lambda record, wanted=wanted:
                             record["publisher"]["id"] in wanted)
            else:
                tests.append(_local_test(name, value, self._observed(name, kind)))
        return [r for r in records if all(test(r) for test in tests)]

    # -- publishers --------------------------------------------------------

    def _rows(self) -> List[Dict[str, Any]]:
        """One row per publisher this Catalog holds something from."""
        if self._publisher_rows is None:
            counts: Dict[str, List[int]] = {}
            for column, records in enumerate((self._records, self._services)):
                for record in records:
                    pid = record["publisher"]["id"]
                    if pid:
                        counts.setdefault(pid, [0, 0])[column] += 1
            rows = [dict(self._publishers.entities[pid],
                         dataset_count=datasets, data_service_count=services)
                    for pid, (datasets, services) in counts.items()]
            rows.sort(key=lambda row: (-row["dataset_count"], row["id"]))
            self._publisher_rows = rows
        return self._publisher_rows

    def publishers(self) -> List[Publisher]:
        """Every publisher this Catalog holds something from, biggest first.

            >>> catalog.publishers()[0]                    # doctest: +SKIP
            {'id': 'radet_for_framjande_av_kommunala_analyser_kolada',
             'uri': 'http://dataportal.se/organisation/SE2220000315',
             'name': {'sv': 'Rådet för främjande av kommunala analyser - Kolada'},
             'aliases': ['kolada'], 'type': 'non_governmental_organisation',
             'homepage': ..., 'email': ..., 'identifiers': ['2220000315'],
             'dataset_count': 5863, 'data_service_count': 1}

        A plain list, not paginated and without arguments: it is a few hundred
        rows, and "who publishes the most CSV" is the ``publisher`` facet of a
        search. ``id`` is what ``publisher=`` takes and what that facet
        reports. The two counts are this Catalog's -- they equal
        ``datasets(publisher=id, limit=0).total`` and the same for data
        services -- so a publisher with nothing in scope is not listed.
        """
        return [_copy_publisher(row) for row in self._rows()]

    def publisher(self, value: str) -> Optional[PublisherDetail]:
        """One publisher, with what it publishes; ``None`` if nothing here.

            >>> catalog.publisher("scb")["facets"]["format"]   # doctest: +SKIP
            {'json': 4270, 'html': 16, ...}

        ``value`` is anything a publisher shows: its ``id``, an alias, its
        URI, an organisation number, or its name in either language -- the
        same resolver ``publisher=`` uses. The result is a ``publishers()``
        row plus ``facets``: exactly
        ``datasets(publisher=id, limit=0).facets.to_dict()`` over theme,
        format, license, access_rights, updated and language, so every value
        in it can be fed back in beside ``publisher=``.

        ``None`` for an organisation with nothing in this Catalog; a value
        nobody knows raises :class:`QueryError` with suggestions, as the
        filter does.
        """
        pid = self._publishers.resolve(value)
        row = next((row for row in self._rows() if row["id"] == pid), None)
        if row is None:
            return None
        mine = [r for r in self._records if r["publisher"]["id"] == pid]
        return dict(_copy_publisher(row),
                    facets=local_facets(mine, filters=_PUBLISHER_FACETS).to_dict())

    def datasets(
        self,
        *,
        query: Optional[str] = None,
        limit: Optional[int] = 50,
        offset: int = 0,
        facet_limit: Optional[int] = None,
        **filters: Any,
    ) -> Results[DatasetRecord]:
        """Search datasets. A list of dicts that knows its own total.

            >>> page = cat.datasets(theme="transport")       # doctest: +SKIP
            >>> page.total                                   # doctest: +SKIP
            545
            >>> text(page[0]["title"])                       # doctest: +SKIP
            'Ändamålskatalogen'

        ``limit`` caps the rows you hold -- ``None`` for every match, ``0`` for
        the count and the facets alone. ``facet_limit`` caps each facet, and
        what it cuts is counted in :attr:`~dataportalen.Facets.omitted`.
        """
        for unsupported in ("sort", "page_size"):
            if unsupported in filters:
                raise QueryError(
                    "%r is a search-index argument; a local catalogue matches "
                    "every record at once" % unsupported)
        if query is not None:
            filters = dict(filters, query=query)
        self._window(limit, offset)
        self._check(filters, DATASET_FILTERS + ("query",) + _DATE_FILTERS,
                    "datasets")
        found = self._matching(self._records, filters)
        facets = local_facets(found, limit=facet_limit, filters=DATASET_FILTERS,
                              names=self._publishers.names)
        window = found[offset:] if limit is None else found[offset:offset + limit]
        return Results(window, total=len(found), offset=offset, limit=limit,
                       facets=facets)

    def facets(self, limit: Optional[int] = None) -> Facets:
        """Every dataset facet: which values each filter has, with counts.

        The one thing a search cannot tell you: the options, before you search.

            >>> catalog.facets()["publisher"][0]           # doctest: +SKIP
            FacetValue(value='radet_for_..._kolada', count=5863)
            >>> list(catalog.facets())                     # doctest: +SKIP
            ['publisher', 'publisher_type', 'theme', 'keyword', ...]

        The same :class:`~dataportalen.Facets` a search carries as
        ``page.facets``, counted over every dataset this Catalog holds --
        about half a second for the whole corpus. ``limit`` caps each facet
        and what it cuts is counted in :attr:`~dataportalen.Facets.omitted`.

        Data service facets are ``data_services(limit=0).facets``, the
        identical structure over that kind's own filters.
        """
        return local_facets(self._records, limit=limit, filters=DATASET_FILTERS,
                            names=self._publishers.names)

    def data_services(
        self,
        *,
        query: Optional[str] = None,
        limit: Optional[int] = 50,
        offset: int = 0,
        facet_limit: Optional[int] = None,
        **filters: Any,
    ) -> Results[DataServiceRecord]:
        """Search data services -- the registry's APIs rather than its files.

            >>> cat.data_services(service_type="view")     # doctest: +SKIP

        Same signature and same record shape as :meth:`datasets`, and served
        from the same file. Four filters do not apply and are refused rather
        than quietly matching nothing: a data service has no distributions
        (so no ``format``), no ``accrual_periodicity`` (no ``updated``), and
        across all 599 of them ``place`` is set on 8% and ``language`` has a
        single value. There are no date filters either -- ``modified`` is on
        7.5% and ``issued`` on 0.8%.
        """
        for unsupported in ("sort", "page_size"):
            if unsupported in filters:
                raise QueryError(
                    "%r is a search-index argument; a local catalogue matches "
                    "every record at once" % unsupported)
        if query is not None:
            filters = dict(filters, query=query)
        self._window(limit, offset)
        self._check(filters, DATA_SERVICE_FILTERS + ("query",), "data services")
        found = self._matching(self._services, filters, "data_service")
        facets = local_facets(found, limit=facet_limit,
                              filters=DATA_SERVICE_FILTERS,
                              names=self._publishers.names)
        window = found[offset:] if limit is None else found[offset:offset + limit]
        return Results(window, total=len(found), offset=offset, limit=limit,
                       facets=facets)

    def get(self, uri: str, format: str = "dict") -> Any:
        """One record by its URI, or ``None`` if this copy has no such thing.

            >>> cat.get("https://data.svk.se/dataset/c3c2...")   # doctest: +SKIP
            {'uri': 'https://data.svk.se/dataset/c3c2...', 'title': {...}, ...}

        ``format="dict"`` is local and immediate. Any other format is fetched
        from the registry as RDF -- ``"turtle"``, ``"rdf/xml"``, ``"n-triples"``
        or a media type -- and returned as text. That is the only request this
        class makes outside a download.

        A handful of dataset URIs are shared by two records, because the
        same dataset was harvested into two catalogues; the first is returned.
        """
        if format == "dict":
            if self._by_uri is None:
                self._by_uri = {}
                for record in self._records + self._services:
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


#: The registry's reasons that are a server answering with an error. Every
#: standard HTTP reason phrase from 400 up, the bare status numbers, and the
#: three non-standard phrasings the registry's checker has been seen to use.
#: Matched case-insensitively against the whole message.
#:
#: Except 429. Too Many Requests is a server telling the registry's checker
#: to slow down, which says the file is there, not that it is gone.
_DEAD_STATUSES = [status for status in HTTPStatus
                  if status >= 400 and status != HTTPStatus.TOO_MANY_REQUESTS]
_DEAD_REASONS = frozenset(
    [status.phrase.casefold() for status in _DEAD_STATUSES]
    + [str(int(status)) for status in _DEAD_STATUSES]
    + ["file not found", "access denied", "site not found"]
)

#: The one failed request that is an answer: the host is not in DNS. The
#: message embeds the URL, so it is matched by its ending, not as a whole.
#: A reset, refused or unreachable connection is a host that is there.
_DEAD_HOST = "reason: getaddrinfo enotfound "


def _is_dead(reason: Any) -> bool:
    """Whether the registry's reason for `broken` says the file is gone.

    The registry records no status code for a broken link (it is null on all
    11,900), only a message, and the messages are two different things:

    ========================================================  ======
    an HTTP error: Not Found 395, Forbidden 212, Internal
    Server Error 40, Bad Request 33, Unauthorized 14,
    Access Denied 8, ...                                        718
    a host that is not in DNS: `request to ... failed,
    reason: getaddrinfo ENOTFOUND ...`                           185
    no usable answer: no message 5,263, `request to ...
    failed` for any other reason 2,698, Too Many Requests
    2,624, `timeout` 255, `maximum redirect` 11, an ftp://
    URL with credentials 7                                    10,858
    ========================================================  ======

    The first two are dead: a server saying no, or no server to ask. The
    third is the registry's checker failing to get through: 7,091 of SCB's
    14,228 links are in it, because api.scb.se resets the checker's
    connections, and each of those files answers 200 to an ordinary GET.
    Treating all of it as dead removed 4,270 of SCB's 4,306 datasets from the
    default catalogue.

    An allow-list, not a deny-list: a message the checker invents next year
    is unverified until someone adds it here, so the default keeps files
    rather than dropping them.
    """
    if not reason:
        return False
    text = str(reason).strip().casefold()
    return text in _DEAD_REASONS or _DEAD_HOST in text


def _present(record: Dict[str, Any]) -> Dict[str, Any]:
    """A stored record as the package hands it out.

    The database keeps what the registry said; this turns it into what a
    caller reads, and it is the one place that happens, so `Catalog` and
    :func:`read_catalog` cannot disagree about a record.

    The download marks every file the registry called broken with
    ``broken: {reason, checked}``. Here the ones whose reason is not an HTTP
    error become ``unverified`` instead -- same two keys -- so ``broken``
    means dead and nothing else. Because it is done on reading, changing the
    list is not a re-download.
    """
    for dist in record.get("distributions") or ():
        mark = dist.get("broken")
        if mark is not None and not _is_dead(mark.get("reason")):
            dist["unverified"] = dist.pop("broken")
        # What the distribution is -- a file, an API, a web page -- read out
        # of its metadata. Added here for the same reason as the split above:
        # the rules can change without anybody downloading anything.
        dist["kind"] = classify(dist)

    # `id` is what publisher= takes and what the publisher facet reports;
    # without it a record had no public route to its own filter value, and an
    # alias would point at nothing. Both are the package's, not the
    # registry's, and both can change between releases (the organisation
    # table, aliases.json) while the database does not -- so they are added
    # here rather than stored, and a new alias needs no rebuild.
    record["publisher"] = _publisher_dict(record.get("publisher") or {})
    return record


def _publisher_dict(agent: Dict[str, Any]) -> Dict[str, Any]:
    """A registry agent as the nested ``publisher`` of a record."""
    slug = _org_slug(agent)
    return {
        "id": slug,
        "uri": agent.get("uri"),
        "name": agent.get("name") or {},
        "aliases": aliases_for(slug) if slug else [],
        "type": agent.get("type"),
        "homepage": agent.get("homepage"),
        "email": agent.get("email"),
        "identifiers": list(agent.get("identifiers") or []),
    }


#: The filters whose facets describe a publisher. `keyword` is left out -- one
#: publisher has 4,230 distinct keywords -- and so are `publisher` and
#: `publisher_type`, which are the publisher itself.
_PUBLISHER_FACETS = ("theme", "format", "kind", "license", "access_rights",
                     "updated", "language")


class _Publishers:
    """Who publishes, worked out from every record in the database.

    Built before ``access_rights`` and ``exclude_broken`` narrow anything, so
    who a publisher *is* does not depend on scope -- only how much of theirs a
    Catalog holds does. Built from the scoped records, Region Uppsala would
    be a regional authority in one Catalog and a local one in another.

    A publisher is one or more registry agents, one per URI: 8 of the 356
    have two. Its attributes are those of **one** of them -- the URI the
    package's table knows first, then the one on most records, then the
    lowest URI -- never a merge. Field-by-field majority was measured against
    this: it differs for 2 publishers and builds an object no agent matches
    (Folkhälsomyndigheten's fohm-app URI beside an organisation number only
    its other agent carries).
    """

    def __init__(self, seen: Iterable[Tuple[Dict[str, Any], int]]) -> None:
        # `seen` is (publisher dict, how many records carry it): one pair per
        # record from a database, one per agent from the registry's own
        # publisher facet -- so both classes work out who is who the same way.
        agents: Dict[str, Dict[Optional[str], List[Any]]] = {}
        for publisher, n in seen:
            pid = (publisher or {}).get("id")
            if not pid:
                continue                 # 27 records name no publisher at all
            slot = agents.setdefault(pid, {}).setdefault(
                publisher.get("uri"), [publisher, 0])
            slot[1] += n
        #: Every URI each publisher goes by -- what the registry is asked for.
        self.uris = {pid: sorted(u for u in by_uri if u)
                     for pid, by_uri in agents.items()}

        self.entities: Dict[str, Dict[str, Any]] = {}
        for pid, by_uri in agents.items():
            ordered = sorted(by_uri.items(), key=lambda item: (
                publisher_for(item[0]) is None, -item[1][1], item[0] or ""))
            self.entities[pid] = ordered[0][1][0]
        self.names = {pid: agent["name"] for pid, agent in self.entities.items()}

        # What a publisher object shows, the filter takes. In tiers, and the
        # first tier to claim a key keeps it: an id always means itself, even
        # where it is also somebody else's slugified name (one such case).
        keys: Dict[str, str] = {}
        ordered_ids = sorted(agents)
        for pid in ordered_ids:
            keys.setdefault(pid, pid)
        for pid in ordered_ids:
            for alias in aliases_for(pid):
                keys.setdefault(alias, pid)
        for pid in ordered_ids:
            for uri in sorted(u for u in agents[pid] if u):
                keys.setdefault(uri, pid)
        for pid in ordered_ids:
            for agent, _ in agents[pid].values():
                for identifier in agent.get("identifiers") or ():
                    keys.setdefault(str(identifier).strip(), pid)
        for pid in ordered_ids:
            for agent, _ in agents[pid].values():
                name = agent.get("name")
                for spelling in (name.values() if isinstance(name, dict) else [name]):
                    if spelling:
                        keys.setdefault(slugify(spelling), pid)
        self._keys = keys

    def resolve(self, value: Any) -> str:
        """The id of the publisher ``value`` names.

        An id, an alias, a URI, an organisation number or a name in either
        language. An organisation the package knows but this database does
        not hold still resolves, to an id nothing carries -- so the filter
        answers 0 and ``publisher()`` answers ``None`` rather than raising.
        Only a value nobody knows is an error.
        """
        if not isinstance(value, str) or not value.strip():
            raise QueryError("publisher needs a value; got %r" % (value,))
        raw = value.strip()
        for key in (raw, slugify(raw)):
            if key in self._keys:
                return self._keys[key]
        try:
            uris = resolve_publisher(raw)
        except QueryError:
            known = sorted(set(self.entities) | {
                alias for pid in self.entities for alias in aliases_for(pid)})
            raise _suggest_from(slugify(raw) or raw, known, "publisher") from None
        for uri in uris:
            if uri in self._keys:
                return self._keys[uri]
        for uri in uris:
            known = publisher_for(uri)
            if known:
                return known
        return slugify(raw)


def _copy_publisher(row: Dict[str, Any]) -> Dict[str, Any]:
    """A publisher dict the caller may change without changing ours."""
    return dict(row, name=dict(row["name"]), aliases=list(row["aliases"]),
                identifiers=list(row["identifiers"]))


def read_catalog(path: str) -> List[Union[DatasetRecord, DataServiceRecord]]:
    """Every record in a catalogue database, in insertion order.

    For reading a copy without building a :class:`Catalog` around it. The
    whole registry as downloaded: every ``access_rights`` value and every
    file, the dead ones carrying ``broken`` and the ones the registry could
    not check carrying ``unverified``.
    """
    if not os.path.exists(path):
        # _connect would create an empty database here and this would return
        # [] -- a typo in the path read as "a catalogue with nothing in it".
        raise FileNotFoundError("%s does not exist" % path)
    db = _connect(path)
    try:
        return [_present(json.loads(row["doc"]))
                for row in db.execute("SELECT doc FROM record")]
    finally:
        db.close()


def _local_values(record: Dict[str, Any], filter: str) -> List[str]:
    """Every value one filter reads out of one record, blanks excluded.

    33 datasets carry a keyword that is nothing but a newline and four
    spaces. It is not a value anybody can filter on, so it does not belong in
    facets that promise every row can be fed back in.
    """
    return [v for v in _local_values_raw(record, filter) if str(v).strip()]


def _local_values_raw(record: Dict[str, Any], filter: str) -> List[str]:
    if filter in ("theme", "themes"):
        return list(record.get("themes") or [])
    if filter == "language":
        return list(record.get("languages") or [])
    if filter == "keyword":
        keywords = record.get("keywords") or []
        if isinstance(keywords, dict):                      # {sv: [...], en: [...]}
            return [k for values in keywords.values() for k in values]
        return list(keywords)
    if filter == "format":
        return [d["format"] for d in (record.get("distributions") or [])
                if d.get("format")]
    if filter == "kind":
        return [d.get("kind") or classify(d)
                for d in (record.get("distributions") or [])]
    if filter == "updated":
        value = record.get("accrual_periodicity")
        return [value] if value else []
    if filter == "license":
        value = (record.get("license") or {}).get("id")
        return [value] if value else []
    if filter == "access_rights":
        value = record.get(filter)
        return [value] if value else []
    if filter == "publisher":
        publisher = record.get("publisher") or {}
        slug = publisher.get("id") or _org_slug(publisher)
        return [slug] if slug else []
    if filter == "publisher_type":
        kind = (record.get("publisher") or {}).get("type")
        return [kind] if kind else []
    if filter == "service_type":
        value = record.get("service_type")
        return [value] if value else []
    raise QueryError(
        "cannot count values for %r; try one of: %s"
        % (filter, ", ".join(sorted(set(DATASET_FILTERS + DATA_SERVICE_FILTERS)))))


def _solr_stamp(value: Any) -> str:
    """A timestamp the index will accept in a range, from anything date-ish."""
    text = _iso(value) if not isinstance(value, str) else value
    text = (text or "").strip()
    if not text:
        raise QueryError("since needs a timestamp, got %r" % (value,))
    if len(text) == 10:
        text += "T00:00:00Z"
    elif not text.endswith("Z"):
        text = text.split("+")[0].split(".")[0]
        text += "Z"
    return text


def _require_date(value: Any, name: str) -> str:
    """A date bound that is really a date.

    `_date` normalises the shapes publishers write, but it checked the shape
    and not the calendar, so `modified_after="2024-13-45"` became a bound that
    silently matched 12,584 datasets and `modified_after=None` became the
    empty string, which every stamp sorts after. Both looked like an answer.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise QueryError("%s needs a date; got %r" % (name, value))
    stamp = _date(value)
    if not stamp:
        raise QueryError("%s needs a date; got %r" % (name, value))
    try:
        _dt.datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise QueryError(
            "%s needs a real date such as \"2024-01-01\", \"2024-01\" or "
            "\"2024\"; got %r" % (name, value)) from None
    return stamp


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

    **Exact first.** A record stores a slug, not a URI, so if the file contains
    the value as given then that value is the whole answer. Expanding it
    through the vocabulary would over-match: ``json`` also resolves to
    ``application/json+zip``, whose own slug is ``json_in_a_zip``, so
    ``format="json"`` would quietly return 54 datasets the facet counts
    under a different name -- and the facet's counts would stop agreeing
    with the searches its values produce.

    Failing that, the vocabulary resolves synonyms and extension aliases, so
    ``xlsx`` still finds what is stored as ``microsoft_excel_xml_xlsx``. And a
    value the table has never heard of but the file does contain -- ``parquet``,
    a bare GeoNames id -- resolves to itself, because everything a facet
    reports has to be usable as a filter. Only a value that is neither known
    nor present is an error, and then the suggestions come from the file.
    """
    slug = slugify(value)
    if observed is not None and slug in observed:
        return {slug}
    slugs = {slug}
    try:
        slugs.update(slug_for(uri) for uri in resolve(value, name))
    except QueryError:
        if observed is None or not (slugs & set(observed)):
            raise
    return slugs


def _suggest_from(value: str, observed: Any, what: str) -> QueryError:
    """The unknown-value error, with the suggestions drawn from this file."""
    close = difflib.get_close_matches(value, sorted(observed), n=3, cutoff=0.7)
    hint = (" Did you mean: %s?" % ", ".join(close)) if close else ""
    return QueryError("unknown %s %r.%s" % (what, value, hint))


def _local_test(name: str, value: Any, observed: Optional[Any] = None) -> Any:
    """One filter as a predicate over a record."""
    if name in ("query", "keyword", "publisher", "theme", "format", "kind",
                "license", "access_rights", "updated", "language",
                "publisher_type", "service_type"):
        _require_values(value, name)

    if name == "text":
        raise QueryError("'text' is called 'query' now: datasets(query=%r)"
                         % (value,))

    if name == "query":
        # The one free-text input: a phrase, matched as a case-insensitive
        # substring of the title, the description and every keyword, in both
        # languages. `query` is the conventional name for it; the matching is
        # what `text=` did. Splitting it into terms that must all match was
        # considered and measured -- "air quality" goes from 15 datasets to
        # 481 -- and is a different feature from a rename.
        needle = str(value).lower()

        def text_test(record: Dict[str, Any]) -> bool:
            parts = [record.get("title"), record.get("description")]
            parts.extend(_local_values(record, "keyword"))
            for part in parts:
                if isinstance(part, dict):                  # a localized field
                    part = " ".join(str(v) for v in part.values())
                if part and needle in str(part).lower():
                    return True
            return False

        return text_test

    field, _, edge = name.rpartition("_")
    if edge in ("after", "before") and field in ("modified", "issued"):
        # Named after the record field they read, so there is nothing to
        # translate: modified_after= bounds record["modified"]. Any other
        # *_after falls through to the unknown-filter message, which names
        # these four -- the only way to learn what updated_after became.
        bound = _iso_stamp(_require_date(value, name))
        after = name.endswith("_after")

        def date_test(record):
            stamp = _iso_stamp(record.get(field))
            if not stamp:
                return False
            return stamp >= bound if after else stamp <= bound

        return date_test

    if name == "keyword":
        # An ordinary filter now: exact, case-insensitive, any of a list, and
        # an unknown value is an error. It used to be three special cases --
        # case-sensitive, substring when the value was not in the file, and
        # all-of-a-list where every other filter is any-of. Substring search
        # is what `query` is for; `BARN` must not match `BARNOMSORG`.
        wanted = set()
        for item in _as_list(value):
            key = _fold_keyword(item)
            if observed is not None and key not in observed:
                raise _suggest_from(key, observed, "keyword")
            wanted.add(key)

        def keyword_test(record: Dict[str, Any]) -> bool:
            return any(_fold_keyword(keyword) in wanted
                       for keyword in _local_values(record, "keyword"))

        return keyword_test

    if name == "kind":
        # The package's own classification, not a vocabulary: the values are
        # the keys of retrieval.KINDS and nothing else.
        wanted = set()
        for item in _as_list(value):
            key = slugify(item)
            if key not in KINDS:
                raise _suggest_from(key, KINDS, "kind")
            wanted.add(key)
        return lambda record: bool(wanted & set(_local_values(record, "kind")))

    if name in ("theme", "format", "license", "access_rights", "updated",
                "language", "publisher_type", "service_type"):
        wanted = set()
        for item in _as_list(value):
            wanted.update(_local_slugs(item, name, observed))
        return lambda record: bool(wanted & set(_local_values(record, name)))

    raise QueryError(
        "unknown filter %r; supported: %s, query, %s"
        % (name, ", ".join(sorted(set(DATASET_FILTERS + DATA_SERVICE_FILTERS))),
           ", ".join(_DATE_FILTERS)))
