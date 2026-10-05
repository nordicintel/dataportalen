"""The registry's nightly link check, and what the catalogue does with it.

The registry says `broken`; its message says why, and the why is two
different things. A server that answered with an error is dead. A checker
that got no answer has told us nothing about the file. Both verdicts are the
registry's own data -- this package tests no link itself -- but only the
first one removes anything.
"""

from __future__ import annotations

import pytest

from conftest import CATALOG_RECORDS, write_catalog
from dataportalen import Catalog, LinkMark, QueryError, read_catalog
from dataportalen.client import _is_dead

CHECKED = "2026-09-28T02:44:17"
ALIVE = object()


def with_files(record, *pairs):
    """A copy of `record` whose files carry what the download stored.

    The download stores `broken: {reason, checked}` on every file the registry
    called broken, whatever the reason; the split happens on reading.
    """
    dists = []
    for (url, reason) in pairs:
        dist = {"format": "csv", "access_url": url, "download_url": None}
        if reason is not ALIVE:
            dist["broken"] = {"reason": reason, "checked": CHECKED}
        dists.append(dist)
    return dict(record, distributions=dists)


PUBLIC = dict(CATALOG_RECORDS[1], access_rights="public")


@pytest.fixture
def path(tmp_path):
    return write_catalog(tmp_path, [
        with_files(CATALOG_RECORDS[0],
                   ("https://example.org/a.csv", "Not Found"),
                   ("https://example.org/b.csv", ALIVE)),
        with_files(PUBLIC, ("https://example.org/c.csv", "Forbidden")),
    ])


# -- which reasons are dead ---------------------------------------------------


@pytest.mark.parametrize("reason", [
    "Not Found", "Forbidden", "Internal Server Error",
    "Bad Request", "Unauthorized", "Access Denied", "Gone", "404",
    "File not found", "Service Unavailable", "Method Not Allowed", "400",
    "Bad Gateway", "not found", " Not Found ",
])
def test_an_http_error_is_dead(reason):
    assert _is_dead(reason)


@pytest.mark.parametrize("reason", [
    None, "",
    "request to https://api.scb.se/OV0104/v1/doris/sv/ssd/x failed, reason: read ECONNRESET",
    "timeout",
    "maximum redirect reached at: https://example.org/boverror/405",
    "ftp://OpenDataSource@opendata.prv.se/ is an url with embedded credentials.",
    "a message the checker has never produced before",
    "OK",
])
def test_no_usable_answer_is_not_dead(reason):
    """An allow-list: what is not a known HTTP error keeps the file."""
    assert not _is_dead(reason)


@pytest.mark.parametrize("reason", [
    "request to https://katalog.datahotell.se/store/99/resource/17 failed, "
    "reason: getaddrinfo ENOTFOUND katalog.datahotell.se",
    "request to http://undefined/ failed, reason: getaddrinfo ENOTFOUND undefined",
])
def test_a_host_that_is_not_in_dns_is_dead(reason):
    """No server to ask is as gone as a server saying no."""
    assert _is_dead(reason)


@pytest.mark.parametrize("reason", [
    "request to http://maps.lantmateriet.se/wms failed, "
    "reason: connect ECONNREFUSED 192.0.2.1:80",
    "request to https://ext-geodata.lansstyrelsen.se/wms failed, "
    "reason: connect EHOSTUNREACH 192.0.2.1:443",
    "request to https://leverantorsfakturor.svedala.se/ failed, reason: ",
    "request to https://example.org/x failed, reason: getaddrinfo EAI_AGAIN example.org",
])
def test_a_host_that_is_there_but_did_not_answer_is_not_dead(reason):
    assert not _is_dead(reason)


@pytest.mark.parametrize("reason", ["Too Many Requests", "too many requests", "429"])
def test_a_rate_limit_is_not_dead(reason):
    """The server asked the checker to slow down. The file is still there."""
    assert not _is_dead(reason)


def test_a_rate_limited_file_says_unverified_and_is_kept(tmp_path, transport):
    record = with_files(CATALOG_RECORDS[0],
                        ("https://example.org/a.csv", "Too Many Requests"))
    for exclude in (True, False):
        cat = Catalog(write_catalog(tmp_path, [record]), max_age=None,
                      _transport=transport, exclude_broken=exclude)
        (a,) = cat.datasets()[0].distributions
        assert a.unverified == LinkMark("Too Many Requests", CHECKED)
        assert a.broken is None


# -- what a record carries ----------------------------------------------------


def test_a_dead_file_says_broken_and_a_working_one_says_nothing(path, transport):
    cat = Catalog(path, max_age=None, _transport=transport, exclude_broken=False)
    a, b = cat.datasets()[0].distributions
    assert a.broken == LinkMark("Not Found", CHECKED)
    assert a.to_dict()["broken"] == {"reason": "Not Found", "checked": CHECKED,
                                     "by": None}
    assert b.broken is None and b.unverified is None
    assert "link" not in a.to_dict() and "link" not in b.to_dict()


def test_a_file_the_checker_could_not_reach_says_unverified(tmp_path, transport):
    """Same two keys, different name, and it is never removed."""
    reset = "request to https://api.scb.se/x failed, reason: read ECONNRESET"
    record = with_files(CATALOG_RECORDS[0],
                        ("https://api.scb.se/x", reset),
                        ("https://api.scb.se/y", None))
    for exclude in (True, False):
        cat = Catalog(write_catalog(tmp_path, [record]), max_age=None,
                      _transport=transport, exclude_broken=exclude)
        x, y = cat.datasets()[0].distributions
        assert x.unverified == LinkMark(reset, CHECKED)
        assert y.unverified == LinkMark(None, CHECKED)
        assert x.broken is None and y.broken is None


def test_the_record_itself_carries_no_verdict(path, transport):
    """277 datasets had a broken landing page and perfectly good files."""
    cat = Catalog(path, max_age=None, _transport=transport, exclude_broken=False)
    for record in cat.datasets():
        assert not {"link", "broken", "unverified"} & set(record.to_dict())


# -- the default leaves dead files, and dead datasets, out --------------------


def test_dead_files_are_left_out_by_default(path, transport):
    cat = Catalog(path, max_age=None, _transport=transport)
    assert cat.search(limit=0).total == 1
    dists = cat.datasets()[0].distributions
    assert [d.access_url for d in dists] == ["https://example.org/b.csv"]


def test_a_dataset_whose_every_file_is_dead_is_left_out(path, transport):
    """127 public datasets. Metadata with nothing to fetch is what dead means."""
    cat = Catalog(path, max_age=None, _transport=transport)
    assert cat.get(PUBLIC["uri"]) is None
    assert cat.get(CATALOG_RECORDS[0]["uri"]) is not None


def test_a_dataset_the_checker_could_not_reach_is_kept(tmp_path, transport):
    """SCB: 4,270 of its 4,306 datasets had every file marked broken, every
    one for want of an answer. None of them is dead."""
    record = with_files(CATALOG_RECORDS[0],
                        ("https://api.scb.se/x", ""),
                        ("https://api.scb.se/y", "timeout"))
    cat = Catalog(write_catalog(tmp_path, [record]), max_age=None,
                  _transport=transport)
    assert cat.search(limit=0).total == 1
    assert len(cat.datasets()[0].distributions) == 2


def test_a_dataset_with_a_dead_file_and_an_unverified_one_keeps_the_second(
        tmp_path, transport):
    record = with_files(CATALOG_RECORDS[0],
                        ("https://example.org/gone.csv", "Not Found"),
                        ("https://example.org/maybe.csv", "timeout"))
    cat = Catalog(write_catalog(tmp_path, [record]), max_age=None,
                  _transport=transport)
    dists = cat.datasets()[0].distributions
    assert [d.access_url for d in dists] == ["https://example.org/maybe.csv"]
    assert dists[0].unverified.reason == "timeout"


def test_a_dataset_that_never_had_files_stays(tmp_path, transport):
    """1,647 of them: APIs and registers. Not dead, just not files."""
    record = dict(CATALOG_RECORDS[0], distributions=[])
    cat = Catalog(write_catalog(tmp_path, [record]), max_age=None,
                  _transport=transport)
    assert cat.search(limit=0).total == 1


def test_exclude_broken_false_keeps_everything(path, transport):
    cat = Catalog(path, max_age=None, _transport=transport, exclude_broken=False)
    assert cat.search(limit=0).total == 2
    assert len(cat.datasets()[0].distributions) == 2


# -- one reading of a record --------------------------------------------------


def test_read_catalog_and_catalog_agree_on_every_file(tmp_path, transport):
    """The split is done where records are read, by both, or the same file
    would be `broken` from one and `unverified` from the other."""
    records = [
        with_files(CATALOG_RECORDS[0],
                   ("https://example.org/a.csv", "Not Found"),
                   ("https://example.org/b.csv", "timeout"),
                   ("https://example.org/c.csv", ALIVE)),
    ]
    path = write_catalog(tmp_path, records)
    raw = read_catalog(path)[0]["distributions"]
    held = Catalog(path, max_age=None, _transport=transport,
                   exclude_broken=False).datasets()[0].distributions
    for key in ("broken", "unverified"):
        assert [LinkMark(**d[key]) if key in d else None for d in raw] == [
            getattr(d, key) for d in held]
    assert [sorted(set(d) & {"broken", "unverified"}) for d in raw] == [
        ["broken"], ["unverified"], []]


def test_link_is_not_a_filter(path, transport):
    cat = Catalog(path, max_age=None, _transport=transport)
    with pytest.raises(QueryError) as info:
        cat.datasets(link="broken")
    assert "unknown filter" in str(info.value)
    assert "link" not in cat.facets()
    with pytest.raises(QueryError) as info:
        cat.data_services(link="broken")
    assert "unknown filter" in str(info.value)


# -- a file the check never saw -----------------------------------------------


def test_a_file_the_check_never_saw_is_simply_unmarked(tmp_path, transport):
    """An older file, or one written with links=False, is not 'broken'."""
    cat = Catalog(write_catalog(tmp_path), max_age=None, _transport=transport)
    for record in cat.datasets():
        for dist in record.distributions:
            assert dist.broken is None and dist.unverified is None
    assert cat.search(limit=0).total == 2


# -- what was left out is reported --------------------------------------------


def test_info_counts_what_the_scope_left_out(path, transport):
    """A shortcoming is reported with the answer, not only in a log line."""
    info = Catalog(path, max_age=None, _transport=transport).info()
    assert info["excluded"] == {"dead_distributions": 2, "dead_datasets": 1}
    everything = Catalog(path, max_age=None, _transport=transport,
                         exclude_broken=False).info()
    assert everything["excluded"] == {"dead_distributions": 0, "dead_datasets": 0}


def test_no_access_rights_are_left_out(tmp_path, transport):
    """The catalogue holds every access_rights value; it is a filter."""
    path = write_catalog(tmp_path)
    cat = Catalog(path, max_age=None, _transport=transport)
    assert "access_rights" not in cat.info()["excluded"]
    assert cat.search(access_rights="non_public", limit=0).total == 1
    with pytest.raises(TypeError):
        Catalog(path, max_age=None, _transport=transport, access_rights="public")


def test_info_counts_the_unverified_it_still_holds(tmp_path, transport):
    record = with_files(CATALOG_RECORDS[0],
                        ("https://example.org/a.csv", "timeout"),
                        ("https://example.org/b.csv", "Too Many Requests"),
                        ("https://example.org/c.csv", ALIVE))
    cat = Catalog(write_catalog(tmp_path, [record]), max_age=None,
                  _transport=transport)
    assert cat.info()["unverified_distributions"] == 2
