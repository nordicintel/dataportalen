"""The asyncio client, mirroring :class:`dataportal_se.Dataportal`.

Requires ``httpx``::

    pip install "dataportal[async]"

Usage::

    import asyncio
    from dataportal_se.aio import AsyncDataportal

    async def main():
        async with AsyncDataportal() as dp:
            page = await dp.datasets(title="bidrag", limit=10)
            for dataset in page:
                print(dataset.title)

    asyncio.run(main())

Model objects returned here carry no client, so their synchronous
convenience methods (``dataset.publisher()``, ``dataset.distributions()``)
are unavailable; use the client's ``lookup``/``lookup_many`` coroutines
instead.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import os
import random
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Type,
    Union,
)

from .client import DEFAULT_BASE_URL, DUMP_URL, MAX_LIMIT, _LRU, _RETRY_STATUSES
from .exceptions import HTTPError, ParseError, TransportError
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
from .rdf import DEFAULT_LANGUAGES, Graph
from .transport import DEFAULT_USER_AGENT, AsyncHttpxTransport, Response, build_url

__all__ = ["AsyncDataportal"]

_DateLike = Union[str, _dt.date, _dt.datetime]


class AsyncDataportal:
    """Asynchronous client for ``admin.dataportal.se``.

    The method names, arguments and return types match
    :class:`dataportal_se.Dataportal`; every request-issuing method is a
    coroutine, and the iterators are async generators.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        transport: Optional[AsyncHttpxTransport] = None,
        timeout: float = 30.0,
        languages: Sequence[str] = DEFAULT_LANGUAGES,
        user_agent: Optional[str] = None,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        cache_size: int = 512,
        public_only: bool = True,
        default_sort: Optional[str] = SORT_MODIFIED_DESC,
        max_concurrency: int = 8,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.languages = tuple(languages)
        self.max_retries = max(0, int(max_retries))
        self.backoff_factor = backoff_factor
        self.public_only = public_only
        self.default_sort = default_sort
        self.user_agent = user_agent or os.environ.get("DATAPORTAL_USER_AGENT") or DEFAULT_USER_AGENT
        self._transport = transport if transport is not None else AsyncHttpxTransport()
        self._owns_transport = transport is None
        self._cache = _LRU(cache_size)
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))

    # -- lifecycle ---------------------------------------------------------

    async def aclose(self) -> None:
        if self._owns_transport:
            await self._transport.aclose()

    async def __aenter__(self) -> "AsyncDataportal":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    def clear_cache(self) -> None:
        self._cache.clear()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<AsyncDataportal %s>" % self.base_url

    # -- plumbing ----------------------------------------------------------

    def _build_headers(self, accept: Optional[str]) -> Dict[str, str]:
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}
        if accept:
            headers["Accept"] = accept
        return headers

    async def _sleep_for(self, attempt: int, retry_after: Optional[float]) -> None:
        if retry_after is not None:
            await asyncio.sleep(min(retry_after, 60.0))
            return
        delay = self.backoff_factor * (2 ** attempt)
        await asyncio.sleep(min(delay + random.uniform(0, delay * 0.1), 30.0))

    async def request(
        self,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        *,
        method: str = "GET",
        accept: Optional[str] = None,
        absolute_url: Optional[str] = None,
    ) -> Response:
        """Issue one request, with retries and bounded concurrency."""
        from .client import Dataportal  # local import avoids a cycle at import time

        url = absolute_url or build_url(self.base_url, path, params)
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            async with self._semaphore:
                try:
                    response = await self._transport.request(
                        method, url, headers=self._build_headers(accept), timeout=self.timeout
                    )
                except TransportError as exc:
                    last_exc = exc
                    if attempt >= self.max_retries:
                        raise
                    await self._sleep_for(attempt, None)
                    continue
            if response.status in _RETRY_STATUSES and attempt < self.max_retries:
                retry_after = response.headers.get("retry-after")
                try:
                    parsed = float(retry_after) if retry_after else None
                except ValueError:
                    parsed = None
                await self._sleep_for(attempt, parsed)
                continue
            Dataportal._raise_for_status(response)
            return response
        raise last_exc or TransportError("request to %s failed" % url)

    async def get_json(self, path: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        response = await self.request(path, params, accept="application/json")
        return response.json()

    # -- search ------------------------------------------------------------

    def _finalize_query(self, query: Union[str, Q, None]) -> str:
        # Reuse the sync implementations: they only read attributes both
        # clients define (public_only, base_url, context_uri).
        from .client import Dataportal

        return Dataportal._finalize_query(self, query)  # type: ignore[arg-type]

    async def search_raw(
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
        response = await self.request("/store/search", params, accept="application/json")
        data = response.json()
        if not isinstance(data, dict):
            raise ParseError("unexpected search response: %r" % (type(data),))
        return data

    async def search(
        self,
        query: Union[str, Q, None] = None,
        *,
        model: Optional[Type[Entry]] = None,
        limit: int = 50,
        offset: int = 0,
        sort: Optional[str] = ...,  # type: ignore[assignment]
        **kwargs: Any,
    ) -> SearchPage:
        """Run a Solr search and return a page of typed entries."""
        data = await self.search_raw(query, limit=limit, offset=offset, sort=sort, **kwargs)
        children = ((data.get("resource") or {}).get("children")) or []
        entries = [
            wrap_entry(child, client=None, languages=self.languages, default=model)
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
            client=None,
        )

    async def iter_search(
        self,
        query: Union[str, Q, None] = None,
        *,
        model: Optional[Type[Entry]] = None,
        limit: Optional[int] = None,
        page_size: int = MAX_LIMIT,
        offset: int = 0,
        sort: Optional[str] = ...,  # type: ignore[assignment]
        **kwargs: Any,
    ) -> AsyncIterator[Entry]:
        """Yield every hit, fetching pages as needed."""
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
            page = await self.search(
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

    async def count(self, query: Union[str, Q, None] = None, **kwargs: Any) -> int:
        data = await self.search_raw(query, limit=1, offset=0, sort=None, **kwargs)
        return int(data.get("results", 0) or 0)

    async def facet(
        self,
        field: str,
        query: Union[str, Q, None] = None,
        *,
        limit: int = 100,
        min_count: int = 1,
        matches: Optional[str] = None,
        **kwargs: Any,
    ) -> Facet:
        page = await self.search(
            query, limit=1, sort=None, facet_fields=[field], facet_limit=limit,
            facet_min_count=min_count, facet_matches=matches, **kwargs,
        )
        return page.facet(field) or Facet(field, [])

    # -- single entries ----------------------------------------------------

    async def entry_raw(
        self,
        context_id: Union[str, int],
        entry_id: Union[str, int],
        *,
        part: str = "metadata",
        recursive: Union[bool, str] = False,
        format: Optional[str] = None,
    ) -> Response:
        if part not in ("metadata", "entry", "resource"):
            raise ValueError("part must be 'metadata', 'entry' or 'resource'")
        params: Dict[str, Any] = {}
        if recursive:
            params["recursive"] = "dcat" if recursive is True else recursive
        if format:
            params["format"] = format
        return await self.request(
            "/store/%s/%s/%s" % (context_id, part, entry_id), params, accept=format
        )

    async def metadata_graph(
        self,
        context_id: Union[str, int],
        entry_id: Union[str, int],
        *,
        recursive: Union[bool, str] = True,
    ) -> Graph:
        response = await self.entry_raw(
            context_id, entry_id, part="metadata", recursive=recursive,
            format="application/rdf+json",
        )
        return Graph(response.json())

    async def entry_graph(
        self, context_id: Union[str, int], entry_id: Union[str, int]
    ) -> Graph:
        response = await self.entry_raw(
            context_id, entry_id, part="entry", format="application/rdf+json"
        )
        return Graph(response.json())

    async def entry(
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
        """Fetch one entry by its registry ids.

        With ``with_info`` the metadata and envelope requests run concurrently.
        """
        if info is None and with_info:
            metadata, info = await asyncio.gather(
                self.metadata_graph(context_id, entry_id, recursive=recursive),
                self.entry_graph(context_id, entry_id),
            )
        else:
            metadata = await self.metadata_graph(context_id, entry_id, recursive=recursive)
        payload: Dict[str, Any] = {
            "metadata": metadata.to_json(),
            "contextId": str(context_id),
            "entryId": str(entry_id),
        }
        if info is not None:
            payload["info"] = info.to_json()
        if rights:
            payload["rights"] = list(rights)
        return wrap_entry(payload, client=None, languages=self.languages, default=model)

    async def lookup(
        self,
        uri: str,
        *,
        model: Optional[Type[Entry]] = None,
        use_cache: bool = True,
    ) -> Optional[Entry]:
        """Find a managed entry by its resource URI."""
        if use_cache:
            cached = self._cache.get(uri)
            if cached is not None:
                return cached.as_(model) if model else cached
        page = await self.search(Q.resource(uri), limit=1, sort=None, model=model)
        found = page.entries[0] if page.entries else None
        if found is not None and use_cache:
            self._cache.set(uri, found)
        return found

    async def lookup_many(
        self,
        uris: Sequence[str],
        *,
        model: Optional[Type[Entry]] = None,
        use_cache: bool = True,
        batch_size: int = 20,
    ) -> List[Entry]:
        """Resolve several resource URIs; batches run concurrently."""
        wanted = [u for u in dict.fromkeys(uris) if u]
        found: Dict[str, Entry] = {}
        pending: List[str] = []
        for uri in wanted:
            cached = self._cache.get(uri) if use_cache else None
            if cached is not None:
                found[uri] = cached
            else:
                pending.append(uri)
        batches = [pending[i:i + batch_size] for i in range(0, len(pending), batch_size)]
        pages = await asyncio.gather(
            *(
                self.search(
                    Q.resource(*batch), limit=min(MAX_LIMIT, max(len(batch), 1)),
                    sort=None, model=model,
                )
                for batch in batches
            )
        )
        for page in pages:
            for entry in page.entries:
                uri = entry.resource_uri
                if uri:
                    found[uri] = entry
                    if use_cache:
                        self._cache.set(uri, entry)
        out = [found[u] for u in wanted if u in found]
        return [e.as_(model) for e in out] if model else out

    # -- typed searches ----------------------------------------------------

    def context_uri(self, context_id: Union[str, int]) -> str:
        text = str(context_id)
        return text if "://" in text else "%s/store/%s" % (self.base_url, text)

    def _entity_query(self, rdf_type: Optional[str], **filters: Any) -> Q:
        from .client import Dataportal

        return Dataportal._entity_query(self, rdf_type, **filters)  # type: ignore[arg-type]

    async def datasets(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        sort: Optional[str] = ...,  # type: ignore[assignment]
        facet_fields: Optional[Sequence[str]] = None,
        **filters: Any,
    ) -> SearchPage:
        """Search datasets (``dcat:Dataset``)."""
        return await self.search(
            self._entity_query(DCAT.Dataset, **filters),
            model=Dataset, limit=limit, offset=offset, sort=sort,
            facet_fields=facet_fields,
        )

    def iter_datasets(
        self,
        *,
        limit: Optional[int] = None,
        page_size: int = MAX_LIMIT,
        sort: Optional[str] = ...,  # type: ignore[assignment]
        **filters: Any,
    ) -> AsyncIterator[Entry]:
        """Yield matching datasets across pages."""
        return self.iter_search(
            self._entity_query(DCAT.Dataset, **filters),
            model=Dataset, limit=limit, page_size=page_size, sort=sort,
        )

    async def dataset(
        self,
        uri: Optional[str] = None,
        *,
        context_id: Optional[Union[str, int]] = None,
        entry_id: Optional[Union[str, int]] = None,
        recursive: bool = True,
    ) -> Optional[Dataset]:
        """Fetch a single dataset, by its own URI or by registry ids."""
        if uri:
            found = await self.lookup(uri, model=Dataset)
            if found is None:
                return None
            if recursive and found.context_id and found.entry_id:
                return await self.entry(  # type: ignore[return-value]
                    found.context_id, found.entry_id, recursive=True,
                    model=Dataset, info=found.info, rights=found.rights,
                )
            return found  # type: ignore[return-value]
        if context_id is None or entry_id is None:
            raise TypeError("pass either uri= or both context_id= and entry_id=")
        return await self.entry(  # type: ignore[return-value]
            context_id, entry_id, recursive=recursive, model=Dataset
        )

    async def distributions(self, *, limit: int = 50, offset: int = 0, **filters: Any) -> SearchPage:
        return await self.search(
            self._entity_query(DCAT.Distribution, **filters),
            model=Distribution, limit=limit, offset=offset,
        )

    async def data_services(self, *, limit: int = 50, offset: int = 0, **filters: Any) -> SearchPage:
        return await self.search(
            self._entity_query(DCAT.DataService, **filters),
            model=DataService, limit=limit, offset=offset,
        )

    async def dataset_series(self, *, limit: int = 50, offset: int = 0, **filters: Any) -> SearchPage:
        return await self.search(
            self._entity_query(DCAT.DatasetSeries, **filters),
            model=DatasetSeries, limit=limit, offset=offset,
        )

    async def catalogs(self, *, limit: int = 50, offset: int = 0, **filters: Any) -> SearchPage:
        return await self.search(
            self._entity_query(DCAT.Catalog, **filters),
            model=Catalog, limit=limit, offset=offset,
        )

    async def agents(self, *, limit: int = 50, offset: int = 0, **filters: Any) -> SearchPage:
        return await self.search(
            self._entity_query(FOAF.Agent, **filters),
            model=Agent, limit=limit, offset=offset,
        )

    async def agent(self, uri: str) -> Optional[Agent]:
        return await self.lookup(uri, model=Agent)  # type: ignore[return-value]

    async def standards(self, *, limit: int = 50, offset: int = 0, **filters: Any) -> SearchPage:
        return await self.search(
            self._entity_query(DCTERMS.Standard, **filters),
            model=Standard, limit=limit, offset=offset,
        )

    def datasets_in_context(
        self,
        context_id: Union[str, int],
        *,
        limit: Optional[int] = None,
        page_size: int = MAX_LIMIT,
    ) -> AsyncIterator[Entry]:
        return self.iter_datasets(context=context_id, limit=limit, page_size=page_size)

    # -- registry-wide statistics -----------------------------------------

    async def organisations(self) -> List[OrganisationStats]:
        """Dataset counts per publishing organisation."""
        data = await self.get_json("/charts/orgData.json")
        labels = data.get("labels") or []
        values = data.get("values") or []
        series = (data.get("series") or [[]])[0]
        out: List[OrganisationStats] = []
        for index, uri in enumerate(values):
            name = labels[index] if index < len(labels) else ""
            count = int(series[index]) if index < len(series) else 0
            out.append(OrganisationStats(uri, name, count))
        return out

    async def organisation_summary(self) -> Dict[str, int]:
        data = await self.get_json("/charts/orgData.json")
        return {
            "datasets": int(data.get("datasetCount", 0) or 0),
            "independent_data_services": int(data.get("independentDataserviceCount", 0) or 0),
            "publishers": int(data.get("publisherCount", 0) or 0),
        }

    async def link_check_reports(
        self,
        *,
        limit: int = MAX_LIMIT,
        failing_only: bool = False,
        context: Optional[Union[str, int]] = None,
    ) -> List[LinkCheckReport]:
        """The nightly link check, one report per catalog."""
        query = Q.rdf_type(Types.LINK_CHECK_REPORT)
        if context is not None:
            query = query & Q.context(self.context_uri(context))
        reports: List[LinkCheckReport] = []
        async for entry in self.iter_search(
            query, model=LinkCheckReport, limit=limit, sort=None
        ):
            reports.append(entry)  # type: ignore[arg-type]
        if failing_only:
            reports = [r for r in reports if (r.failed or 0) > 0]
        return reports

    async def metadata_quality(
        self,
        *,
        limit: int = MAX_LIMIT,
        include_total: bool = True,
    ) -> List[MetadataQuality]:
        """DCAT-AP metadata quality (MQA) scores, one per catalog."""
        types = [Types.MQA, Types.MQA_TOTAL] if include_total else [Types.MQA]
        out: List[MetadataQuality] = []
        async for entry in self.iter_search(
            Q.rdf_type(*types), model=MetadataQuality, limit=limit, sort=None
        ):
            out.append(entry)  # type: ignore[arg-type]
        return out

    async def catalog_statistics(
        self, *, limit: int = 1, offset: int = 0
    ) -> List[CatalogStatistics]:
        """Nightly registry-wide snapshots, newest first."""
        page = await self.search(
            Q.rdf_type(Types.CATALOG_STATISTICS), model=CatalogStatistics,
            limit=limit, offset=offset, sort=SORT_MODIFIED_DESC,
        )
        return list(page.entries)  # type: ignore[arg-type]

    async def context_names(self, *, limit: Optional[int] = None) -> Dict[str, str]:
        """Map ``contextId`` to the catalog's title."""
        out: Dict[str, str] = {}
        async for catalog in self.iter_search(
            self._entity_query(DCAT.Catalog), model=Catalog, limit=limit, sort=None
        ):
            if catalog.context_id and catalog.title:
                out[str(catalog.context_id)] = catalog.title
        return out

    async def context_publishers(self, *, limit: Optional[int] = None) -> Dict[str, str]:
        """Map ``contextId`` to the publishing organisation's URI."""
        out: Dict[str, str] = {}
        async for catalog in self.iter_search(
            self._entity_query(DCAT.Catalog), model=Catalog, limit=limit, sort=None
        ):
            if catalog.context_id and catalog.publisher_uri:
                out[str(catalog.context_id)] = catalog.publisher_uri
        return out

    async def datasets_per_organisation(self, *, day: int = 0) -> List[Tuple[str, str, int]]:
        """``(contextId, organisation name, dataset count)`` for one day."""
        stats, names = await asyncio.gather(
            self.catalog_statistics(limit=1, offset=day), self.context_names()
        )
        if not stats:
            return []
        rows = [
            (ctx, names.get(ctx, ""), count)
            for ctx, count in stats[0].datasets_per_context.items()
        ]
        rows.sort(key=lambda row: row[2], reverse=True)
        return rows

    # -- the nightly dump --------------------------------------------------

    async def download_dump(
        self,
        destination: str,
        *,
        chunk_size: int = 1 << 20,
        url: str = DUMP_URL,
        progress: Optional[Callable[[int], None]] = None,
    ) -> str:
        """Stream the nightly RDF/XML dump to ``destination``."""
        total = 0
        async with self._transport.stream(
            "GET", url, headers=self._build_headers("application/rdf+xml"), timeout=None
        ) as response:
            if not 200 <= response.status_code < 300:
                body = (await response.aread())[:2048].decode("utf-8", "replace")
                raise HTTPError(
                    "dump download failed", status=response.status_code, url=url, body=body,
                    headers=dict(response.headers),
                )
            with open(destination, "wb") as handle:
                async for chunk in response.aiter_bytes(chunk_size):
                    handle.write(chunk)
                    total += len(chunk)
                    if progress is not None:
                        progress(total)
        return destination
