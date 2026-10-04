"""Asking a distribution's server whether it is there.

The registry's nightly link check is the only verdict a catalogue carries by
default, and for a third of the distributions it marks it has none: its
checker was reset, timed out or told to slow down. This asks again, from
here, and only when told to -- :meth:`Catalog.verify` is the one way in.

What makes a verdict:

- ``HEAD`` first. A server that answers it with an error is asked again with
  a one-byte ranged ``GET``, because plenty of servers refuse ``HEAD`` and
  serve the file. Only what the ``GET`` says counts as dead.
- A connection error or a timeout is tried once more. One lost packet must
  not become a claim about a publisher.
- A certificate error is retried without verification. The distribution is
  there; the certificate is what is wrong, and that is reported as such.
- No answer at all is checked against DNS. A host that does not exist is
  dead; a host that exists and did not answer is unverified.
- ``429`` is unverified, and nothing more is asked of that host in this run.
  Nor after two addresses in a row that got no answer: a host that is down
  is not asked two hundred times, twenty seconds each.
- A redirect to wherever the site sends every unknown path is a soft 404.

Dead is narrower here than for the registry's verdicts: ``404``, ``410``, no
such host, a soft 404. Any other error status is a server that is there and
would not serve this request as asked -- a WMS endpoint answers ``400`` to
an address with no parameters, a login wall ``401``, an overloaded server
``503`` -- and measured on 200 addresses, half of what the wider rule called
dead was exactly that. Those stay unverified, with the status as the reason.

One host is asked one thing at a time, with a pause in between.
"""

from __future__ import annotations

import concurrent.futures
import datetime as _dt
import secrets
import socket
import threading
import time
from http import HTTPStatus
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

from .core import BaseTransport, TimeoutError, TransportError, logger

__all__ = ["check_links"]

#: Hosts asked at once, and the pause between two requests to the same one.
#: api.scb.se allows 30 requests in 10 seconds; this stays under it.
WORKERS = 8
PAUSE = 0.4
TIMEOUT = 15.0

#: The statuses that say the address itself is gone.
_GONE = frozenset([404, 410])

#: How many addresses in a row may go unanswered before a host is left alone.
_GIVE_UP_AFTER = 2

_NO_SUCH_HOST = frozenset(
    code for code in (getattr(socket, "EAI_NONAME", None), 11001) if code is not None)


def host_exists(host: str) -> bool:
    """Whether DNS knows the host. Only "no such name" is a no.

    A resolver that is busy or unreachable says nothing about the host, so it
    counts as existing and the verdict stays with what the request said. A no
    is asked for twice.
    """
    for attempt in range(2):
        try:
            socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            return True
        except socket.gaierror as error:
            if error.errno not in _NO_SUCH_HOST:
                return True
        except OSError:
            return True
        if attempt == 0:
            time.sleep(1.0)
    return False


def _is_certificate_error(error: BaseException) -> bool:
    text = ("%s %r" % (error, error.__cause__)).lower()
    return "certificate" in text or "sslerror" in text or "ssl:" in text


def _phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return str(status)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(
        microsecond=0, tzinfo=None).isoformat()


def _interleave(urls: Iterable[str]) -> List[str]:
    """One URL per host in turn, so the first N are N hosts and not one."""
    by_host: Dict[str, List[str]] = {}
    for url in dict.fromkeys(urls):
        by_host.setdefault(urlparse(url).hostname or "", []).append(url)
    out: List[str] = []
    queues = list(by_host.values())
    depth = 0
    while queues:
        queues = [queue for queue in queues if depth < len(queue)]
        out.extend(queue[depth] for queue in queues)
        depth += 1
    return out


class _Checker:
    def __init__(
        self,
        transport: BaseTransport,
        insecure: Optional[BaseTransport],
        headers: Mapping[str, str],
        timeout: float,
        pause: float,
        resolve: Callable[[str], bool],
        sleep: Callable[[float], None],
    ) -> None:
        self.transport = transport
        self.insecure = insecure
        self.headers = dict(headers)
        self.timeout = timeout
        self.pause = pause
        self.resolve = resolve
        self.sleep = sleep
        self.requests = 0
        self._lock = threading.Lock()
        self._stopped: Dict[str, str] = {}
        self._silent: Dict[str, int] = {}
        self._baselines: Dict[str, Optional[str]] = {}

    # -- one request -------------------------------------------------------

    def _count(self) -> None:
        with self._lock:
            self.requests += 1

    def _head(self, transport: BaseTransport, url: str) -> Tuple[int, str]:
        self._count()
        response = transport.request("HEAD", url, headers=self.headers,
                                     timeout=self.timeout)
        return response.status, response.url

    def _ranged_get(self, transport: BaseTransport, url: str) -> int:
        """The status of a GET, without the body: one byte is asked for, and
        whatever the server sends instead is dropped after the first chunk."""
        self._count()
        headers = dict(self.headers, Range="bytes=0-0")
        status, _, body = transport.stream("GET", url, headers=headers,
                                           timeout=self.timeout)
        chunks = iter(body)
        next(chunks, None)
        close = getattr(chunks, "close", None)
        if close is not None:
            close()
        return status

    def _ask(self, transport: BaseTransport, url: str) -> Tuple[int, Optional[str]]:
        status, final = self._head(transport, url)
        if status >= 400 and status != 429:
            # Plenty of servers refuse HEAD -- 405, 501, but also 403 and
            # 404 -- and serve the same address to a GET.
            return self._ranged_get(transport, url), None
        return status, final

    def _ask_twice(self, transport: BaseTransport, url: str) -> Tuple[int, Optional[str]]:
        try:
            return self._ask(transport, url)
        except TransportError as error:
            if _is_certificate_error(error):
                raise
            self.sleep(max(self.pause, 1.0))
            return self._ask(transport, url)

    # -- a soft 404 --------------------------------------------------------

    def _baseline(self, url: str) -> Optional[str]:
        """Where this site sends a path that cannot exist, if it answers 200."""
        parsed = urlparse(url)
        root = "%s://%s" % (parsed.scheme, parsed.netloc)
        with self._lock:
            if root in self._baselines:
                return self._baselines[root]
        probe = "%s/dataportalen-link-check-%s" % (root, secrets.token_hex(4))
        landed: Optional[str] = None
        try:
            status, final = self._head(self.transport, probe)
            if status < 300 and final:
                landed = final.rstrip("/")
        except TransportError:
            pass
        with self._lock:
            self._baselines[root] = landed
        return landed

    # -- the verdict -------------------------------------------------------

    def check(self, url: str) -> Dict[str, Any]:
        def verdict(status: str, reason: Optional[str], cert: bool = False):
            return {"status": status, "reason": reason, "checked": _now(),
                    "invalid_cert": cert}

        parsed = urlparse(url)
        host = parsed.hostname
        if parsed.scheme not in ("http", "https") or not host:
            return verdict("unverified", "not an http address")
        with self._lock:
            if host in self._stopped:
                return verdict("unverified", self._stopped[host])

        def unanswered(reason: str):
            with self._lock:
                self._silent[host] = self._silent.get(host, 0) + 1
                if self._silent[host] >= _GIVE_UP_AFTER:
                    self._stopped.setdefault(host, reason)
            return verdict("unverified", reason)

        cert = False
        try:
            try:
                status, final = self._ask_twice(self.transport, url)
            except TransportError as error:
                if not (_is_certificate_error(error) and self.insecure is not None):
                    raise
                cert = True
                status, final = self._ask_twice(self.insecure, url)
        except TimeoutError:
            return unanswered("timeout")
        except TransportError as error:
            if not self.resolve(host):
                return verdict("dead", "host not found")
            return unanswered(str(error)[:200])

        with self._lock:
            self._silent.pop(host, None)
        if status == 429:
            with self._lock:
                self._stopped.setdefault(host, "Too Many Requests")
            return verdict("unverified", "Too Many Requests")
        if status in _GONE:
            return verdict("dead", _phrase(status), cert)
        if status >= 400:
            return verdict("unverified", _phrase(status), cert)
        if final and final.rstrip("/") != url.rstrip("/"):
            if final.rstrip("/") == self._baseline(url):
                return verdict("dead", "soft 404", cert)
        return verdict("alive", None, cert)


def check_links(
    urls: Iterable[str],
    transport: BaseTransport,
    *,
    insecure: Optional[BaseTransport] = None,
    headers: Optional[Mapping[str, str]] = None,
    limit: Optional[int] = None,
    workers: int = WORKERS,
    pause: float = PAUSE,
    timeout: float = TIMEOUT,
    resolve: Optional[Callable[[str], bool]] = None,
    sleep: Optional[Callable[[float], None]] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Tuple[Dict[str, Dict[str, Any]], int]:
    """``({url: verdict}, requests made)`` for every distinct URL.

    A verdict is ``{"status", "reason", "checked", "invalid_cert"}`` with
    ``status`` one of ``alive``, ``dead`` and ``unverified``. ``limit`` takes
    the first N after the URLs are interleaved by host, so a small sample is
    spread over many publishers rather than spent on the largest.
    """
    resolve = resolve or host_exists
    sleep = sleep or time.sleep
    ordered = _interleave(urls)
    if limit is not None:
        ordered = ordered[:limit]
    by_host: Dict[str, List[str]] = {}
    for url in ordered:
        by_host.setdefault(urlparse(url).hostname or "", []).append(url)

    checker = _Checker(transport, insecure, headers or {}, timeout, pause,
                       resolve, sleep)
    verdicts: Dict[str, Dict[str, Any]] = {}
    done = 0
    lock = threading.Lock()

    def one_host(batch: List[str]) -> None:
        nonlocal done
        for index, url in enumerate(batch):
            if index:
                sleep(pause)
            try:
                found = checker.check(url)
            except Exception as error:                    # noqa: BLE001
                # One odd address must not end a run over ten thousand.
                logger.warning("could not check %s: %s", url, error)
                found = {"status": "unverified", "reason": str(error)[:200],
                         "checked": _now(), "invalid_cert": False}
            with lock:
                verdicts[url] = found
                done += 1
                if progress is not None:
                    progress(done, len(ordered))

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(one_host, by_host.values()))
    return verdicts, checker.requests
