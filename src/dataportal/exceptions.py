"""Exception hierarchy for :mod:`dataportal`."""

from __future__ import annotations

from typing import Any, Mapping, Optional

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
    "MissingDependencyError",
]


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


class MissingDependencyError(DataportalError):
    """An optional dependency is required for the requested feature."""
