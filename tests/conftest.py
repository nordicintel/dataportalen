"""Shared fixtures: a fake transport that replays canned registry responses."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dataportal_se.transport import BaseTransport, Response  # noqa: E402

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


@pytest.fixture
def client(transport: FakeTransport):
    from dataportal_se import Dataportal

    with Dataportal(transport=transport, max_retries=0) as dp:
        yield dp


@pytest.fixture
def search_response() -> Dict[str, Any]:
    return load_fixture("search_datasets.json")


@pytest.fixture
def org_data() -> Dict[str, Any]:
    return load_fixture("org_data.json")
