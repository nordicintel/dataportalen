"""URL building, response decoding and transport selection."""

from __future__ import annotations

import gzip
import json
from urllib.parse import parse_qs, urlparse

import pytest

from dataportal_se.exceptions import ParseError
from dataportal_se.transport import (
    BaseTransport,
    Response,
    UrllibTransport,
    build_url,
    default_transport,
)


def test_build_url_joins_base_and_path():
    assert build_url("https://x.se/", "/store/search") == "https://x.se/store/search"
    assert build_url("https://x.se", "store/search") == "https://x.se/store/search"


def test_build_url_drops_none_and_expands_sequences():
    url = build_url("https://x.se", "/a", {"q": "1", "skip": None, "f": ["a", "b"]})
    query = parse_qs(urlparse(url).query)
    assert query == {"q": ["1"], "f": ["a", "b"]}


def test_build_url_renders_booleans_as_solr_expects():
    url = build_url("https://x.se", "/a", {"facetMissing": True})
    assert url.endswith("facetMissing=true")


def test_build_url_escapes_query_metacharacters():
    url = build_url("https://x.se", "/a", {"query": r"rdfType:http\://x#Y"})
    assert "%5C%3A" in url  # the backslash-colon survives encoding


def test_response_decodes_json_and_text():
    response = Response(200, {"content-type": "application/json"}, b'{"a": 1}', "u")
    assert response.json() == {"a": 1}
    assert response.text == '{"a": 1}'


def test_response_honours_the_charset_header():
    body = "Örebro".encode("iso-8859-1")
    response = Response(200, {"content-type": "text/plain; charset=iso-8859-1"}, body, "u")
    assert response.encoding == "iso-8859-1"
    assert response.text == "Örebro"


def test_response_raises_parse_error_on_non_json():
    response = Response(200, {"content-type": "application/json"}, b"<html>", "u")
    with pytest.raises(ParseError):
        response.json()


def test_urllib_transport_decompresses_gzip(monkeypatch):
    payload = gzip.compress(json.dumps({"ok": True}).encode())

    class FakeRaw:
        status = 200
        url = "https://x.se/a"
        headers = {"Content-Encoding": "gzip", "Content-Type": "application/json"}

        def read(self, *args):
            return payload

        def close(self):
            pass

    transport = UrllibTransport()
    monkeypatch.setattr(transport, "_open", lambda *a, **k: FakeRaw())
    assert transport.request("GET", "https://x.se/a").json() == {"ok": True}


def test_default_transport_is_usable():
    transport = default_transport()
    assert isinstance(transport, BaseTransport)
    transport.close()


def test_base_transport_stream_falls_back_to_a_single_chunk():
    class Single(BaseTransport):
        def request(self, method, url, *, headers=None, timeout=None):
            return Response(200, {}, b"abc", url)

    status, _, chunks = Single().stream("GET", "https://x.se/a")
    assert status == 200
    assert b"".join(chunks) == b"abc"
