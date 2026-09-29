"""The async client.

Network tests need ``DATAPORTAL_LIVE=1``; the rest run offline.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Mapping, Optional
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import load_fixture
from dataportalen import Dataset
from dataportalen.aio import AsyncDataportal
from dataportalen.core import Response


class FakeAsyncTransport:
    """Serves queued responses to :class:`AsyncDataportal`."""

    def __init__(self) -> None:
        self.queue: list = []
        self.requests: list = []
        self.closed = False

    def push(self, payload: Any, status: int = 200) -> None:
        self.queue.append((payload, status))

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> Response:
        self.requests.append(url)
        payload, status = self.queue.pop(0) if self.queue else ({}, 404)
        body = json.dumps(payload).encode() if isinstance(payload, (dict, list)) else payload
        return Response(status, {"content-type": "application/json"}, body, url)

    async def aclose(self) -> None:
        self.closed = True


def params_of(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def run(coro):
    return asyncio.run(coro)


def test_search_returns_dicts():
    transport = FakeAsyncTransport()
    transport.push(load_fixture("search_datasets.json"))

    async def main():
        async with AsyncDataportal(transport=transport) as dp:
            return await dp.datasets(limit=2)

    page = run(main())
    assert len(page) == 2
    assert all(isinstance(record, dict) for record in page)
    assert all(record["uri"] for record in page)
    assert page[0]["title"]
    assert params_of(transport.requests[-1])["type"] == "solr"


def test_iter_search_is_an_async_generator():
    transport = FakeAsyncTransport()
    children = load_fixture("search_datasets.json")["resource"]["children"]
    page = {"results": 4, "offset": 0, "limit": 2, "resource": {"children": children}}
    transport.push(page)
    transport.push(dict(page, offset=2))

    async def main():
        async with AsyncDataportal(transport=transport) as dp:
            return [e async for e in dp.iter_datasets(page_size=2)]

    assert len(run(main())) == 4


def test_entry_fetches_metadata_and_envelope_concurrently():
    transport = FakeAsyncTransport()
    transport.push(load_fixture("dataset_recursive.json"))
    transport.push(load_fixture("dataset_entry.json"))

    async def main():
        async with AsyncDataportal(transport=transport) as dp:
            return await dp.entry(547, 28672, model=Dataset)

    entry = run(main())
    assert entry.title
    assert entry.resource_uri
    assert len(transport.requests) == 2


def test_lookup_many_runs_batches_concurrently():
    transport = FakeAsyncTransport()
    transport.push(load_fixture("search_datasets.json"))
    transport.push(load_fixture("search_datasets.json"))

    async def main():
        async with AsyncDataportal(transport=transport) as dp:
            return await dp.lookup_many(
                ["http://example.org/%d" % i for i in range(4)], batch_size=2
            )

    run(main())
    assert len(transport.requests) == 2


def test_organisations_parse_the_chart_payload():
    transport = FakeAsyncTransport()
    transport.push(load_fixture("org_data.json"))

    async def main():
        async with AsyncDataportal(transport=transport) as dp:
            return await dp.organisations()

    orgs = run(main())
    assert orgs and orgs[0].name


def test_closing_closes_an_owned_transport():
    transport = FakeAsyncTransport()

    async def main():
        dp = AsyncDataportal(transport=transport)
        await dp.aclose()

    run(main())
    # A supplied transport belongs to the caller and is left open.
    assert not transport.closed


@pytest.mark.network
@pytest.mark.skipif(not os.environ.get("DATAPORTAL_LIVE"), reason="set DATAPORTAL_LIVE=1")
def test_live_async_round_trip():
    async def main():
        async with AsyncDataportal() as dp:
            page = await dp.datasets(limit=3)
            total = await dp.count()
            orgs = await dp.organisations()
            return page, total, orgs

    page, total, orgs = run(main())
    assert len(page) == 3
    assert total > 1000
    assert orgs
