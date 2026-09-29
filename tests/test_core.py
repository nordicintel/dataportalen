"""The plumbing: HTTP transports, logging and progress."""

from __future__ import annotations

import io
import logging
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import load_fixture
from dataportalen import Dataportal, enable_logging, logger
from dataportalen.core import (
    BaseTransport,
    ParseError,
    Response,
    _duration,
    _TerminalProgress,
    build_url,
    default_transport,
    progress_reporter,
)

# ==========================================================================
# URL building, response decoding and transport selection.
# ==========================================================================





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


# ==========================================================================
# Logging and progress reporting.
# ==========================================================================





@pytest.fixture(autouse=True)
def _clean_logger():
    before = list(logger.handlers), logger.level
    yield
    logger.handlers[:] = before[0]
    logger.setLevel(before[1])


def test_the_library_never_configures_logging_itself():
    # A library that calls basicConfig hijacks the whole application.
    assert any(isinstance(h, logging.NullHandler) for h in logger.handlers)


def test_enable_logging_attaches_one_handler():
    stream = io.StringIO()
    enable_logging("INFO", stream=stream)
    enable_logging("INFO", stream=stream)  # twice must not double the output
    ours = [h for h in logger.handlers if getattr(h, "_dataportalen", False)]
    assert len(ours) == 1
    logger.info("hello")
    assert stream.getvalue().count("hello") == 1


def test_enable_logging_accepts_a_level_name():
    enable_logging("DEBUG", stream=io.StringIO())
    assert logger.level == logging.DEBUG


def test_requests_are_logged_at_debug(transport, search_response):
    stream = io.StringIO()
    enable_logging("DEBUG", stream=stream)
    transport.push(search_response)
    with Dataportal(transport=transport) as client:
        client.search()
    out = stream.getvalue()
    assert "GET" in out and "-> 200" in out


def test_retries_are_logged_at_warning(transport, search_response):
    stream = io.StringIO()
    enable_logging("WARNING", stream=stream)
    transport.push({"error": "boom"}, status=503)
    transport.push(search_response)
    with Dataportal(transport=transport, max_retries=2, backoff_factor=0) as client:
        client.search()
    assert "retrying" in stream.getvalue()


def test_log_level_argument_is_a_shortcut(transport, search_response):
    transport.push(search_response)
    with Dataportal(transport=transport, log_level="DEBUG") as client:
        # The handler goes to stderr by default; just prove the level took.
        assert logger.level == logging.DEBUG
        client.search()


def test_terminal_progress_rewrites_one_line():
    stream = io.StringIO()
    report = _TerminalProgress("datasets", stream=stream, min_interval=0)
    report(0, 100)
    report(50, 100)
    report(100, 100)
    text = stream.getvalue()
    assert text.count("\r") == 3          # rewrites, never scrolls
    assert text.endswith("\n")            # and closes the line at the end
    assert "100.0%" in text and "100/100" in text


def test_terminal_progress_throttles():
    stream = io.StringIO()
    report = _TerminalProgress("x", stream=stream, min_interval=60)
    report(1, 100)
    report(2, 100)      # suppressed: too soon
    assert stream.getvalue().count("\r") == 1


def test_terminal_progress_always_shows_the_final_update():
    stream = io.StringIO()
    report = _TerminalProgress("x", stream=stream, min_interval=60)
    report(1, 100)
    report(100, 100)    # final must never be throttled away
    assert stream.getvalue().count("\r") == 2


def test_progress_reporter_modes():
    assert progress_reporter(None, "x") is None
    callback = lambda done, total: None            # noqa: E731
    assert progress_reporter(callback, "x") is callback
    assert callable(progress_reporter("auto", "x"))
    with pytest.raises(ValueError):
        progress_reporter("nonsense", "x")


def test_non_tty_progress_logs_instead_of_drawing():
    stream = io.StringIO()
    enable_logging("INFO", stream=stream)
    # stderr under pytest is not a tty, so "auto" takes the logging path.
    report = progress_reporter("auto", "datasets", log_every=10)
    for done in range(1, 21):
        report(done, 20)
    out = stream.getvalue()
    assert "datasets" in out
    assert "\r" not in out


@pytest.mark.parametrize("seconds,expected", [
    (5, "5s"), (65, "1m05s"), (3700, "1h01m"), (-1, "0s"),
])
def test_duration_formatting(seconds, expected):
    assert _duration(seconds) == expected


def test_export_reports_progress_by_default(transport, tmp_path):
    """The five-minute-silence bug: 'auto' must be the default."""
    from dataportalen import download_catalog

    stream = io.StringIO()
    enable_logging("INFO", stream=stream)
    children = load_fixture("search_datasets.json")["resource"]["children"]
    page = {"results": len(children), "offset": 0, "limit": 100,
            "resource": {"children": children}, "facetFields": []}
    empty = {"results": 0, "offset": 0, "limit": 100,
             "resource": {"children": []}, "facetFields": []}
    transport.push(empty)
    transport.push(page)
    for _ in range(6):
        transport.push(empty)

    download_catalog(str(tmp_path / "c.jsonl"), limit=len(children),
                     client=Dataportal(transport=transport))
    out = stream.getvalue()
    assert "exporting" in out
    assert "wrote" in out
