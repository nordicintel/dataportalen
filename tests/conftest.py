"""Shared fixtures: a fake transport that replays canned registry responses."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dataportalen.core import BaseTransport, Response  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    with open(FIXTURES / name, encoding="utf-8") as handle:
        return json.load(handle)


class FakeTransport(BaseTransport):
    """Serves queued responses and records every request it was asked for."""

    def __init__(self, routes: Optional[Mapping[str, Any]] = None) -> None:
        #: path -> payload (dict/list are JSON-encoded, str/bytes sent as is)
        self.routes: Dict[str, Any] = dict(routes or {})
        #: FIFO of responses that win over `routes`, for multi-step flows
        self.queue: List[Any] = []
        self.requests: List[str] = []
        self.closed = False

    def push(self, payload: Any, status: int = 200, content_type: str = "application/json") -> None:
        self.queue.append((payload, status, content_type))

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> Response:
        self.requests.append(url)
        if self.queue:
            payload, status, content_type = self.queue.pop(0)
        else:
            path = urlparse(url).path
            if path not in self.routes:
                return Response(404, {"content-type": "text/plain"}, b"not found", url)
            payload, status, content_type = self.routes[path], 200, "application/json"
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload).encode("utf-8")
        elif isinstance(payload, str):
            body = payload.encode("utf-8")
        else:
            body = payload
        return Response(status, {"content-type": content_type}, body, url)

    def stream(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
        chunk_size: int = 1 << 16,
    ) -> Tuple[int, Mapping[str, str], Iterator[bytes]]:
        response = self.request(method, url, headers=headers, timeout=timeout)
        return response.status, response.headers, iter([response.content])

    def close(self) -> None:
        self.closed = True

    # -- assertions helpers ------------------------------------------------

    @property
    def last_query(self) -> Dict[str, List[str]]:
        return parse_qs(urlparse(self.requests[-1]).query)

    def last_param(self, name: str) -> Optional[str]:
        values = self.last_query.get(name)
        return values[0] if values else None


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


#: Two datasets, enough to exercise every filter and the breakdown.
CATALOG_RECORDS = [
    {
        "uri": "https://example.org/roads",
        "context_id": "50",
        "title": "Vägtrafiknät",
        "description": "Nationellt vägnät med cykelvägar",
        "keywords": ["vägnät", "Geodata"],
        "themes": ["transport"],
        "license": "cc_by_4_0",
        "access_rights": "public",
        "accrual_periodicity": "annual",
        "languages": ["swedish"],
        "spatial": ["kingdom_of_sweden"],
        "issued": "2020-03-04",
        "modified": "2024-05-06T09:00:00+02:00",
        "publisher": {"uri": "http://dataportal.se/organisation/SE2021006297",
                      "name": "Trafikverket", "type": "national_authority"},
        "distributions": [{"format": "csv"}, {"format": "json"}],
    },
    {
        "uri": "https://example.org/budget",
        "context_id": "51",
        "title": "Kommunalt bidrag",
        "description": "Utbetalda bidrag per kommun",
        "keywords": ["ekonomi"],
        "themes": ["economy_and_finance"],
        "license": "cc0_1_0",
        "access_rights": "non_public",
        "accrual_periodicity": "monthly",
        "languages": ["swedish", "english"],
        "spatial": [],
        "issued": "2014-01-01",
        "modified": "2019-01-01",
        "publisher": {"uri": "http://dataportal.se/organisation/SE2021005521",
                      "name": "Försäkringskassan", "type": "national_authority"},
        "distributions": [{"format": "xlsx"}],
    },
]


@pytest.fixture
def client(transport: FakeTransport, tmp_path):
    """A client wired to the fake transport, with a tiny catalogue on disk.

    Dataset search reads that file; everything else goes to the transport.
    """
    import json

    from dataportalen import Dataportal

    path = tmp_path / "catalog.jsonl"
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in CATALOG_RECORDS) + "\n",
        encoding="utf-8")
    with Dataportal(transport=transport, max_retries=0,
                    catalog_path=str(path)) as dp:
        yield dp


@pytest.fixture
def search_response() -> Dict[str, Any]:
    return load_fixture("search_datasets.json")


@pytest.fixture
def org_data() -> Dict[str, Any]:
    return load_fixture("org_data.json")
