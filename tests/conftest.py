"""Shared fixtures: a fake transport that replays canned registry responses."""

from __future__ import annotations

import datetime as _dt
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


#: Two datasets, enough to exercise every filter and the breakdown. Text is
#: a language map, as every record written since 0.7.0 is.
CATALOG_RECORDS = [
    {
        "uri": "https://example.org/roads",
        "type": "dataset",
        "context_id": "50",
        "entry_id": "1",
        "title": {"sv": "Vägtrafiknät", "en": "Road traffic network"},
        "description": {"sv": "Nationellt vägnät med cykelvägar"},
        "keywords": {"sv": ["vägnät", "Geodata"]},
        "themes": ["transport"],
        "license": {"id": "cc_by_4_0", "label": {"en": "CC BY 4.0 (Attribution)"},
                    "uri": "http://creativecommons.org/licenses/by/4.0/"},
        "access_rights": "public",
        "accrual_periodicity": "annual",
        "languages": ["sv"],
        "spatial": ["kingdom_of_sweden"],
        "issued": "2020-03-04",
        "modified": "2024-05-06T09:00:00+02:00",
        "publisher": {"uri": "http://dataportal.se/organisation/SE2021006297",
                      "name": {"sv": "Trafikverket"}, "type": "national_authority"},
        "distributions": [{"format": "csv"}, {"format": "json"}],
    },
    {
        "uri": "https://example.org/budget",
        "type": "dataset",
        "context_id": "51",
        "entry_id": "2",
        "title": {"sv": "Kommunalt bidrag"},
        "description": {"sv": "Utbetalda bidrag per kommun"},
        "keywords": {"sv": ["ekonomi"]},
        "themes": ["economy_and_finance"],
        "license": {"id": "cc0_1_0",
                    "label": {"en": "CC0 1.0 (Public Domain Dedication, No Copyright)"},
                    "uri": "http://creativecommons.org/publicdomain/zero/1.0/"},
        "access_rights": "non_public",
        "accrual_periodicity": "monthly",
        "languages": ["sv", "en"],
        "spatial": [],
        "issued": "2014-01-01",
        "modified": "2019-01-01",
        "publisher": {"uri": "http://dataportal.se/organisation/SE2021005521",
                      "name": {"sv": "Försäkringskassan"},
                      "type": "national_authority"},
        "distributions": [{"format": "xlsx"}],
    },
]


#: Two data services. A different shape on purpose: no distributions, no
#: accrual_periodicity, no spatial -- the keys a data service does not have
#: are absent rather than empty.
SERVICE_RECORDS = [
    {
        "uri": "https://api.example.org/v1",
        "type": "data_service",
        "context_id": "14",
        "entry_id": "9283",
        "title": {"sv": "Utlysningar", "en": "Calls"},
        "description": {"sv": "API för utlysningar"},
        "keywords": {"sv": ["Innovation"]},
        "service_type": "rest",
        "endpoint_url": "https://api.example.org/v1",
        "themes": ["education_culture_and_sport"],
        "license": {"id": "cc0_1_0",
                    "label": {"en": "CC0 1.0 (Public Domain Dedication, No Copyright)"},
                    "uri": "http://creativecommons.org/publicdomain/zero/1.0/"},
        "access_rights": "public",
        "publisher": {"uri": "http://dataportal.se/organisation/SE2021006297",
                      "name": {"sv": "Trafikverket"},
                      "type": "national_authority"},
    },
    {
        "uri": "https://geodata.example.org/wms",
        "type": "data_service",
        "context_id": "15",
        "entry_id": "9284",
        "title": {"sv": "Kartvisning"},
        "description": {"sv": "WMS-tjänst"},
        "keywords": {"sv": ["Geodata"]},
        "service_type": "view_service",
        "endpoint_url": "https://geodata.example.org/wms",
        "themes": ["transport"],
        "license": {"id": "cc_by_4_0", "label": {"en": "CC BY 4.0 (Attribution)"},
                    "uri": "http://creativecommons.org/licenses/by/4.0/"},
        "access_rights": "public",
        "publisher": {"uri": "http://dataportal.se/organisation/SE2021005521",
                      "name": {"sv": "Försäkringskassan"},
                      "type": "national_authority"},
    },
]


#: What the registry's nightly link check says about the fixture's files.
#: The verdicts are its vocabulary, not ours: success, broken, excluded.
LINK_CHECKS = [
    {"entryType": "dcat:Distribution", "context": "50",
     "uri": "https://example.org/roads.csv", "property": "dcat:accessURL",
     "status": "broken", "statusMessage": "Not Found",
     "checkedAt": "2026-09-28T02:44:17.095Z", "attempts": 3},
    {"entryType": "dcat:Distribution", "context": "50",
     "uri": "https://example.org/roads.json", "property": "dcat:accessURL",
     "status": "success", "statusMessage": "OK",
     "checkedAt": "2026-09-28T02:44:18.001Z", "attempts": 1},
    {"entryType": "dcat:Distribution", "context": "51",
     "uri": "https://example.org/budget.xlsx", "property": "dcat:accessURL",
     "status": "broken", "statusMessage": "Too Many Requests",
     "checkedAt": "2026-09-28T02:44:19.002Z", "attempts": 2},
]


def write_catalog(tmp_path, records=None, name="catalog.sqlite", stamp=None):
    """A catalogue database on disk, for a Catalog that must not download.

    `stamp` defaults to now, so the copy is fresh whenever the suite runs. It
    used to be the literal date the fixture was written, which made every
    `age_days == 0` assertion pass for one day and fail forever after. Pass a
    stamp explicitly to test what age and staleness do.
    """
    from dataportalen.client import SCHEMA_VERSION, _connect, _meta_set, _write_records

    rows = CATALOG_RECORDS + SERVICE_RECORDS if records is None else records
    stamp = stamp or _dt.datetime.now().replace(microsecond=0).isoformat()
    path = tmp_path / name
    db = _connect(str(path))
    with db:
        _write_records(db, [
            (r.get("type", "dataset"), r.get("modified"), r) for r in rows])
        _meta_set(db, schema=SCHEMA_VERSION,
                  first_retrieved=stamp, last_refreshed=stamp)
    db.close()
    return str(path)


@pytest.fixture
def client(transport: FakeTransport):
    """The registry engine wired to the fake transport -- no file, no network."""
    from dataportalen.client import _Registry

    with _Registry(transport=transport, max_retries=0) as registry:
        yield registry


@pytest.fixture
def cat(transport: FakeTransport, tmp_path):
    """A Catalog over the two canned records; nothing is downloaded."""
    from dataportalen import Catalog

    with Catalog(write_catalog(tmp_path), max_age=None,
                 _transport=transport, access_rights=None) as catalog:
        yield catalog


@pytest.fixture
def search_response() -> Dict[str, Any]:
    return load_fixture("search_datasets.json")


@pytest.fixture
def org_data() -> Dict[str, Any]:
    return load_fixture("org_data.json")
