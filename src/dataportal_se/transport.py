"""HTTP transports.

The package works with nothing but the standard library
(:class:`UrllibTransport`). If ``httpx`` or ``requests`` is installed it is
used instead, and ``httpx`` additionally unlocks the async client.
"""

from __future__ import annotations

import gzip
import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any, Iterator, List, Mapping, Optional, Tuple

from .exceptions import MissingDependencyError, ParseError, TimeoutError, TransportError

__all__ = [
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

DEFAULT_USER_AGENT = "dataportal-se/0.1.0 (+https://github.com/nordicintel/dataportal)"


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
            urllib.request.HTTPSHandler(context=ssl_context) if ssl_context else urllib.request.HTTPSHandler()
        )

    def _open(self, method: str, url: str, headers: Optional[Mapping[str, str]], timeout: Optional[float]):
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
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise MissingDependencyError("RequestsTransport needs `pip install requests`") from exc

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
            resp = self._session.request(method.upper(), url, headers=dict(headers or {}), timeout=timeout)
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
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise MissingDependencyError("HttpxTransport needs `pip install httpx`") from exc
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
            resp = self._client.request(method.upper(), url, headers=dict(headers or {}), timeout=timeout)
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
        manager = self._client.stream(method.upper(), url, headers=dict(headers or {}), timeout=timeout)
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
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise MissingDependencyError(
                "the async client needs `pip install dataportal[async]` (httpx)"
            ) from exc
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
    """Pick the best available synchronous transport."""
    try:
        return HttpxTransport()
    except MissingDependencyError:
        pass
    try:
        return RequestsTransport()
    except MissingDependencyError:
        pass
    return UrllibTransport()


def default_async_transport() -> AsyncHttpxTransport:
    """The async transport; requires ``httpx``."""
    return AsyncHttpxTransport()


def sleep(seconds: float) -> None:
    """Indirection that keeps retry backoff easy to patch in tests."""
    time.sleep(seconds)
