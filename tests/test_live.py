"""Tests that hit the real registry.

Skipped by default. Run them with::

    DATAPORTAL_LIVE=1 pytest -m network

They are deliberately tolerant about content -- the registry re-harvests
nightly -- and strict about the things a release could quietly break: that the
download still assembles a complete record, that the local answers still match
the live ones, and that the registry's own limits are what the package assumes.

A few need a downloaded catalogue. They use whatever copy is already on the
machine rather than spending seven minutes, and skip if there is none.
"""

from __future__ import annotations

import os

import pytest

from dataportalen import (
    Catalog,
    DataportalWarning,
    MultilingualText,
    QueryError,
    default_catalog_path,
    read_catalog,
)
from dataportalen.client import _Registry, download_catalog
from dataportalen.entries import AgentEntry, DataServiceEntry, DatasetEntry
from dataportalen.query import Q
from dataportalen.rdf import DCAT

pytestmark = pytest.mark.network

if not os.environ.get("DATAPORTAL_LIVE"):
    pytest.skip(
        "live registry tests; set DATAPORTAL_LIVE=1 to run", allow_module_level=True
    )


@pytest.fixture(scope="module")
def registry():
    with _Registry() as client:
        yield client


@pytest.fixture(scope="module")
def cat():
    """The copy already on this machine; not worth seven minutes to rebuild."""
    path = default_catalog_path()
    if not os.path.exists(path):
        pytest.skip("no catalogue downloaded; run Catalog() once first")
    return Catalog(path, max_age=None)


# -- what the registry can do, which the design depends on -------------------


def test_the_page_cap_is_still_one_hundred(registry):
    """Asking for more returns 100. The whole local-first design rests on it."""
    response = registry._request("/store/search", {
        "type": "solr", "query": "public:true", "limit": 1000})
    payload = response.json()
    assert payload["limit"] == 100
    assert len(payload["resource"]["children"]) == 100


def test_the_corpus_is_the_size_the_docs_claim(registry):
    datasets = registry._count(Q.rdf_type(DCAT.Dataset) & Q.public())
    services = registry._count(Q.rdf_type(DCAT.DataService) & Q.public())
    assert 20_000 < datasets < 30_000, datasets
    assert 400 < services < 1_200, services


def test_deep_paging_reaches_the_end(registry):
    page = registry._search(Q.rdf_type(DCAT.Dataset) & Q.public(),
                            limit=5, offset=23_000, sort="uri asc")
    assert page.total > 23_000


def test_paging_yields_distinct_entries(registry):
    """The stable sort has to be unique per entry or a boundary can move."""
    from dataportalen.client import STABLE_SORT

    seen = []
    for offset in (0, 100, 200):
        page = registry._search(Q.rdf_type(DCAT.Dataset) & Q.public(),
                                limit=100, offset=offset, sort=STABLE_SORT,
                                model=DatasetEntry)
        seen.extend(e.entry_uri for e in page)
    assert len(seen) == 300
    assert len(set(seen)) == 300


def test_unknown_entry_ids_raise_not_found(registry):
    from dataportalen import NotFoundError

    with pytest.raises(NotFoundError):
        registry._entry_raw("999999", "999999")


def test_the_transport_reaches_the_registry():
    from dataportalen.core import RequestsTransport

    with _Registry(transport=RequestsTransport()) as client:
        assert client._count(Q.public()) > 0


# -- the download assembles a complete record --------------------------------


def test_a_small_export_is_complete_and_parseable(tmp_path):
    out = tmp_path / "sample.sqlite"
    summary = download_catalog(str(out), limit=200)
    assert summary.datasets == 200

    records = read_catalog(str(out))
    assert len(records) == 200
    for record in records:
        assert record["type"] == "dataset"
        assert record["uri"]
        assert isinstance(record["title"], dict)
        assert set(record["title"]) <= {"sv", "en"}, record["title"]
        assert MultilingualText(record["title"]).text(), (
            "every dataset has a title in some language")

    # A dataset that references distributions must carry them, not just URIs.
    withdists = [r for r in records if r["distributions"]]
    assert withdists, "200 datasets with no distributions at all is wrong"
    for record in withdists:
        for dist in record["distributions"]:
            assert dist["uri"]


def test_an_export_is_a_usable_database(tmp_path):
    """Not a stream of lines: it has to open as SQLite and carry its meta."""
    import sqlite3

    out = tmp_path / "sample.sqlite"
    download_catalog(str(out), limit=20)
    assert out.read_bytes()[:15] == b"SQLite format 3"

    db = sqlite3.connect(str(out))
    try:
        assert db.execute("SELECT count(*) FROM record").fetchone()[0] == 20
        meta = dict(db.execute("SELECT key, value FROM meta"))
    finally:
        db.close()
    assert meta["first_retrieved"] and meta["last_refreshed"]
    assert meta["schema"]


def test_agents_resolve_against_all_three_rdf_types(registry):
    """Publishers are spread over three rdf:types, not one."""
    from dataportalen.client import _AGENT_TYPES

    every = registry._count(Q.rdf_type(*_AGENT_TYPES) & Q.public())
    agents_only = registry._count(Q.rdf_type("foaf:Agent") & Q.public())
    assert every > agents_only, (
        "foaf:Agent alone misses the foaf:Organization and prov:Agent "
        "entries, which are a quarter of the index")


def test_a_uri_can_belong_to_two_entries(registry):
    """The same dataset harvested into two catalogues shares one resource URI."""
    page = registry._search(Q.rdf_type(DCAT.Dataset) & Q.public(),
                            limit=100, sort="uri asc", model=DatasetEntry)
    uris = [e.resource_uri for e in page]
    assert len(uris) == 100
    # Not an assertion about duplicates existing -- just that resource_uri is
    # not what identifies an entry. context/entry ids are.
    assert all(e.context_id and e.entry_id for e in page)


# -- local answers match live ones -------------------------------------------


@pytest.mark.parametrize("filters,query", [
    ({"theme": "transport"}, None),
    ({"access_rights": "public"}, None),
    ({"publisher": "trafikverket"}, None),
])
def test_a_local_count_is_in_the_same_league_as_the_registry(cat, registry,
                                                             filters, query):
    """Not equality: the registry re-harvests nightly and the copy does not."""
    local = cat.search(limit=0, **filters).total
    assert local > 0
    live = registry._count(Q.rdf_type(DCAT.Dataset) & Q.public())
    assert local <= live


def test_every_dataset_filter_matches_something_locally(cat):
    """A filter that matches nothing across 23k datasets is a broken filter."""
    from dataportalen.models import DATASET_FILTERS

    options = cat.facets()
    for name in DATASET_FILTERS:
        values = options[name]
        assert values, "%s has no values at all" % name
        value, count = values[0]
        assert cat.search(limit=0, **{name: value}).total == count, name


def test_every_data_service_filter_matches_something_locally(cat):
    from dataportalen.models import DATA_SERVICE_FILTERS

    services = cat.data_services()
    read = {
        "publisher": lambda s: [s.publisher.id],
        "publisher_type": lambda s: [s.publisher.type],
        "service_type": lambda s: [s.service_type],
        "theme": lambda s: s.themes,
        "keyword": lambda s: s.keywords.all(),
        "access_rights": lambda s: [s.access_rights],
    }
    assert set(read) == set(DATA_SERVICE_FILTERS)
    for name in DATA_SERVICE_FILTERS:
        holder = next((s for s in services if any(read[name](s))), None)
        if holder is None:
            continue            # some are sparse on services; not an error
        value = next(v for v in read[name](holder) if v)
        found = cat.data_services(**{name: value})
        assert holder.uri in {s.uri for s in found}, name
        if name != "keyword":                   # keywords match folded
            assert all(value in read[name](s) for s in found), name


def test_get_returns_a_record_the_search_returned(cat):
    dataset = cat.search(limit=1).datasets[0]
    assert cat.get(dataset.uri).to_dict() == dataset.to_dict()


def test_get_in_turtle_comes_from_the_registry(cat):
    dataset = cat.search(limit=1).datasets[0]
    turtle = cat.get(dataset.uri, format="turtle")
    assert turtle and "<" in turtle


def test_vocabulary_labels_resolve_on_live_data(cat):
    """A short value with no label would leak a URI tail into the output."""
    from dataportalen.rdf import label_for

    options = cat.facets()
    for name in ("theme", "access_rights", "accrual_periodicity"):
        labelled = sum(1 for row in options[name] if label_for(row.value))
        assert labelled >= len(options[name]) * 0.7, (
            "%s: only %d of %d values have a label"
            % (name, labelled, len(options[name])))


def test_publishers_lead_into_a_search(cat):
    for row in cat.facets()["publisher"][:5]:
        assert cat.search(limit=0, publisher=row.value).total == row.count
        assert row.label, "a publisher row without a name is not much use"


# -- data services, live -----------------------------------------------------


def test_data_services_are_typed_and_have_endpoints(registry):
    page = registry._search(Q.rdf_type(DCAT.DataService) & Q.public(),
                            limit=20, model=DataServiceEntry)
    assert len(page) == 20
    with_endpoint = [e for e in page if e.endpoint_url]
    assert len(with_endpoint) >= 15, "endpointURL is on 99.8% of them"


def test_no_record_carries_creators(cat):
    """Dropped in 0.9.0. A stale copy would still have them, so this also
    proves the schema bump forced a rebuild."""
    for record in cat.datasets()[:200] + cat.data_services()[:200]:
        assert "creators" not in record.to_dict()


def test_a_publisher_uri_resolves_to_an_agent(registry, cat):
    dataset = next(d for d in cat.search(limit=50).datasets if d.publisher.uri)
    found = registry._lookup_many([dataset.publisher.uri], model=AgentEntry)
    assert found and found[0].name


def test_the_registry_can_narrow_by_its_own_timestamp(registry):
    """The whole incremental design rests on this range query working."""
    from dataportalen.client import _solr_stamp

    base = Q.rdf_type(DCAT.Dataset) & Q.public()
    everything = registry._count(base)
    recent = registry._count(
        base & Q.raw("modified:[%s TO *]" % _solr_stamp("2026-09-01")))
    assert 0 < recent < everything, (recent, everything)


def test_an_incremental_refresh_replaces_rows_without_dropping_any(tmp_path):
    """A refresh must leave the rows it did not ask about alone."""
    import sqlite3

    from dataportalen.client import _connect, _meta_get, _meta_set

    out = str(tmp_path / "inc.sqlite")
    download_catalog(out, limit=200)
    before = sqlite3.connect(out).execute(
        "SELECT count(*) FROM record").fetchone()[0]
    assert before == 200
    built = _meta_get(_connect(out), "first_retrieved")

    # Ask only for what the registry has touched very recently, so the refresh
    # is small, then check nothing else was disturbed.
    db = _connect(out)
    with db:
        _meta_set(db, last_refreshed="2026-09-29T00:00:00")
    db.close()
    download_catalog(out, since="2026-09-29T00:00:00")

    after = sqlite3.connect(out).execute(
        "SELECT count(*) FROM record").fetchone()[0]
    assert after >= before, "a refresh must not lose rows"
    assert _meta_get(_connect(out), "first_retrieved") == built
    assert _meta_get(_connect(out), "last_refreshed") > "2026-09-29T00:00:00"


# -- Catalog(live=True) against the local copy --------------------------------
#
# The copy on this machine and the registry drift apart as the registry
# re-harvests, so a value is allowed to differ by a little for that reason
# alone. Beyond that, each known difference is listed by name with why.


#: Values the registry's index counts differently, measured 2026-10-01.
#: Nested nodes and second values make most of them "more"; format's
#: dataset-level index lacks a few; a value only nested nodes carry is one
#: no record has (accrual_periodicity=quadrennial, format=wms_tjanst).
KNOWN = {
    ("datasets", "format", "microsoft_excel"),
    ("datasets", "format", "wms_tjanst"),
    ("datasets", "format", "zip"),              # two spellings, counts added
    ("datasets", "accrual_periodicity", "continuous"),   # 650 live, 634 locally
    ("datasets", "accrual_periodicity", "quadrennial"),  # 5 live, nested only
    ("datasets", "accrual_periodicity", "decennial"),    # 1 live, the same
}

#: How far a count may drift between the copy and the registry: a re-harvest
#: touches about 630 datasets a day.
DRIFT = 0.02


@pytest.fixture(scope="module")
def whole():
    """The copy with nothing left out: the registry cannot see link health."""
    path = default_catalog_path()
    if not os.path.exists(path):
        pytest.skip("no catalogue downloaded; run Catalog() once first")
    return Catalog(path, max_age=None, exclude_broken=False)


@pytest.fixture(scope="module")
def live():
    with pytest.warns(DataportalWarning, match="live=True"):
        catalog = Catalog(live=True)
    with catalog:
        yield catalog


def _close(a, b):
    return abs(a - b) <= max(3, DRIFT * max(a, b))


def _counted(catalog, kind, **filters):
    """``(total, facets)`` for one kind of record, from whichever engine.

    Only datasets have a public search with facets; data services are a
    plain list, so their facets are asked of the engines underneath.
    """
    find = catalog._live().find if catalog.live else catalog._find
    _, total, facets = find(kind, filters, limit=0)
    return total, facets


@pytest.mark.parametrize("kind", ["datasets", "data_services"])
def test_every_live_facet_value_is_its_filter_s_count_and_near_the_local_one(
        whole, live, kind):
    """Over every value of every filter the registry can facet.

    Equal to the local count up to drift, except the named ones; and never
    fewer than locally except where the index is known to lack values.
    """
    record = kind[:-1]
    local, remote = _counted(whole, record)[1], _counted(live, record)[1]
    problems = []
    for name in remote:
        mine = {row.value: row.count for row in local[name]}
        for row in remote[name]:
            key = (kind, name, row.value)
            found = _counted(live, record, **{name: row.value})[0]
            if found != row.count and key not in KNOWN:
                problems.append("%s=%s: facet %d, filter %d" % (
                    name, row.value, row.count, found))
            if key not in KNOWN and not _close(found, mine.get(row.value, 0)):
                problems.append("%s=%s: live %d, local %d" % (
                    name, row.value, found, mine.get(row.value, 0)))
    assert not problems, problems


def test_live_records_are_the_local_records(whole, live):
    """The same assembly code, so the same model -- less link health."""
    def without_links(record):
        record = record.to_dict()
        record["distributions"] = [
            {k: v for k, v in d.items() if k not in ("broken", "unverified")}
            for d in record["distributions"]]
        return record

    page = live.search(limit=50, offset=5000).datasets
    same = [r for r in page if whole.get(r.uri)
            and without_links(whole.get(r.uri)) == without_links(r)]
    assert len(same) >= 45, "%d of %d identical" % (len(same), len(page))
    # Twenty, not data_services(): that would be every page of the registry's.
    services, _, _ = live._live().find("data_service", {}, limit=20, facets=False)
    assert sum(1 for r in services if whole._get_local(r["uri"]) == r) >= 18


def test_live_publishers_are_the_local_publishers(whole, live):
    mine = {row.id: row.to_dict() for row in whole.publishers()}
    theirs = {row.id: row.to_dict() for row in live.publishers()}
    assert set(theirs) == set(mine)
    for pid, row in mine.items():
        other = theirs[pid]
        assert {k: v for k, v in other.items() if not k.endswith("_count")} == {
            k: v for k, v in row.items() if not k.endswith("_count")}, pid
        assert _close(other["dataset_count"], row["dataset_count"]), pid


def test_a_live_keyword_is_case_insensitive_like_the_local_one(whole, live):
    for keyword in ("kommun", "KOMMUN", "Hälsa", "Öppna data"):
        assert _close(live.search(keyword=keyword, limit=0).total,
                      whole.search(keyword=keyword, limit=0).total), keyword


def test_live_dates_are_near_the_local_ones(whole, live):
    """The index holds a date from any node, so either way, but not far.

    modified_after is the one date filter now, and both engines read issued
    for a dataset that has no modified.
    """
    for value in ("2025", "2015-06"):
        a = live.search(limit=0, modified_after=value).total
        b = whole.search(limit=0, modified_after=value).total
        assert abs(a - b) <= 0.05 * b, (value, a, b)


def test_publisher_type_would_not_fit_in_a_request(whole):
    """Why live search refuses it: the URIs of every national authority."""
    from dataportalen.client import MAX_URL

    uris = {row.uri for row in whole.publishers()
            if row.type == "national_authority"}
    assert len(" OR ".join(uris)) * 1.3 > MAX_URL      # escaped, encoded
    with pytest.raises(QueryError, match="publisher_type"):
        whole.search(limit=0, live=True, publisher_type="national_authority")


# -- harvest status and link verification --------------------------------------


def test_the_harvest_results_are_readable_and_say_success_or_failed(registry):
    """What `stale` and `info()["sources"]` rest on: one latest result per
    source, public, with a status the code knows."""
    from dataportalen.client import _Counter, _harvest_status

    found = _harvest_status(registry, _Counter())
    assert found is not None and len(found) > 500
    assert {row["status"] for row in found.values()} <= {"success", "failed"}
    assert all(row["harvested"] for row in found.values())


def test_verify_gets_answers_from_real_servers(tmp_path):
    """Twenty-five addresses, one per host in turn. Not a claim about any of them:
    only that asking works and something answers."""
    import shutil

    source = default_catalog_path()
    if not os.path.exists(source):
        pytest.skip("no catalogue downloaded; run Catalog() once first")
    copy = str(tmp_path / "catalog.sqlite")
    shutil.copyfile(source, copy)
    with Catalog(copy, max_age=None) as catalog:
        summary = catalog._verify(limit=25)
    assert summary["checked"] == 25
    assert summary["alive"] + summary["dead"] + summary["unverified"] == 25
    assert summary["alive"] + summary["dead"] >= 1
