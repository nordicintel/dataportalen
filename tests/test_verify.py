"""Asking a server whether a distribution is there.

The registry's checker could not get through to a third of what it marks.
`Catalog._verify` asks again from here -- only when called -- and this is how
an answer becomes a verdict.
"""

from __future__ import annotations

from urllib.parse import urlparse

import pytest

from conftest import CATALOG_RECORDS, write_catalog
from dataportalen import Catalog, QueryError, TimeoutError, TransportError, read_catalog
from dataportalen.core import BaseTransport, Response
from dataportalen.verify import _interleave, check_links

CHECKED = "2026-09-28T02:44:17"


class Site(BaseTransport):
    """A fake web: ``answers[(method, url)]`` is a status, an exception, or a
    ``(status, final_url)`` pair. Anything not listed answers 200."""

    def __init__(self, answers=None):
        self.answers = dict(answers or {})
        self.calls = []

    def request(self, method, url, *, headers=None, timeout=None):
        self.calls.append((method, url, dict(headers or {})))
        answer = self.answers.get((method, url), 200)
        if isinstance(answer, list):                # one answer per attempt
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, Exception):
            raise answer
        status, final = answer if isinstance(answer, tuple) else (answer, url)
        return Response(status, {}, b"", final)

    def methods(self, url):
        return [method for method, asked, _ in self.calls if asked == url]


def run(urls, site, **kwargs):
    kwargs.setdefault("resolve", lambda host: True)
    kwargs.setdefault("sleep", lambda seconds: None)
    verdicts, requests = check_links(urls, site, **kwargs)
    return verdicts


A = "https://example.org/a.csv"


def status_of(site, url=A, **kwargs):
    verdict = run([url], site, **kwargs)[url]
    return verdict["status"], verdict["reason"]


# -- an answer ---------------------------------------------------------------


def test_a_head_that_answers_is_alive():
    site = Site()
    assert status_of(site) == ("alive", None)
    assert site.methods(A) == ["HEAD"]


@pytest.mark.parametrize("refusal", [405, 501, 403, 404])
def test_a_refused_head_is_asked_again_with_a_one_byte_get(refusal):
    """Plenty of servers refuse HEAD and serve the file."""
    site = Site({("HEAD", A): refusal})
    assert status_of(site) == ("alive", None)
    assert site.methods(A) == ["HEAD", "GET"]
    assert site.calls[-1][2]["Range"] == "bytes=0-0"


@pytest.mark.parametrize("status, phrase", [(404, "Not Found"), (410, "Gone")])
def test_only_what_the_get_says_is_dead(status, phrase):
    site = Site({("HEAD", A): status, ("GET", A): status})
    assert status_of(site) == ("dead", phrase)


@pytest.mark.parametrize("status, phrase", [
    (400, "Bad Request"), (401, "Unauthorized"), (403, "Forbidden"),
    (500, "Internal Server Error"), (503, "Service Unavailable"),
])
def test_a_server_that_is_there_and_will_not_serve_this_is_unverified(status, phrase):
    """A WMS endpoint answers 400 to an address with no parameters. The
    server is there; what it has not said is that the address is gone."""
    site = Site({("HEAD", A): status, ("GET", A): status})
    assert status_of(site) == ("unverified", phrase)


def test_a_redirect_followed_to_a_real_page_is_alive():
    site = Site({("HEAD", A): (200, "https://example.org/files/a.csv")})
    assert status_of(site) == ("alive", None)


# -- no answer -----------------------------------------------------------------


def test_a_lost_connection_is_tried_once_more():
    site = Site({("HEAD", A): [TransportError("reset"), 200]})
    assert status_of(site) == ("alive", None)
    assert site.methods(A) == ["HEAD", "HEAD"]


def test_a_host_that_exists_and_does_not_answer_is_unverified():
    site = Site({("HEAD", A): TransportError("connection reset")})
    status, reason = status_of(site)
    assert status == "unverified" and "reset" in reason
    assert site.methods(A) == ["HEAD", "HEAD"]


def test_a_host_that_is_not_in_dns_is_dead():
    site = Site({("HEAD", A): TransportError("getaddrinfo failed")})
    assert status_of(site, resolve=lambda host: False) == ("dead", "host not found")


def test_a_timeout_is_unverified_even_if_dns_would_say_no():
    site = Site({("HEAD", A): TimeoutError("timed out")})
    assert status_of(site, resolve=lambda host: False) == ("unverified", "timeout")


def test_a_host_that_stays_silent_is_left_alone_after_two():
    urls = ["https://down.example.org/%d" % n for n in range(5)]
    site = Site({("HEAD", url): TimeoutError("timed out") for url in urls})
    verdicts = run(urls, site)
    assert {v["reason"] for v in verdicts.values()} == {"timeout"}
    assert [len(site.methods(url)) for url in urls] == [2, 2, 0, 0, 0]


def test_an_answer_in_between_keeps_the_host_in_play():
    urls = ["https://flaky.example.org/%d" % n for n in range(4)]
    site = Site({("HEAD", urls[0]): TimeoutError("timed out"),
                 ("HEAD", urls[2]): TimeoutError("timed out")})
    verdicts = run(urls, site)
    assert [verdicts[url]["status"] for url in urls] == [
        "unverified", "alive", "unverified", "alive"]


def test_something_that_is_not_a_web_address_is_not_asked():
    site = Site()
    assert status_of(site, "ftp://user:pw@example.org/x") == (
        "unverified", "not an http address")
    assert site.calls == []


# -- a certificate -------------------------------------------------------------


def test_a_bad_certificate_is_asked_again_without_verification():
    strict = Site({("HEAD", A): TransportError(
        "request failed: SSLError(certificate verify failed)")})
    lenient = Site()
    verdict = run([A], strict, insecure=lenient)[A]
    assert verdict["status"] == "alive" and verdict["invalid_cert"] is True
    assert strict.methods(A) == ["HEAD"]            # not retried: it would fail again
    assert lenient.methods(A) == ["HEAD"]


def test_a_bad_certificate_with_nothing_behind_it_is_still_dead():
    strict = Site({("HEAD", A): TransportError("certificate has expired")})
    lenient = Site({("HEAD", A): 404, ("GET", A): 404})
    verdict = run([A], strict, insecure=lenient)[A]
    assert (verdict["status"], verdict["invalid_cert"]) == ("dead", True)


# -- a rate limit --------------------------------------------------------------


def test_a_rate_limit_is_unverified_and_stops_that_host():
    urls = ["https://busy.example.org/%d" % n for n in range(4)]
    site = Site({("HEAD", urls[1]): 429})
    verdicts = run(urls + [A], site)
    assert [verdicts[url]["status"] for url in urls] == [
        "alive", "unverified", "unverified", "unverified"]
    assert verdicts[urls[1]]["reason"] == "Too Many Requests"
    assert site.methods(urls[2]) == [] and site.methods(urls[3]) == []
    assert verdicts[A]["status"] == "alive"         # another host is unaffected


# -- a soft 404 ----------------------------------------------------------------


def test_a_redirect_to_where_every_unknown_path_goes_is_dead():
    home = "https://example.org/start"

    class Soft(Site):
        def request(self, method, url, *, headers=None, timeout=None):
            if "dataportalen-link-check-" in url:
                self.calls.append((method, url, {}))
                return Response(200, {}, b"", home)
            return super().request(method, url, headers=headers, timeout=timeout)

    site = Soft({("HEAD", A): (200, home)})
    assert status_of(site) == ("dead", "soft 404")


def test_a_site_with_a_real_404_is_not_soft():
    class Hard(Site):
        def request(self, method, url, *, headers=None, timeout=None):
            if "dataportalen-link-check-" in url:
                return Response(404, {}, b"", url)
            return super().request(method, url, headers=headers, timeout=timeout)

    site = Hard({("HEAD", A): (200, "https://example.org/moved/a.csv")})
    assert status_of(site) == ("alive", None)


# -- the run -------------------------------------------------------------------


def test_a_url_is_asked_once_however_often_it_is_named():
    site = Site()
    assert len(run([A, A, A], site)) == 1
    assert site.methods(A) == ["HEAD"]


def test_a_limit_is_spread_over_the_hosts():
    urls = (["https://big.example.org/%d" % n for n in range(5)]
            + ["https://small.example.org/1", "https://tiny.example.org/1"])
    assert sorted(urlparse(u).hostname for u in _interleave(urls)[:3]) == [
        "big.example.org", "small.example.org", "tiny.example.org"]
    assert len(run(urls, Site(), limit=3)) == 3


def test_one_host_is_paused_between_requests():
    pauses = []
    urls = ["https://example.org/%d" % n for n in range(3)]
    check_links(urls, Site(), sleep=pauses.append, pause=0.4,
                resolve=lambda host: True)
    assert pauses == [0.4, 0.4]


# -- Catalog._verify -----------------------------------------------------------


def with_files(*pairs):
    dists = []
    for url, mark in pairs:
        dist = dict(CATALOG_RECORDS[0]["distributions"][0], access_url=url)
        if mark:
            dist["broken"] = {"reason": mark, "checked": CHECKED}
        dists.append(dist)
    return dict(CATALOG_RECORDS[0], distributions=dists)


GONE = "https://example.org/gone.csv"
RESET = "https://api.scb.se/x"
FINE = "https://example.org/fine.csv"
STILL = "https://slow.example.org/y"


@pytest.fixture
def path(tmp_path):
    return write_catalog(tmp_path, [with_files(
        (GONE, "timeout"), (RESET, "request to x failed, reason: read ECONNRESET"),
        (FINE, None), (STILL, "maximum redirect reached"))])


def verified(path, site, **kwargs):
    cat = Catalog(path, max_age=None, _transport=site, exclude_broken=False)
    summary = cat._verify(_insecure=site, **kwargs)
    return cat, summary


def test_verify_asks_about_the_unverified_and_nothing_else(path, monkeypatch):
    monkeypatch.setattr("dataportalen.verify.time.sleep", lambda seconds: None)
    site = Site({("HEAD", GONE): 404, ("GET", GONE): 404,
                 ("HEAD", STILL): TimeoutError("timed out")})
    cat, summary = verified(path, site)
    assert site.methods(FINE) == []
    assert summary["checked"] == 3
    assert (summary["alive"], summary["dead"], summary["unverified"]) == (1, 1, 1)

    by_url = {d.access_url: d for d in cat.datasets()[0].distributions}
    assert by_url[RESET].unverified is None and by_url[RESET].broken is None
    assert by_url[GONE].broken.reason == "Not Found"
    assert by_url[GONE].broken.by == "local"
    assert by_url[STILL].unverified.to_dict() == {
        "reason": "maximum redirect reached", "checked": CHECKED, "by": None}


def test_what_it_found_is_stored_and_read_by_everything(path, monkeypatch):
    monkeypatch.setattr("dataportalen.verify.time.sleep", lambda seconds: None)
    site = Site({("HEAD", GONE): 404, ("GET", GONE): 404})
    verified(path, site)
    by_url = {d["access_url"]: d for d in read_catalog(path)[0]["distributions"]}
    assert by_url[GONE]["broken"]["by"] == "local"
    later = Catalog(path, max_age=None, _transport=Site())
    assert GONE not in [d.access_url for d in later.datasets()[0].distributions]
    assert later.info()["excluded"]["dead_distributions"] == 1


def test_a_later_registry_verdict_beats_an_older_local_one(tmp_path, monkeypatch):
    """The registry said dead after we looked: it looked last."""
    monkeypatch.setattr("dataportalen.verify.time.sleep", lambda seconds: None)
    record = with_files((GONE, "Not Found"))
    record["distributions"][0]["broken"]["checked"] = "2999-01-01T00:00:00"
    path = write_catalog(tmp_path, [record])
    cat, _ = verified(path, Site(), which="broken")
    assert cat.datasets()[0].distributions[0].broken.to_dict() == {
        "reason": "Not Found", "checked": "2999-01-01T00:00:00", "by": None}


def test_a_newer_local_look_brings_a_dead_distribution_back(tmp_path, monkeypatch):
    monkeypatch.setattr("dataportalen.verify.time.sleep", lambda seconds: None)
    path = write_catalog(tmp_path, [with_files((GONE, "Not Found"))])
    cat, summary = verified(path, Site(), which="broken")
    assert summary["alive"] == 1
    assert cat.datasets()[0].distributions[0].broken is None


def test_which_must_be_one_of_three(path):
    cat = Catalog(path, max_age=None, _transport=Site())
    with pytest.raises(QueryError):
        cat._verify("everything")


def test_verify_is_not_public(path):
    assert not hasattr(Catalog(path, max_age=None, _transport=Site()), "verify")


def test_nothing_is_asked_unless_verify_is_called(path):
    site = Site()
    cat = Catalog(path, max_age=None, _transport=site)
    cat.datasets()
    cat.info()
    assert site.calls == []
