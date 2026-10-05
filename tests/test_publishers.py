"""Publishers: the third thing the registry holds.

A publisher is who a dataset or a data service comes from. It has an `id` --
what `publisher=` takes and what the `publisher` facet reports -- and that id,
not its URI, is its identity: 8 of the registry's 356 publishers have two
URIs. These tests hold the invariants a design panel measured on the real
catalogue, on a fixture small enough to read.
"""

from __future__ import annotations

import pytest

from conftest import CATALOG_RECORDS, SERVICE_RECORDS, write_catalog
from dataportalen import Catalog, Facets, MultilingualText, Publisher, QueryError, read_catalog
from dataportalen.models import DATASET_FILTERS

TRV = "http://dataportal.se/organisation/SE2021006297"
FK = "http://dataportal.se/organisation/SE2021005521"
SCB = "http://dataportal.se/organisation/SE2021000837"
FOHM_TABLE = "http://dataportal.se/organisation/SE2021006545"
FOHM_APP = "https://fohm-app.folkhalsomyndigheten.se/Folkhalsodata/organization/1669"

ROW_KEYS = ["id", "uri", "name", "alias", "type", "homepage", "email",
            "identifiers", "dataset_count", "data_service_count", "facets"]
NESTED_KEYS = ROW_KEYS[:8]
COUNT_KEYS = ROW_KEYS[:10]


def record(n, publisher, **over):
    """A dataset record with its own identity and the given publisher."""
    base = dict(CATALOG_RECORDS[0], uri="https://example.org/d%d" % n,
                context_id="9", entry_id=str(n), publisher=publisher)
    base.update(over)
    return base


def build(tmp_path, transport, records, **options):
    return Catalog(write_catalog(tmp_path, records), max_age=None,
                   _transport=transport, **options)


def nested(publisher):
    """The fields a publisher nested in a record fills."""
    return {key: publisher.to_dict()[key] for key in NESTED_KEYS}


@pytest.fixture
def catalog(tmp_path, transport):
    return build(tmp_path, transport, CATALOG_RECORDS + SERVICE_RECORDS)


# -- the list -----------------------------------------------------------------


def test_publishers_is_a_plain_list_of_rows(catalog):
    rows = catalog.publishers()
    assert type(rows) is list
    assert [row.id for row in rows] == ["forsakringskassan", "trafikverket"]
    for row in rows:
        assert isinstance(row, Publisher)
        assert list(row.to_dict()) == ROW_KEYS
        assert isinstance(row.name, MultilingualText)
        assert row.alias is None or isinstance(row.alias, str)
        assert isinstance(row.identifiers, list)
        assert row.facets is None


def test_as_dict_is_the_models_to_dict(catalog):
    assert catalog.publishers(as_dict=True) == [
        row.to_dict() for row in catalog.publishers()]
    assert catalog.publisher("trafikverket", as_dict=True) == \
        catalog.publisher("trafikverket").to_dict()


def test_a_row_says_who_and_how_much(catalog):
    row = catalog.publisher("trafikverket")
    assert row.uri == TRV
    assert row.name == {"sv": "Trafikverket"}
    assert row.type == "national_authority"
    assert (row.dataset_count, row.data_service_count) == (1, 1)


def test_the_counts_are_what_the_filter_returns(catalog):
    """For every row, and neither call raises."""
    for row in catalog.publishers():
        assert catalog.search(publisher=row.id, limit=0).total == row.dataset_count
        assert len(catalog.data_services(publisher=row.id)) == \
            row.data_service_count


def test_rows_with_datasets_are_the_publisher_facet(catalog):
    rows = [(row.id, row.dataset_count) for row in catalog.publishers()
            if row.dataset_count]
    assert rows == [(v.value, v.count) for v in catalog.facets()["publisher"]]


def test_a_facet_row_is_labelled_with_the_publisher_s_name(catalog):
    for value in catalog.facets()["publisher"]:
        assert value.label == catalog.publisher(value.value).name


def test_info_counts_publishers_the_same_way(catalog):
    assert catalog.info()["publishers"] == len(catalog.publishers()) == 2


def test_most_datasets_first_then_by_id(tmp_path, transport):
    a = {"uri": TRV, "name": {"sv": "Trafikverket"}}
    b = {"uri": FK, "name": {"sv": "Försäkringskassan"}}
    catalog = build(tmp_path, transport,
                    [record(1, a), record(2, a), record(3, b)])
    assert [(r.id, r.dataset_count) for r in catalog.publishers()] == [
        ("trafikverket", 2), ("forsakringskassan", 1)]


def test_what_you_get_back_is_yours_to_change(catalog):
    first = catalog.publishers()[0]
    first.to_dict()["name"]["sv"] = "changed"
    first.identifiers.append("x")
    catalog.publisher("trafikverket").identifiers.append("x")
    assert catalog.publishers()[0].name == {"sv": "Försäkringskassan"}
    assert catalog.publishers()[0].identifiers == []
    assert catalog.publisher("trafikverket").identifiers == []


def test_a_name_is_read_only(catalog):
    with pytest.raises(TypeError):
        catalog.publishers()[0].name["sv"] = "changed"


def test_the_list_takes_only_the_filters_both_kinds_share(catalog):
    """A publisher's two counts answer the same question, so a dataset-only
    filter is refused rather than silently counting no data services."""
    with pytest.raises(QueryError):
        catalog.publishers(format="csv")
    assert [r.id for r in catalog.publishers(publisher="trafikverket")] == \
        ["trafikverket"]


# -- the detail ---------------------------------------------------------------


def test_the_detail_is_the_row_plus_facets(catalog):
    detail = catalog.publisher("trafikverket")
    assert list(detail.to_dict()) == ROW_KEYS
    assert isinstance(detail.facets, Facets)
    row = next(r for r in catalog.publishers() if r.id == "trafikverket")
    assert {k: detail.to_dict()[k] for k in COUNT_KEYS} == \
        {k: row.to_dict()[k] for k in COUNT_KEYS}


def test_the_facets_are_the_search_s_own(catalog):
    """Five of them -- not publisher or publisher_type, which are the publisher
    itself, nor keyword -- and every value feeds back in beside publisher=."""
    for row in catalog.publishers():
        facets = catalog.publisher(row.id).facets.counts()
        assert list(facets) == ["theme", "format", "kind", "access_rights",
                                "accrual_periodicity"]
        assert set(facets) < set(DATASET_FILTERS)
        search = catalog.search(publisher=row.id, limit=0).facets.counts()
        for name in facets:
            assert facets[name] == search[name]
            for value, count in facets[name].items():
                assert catalog.search(publisher=row.id, limit=0,
                                      **{name: value}).total == count


def test_a_publisher_with_only_data_services_keeps_the_full_shape(
        tmp_path, transport):
    """Sjöfartsverket, in the default catalogue: 0 datasets, 8 services."""
    catalog = build(tmp_path, transport, [CATALOG_RECORDS[0], SERVICE_RECORDS[1]])
    detail = catalog.publisher("forsakringskassan")
    assert (detail.dataset_count, detail.data_service_count) == (0, 1)
    assert detail.facets.counts() == {name: {} for name in detail.facets}
    assert "forsakringskassan" not in [v.value for v in catalog.facets()["publisher"]]


# -- one resolver, for publisher() and publisher= ----------------------------


@pytest.fixture
def scb(tmp_path, transport):
    agent = {"uri": SCB, "type": "national_authority", "identifiers": ["2021000837"],
             "name": {"sv": "Statistikmyndigheten SCB", "en": "Statistics Sweden"}}
    return build(tmp_path, transport, [record(1, agent), CATALOG_RECORDS[0]])


@pytest.mark.parametrize("value", [
    "statistikmyndigheten_scb_statistiska_centralbyran",   # its id
    "scb", "SCB",                                           # its alias
    SCB,                                                    # its URI
    "2021000837",                                           # the number as written
    "SE2021000837",                                         # ...and as the table has it
    "Statistikmyndigheten SCB",                             # its Swedish name
    "Statistics Sweden",                                    # its English name
])
def test_everything_a_publisher_shows_resolves_to_it(scb, value):
    """The cheat sheet promises "alias | slug | name | org.nr | URI". Before this a
    URI raised, a bare organisation number raised and 24 names raised."""
    assert scb.publisher(value).id == \
        "statistikmyndigheten_scb_statistiska_centralbyran"
    assert scb.search(publisher=value, limit=0).total == 1


def test_a_list_is_any_of(scb):
    assert scb.search(publisher=["scb", "trafikverket"], limit=0).total == 2


def test_an_unknown_publisher_raises_and_suggests(catalog):
    for call in (catalog.publisher, lambda v: catalog.datasets(publisher=v),
                 lambda v: catalog.search(publisher=v)):
        with pytest.raises(QueryError) as info:
            call("trafikvrket")
        assert "unknown publisher" in str(info.value)
        assert "trafikverket" in str(info.value)


@pytest.mark.parametrize("value", ["", "   ", None, 7])
def test_a_blank_publisher_is_refused(catalog, value):
    with pytest.raises(QueryError):
        catalog.publisher(value)


def test_a_known_organisation_with_nothing_here_is_none_not_an_error(catalog):
    """UHR is in the package's table and in no record of this catalogue."""
    assert catalog.publisher("uhr") is None
    assert catalog.search(publisher="uhr", limit=0).total == 0


def test_a_publisher_of_datasets_only_does_not_break_a_service_search(
        tmp_path, transport):
    """It raised "unknown publisher": ids were vouched for per kind of record,
    and one whose id comes from its name has no data service to vouch for it."""
    agent = {"uri": "https://www.katrineholm.se/", "name": {"sv": "Katrineholms kommun"}}
    catalog = build(tmp_path, transport, [record(1, agent)] + SERVICE_RECORDS)
    assert catalog.search(publisher="katrineholms_kommun", limit=0).total == 1
    assert catalog.data_services(publisher="katrineholms_kommun") == []


# -- identity: the id, not the URI --------------------------------------------


@pytest.fixture
def fohm(tmp_path, transport):
    """Folkhälsomyndigheten as the registry has it: two agents, one id.

    1,668 datasets sit under an agent on its own server with both names and
    nothing else; 29 under the dataportal.se agent with a type, a homepage and
    an organisation number.
    """
    app = {"uri": FOHM_APP, "name": {"sv": "Folkhälsomyndigheten",
                                     "en": "Public Health Agency of Sweden"}}
    table = {"uri": FOHM_TABLE, "name": {"sv": "Folkhälsomyndigheten"},
             "type": "national_authority", "identifiers": ["2021006545"],
             "homepage": "https://www.folkhalsomyndigheten.se/"}
    return build(tmp_path, transport,
                 [record(1, app), record(2, app), record(3, app), record(4, table)])


def test_two_uris_are_one_publisher_and_the_counts_add_up(fohm):
    rows = fohm.publishers()
    assert [(r.id, r.dataset_count) for r in rows] == [
        ("folkhalsomyndigheten", 4)]
    assert rows[0].alias == "fohm"


def test_the_publisher_is_one_real_agent_never_a_merge(fohm):
    """The agent the package's table knows, though it is on fewer records.
    A field-by-field majority would show the fohm-app URI beside an
    organisation number only the other agent carries: an object no agent is."""
    row = fohm.publisher("fohm")
    assert row.uri == FOHM_TABLE
    assert row.identifiers == ["2021006545"]
    assert row.name == {"sv": "Folkhälsomyndigheten"}
    assert nested(row) in [nested(r.publisher) for r in fohm.datasets()]


def test_either_uri_and_either_name_find_it(fohm):
    for value in (FOHM_APP, FOHM_TABLE, "Public Health Agency of Sweden",
                  "2021006545", "fohm", "folkhalsomyndigheten"):
        assert fohm.publisher(value).dataset_count == 4, value


def test_without_a_table_known_uri_the_busiest_agent_wins_then_the_lowest_uri(
        tmp_path, transport):
    a = {"uri": "https://b.example.org/org", "name": {"sv": "Ny Organisation"},
         "type": "company"}
    b = {"uri": "https://a.example.org/org", "name": {"sv": "Ny organisation"},
         "type": "local_authority"}
    busiest = build(tmp_path, transport, [record(1, a), record(2, a), record(3, b)])
    assert busiest.publisher("ny_organisation").type == "company"
    tied = build(tmp_path, transport, [record(1, a), record(2, b)])
    assert tied.publisher("ny_organisation").uri == "https://a.example.org/org"


def test_who_a_publisher_is_does_not_depend_on_the_filter(tmp_path, transport):
    """Region Uppsala: a regional authority under one agent, a local one under
    another, and only the second has a public record. Built from the matching
    records its type would change with access_rights=."""
    table = {"uri": FOHM_TABLE, "name": {"sv": "Folkhälsomyndigheten"},
             "type": "national_authority"}
    other = {"uri": FOHM_APP, "name": {"sv": "Folkhälsomyndigheten"},
             "type": "local_authority"}
    catalog = build(tmp_path, transport,
                    [record(1, table, access_rights="non_public"),
                     record(2, other, access_rights="public")])
    assert catalog.publisher("fohm").type == "national_authority"
    assert [(r.id, r.type, r.dataset_count)
            for r in catalog.publishers(access_rights="public")] == [
        ("folkhalsomyndigheten", "national_authority", 1)]


def test_a_publisher_with_nothing_that_matches_is_not_listed(catalog):
    """The catalogue holds every access_rights value; the filter narrows it."""
    public = [(r.id, r.dataset_count, r.data_service_count)
              for r in catalog.publishers(access_rights="public")]
    assert public == [("trafikverket", 1, 1), ("forsakringskassan", 0, 1)]
    assert [r.id for r in catalog.publishers(access_rights="non_public")] == \
        ["forsakringskassan"]
    assert catalog.search(publisher="forsakringskassan", access_rights="public",
                          limit=0).total == 0
    assert catalog.publisher("forsakringskassan").dataset_count == 1


def test_an_id_wins_over_somebody_else_s_name(tmp_path, transport):
    """The one collision in the registry: 'Göteborgs Naturhistoriska Museum'
    is a name of one publisher and slugifies to the id of another."""
    one = {"uri": "https://ror.org/01yhex183",
           "name": {"sv": "Göteborgs Naturhistoriska Museum",
                    "en": "Gothenburg Natural History Museum"}}
    two = {"uri": "http://www.gnm.se/", "name": {"sv": "Göteborgs naturhistoriska museum"}}
    catalog = build(tmp_path, transport, [record(1, one), record(2, two)])
    ids = sorted(r.id for r in catalog.publishers())
    assert len(ids) == 2
    for pid in ids:
        assert catalog.publisher(pid).id == pid


# -- the nested publisher -----------------------------------------------------


def test_every_record_s_publisher_carries_id_and_alias(scb):
    for found in scb.datasets() + scb.data_services():
        assert list(found.publisher.to_dict()) == ROW_KEYS
        assert found.publisher.dataset_count is None
        assert found.publisher.data_service_count is None
        assert found.publisher.facets is None
    mine = scb.search(publisher="scb").datasets[0].publisher
    assert mine.id == "statistikmyndigheten_scb_statistiska_centralbyran"
    assert mine.alias == "scb"


def test_a_record_with_no_publisher_has_the_same_keys_and_no_id(tmp_path, transport):
    """27 records name no publisher. They belong to no row."""
    catalog = build(tmp_path, transport, [record(1, None), CATALOG_RECORDS[0]])
    nobody = catalog.get("https://example.org/d1").publisher
    assert nobody.to_dict() == {
        "id": None, "uri": None, "name": {}, "alias": None, "type": None,
        "homepage": None, "email": None, "identifiers": [],
        "dataset_count": None, "data_service_count": None, "facets": None}
    assert sum(r.dataset_count for r in catalog.publishers()) == len(catalog) - 1


def test_two_empty_publishers_do_not_share_their_lists(tmp_path, transport):
    catalog = build(tmp_path, transport, [record(1, None), record(2, None)])
    first, second = (r.publisher for r in catalog.datasets())
    first.identifiers.append("x")
    assert second.identifiers == []


def test_read_catalog_gives_the_same_publisher_dict(tmp_path, transport):
    path = write_catalog(tmp_path, CATALOG_RECORDS + SERVICE_RECORDS)
    held = Catalog(path, max_age=None, _transport=transport, exclude_broken=False)
    by_uri = {r["uri"]: r["publisher"] for r in read_catalog(path)}
    for found in held.datasets() + held.data_services():
        assert by_uri[found.uri] == nested(found.publisher)


def test_publisher_type_still_reads_the_record_s_own_agent(fohm):
    """Left alone on purpose. The agent on three of these four records states
    no type, so they do not match -- and a record a filter returns never
    contradicts that filter. Making it read the publisher would move 1,680
    datasets in the registry."""
    assert fohm.publisher("fohm").type == "national_authority"
    found = fohm.search(publisher_type="national_authority")
    assert found.total == 1
    assert all(r.publisher.type == "national_authority" for r in found)


def test_the_resolver_is_built_from_pairs_and_knows_every_uri():
    """What the live backend feeds it: one pair per agent, with the facet's count."""
    from dataportalen.client import _publisher_dict, _Publishers

    def agent(uri, name):
        return _publisher_dict({"uri": uri, "name": {"sv": name}})

    index = _Publishers([
        (agent("https://example.org/agents/1", "Exempelverket"), 9),
        (agent("http://example.org/agents/1", "Exempelverket"), 1),
        (agent("https://example.org/agents/2", "Andra verket"), 4),
        (agent(None, None), 3),
    ])
    assert index.uris == {
        "exempelverket": ["http://example.org/agents/1", "https://example.org/agents/1"],
        "andra_verket": ["https://example.org/agents/2"]}
    # The agent on most records is the one the publisher is described by.
    assert index.entities["exempelverket"]["uri"] == "https://example.org/agents/1"
    assert index.resolve("http://example.org/agents/1") == "exempelverket"
    assert index.resolve("Andra verket") == "andra_verket"
