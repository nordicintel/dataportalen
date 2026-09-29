"""Version, errors, logging and HTTP.

The plumbing everything else sits on: the package version, the exception
hierarchy, the ``dataportalen`` logger and progress reporting, and the three
HTTP transports (requests, httpx, stdlib urllib).
"""

from __future__ import annotations

import gzip
import json
import logging
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any, Callable, Iterator, List, Mapping, Optional, Tuple

# ==========================================================================
# version: The package version, in one place.
# ==========================================================================

__version__ = "0.5.0"


# ==========================================================================
# exceptions: Exception hierarchy for :mod:`dataportal`.
# ==========================================================================




class DataportalError(Exception):
    """Base class for every error raised by this package."""


class TransportError(DataportalError):
    """The request never produced an HTTP response (DNS, TLS, socket, ...)."""


class TimeoutError(TransportError):  # noqa: A001 - deliberate shadowing, scoped to package
    """The request timed out."""


class HTTPError(DataportalError):
    """The server answered with a non-2xx status code."""

    def __init__(
        self,
        message: str,
        *,
        status: int,
        url: str,
        body: str = "",
        headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.url = url
        self.body = body
        self.headers: Mapping[str, str] = dict(headers or {})

    def __str__(self) -> str:  # pragma: no cover - trivial
        base = super().__str__()
        snippet = self.body[:300].strip()
        return f"{base} [{self.status}] {self.url}" + (f"\n{snippet}" if snippet else "")


class NotFoundError(HTTPError):
    """HTTP 404 - the entry, context or resource does not exist."""


class RateLimitError(HTTPError):
    """HTTP 429 - too many requests."""

    def __init__(self, *args: Any, retry_after: Optional[float] = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


class ServerError(HTTPError):
    """HTTP 5xx."""


class ParseError(DataportalError):
    """The response body could not be decoded into the expected shape."""


class QueryError(DataportalError):
    """A Solr query could not be built from the given arguments."""


# ==========================================================================
# log: Logging and progress reporting.
# ==========================================================================



#: The package logger. Configure it as you would any other.
logger = logging.getLogger("dataportalen")
logger.addHandler(logging.NullHandler())


def enable_logging(
    level: Any = logging.INFO,
    stream: Any = None,
    fmt: str = "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
) -> logging.Logger:
    """Send this package's log records to ``stream`` (default stderr).

    A convenience for scripts and notebooks. Applications that already
    configure logging should ignore this and handle the ``dataportalen``
    logger themselves.

    Calling it twice replaces the handler rather than doubling the output.
    """
    if isinstance(level, str):
        level = logging.getLevelName(level.upper())
    for existing in list(logger.handlers):
        if getattr(existing, "_dataportalen", False):
            logger.removeHandler(existing)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))
    handler._dataportalen = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(level)
    return logger


class _TerminalProgress:
    """A single rewriting line on stderr, throttled to ~5 updates a second."""

    def __init__(self, label: str, stream: Any = None, min_interval: float = 0.2) -> None:
        self.label = label
        self.stream = stream or sys.stderr
        self.min_interval = min_interval
        self.started = time.time()
        self._last = 0.0
        self._width = 0

    def __call__(self, done: int, total: int) -> None:
        now = time.time()
        final = total and done >= total
        if not final and now - self._last < self.min_interval:
            return
        self._last = now

        elapsed = now - self.started
        rate = done / elapsed if elapsed > 0 else 0.0
        if total:
            pct = 100.0 * done / total
            remaining = (total - done) / rate if rate > 0 else 0
            text = "%s %6.1f%%  %s/%s  %.0f/s  eta %s" % (
                self.label, pct, f"{done:,}", f"{total:,}", rate, _duration(remaining))
        else:
            text = "%s %s  %.0f/s" % (self.label, f"{done:,}", rate)

        padded = text.ljust(self._width)
        self._width = max(self._width, len(text))
        try:
            self.stream.write("\r" + padded)
            if final:
                self.stream.write("\n")
            self.stream.flush()
        except Exception:  # pragma: no cover - a closed or odd stream
            pass


def _duration(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm%02ds" % (seconds // 60, seconds % 60)
    return "%dh%02dm" % (seconds // 3600, (seconds % 3600) // 60)


def progress_reporter(
    progress: Any,
    label: str,
    log_every: int = 2000,
) -> Optional[Callable[[int, int], None]]:
    """Turn the ``progress`` argument into a callback.

    ``"auto"`` (the default for long operations) draws a live line when
    stderr is a terminal, and otherwise logs a line every ``log_every`` items
    so a redirected run still leaves a trail. ``None`` is silent, and a
    callable is used as given.
    """
    if progress is None:
        return None
    if callable(progress):
        return progress
    if progress != "auto":
        raise ValueError("progress must be 'auto', None, or a callable")

    stream = sys.stderr
    if getattr(stream, "isatty", lambda: False)():
        return _TerminalProgress(label)

    state = {"next": log_every}

    def log_progress(done: int, total: int) -> None:
        if done >= state["next"] or (total and done >= total):
            state["next"] = done + log_every
            if total:
                logger.info("%s %d/%d (%.0f%%)", label, done, total, 100.0 * done / total)
            else:
                logger.info("%s %d", label, done)

    return log_progress


# ==========================================================================
# transport: HTTP transports.
# ==========================================================================




DEFAULT_USER_AGENT = "dataportalen/%s (+https://github.com/nordicintel/dataportal)" % __version__


def build_url(base: str, path: str, params: Optional[Mapping[str, Any]] = None) -> str:
    """Join ``base`` and ``path`` and append a query string.

    ``None`` parameter values are dropped; sequences expand to repeated keys.
    """
    url = base.rstrip("/") + "/" + str(path).lstrip("/") if path else base.rstrip("/")
    if not params:
        return url
    pairs: List[Tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            pairs.append((key, "true" if value else "false"))
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                if item is not None:
                    pairs.append((key, str(item)))
        else:
            pairs.append((key, str(value)))
    if not pairs:
        return url
    query = urllib.parse.urlencode(pairs, quote_via=urllib.parse.quote)
    return "%s%s%s" % (url, "&" if "?" in url else "?", query)


class Response:
    """A minimal HTTP response, uniform across transports."""

    __slots__ = ("status", "headers", "content", "url")

    def __init__(
        self,
        status: int,
        headers: Mapping[str, str],
        content: bytes,
        url: str,
    ) -> None:
        self.status = status
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.content = content
        self.url = url

    @property
    def encoding(self) -> str:
        content_type = self.headers.get("content-type", "")
        for part in content_type.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                return part.split("=", 1)[1].strip().strip('"') or "utf-8"
        return "utf-8"

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding, errors="replace")

    def json(self) -> Any:
        try:
            return json.loads(self.content.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ParseError("response from %s is not valid JSON: %s" % (self.url, exc)) from exc

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<Response %d %s (%d bytes)>" % (self.status, self.url, len(self.content))


class BaseTransport:
    """Interface implemented by every synchronous transport."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> Response:  # pragma: no cover - abstract
        raise NotImplementedError

    def stream(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
        chunk_size: int = 1 << 16,
    ) -> Tuple[int, Mapping[str, str], Iterator[bytes]]:
        """Stream a response body; used for the multi-hundred-MB nightly dump."""
        response = self.request(method, url, headers=headers, timeout=timeout)
        return response.status, response.headers, iter([response.content])

    def close(self) -> None:
        pass

    def __enter__(self) -> "BaseTransport":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _decompress(data: bytes, encoding: str) -> bytes:
    encoding = (encoding or "").lower()
    if encoding == "gzip":
        try:
            return gzip.decompress(data)
        except OSError:
            return data
    if encoding == "deflate":
        try:
            return zlib.decompress(data)
        except zlib.error:
            try:
                return zlib.decompress(data, -zlib.MAX_WBITS)
            except zlib.error:
                return data
    return data


class UrllibTransport(BaseTransport):
    """Standard-library transport -- no third-party dependency required."""

    def __init__(self, ssl_context: Optional[ssl.SSLContext] = None) -> None:
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl_context)
            if ssl_context
            else urllib.request.HTTPSHandler()
        )

    def _open(
        self,
        method: str,
        url: str,
        headers: Optional[Mapping[str, str]],
        timeout: Optional[float],
    ):
        request = urllib.request.Request(url, method=method.upper())
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            return self._opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            return exc  # HTTPError is itself a readable response object
        except socket.timeout as exc:
            raise TimeoutError("request to %s timed out" % url) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                raise TimeoutError("request to %s timed out" % url) from exc
            raise TransportError("request to %s failed: %s" % (url, exc.reason)) from exc
        except OSError as exc:
            raise TransportError("request to %s failed: %s" % (url, exc)) from exc

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> Response:
        raw = self._open(method, url, headers, timeout)
        try:
            body = raw.read()
        except socket.timeout as exc:
            raise TimeoutError("reading %s timed out" % url) from exc
        except OSError as exc:
            raise TransportError("reading %s failed: %s" % (url, exc)) from exc
        finally:
            try:
                raw.close()
            except Exception:  # pragma: no cover - best effort
                pass
        response_headers = dict(raw.headers.items()) if raw.headers else {}
        body = _decompress(body, response_headers.get("Content-Encoding", ""))
        status = getattr(raw, "status", None) or getattr(raw, "code", 0)
        return Response(int(status), response_headers, body, getattr(raw, "url", url))

    def stream(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
        chunk_size: int = 1 << 16,
    ) -> Tuple[int, Mapping[str, str], Iterator[bytes]]:
        # Compressed streams would need incremental inflation; ask for identity.
        merged = dict(headers or {})
        merged["Accept-Encoding"] = "identity"
        raw = self._open(method, url, merged, timeout)
        response_headers = dict(raw.headers.items()) if raw.headers else {}
        status = int(getattr(raw, "status", None) or getattr(raw, "code", 0))

        def chunks() -> Iterator[bytes]:
            try:
                while True:
                    chunk = raw.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
            except socket.timeout as exc:
                raise TimeoutError("reading %s timed out" % url) from exc
            except OSError as exc:
                raise TransportError("reading %s failed: %s" % (url, exc)) from exc
            finally:
                try:
                    raw.close()
                except Exception:  # pragma: no cover - best effort
                    pass

        return status, response_headers, chunks()


class RequestsTransport(BaseTransport):
    """Transport backed by :mod:`requests` (connection pooling, keep-alive)."""

    def __init__(self, session: Any = None) -> None:
        import requests  # a declared dependency; always present

        self._requests = requests
        self._session = session or requests.Session()
        self._owns_session = session is None

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> Response:
        try:
            resp = self._session.request(
                method.upper(), url, headers=dict(headers or {}), timeout=timeout
            )
        except self._requests.Timeout as exc:
            raise TimeoutError("request to %s timed out" % url) from exc
        except self._requests.RequestException as exc:
            raise TransportError("request to %s failed: %s" % (url, exc)) from exc
        return Response(resp.status_code, dict(resp.headers), resp.content, resp.url)

    def stream(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
        chunk_size: int = 1 << 16,
    ) -> Tuple[int, Mapping[str, str], Iterator[bytes]]:
        try:
            resp = self._session.request(
                method.upper(), url, headers=dict(headers or {}), timeout=timeout, stream=True
            )
        except self._requests.Timeout as exc:
            raise TimeoutError("request to %s timed out" % url) from exc
        except self._requests.RequestException as exc:
            raise TransportError("request to %s failed: %s" % (url, exc)) from exc

        def chunks() -> Iterator[bytes]:
            try:
                for chunk in resp.iter_content(chunk_size):
                    if chunk:
                        yield chunk
            finally:
                resp.close()

        return resp.status_code, dict(resp.headers), chunks()

    def close(self) -> None:
        if self._owns_session:
            self._session.close()


class HttpxTransport(BaseTransport):
    """Transport backed by :mod:`httpx` (HTTP/2-capable, pooled)."""

    def __init__(self, client: Any = None, **client_kwargs: Any) -> None:
        import httpx  # a declared dependency; always present

        self._httpx = httpx
        client_kwargs.setdefault("follow_redirects", True)
        self._client = client or httpx.Client(**client_kwargs)
        self._owns_client = client is None

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> Response:
        try:
            resp = self._client.request(
                method.upper(), url, headers=dict(headers or {}), timeout=timeout
            )
        except self._httpx.TimeoutException as exc:
            raise TimeoutError("request to %s timed out" % url) from exc
        except self._httpx.HTTPError as exc:
            raise TransportError("request to %s failed: %s" % (url, exc)) from exc
        return Response(resp.status_code, dict(resp.headers), resp.content, str(resp.url))

    def stream(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
        chunk_size: int = 1 << 16,
    ) -> Tuple[int, Mapping[str, str], Iterator[bytes]]:
        manager = self._client.stream(
            method.upper(), url, headers=dict(headers or {}), timeout=timeout
        )
        try:
            resp = manager.__enter__()
        except self._httpx.TimeoutException as exc:
            raise TimeoutError("request to %s timed out" % url) from exc
        except self._httpx.HTTPError as exc:
            raise TransportError("request to %s failed: %s" % (url, exc)) from exc

        def chunks() -> Iterator[bytes]:
            try:
                for chunk in resp.iter_bytes(chunk_size):
                    if chunk:
                        yield chunk
            finally:
                manager.__exit__(None, None, None)

        return resp.status_code, dict(resp.headers), chunks()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


class AsyncHttpxTransport:
    """Asynchronous transport backed by :mod:`httpx`."""

    def __init__(self, client: Any = None, **client_kwargs: Any) -> None:
        import httpx  # a declared dependency; always present

        self._httpx = httpx
        client_kwargs.setdefault("follow_redirects", True)
        self._client = client or httpx.AsyncClient(**client_kwargs)
        self._owns_client = client is None

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> Response:
        try:
            resp = await self._client.request(
                method.upper(), url, headers=dict(headers or {}), timeout=timeout
            )
        except self._httpx.TimeoutException as exc:
            raise TimeoutError("request to %s timed out" % url) from exc
        except self._httpx.HTTPError as exc:
            raise TransportError("request to %s failed: %s" % (url, exc)) from exc
        return Response(resp.status_code, dict(resp.headers), resp.content, str(resp.url))

    def stream(self, method: str, url: str, **kwargs: Any) -> Any:
        """Return httpx's own async streaming context manager."""
        headers = dict(kwargs.pop("headers", None) or {})
        return self._client.stream(method.upper(), url, headers=headers, **kwargs)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "AsyncHttpxTransport":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()


def default_transport() -> BaseTransport:
    """The default synchronous transport.

    Always ``requests``: it is a declared dependency, so this is predictable
    rather than dependent on what else happens to be installed. Pass
    ``transport=HttpxTransport()`` to the client for HTTP/2.
    """
    return RequestsTransport()


def default_async_transport() -> AsyncHttpxTransport:
    """The async transport; requires ``httpx``."""
    return AsyncHttpxTransport()


def sleep(seconds: float) -> None:
    """Indirection that keeps retry backoff easy to patch in tests."""
    time.sleep(seconds)


__all__ = [
    "DataportalError",
    "TransportError",
    "TimeoutError",
    "HTTPError",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
    "ParseError",
    "QueryError",
    "logger",
    "enable_logging",
    "progress_reporter",
    "Response",
    "BaseTransport",
    "UrllibTransport",
    "RequestsTransport",
    "HttpxTransport",
    "AsyncHttpxTransport",
    "default_transport",
    "default_async_transport",
    "build_url",
]
