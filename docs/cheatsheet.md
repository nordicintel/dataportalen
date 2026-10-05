# Cheat sheet

Every public name in `dataportalen`, once, with every argument spelled out and
every output shape printed underneath. One Python snippet per call; the JSON
its `to_dict()` gives under it, where the shape has not been shown yet.

This is the page to keep open while you write code. For how to use the package
in the order you meet it, read [guide.md](guide.md); for every filter, model
field and error as tables, with fill rates, see [reference.md](reference.md).
Code written against 0.12 has a line-by-line migration table in
[CHANGELOG.md](../CHANGELOG.md).

Written against 0.13.0 and the registry as of 2026-10-01 (23,582 datasets, 599
data services, 35,148 distributions); the default catalogue holds 23,422 of
those datasets. Counts measured 2026-10-05. They move as the registry does.

```python
from dataportalen import (
    Catalog, default_catalog_path, read_catalog,
    Dataset, DataService, Distribution, Publisher, SearchResult,
    MultilingualText, Keywords, License, Contact, Temporal, LinkMark,
    Facets, Facet, FacetValue,
    DataportalError, TransportError, TimeoutError, HTTPError, NotFoundError,
    RateLimitError, ServerError, ParseError, QueryError, DataportalWarning,
    logger, enable_logging, __version__,
)
```

## `Catalog(...)`

What the catalogue holds, and when it may download, is decided here. It holds
every dataset at every access level, minus the distributions the registry
found dead. Nothing is ever downloaded behind a search.

```python
catalog = Catalog(
    database=None,              # str | None -> default_catalog_path()
    max_age=7,                  # int days | None. Older than this, or missing,
                                #   -> brought up to date when built (a download
                                #   if there is nothing, else an incremental
                                #   refresh of what changed, under a minute).
                                #   None: use what is there; download only if
                                #   there is nothing.
    rebuild=False,              # True: fetch everything again now. What a
                                #   schema change asks for, and the only thing
                                #   that drops a dataset the registry withdrew.
    exclude_broken=True,        # drop every dead distribution -- one the
                                #   registry's nightly check got an HTTP error
                                #   for, or whose host is not in DNS (903 of
                                #   35,148) -- and every dataset left with none
                                #   (160). One its checker could not reach, or
                                #   was rate-limited on (10,858), is not dead:
                                #   it stays, marked `unverified`.
                                #   False: keep all, mark the dead `broken`.
    language="sv",              # "sv" | "en": what MultilingualText.text()
                                #   picks first, falling back to the other.
    live=False,                 # True: no database; every call asks the
                                #   registry. Warns when built. See Live.
)
```

Building one reads the database: about 2.4 s for 23,422 datasets. After that
a filtered search takes hundredths of a second.

There is no `access_rights=` argument any more: the catalogue holds every
access level, and `access_rights` is a filter on each call.

## `catalog.search(...)`

Finds datasets and counts what every match is made of. Filters AND together; a
list is OR within one filter. An unknown filter name, an unknown value, or a
negative window raises `QueryError` rather than matching nothing.

```python
result = catalog.search(
    "cykel",                      # query: str | None. A phrase: a case-insensitive
                                  #   substring of the title, description and
                                  #   keywords, both languages
    limit=50,                     # int | None (every match, the default) | 0 (counts only)
    offset=0,                     # int >= 0
    facet_limit=None,             # int | None; what it cuts lands in .facets.omitted
    live=False,                   # True: ask the registry for this one call
    as_dict=False,                # True: result.to_dict() instead of a SearchResult

    publisher="scb",              # id | alias | name | organisation number | URI
    publisher_type="national_authority",
        # national_authority | non_governmental_organisation | local_authority
        # | academia_scientific_organisation | company | regional_authority
        # | non_profit_organisation | private_individual               (8 values)
    theme="transport",
        # population_and_society | government_and_public_sector
        # | education_culture_and_sport | environment | health | regions_and_cities
        # | economy_and_finance | science_and_technology | transport
        # | justice_legal_system_and_public_safety | energy | transport_networks
        # | agriculture_fisheries_forestry_and_food | international_issues
        # | environmental_monitoring_facilities | administrative_units
        # | area_management_restriction_regulation_zones_and_reporting_units
        # | human_health_and_safety | utility_and_governmental_services | land_use
        # | sea_regions | bio_geographical_regions | geology | habitats_and_biotopes
        # | natural_risk_zones | population_distribution_demography | protected_sites
        # | meteorological_geographical_features | buildings | cadastral_parcels
        # | coordinate_reference_systems                              (31 values)
    keyword="Kommun",             # exact, case-insensitive; 21,105 values
    format="csv",
        # json | csv | html | microsoft_excel_xml | wms_service | rdf_xml | json_ld
        # | xml | rdf_as_turtle | n3 | parquet | iso_19139_xml | zip | geopackage
        # | wfs_service | pdf | vnd_google_earth_kml_xml | x_shapefile | text
        # | gml_xml | json_in_a_zip | microsoft_excel | png | atom_xml
        # | octet_stream | gpx | geojson | csv_in_a_zip | wmts_service | vnd_sqlite3
        # | xml_in_a_zip | tiff | shapefile | geo_jsonld | opendocument_spreadsheet
        # | shape | dwg | raster | raster_pdf | svg_xml | tif | vnd_dwg
        # | vnd_openxmlformats_officedocument_wordprocessingml_document
        # | wcs_service | x_tab                                       (45 values)
    kind="pxweb",                 # what a distribution is; any one matches
        # pxweb | kolada | doi | file | geodata | web_page | rowstore | huwise
        # | unknown | api | ckan                                      (11 values)
    access_rights="public",       # public | non_public | restricted
                                  #   | none (the 4,137 that set nothing)
    accrual_periodicity="annual",
        # annual | monthly | continuous | other | quarterly | irregular | unknown
        # | weekly | daily | semiannual | every_two_weeks | continuously_updated
        # | never | biennial | as_needed | triennial | every_two_months
        # | three_times_a_year                                        (18 values)
    modified_after="2025-01-01",  # "YYYY-MM-DD" | "YYYY-MM" | "YYYY" | date | datetime,
                                  #   inclusive. Reads modified, or issued for a
                                  #   dataset with no modified (651 of them)
)                                 # -> SearchResult
```

Some answers from the default catalogue, measured 2026-10-05:

```text
search(theme="transport", format="csv").total     57
search("cykel").total                             386
search("air quality").total                       15
search(keyword="Kommun").total                    4,617
search(modified_after="2025").total               12,708
search(kind="file").total                         2,479
```

With no `limit` the result holds every match: about 2.2 s for an unpaged
search over everything, most of it turning 23,422 datasets into models.

Filters that 0.12 had are refused by name, each with a `QueryError` saying what
to do instead:

```text
updated="annual"                -> accrual_periodicity="annual"
text="cykel"                    -> query="cykel"
license=, language=             -> not filters; every record still carries
                                   dataset.license.id and dataset.languages
issued_after=, issued_before=   -> modified_after, which falls back to issued
modified_before=                -> filter on dataset.modified in Python
```

## `SearchResult`

What `search()` returns: datasets, and the facets of every match.

```python
result.datasets                   # list[Dataset]: every match, or the window asked for
result.facets                     # Facets, counted over every match, not the window
result.total                      # int, exact: how many matched altogether
result.offset                     # int, what you asked for
result.limit                      # int | None, what you asked for
result.has_more                   # bool: offset + len(result.datasets) < total
len(result); list(result)         # len and iteration are over .datasets
result.to_dict()                  # below
```

From `catalog.search(theme="transport", format="csv", limit=2, facet_limit=2)`,
cut short:

```json
{
    "total": 57,
    "offset": 0,
    "limit": 2,
    "datasets": [
        { "uri": "https://metadata.lidingo.se/store/5/resource/101", "...": "a Dataset, below" },
        { "uri": "https://metadata.lidingo.se/store/9/resource/2", "...": "a Dataset, below" }
    ],
    "facets": {
        "publisher": [
            { "value": "transportstyrelsen", "count": 12, "label": { "sv": "Transportstyrelsen" } },
            { "value": "umea_kommun", "count": 12, "label": { "sv": "Umeå kommun" } }
        ],
        "kind": [
            { "value": "file", "count": 40, "label": {} },
            { "value": "rowstore", "count": 18, "label": {} }
        ],
        "access_rights": [
            { "value": "public", "count": 44, "label": { "en": "Public", "sv": "Publik" } }
        ]
    },
    "facets_omitted": {
        "publisher": 16,
        "publisher_type": 2,
        "theme": 11,
        "keyword": 219,
        "format": 15,
        "kind": 4,
        "accrual_periodicity": 5
    }
}
```

`facets` has all eight filters; three are shown. `has_more` is `true` here and
`len(result)` is 2.

## `catalog.datasets(...)`

Every dataset that matches: a plain, complete list. The filters of `search()`,
with no `query`, no window and no facets. `limit`, `offset`, `facet_limit` or
`query` raise `QueryError` pointing at `search()`.

```python
datasets = catalog.datasets(
    live=False,                   # True: ask the registry for this one call
    as_dict=False,                # True: a list of dicts

    publisher="scb",              # every value as for search()
    publisher_type="national_authority",
    theme="economy_and_finance",
    keyword="befolkning",
    format="csv",
    kind="pxweb",
    access_rights="public",
    accrual_periodicity="annual",
    modified_after="2025-01-01",
)                                 # -> list[Dataset]
```

`catalog.datasets(publisher="scb", kind="pxweb")` is 4,284 datasets. With no
filter at all it is every dataset the catalogue holds.

## `catalog.data_services(...)`

Every data service — an API rather than a file — that matches. Six filters, not
nine: `format`, `kind`, `accrual_periodicity` and `modified_after` are refused
with `QueryError` naming the ones that work, because a data service has no
distributions, no `accrual_periodicity`, and `modified` on 7.5% of the 599.
The registry's link check never tests `endpointURL`, so whether an API answers
is not something this can tell you; `exclude_broken` leaves data services
alone.

```python
services = catalog.data_services(
    live=False,
    as_dict=False,

    publisher="skolverket",
    publisher_type="national_authority",   # also: non_governmental_organisation | company
    service_type="rest",
        # rest | view_service | download_service | discovery_service
        # | transformation_service
    theme="education_culture_and_sport",
    keyword="grundskola",
    access_rights="public",
)                                 # -> list[DataService]
```

```json
{
    "uri": "https://editera.dataportal.se/store/162/resource/13",
    "context_id": "124",
    "entry_id": "2971",
    "type": "data_service",
    "title": { "sv": "Skolenhetsregistret", "en": "School unit registry" },
    "description": {
        "en": "The school unit register contains ...",
        "sv": "Skolenhetsregistret innehåller ..."
    },
    "keywords": { "sv": ["grundskola", "skolor", "..."], "en": ["school units", "..."] },
    "service_type": "rest",
    "endpoint_url": "https://api.skolverket.se/skolenhetsregistret/swagger-ui/index.html",
    "endpoint_description": null,
    "serves_datasets": ["https://editera.dataportal.se/store/162/resource/26"],
    "conforms_to": [],
    "publisher": {
        "id": "skolverket",
        "uri": "http://dataportal.se/organisation/SE2021004185",
        "name": { "sv": "Skolverket" },
        "alias": null,
        "type": "national_authority",
        "homepage": "https://www.skolverket.se/",
        "email": "support.oppnadata@skolverket.se",
        "identifiers": ["2021004185"],
        "dataset_count": null,
        "data_service_count": null,
        "facets": null
    },
    "themes": ["education_culture_and_sport"],
    "license": {
        "id": "cc0_1_0",
        "label": { "en": "CC0 1.0 (Public Domain Dedication, No Copyright)" },
        "uri": "http://creativecommons.org/publicdomain/zero/1.0/"
    },
    "access_rights": "public",
    "landing_page": "https://www.skolverket.se/om-oss/oppna-data/api-for-skolenhetsregistret",
    "contact_points": [
        {
            "uri": "https://editera.dataportal.se/store/162/resource/11",
            "name": "Centralsupport Skolverket",
            "email": "supporten@skolverket.se"
        }
    ],
    "stale": null
}
```

There are no data-service facets: `data_services()` is a list, and `search()`
and `facets()` count datasets only.

## `catalog.facets(...)`

You filter with a value; a facet tells you which values exist. This is every
dataset facet before you search: exactly `catalog.search(limit=0).facets`,
counted over every dataset the catalogue holds.

```python
facets = catalog.facets(
    limit=None,                   # int | None, caps each facet
    as_dict=False,                # True: facets.to_dict()
)                                 # -> Facets

facets["theme"]                   # Facet: a list of FacetValue, biggest first
facets["theme"][0].value          # 'population_and_society'  <- feed back into search()
facets["theme"][0].count          # 6434
facets["theme"][0].label          # MultilingualText {"en": "Population and society", "sv": "Befolkning och samhälle"}
facets["theme"][0].label.text()   # 'Befolkning och samhälle'
value, count = facets["theme"][0] # FacetValue is still a 2-tuple
facets["theme"].omitted           # int, values cut by limit
facets["theme"].to_dict()         # [{"value", "count", "label"}, ...]
facets.omitted                    # {filter: cut}, only the non-zero ones
facets.top("kind")                # FacetValue('pxweb', 6704) | None
facets.counts()                   # {filter: {value: count}}, the compact form
facets.to_dict()                  # {filter: [{"value", "count", "label"}, ...]}
facets["license"]                 # QueryError: no such facet, and the ones there are
list(facets); "format" in facets  # the eight dataset filters, in order
```

```json
{
    "value": "population_and_society",
    "count": 6434,
    "label": {
        "en": "Population and society",
        "sv": "Befolkning och samhälle"
    }
}
```

How many values each facet has in the default catalogue:

```json
{
    "publisher": 345,
    "publisher_type": 8,
    "theme": 31,
    "keyword": 21105,
    "format": 45,
    "kind": 11,
    "access_rights": 3,
    "accrual_periodicity": 18
}
```

`label` comes from the vocabulary for a controlled value and from the records
for a publisher; it is empty for a value that is its own label, such as a
keyword or a kind. A `keyword` value is shown as the publisher spelt it — the
commonest spelling of each — and matched case-insensitively, so `Kommun`,
`kommun` and `KOMMUN` are one keyword and all three find the same 4,617
datasets. Counts are per dataset: three CSV files in one dataset count once
under `format` → `csv`.

## `catalog.publishers(...)`

Every publisher with something that matches, most datasets first. A plain,
complete list. `id` is what `publisher=` takes and what the `publisher` facet
reports.

```python
publishers = catalog.publishers(
    live=False,
    as_dict=False,

    publisher=["scb", "smhi"],    # the filters datasets and data services share
    publisher_type="local_authority",
    theme="transport",
    keyword="Kommun",
    access_rights="public",
)                                 # -> list[Publisher]
```

```json
{
    "id": "radet_for_framjande_av_kommunala_analyser_kolada",
    "uri": "http://dataportal.se/organisation/SE2220000315",
    "name": {
        "sv": "Rådet för främjande av kommunala analyser - Kolada",
        "en": "The Council for Advocacy of Municipal Analysis - Kolada"
    },
    "alias": "kolada",
    "type": "non_governmental_organisation",
    "homepage": "https://rka.nu/",
    "email": "rka@rka.nu",
    "identifiers": [],
    "dataset_count": 5863,
    "data_service_count": 1,
    "facets": null
}
```

The counts are what matched. With no filter: every dataset and data service
the catalogue holds, 346 publishers (356 with `exclude_broken=False`). With
filters: what those select, so a publisher with nothing that matches is not
listed — `publishers(publisher_type="local_authority")` is 73,
`publishers(access_rights="public")` 287. Either way a row's `dataset_count`
is the `total` of `catalog.search(limit=0)` with the same filters, narrowed to
that publisher.

## `catalog.publisher(...)`

One publisher, with what it publishes. `None` if it has nothing in this
catalogue; a value nobody knows raises `QueryError` with suggestions.

```python
publisher = catalog.publisher(
    "skolverket",                 # id | alias | name (either language)
                                  # | organisation number | URI -- the same
                                  # resolver publisher= uses
    as_dict=False,                # True: publisher.to_dict()
)                                 # -> Publisher | None
```

```json
{
    "id": "skolverket",
    "uri": "http://dataportal.se/organisation/SE2021004185",
    "name": { "sv": "Skolverket" },
    "alias": null,
    "type": "national_authority",
    "homepage": "https://www.skolverket.se/",
    "email": "support.oppnadata@skolverket.se",
    "identifiers": ["2021004185"],
    "dataset_count": 6,
    "data_service_count": 5,
    "facets": {
        "theme": [
            {
                "value": "education_culture_and_sport",
                "count": 5,
                "label": { "en": "Education, culture and sport", "sv": "Utbildning, kultur och sport" }
            },
            {
                "value": "utility_and_governmental_services",
                "count": 1,
                "label": { "en": "Utility and governmental services" }
            }
        ],
        "format": [
            { "value": "json", "count": 5, "label": { "en": "JSON (.json)", "sv": "JSON (.json)" } },
            { "value": "html", "count": 1, "label": { "en": "HTML" } },
            { "value": "iso_19139_xml", "count": 1, "label": { "en": "ISO 19139 XML" } },
            { "value": "xml", "count": 1, "label": { "en": "XML (.xml)", "sv": "XML (.xml)" } },
            { "value": "zip", "count": 1, "label": { "en": "ZIP" } }
        ],
        "kind": [
            { "value": "file", "count": 5, "label": {} },
            { "value": "pxweb", "count": 1, "label": {} },
            { "value": "web_page", "count": 1, "label": {} }
        ],
        "access_rights": [
            { "value": "public", "count": 6, "label": { "en": "Public", "sv": "Publik" } }
        ],
        "accrual_periodicity": [
            { "value": "semiannual", "count": 1, "label": { "en": "semiannual", "sv": "halvårsvis" } }
        ]
    }
}
```

The row from `publishers()` plus `facets`, which are exactly
`catalog.search(publisher=id, limit=0).facets` less `publisher`,
`publisher_type` and `keyword` — so every value in them can be fed back in
beside `publisher=`.

## `catalog.get(...)`

```python
record = catalog.get(
    "https://catalog.lakemedelsverket.se/store/1/resource/45",
    format="dict",                # "dict" (local, immediate) | "turtle" | "ttl"
                                  # | "rdf/xml" | "rdfxml" | "xml" | "n-triples"
                                  # | "ntriples" | "nt" | "json-ld" | "jsonld"
                                  # | "trig" | any media type
    as_dict=False,                # True, with format="dict": record.to_dict()
)
```

```text
"dict"    -> the Dataset or DataService, or None if this catalogue does not hold it
otherwise -> str of RDF, fetched from the registry; the only request a local
             Catalog makes outside a download. None if the URI is not held.
```

A dataset `exclude_broken` dropped is not held:
`catalog.get("https://editera.dataportal.se/store/163/resource/7")` is `None`
by default, because its only distribution is dead. Where two records share a
URI — the same dataset harvested into two catalogues — the first is returned.

## `catalog.info()`

```python
catalog.info()                    # -> dict
```

```json
{
    "database": "/home/you/.cache/dataportalen/catalog.sqlite",
    "live": false,
    "first_retrieved": "2026-09-30T17:57:14",
    "last_refreshed": "2026-10-01T13:04:54",
    "downloaded": "2026-10-01T13:04:54",
    "age_days": 4,
    "bytes": 98951168,
    "datasets": 23422,
    "data_services": 599,
    "publishers": 346,
    "excluded": {
        "dead_distributions": 903,
        "dead_datasets": 160
    },
    "stale_datasets": 81,
    "unverified_distributions": 10858,
    "sources": {
        "total": 666,
        "succeeded": 239,
        "failed": 427,
        "failed_holding_records": [...]
    }
}
```

`datasets` is what this `Catalog` holds after `exclude_broken`; the database
underneath holds all 23,582. `excluded` is what `exclude_broken` left out when
the object was built: dead distributions, and the datasets that had nothing
but dead ones. `unverified_distributions` and `stale_datasets` count what is
held and marked.

`sources` is the registry's latest harvest of each source catalogue it
harvests, read at the end of every download or refresh, so it is as old as
the copy; `None` for a database written before 0.12. 666 sources, 427 of them
failed (2026-10-04) — but 419 of those are registrations that never yielded a
dataset. The other 8 are listed in `failed_holding_records`; they held 81
datasets unscoped, and each of those carries `stale`. A row:

```text
context_id          str, what dataset.context_id holds
status              "failed"
harvested           "2026-10-04T02:23:44" | None, the latest harvest
title               str | None
dataset_count       int, what this catalogue holds from it
data_service_count  int
```

## `Catalog` properties and lifecycle

```python
catalog.database                  # str; None when live
catalog.max_age                   # int | None
catalog.exclude_broken            # bool
catalog.language                  # "sv" | "en"
catalog.live                      # bool
catalog.first_retrieved           # "2026-09-30T17:57:14" | None, when built
catalog.last_refreshed            # "2026-10-01T13:04:54" | None, what a refresh asks from
catalog.downloaded                # datetime | None
catalog.age_days                  # int >= 0 | None

len(catalog)                      # datasets only: 23422
for dataset in catalog: ...       # datasets only, as Dataset models
catalog.close()                   # releases the HTTP connection; never required
with Catalog() as catalog: ...    # same thing
```

## Live

The same calls asked of the registry itself: no database, nothing downloaded,
every call a request. The records are built by the same code and the models
are the same — `kind` included, less link health and `stale`, which the
registry cannot filter on. About 0.1–0.3 s for a count, a few seconds for a
page of 100 full records.

```python
live = Catalog(live=True)         # DataportalWarning when built. Takes language=;
                                  #   database= or rebuild= raises QueryError

live.search(
    "cykel",                      # the registry's full-text phrase search, not
                                  #   the local substring match: 251 vs 388
    limit=50,                     # any limit; 100 to a page, fetched in turn.
                                  #   None = every page, warning above 1,000
    offset=0,
    facet_limit=None,
    publisher="trafikverket",     # every filter the local search takes, except:
    theme="transport",            #   publisher_type -> QueryError (not indexed)
                                  #   kind -> QueryError (worked out after the
                                  #   fetch; the records still carry it)
    keyword="kommun",             # case-insensitive, as locally; an unknown
                                  #   keyword raises, without suggestions
    modified_after="2025",
)                                 # -> SearchResult, facets without keyword,
                                  #    publisher_type and kind
live.datasets(theme="transport")  # every page
live.data_services(service_type="rest")
live.facets(limit=None)           # = search(limit=0).facets, one request
live.publishers(publisher_type="local_authority")
                                  # ~20 requests the first time, then two a call;
                                  #   publisher_type works here, read off each
                                  #   publisher
live.publisher("scb")             # row + facets: theme, format, access_rights,
                                  #   accrual_periodicity (no kind)
live.get("https://catalog.lakemedelsverket.se/store/1/resource/45",
         format="dict")           # 4 requests; "turtle" etc. as locally
live.info()                       # three counts, below
len(live)                         # one count request
for dataset in live: ...          # every page: 236 of them, with a warning
live.close()                      # with Catalog(live=True) as live: ... too
```

```json
{ "datasets": 23582, "data_services": 599, "publishers": 356, "live": true, "sources": null }
```

That was the registry on 2026-10-01: everything, nothing excluded, so it
matches `Catalog(exclude_broken=False)` rather than `Catalog()`. `database`,
`first_retrieved`, `last_refreshed`, `downloaded` and `age_days` are `None`.

One call against a local catalogue can go live instead. `search()`,
`datasets()`, `data_services()` and `publishers()` take `live=True`; on a
`Catalog(live=True)` it changes nothing and warns.

```python
catalog.search("cykel", limit=10, live=True)
catalog.datasets(publisher="trafikverket", live=True)
catalog.data_services(service_type="rest", live=True)
catalog.publishers(theme="transport", live=True)
```

An unpaged live call fetches every page, one after another: 100 records a page
and a few seconds each. Above 1,000 records it warns `DataportalWarning`
before it starts, with an estimate; everything is 236 pages, about half an
hour by that estimate. Live results come in URI order; local ones in the
database's.

How its answers compare with `Catalog(exclude_broken=False)`, measured over
every facet value of every filter on 2026-10-01:

```text
datasets
  publisher, total            equal (356 of 356 values)
  theme, access_rights        equal for most; a few more live, from nested nodes
                                and second values: theme 3 of 31 values (+1..+3),
                                restricted 247 vs 244
  accrual_periodicity         9 of 20 values more live (continuous 650 vs 634);
                                quadrennial and decennial exist only live
  format                      the registry's own index: microsoft_excel 63 vs 73,
                                zip facet 292 vs filter 291 (two spellings added)
  keyword                     equal for 199 of 200 sampled (Badplatser 20 vs 17)
  modified_after              a few more live: the index holds a date from
                                every node of a record's graph
  query                       a different engine: "cykel" 251 vs 388,
                                "air quality" 15 vs 15
  publisher_type, kind        refused
data services
  every filter                equal (publisher 31, service_type 5, theme 12,
                                access_rights 3 values); publisher_type refused
records                       identical, less broken/unverified/stale (50 of 50 compared)
publishers()                  identical, 356 of 356 rows
```

## `Dataset`

Read by attribute; `dataset["title"]` is a `TypeError`. Every field is always
there: `None`, `[]` or an empty text where the publisher said nothing.

```python
dataset = catalog.get("https://catalog.lakemedelsverket.se/store/1/resource/45")

dataset.uri                       # str | None
dataset.context_id                # str, the registry's catalogue it is kept in
dataset.entry_id                  # str, its entry there
dataset.type                      # "dataset"
dataset.title                     # MultilingualText
dataset.description               # MultilingualText
dataset.keywords                  # Keywords
dataset.identifier                # str | None
dataset.landing_page              # str | None
dataset.publisher                 # Publisher; counts and facets None
dataset.themes                    # list[str], the theme filter's values
dataset.license                   # License | None
dataset.access_rights             # "public" | "non_public" | "restricted" | None
dataset.accrual_periodicity       # str | None, the filter's values
dataset.languages                 # list[str]: ISO 639-1 where one exists, else
                                  #   639-3: sv | en | fi | de | no | ... | fit | swl
dataset.spatial                   # list[str]: kingdom_of_sweden, a municipality, ...
dataset.temporal                  # Temporal | None
dataset.issued                    # "2022-10-20" | None, as the publisher wrote it
dataset.modified                  # str | None
dataset.contact_points            # list[Contact]
dataset.distributions             # list[Distribution]
dataset.stale                     # LinkMark | None
```

```json
{
    "uri": "https://catalog.lakemedelsverket.se/store/1/resource/45",
    "context_id": "140",
    "entry_id": "5467",
    "type": "dataset",
    "title": {
        "sv": "Färdig sökning med alla läkemedel",
        "en": "A Completed Search With all Medicines"
    },
    "description": {
        "sv": "En lista med alla läkemedel i Läkemedelsverkets sökfunktion ...",
        "en": "A list of all medicines in the Medical Products Agency's search function ..."
    },
    "keywords": { "sv": ["läkemedel"], "en": ["medicines"] },
    "identifier": null,
    "landing_page": null,
    "publisher": {
        "id": "lakemedelsverket",
        "uri": "http://dataportal.se/organisation/SE2021004078",
        "name": { "sv": "Läkemedelsverket" },
        "alias": null,
        "type": "national_authority",
        "homepage": "https://www.lakemedelsverket.se",
        "email": "Oppnadata@lakemedelsverket.se",
        "identifiers": ["2021004078"],
        "dataset_count": null,
        "data_service_count": null,
        "facets": null
    },
    "themes": ["health"],
    "license": {
        "id": "cc_by_4_0",
        "label": { "en": "CC BY 4.0 (Attribution)" },
        "uri": "http://creativecommons.org/licenses/by/4.0/"
    },
    "access_rights": "public",
    "accrual_periodicity": "daily",
    "languages": [],
    "spatial": ["kingdom_of_sweden"],
    "temporal": null,
    "issued": null,
    "modified": null,
    "contact_points": [
        {
            "uri": "https://catalog.lakemedelsverket.se/store/1/resource/22",
            "name": "Registrator",
            "email": "registrator@lakemedelsverket.se"
        }
    ],
    "distributions": [
        {
            "uri": "https://catalog.lakemedelsverket.se/store/1/resource/48",
            "title": {
                "sv": "Färdig sökning med alla läkemedel",
                "en": "A Completed Search With all Medicines"
            },
            "description": { "sv": "En lista med alla läkemedel ...", "en": "A list of all medicines ..." },
            "access_url": "https://www.lakemedelsverket.se/sv/sok-lakemedelsfakta?activeTab=3",
            "download_url": "https://www.lakemedelsverket.se/globalassets/excel/Lakemedelsprodukter.xlsx",
            "format": "microsoft_excel_xml",
            "license": {
                "id": "cc_by_4_0",
                "label": { "en": "CC BY 4.0 (Attribution)" },
                "uri": "http://creativecommons.org/licenses/by/4.0/"
            },
            "status": "completed",
            "availability": "stable",
            "languages": ["sv"],
            "issued": null,
            "modified": null,
            "access_service_uris": [],
            "kind": "file",
            "broken": null,
            "unverified": null,
            "byte_size": 5420000
        }
    ],
    "stale": null
}
```

`stale` is set when the source catalogue the record was harvested from failed
its latest harvest. The record is what the last good harvest left; it is
marked, never removed. A data service carries it the same way.

```json
{
    "stale": { "reason": "harvest failed", "checked": "2026-10-04T02:46:11", "by": null }
}
```

## `Distribution`

One distribution of a dataset: a file, an API or a web page.

```python
dist = dataset.distributions[0]

dist.uri                          # str | None
dist.title                        # MultilingualText
dist.description                  # MultilingualText
dist.access_url                   # str | None
dist.download_url                 # str | None
dist.format                       # str | None, the format filter's values
dist.license                      # License | None
dist.status                       # "completed" | ... | None
dist.availability                 # "stable" | ... | None
dist.languages                    # list[str]
dist.issued                       # str | None
dist.modified                     # str | None
dist.access_service_uris          # list[str], the data services it is reached through
dist.kind                         # str, always set: what it is, below
dist.broken                       # LinkMark | None: dead
dist.unverified                   # LinkMark | None: the checker could not reach it
dist.byte_size                    # int | None
```

Three fields are `None` unless they have something to say:

```json
{
    "broken": { "reason": "Not Found", "checked": "2026-10-01T02:53:02", "by": null },
    "unverified": { "reason": "timeout", "checked": "2026-10-01T02:52:59", "by": null },
    "byte_size": 5420000
}
```

`broken` is a dead distribution — the registry got an HTTP error for it, or
found no such host — and shows only in a `Catalog(exclude_broken=False)`;
under the default that distribution is not there, and a dataset with nothing
else is not there either. `unverified` is a distribution the registry's
checker could not reach at all, which says nothing about the distribution; it
is never removed. A distribution never carries both. `byte_size` is on the 479
distributions (1.4%) whose publisher states a size. `by` is `"local"` on a
verdict asked from this machine rather than taken from the registry, which
only the package's own maintenance does.

`kind` is what the distribution is, read from its metadata with no request:

```text
pxweb     a PxWeb statistical table                              6,704
kolada    a Kolada key figure                                    5,952
doi       a DOI, resolving to a research-data landing page       5,021
file      a file to download: CSV, Excel, JSON, PDF, a ZIP       2,479
geodata   a map service or a geo file                            1,098
web_page  a web page; the data is behind it, not at it           1,053
rowstore  EntryScape's rowstore: a table behind an API             460
huwise    a Huwise (Opendatasoft) dataset: records and exports     334
unknown   the metadata does not say                                297
api       an API described by a data service                       143
ckan      a CKAN resource or its API                                80
```

Datasets per kind in the default catalogue: the `kind` facet. A dataset with a
PxWeb table and a CSV file counts under both. A `download_url` whose format is
`html` and whose address has no file extension is a `web_page`, not a `file`.

## `DataService`

A data service: an API rather than a file. No distributions; the JSON is under
[`catalog.data_services(...)`](#catalogdata_services).

```python
service = catalog.get("https://editera.dataportal.se/store/162/resource/13")

service.uri                       # str | None
service.context_id                # str
service.entry_id                  # str
service.type                      # "data_service"
service.title                     # MultilingualText
service.description               # MultilingualText
service.keywords                  # Keywords
service.service_type              # "rest" | "view_service" | ... | None
service.endpoint_url              # str | None, never link-checked
service.endpoint_description      # str | None
service.serves_datasets           # list[str], dataset URIs
service.conforms_to               # list[str]
service.publisher                 # Publisher
service.themes                    # list[str]
service.license                   # License | None
service.access_rights             # str | None
service.landing_page              # str | None
service.contact_points            # list[Contact]
service.stale                     # LinkMark | None
```

## `Publisher`

An authority, a municipality, a company, a university. The JSON is under
[`catalog.publishers(...)`](#catalogpublishers) and
[`catalog.publisher(...)`](#catalogpublisher).

```python
publisher = dataset.publisher

publisher.id                      # str | None, what publisher= takes
publisher.uri                     # str | None
publisher.name                    # MultilingualText
publisher.alias                   # "scb" | None: the one short name, if it has one
publisher.type                    # str | None, the publisher_type filter's values
publisher.homepage                # str | None
publisher.email                   # str | None
publisher.identifiers             # list[str], organisation numbers
publisher.dataset_count           # int | None
publisher.data_service_count      # int | None
publisher.facets                  # Facets | None
```

The counts are filled on what `publishers()` and `publisher()` return, and
`facets` on what `publisher()` returns; a publisher nested in a dataset or
data service leaves all three `None`. A record that names no publisher (24 in the
default catalogue) still has one, with every field `None`.

## `MultilingualText`

Text as the publisher wrote it: Swedish, English or both. 53% of datasets are
in Swedish only, 36% carry both, 10% are in English only, so `title["sv"]` is
not safe to write and `title.text()` is.

```python
title = dataset.title

title.text()                      # the catalogue's language, else the other,
                                  #   else whatever there is; None if empty
title.text(
    lang="en",                    # "sv" | "en" | None (the catalogue's)
)                                 # -> 'A Completed Search With all Medicines'
title["sv"]                       # exactly one language; KeyError if absent
"en" in title; len(title); dict(title)   # a read-only Mapping
title == {"sv": "Färdig sökning med alla läkemedel",
          "en": "A Completed Search With all Medicines"}   # True
title.to_dict()                   # a new plain dict
```

```text
{"sv": "Vägtrafiknät", "en": "Road traffic network"}  .text()  -> 'Vägtrafiknät'
{"en": "Road traffic network"}                        .text()  -> 'Road traffic network'
{}                                                    .text()  -> None
Catalog(language="en"), the first                     .text()  -> 'Road traffic network'
```

Titles, descriptions, publisher names, licence labels and facet labels are all
`MultilingualText`.

## `Keywords`

Keywords per language; empty when there are none.

```python
keywords = dataset.keywords

keywords.list()                   # the catalogue's language, else the other
keywords.list(
    lang="en",                    # "sv" | "en" | None (the catalogue's)
)                                 # -> ['medicines']
keywords.all()                    # both languages, each once, in order:
                                  #   ['läkemedel', 'medicines']
keywords["sv"]                    # ['läkemedel']; KeyError if absent
keywords.to_dict()                # {"sv": [...], "en": [...]}
```

## `License`, `Contact`, `Temporal`, `LinkMark`

```python
dataset.license.id                # "cc_by_4_0"
    # cc0_1_0 | nolicense | cc_by_4_0 | otherlicense | cc_by_nc_4_0
    # | cc_by_nc_sa_4_0 | cc_by_sa_4_0 | cc_by_nc_nd_4_0 | cc_by_nd_4_0
dataset.license.label             # MultilingualText {"en": "CC BY 4.0 (Attribution)"}
dataset.license.uri               # "http://creativecommons.org/licenses/by/4.0/"

contact = dataset.contact_points[0]
contact.uri; contact.name; contact.email      # str | None each

dataset.temporal                  # Temporal | None: None when no period is given
dataset.temporal.start            # "2021-01-01" | None
dataset.temporal.end              # "2021-12-31" | None

mark = dist.unverified            # or dist.broken, or dataset.stale
mark.reason                       # "timeout", "Not Found", "harvest failed", ...
mark.checked                      # "2026-10-01T02:52:59" | None
mark.by                           # "local" | None
```

Licence and language are not filters; filter on them in Python:

```python
[d for d in catalog.datasets() if d.license and d.license.id == "cc0_1_0"]
[d for d in catalog.datasets() if "en" in d.languages]
```

## `to_dict()`, `as_dict=True` and printing

```python
dataset.to_dict()                 # every field, in order, nested models as dicts
str(dataset)                      # json.dumps(dataset.to_dict(), indent=4,
                                  #   ensure_ascii=False)
print(dataset)                    # prints that JSON
repr(dataset)                     # <Dataset 'Färdig sökning med alla läkemedel' https://...>
```

The same holds for every model: `Dataset`, `DataService`, `Distribution`,
`Publisher`, `SearchResult`, `License`, `Contact`, `Temporal`, `LinkMark`,
`MultilingualText`, `Keywords`, `Facets`, `Facet` and `FacetValue`.

`as_dict=True` on a call is `to_dict()` of what it would have returned:

```text
search(..., as_dict=True)          {total, offset, limit, datasets, facets, facets_omitted}
datasets(..., as_dict=True)        [dataset dict, ...]
data_services(..., as_dict=True)   [data service dict, ...]
publishers(..., as_dict=True)      [publisher dict, ...]
publisher(x, as_dict=True)         publisher dict, facets included | None
facets(as_dict=True)               {filter: [{value, count, label}, ...]}
get(uri, as_dict=True)             dataset or data service dict | None
```

In a dict every field is present: `broken`, `unverified`, `byte_size` and
`stale` are `null` when unset, and a nested publisher has `dataset_count`,
`data_service_count` and `facets` set to `null`.

## `default_catalog_path()`

```python
default_catalog_path()            # -> str
```

```text
Windows -> %LOCALAPPDATA%\dataportalen\catalog.sqlite
else    -> $XDG_CACHE_HOME/dataportalen/catalog.sqlite
           or ~/.cache/dataportalen/catalog.sqlite
```

## `read_catalog(...)`

Every record in a database file without building a `Catalog` around it — both
types, in insertion order, as plain dicts, unscoped: every distribution, the
dead ones carrying `broken`. Not models. A path that does not exist raises
`FileNotFoundError`.

```python
records = read_catalog(
    default_catalog_path(),       # str: the database file
)                                 # -> list[dict]
```

A record is the dataset or data-service dict above with two differences:
`broken`, `unverified`, `byte_size` and `stale` are present only when set, and
the nested `publisher` has no `dataset_count`, `data_service_count` or
`facets`. About 2 s for the 24,181 records.

## `enable_logging(...)`, `logger` and `__version__`

```python
import logging, sys

enable_logging(
    level=logging.INFO,           # int | "debug" | "info" | "warning" | "error"
    stream=sys.stderr,            # any file-like; None -> stderr
    fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)                                 # -> the "dataportalen" logger; a second call
                                  #    replaces its handler rather than adding one

logger.setLevel(logging.WARNING)  # the same logger, for apps that configure their own

__version__                       # "0.13.0"
```

## Publisher aliases

Short names for seven publishers with long ids, from the package's
`publishers.json`. An alias is accepted wherever a publisher is, and shows as
the publisher's `alias`.

```json
{
    "scb": "statistikmyndigheten_scb_statistiska_centralbyran",
    "fohm": "folkhalsomyndigheten",
    "slu": "sveriges_lantbruksuniversitet",
    "smhi": "sveriges_meteorologiska_och_hydrologiska_institut",
    "uhr": "universitets_och_hogskoleradet",
    "kolada": "radet_for_framjande_av_kommunala_analyser_kolada",
    "energimyndigheten": "statens_energimyndighet"
}
```

`publishers.json` has one entry per publisher id — its name, its URI and at
most one alias. Organisation numbers are not stored; they are read from a
`dataportal.se/organisation/SE...` URI. An alias that would collide with an id,
an organisation number or another alias is left out, with a logged warning.

## Errors and `DataportalWarning`

```text
DataportalError                   # base; catch this one
  TransportError                  #   the request never completed
    TimeoutError                  #     it took too long
  HTTPError                       #   the registry answered with a status
    NotFoundError                 #     404
    RateLimitError                #     429
    ServerError                   #     5xx
  ParseError                      #   the answer, or the database, was not what it
                                  #     claims: an empty file, an unknown schema
  QueryError                      #   the call was wrong: bad filter, value or window,
                                  #     a filter 0.12 had, a facet that is not there,
                                  #     or a Catalog argument

DataportalWarning                 # a UserWarning, never raised: Catalog(live=True),
                                  #   an unpaged live call above 1,000 records,
                                  #   live=True on a live Catalog

FileNotFoundError                 # read_catalog() on a path that is not there
```
