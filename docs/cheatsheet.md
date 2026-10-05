# Cheat sheet

Every public name in `dataportalen`, once, with every argument spelled out and
every output shape printed underneath. One Python snippet per function; a JSON
snippet under it when the shape has not been shown yet.

This is the page to keep open while you write code. For how to use the package
in the order you meet it, read [guide.md](guide.md); for every filter, record
key and error as tables, with fill rates, see [reference.md](reference.md).

Written against 0.12.0 and the registry as of 2026-10-01 (23,582 datasets, 599
data services, 35,148 distributions). The counts in the examples move as the
registry does.

```python
from dataportalen import (
    Catalog, LiveCatalog, default_catalog_path, read_catalog, text,
    Results, Facets, Facet, FacetValue,
    DatasetRecord, DataServiceRecord, DistributionRecord,
    PublisherRecord, Publisher, PublisherDetail, SourceRecord,
    LicenseRecord, ContactRecord, TemporalRecord, LinkMark,
    LanguageMap, KeywordMap,
    DataportalError, TransportError, TimeoutError, HTTPError, NotFoundError,
    RateLimitError, ServerError, ParseError, QueryError,
    logger, enable_logging, __version__,
)
```

## `Catalog(...)`

What the catalogue holds is decided here. By default it holds the datasets that
say `public`, minus the distributions the registry found dead — and nothing
that is downloaded is ever downloaded behind a search.

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
                                #   (127). One its checker could not reach, or
                                #   was rate-limited on (10,858), is not dead:
                                #   it stays, marked `unverified`, and
                                #   catalog.verify() can ask about it again.
                                #   False: keep all, mark the dead `broken`.
    access_rights=("public",),  # which access_rights values to hold:
                                #   "public" | "non_public" | "restricted"
                                #   | "none" (the 4,167 that set nothing).
                                #   A list, a single string, or None for all.
)
```

## `catalog.datasets(...)`

Filters AND together; a list is OR within one filter. An unknown filter name,
an unknown value, or a negative window raises `QueryError` rather than matching
nothing.

```python
page = catalog.datasets(
    query="cykel",                # a phrase: case-insensitive substring of the
                                  #   title, description and keywords, both languages
    limit=50,                     # int | None (every match) | 0 (counts only)
    offset=0,                     # int >= 0
    facet_limit=None,             # int | None; what it cuts lands in .facets.omitted

    publisher="scb",              # id | alias | name | organisation number | URI
    keyword="Kommun",             # exact, case-insensitive; 16,053 values
    publisher_type="national_authority",
        # national_authority | non_governmental_organisation | local_authority
        # | academia_scientific_organisation | company | regional_authority
        # | non_profit_organisation | private_individual
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
    format="csv",
        # json | csv | html | wms_service | microsoft_excel_xml | rdf_xml | json_ld
        # | xml | rdf_as_turtle | n3 | parquet | geopackage | iso_19139_xml
        # | wfs_service | zip | pdf | vnd_google_earth_kml_xml | x_shapefile
        # | gml_xml | text | microsoft_excel | png | wmts_service | json_in_a_zip
        # | atom_xml | geojson | octet_stream | gpx | csv_in_a_zip | vnd_sqlite3
        # | xml_in_a_zip | shapefile | tiff | geo_jsonld | opendocument_spreadsheet
        # | shape | tif | dwg | raster | raster_pdf | rdf_query | svg_xml | vnd_dwg
        # | vnd_openxmlformats_officedocument_wordprocessingml_document
        # | wcs_service | x_tab | zip_csv                             (47 values)
    kind="pxweb",                 # what a distribution is; any one matches
        # pxweb | kolada | doi | file | huwise | geodata | web_page | rowstore
        # | unknown | ckan | api                                      (11 values)
    license="cc0_1_0",            # the licence's id, see the record below
        # cc0_1_0 | nolicense | cc_by_4_0 | otherlicense | cc_by_nc_4_0
        # | cc_by_nc_sa_4_0 | cc_by_sa_4_0 | cc_by_nc_nd_4_0 | cc_by_nd_4_0
    access_rights="public",       # public | non_public | restricted
    updated="annual",
        # annual | monthly | continuous | other | quarterly | irregular | unknown
        # | weekly | daily | semiannual | every_two_weeks | continuously_updated
        # | never | biennial | as_needed | triennial | every_two_months
        # | three_times_a_year                                        (18 values)
    language="sv",                # ISO 639-1 where one exists, else 639-3:
                                  #   sv | en | fi | de | no | ... | fit | swl

    modified_after="2025-01-01",  # record["modified"]; "YYYY-MM-DD" | date | datetime
    modified_before="2026-01-01",
    issued_after="2020-01-01",    # record["issued"]
    issued_before="2026-01-01",
)
```

```json
{
    "uri": "https://editera.dataportal.se/store/163/resource/7",
    "context_id": "110",
    "entry_id": "2545",
    "type": "dataset",
    "title": {
        "sv": "Antagna och reserver ...",
        "en": "Admitted and reserves ..."
    },
    "description": { "sv": "Här redovisas ...", "en": "Here you can see ..." },
    "keywords": {
        "sv": ["statistik", "antagning"],
        "en": ["admission statistics"]
    },
    "identifier": null,
    "landing_page": "https://www.uhr.se/studier-och-antagning/antagningsstatistik/",
    "publisher": {
        "id": "universitets_och_hogskoleradet",
        "uri": "http://dataportal.se/organisation/SE2021006487",
        "name": {
            "sv": "Universitets- och högskolerådet",
            "en": "Swedish Council for Higher Education"
        },
        "aliases": ["uhr"],
        "type": "national_authority",
        "homepage": "https://www.uhr.se/",
        "email": "registrator@uhr.se",
        "identifiers": ["2021006487"]
    },
    "themes": ["education_culture_and_sport"],
    "license": {
        "id": "cc0_1_0",
        "label": {
            "en": "CC0 1.0 (Public Domain Dedication, No Copyright)"
        },
        "uri": "http://creativecommons.org/publicdomain/zero/1.0/"
    },
    "access_rights": "public",
    "accrual_periodicity": null,
    "languages": ["sv"],
    "spatial": ["kingdom_of_sweden"],
    "temporal": { "start": "2022-10-17", "end": "2023-01-09" },
    "issued": "2022-10-20",
    "modified": null,
    "contact_points": [
        {
            "uri": "https://editera.dataportal.se/store/163/resource/5",
            "name": "Per Zettervall",
            "email": "per.zettervall@uhr.se"
        }
    ],
    "distributions": [
        {
            "uri": "https://editera.dataportal.se/store/163/resource/8",
            "title": {},
            "description": {},
            "access_url": "https://www.uhr.se/.../ikvt23_antagna_urval1_kurser.xlsx",
            "download_url": null,
            "format": "microsoft_excel_xml",
            "kind": "file",
            "license": {
                "id": "cc0_1_0",
                "label": {
                    "en": "CC0 1.0 (Public Domain Dedication, No Copyright)"
                },
                "uri": "http://creativecommons.org/publicdomain/zero/1.0/"
            },
            "status": null,
            "availability": "stable",
            "languages": [],
            "issued": null,
            "modified": null,
            "access_service_uris": [],
            "broken": {
                "reason": "Not Found",
                "checked": "2026-09-30T02:52:17"
            }
        }
    ]
}
```

A distribution has three keys that are there only when they say something:

```json
{
    "broken": { "reason": "Not Found", "checked": "2026-09-30T02:52:17" },
    "unverified": { "reason": "timeout", "checked": "2026-10-01T02:52:59" },
    "byte_size": 5420000
}
```

`broken` is a dead distribution — the registry got an HTTP error for it, or
found no such host — and shows only in a `Catalog(exclude_broken=False)`; under
the default that distribution, and this dataset with it (its only one), are
not there. `unverified` is a distribution the registry's checker could not
reach at all, which says nothing about the distribution; it is never removed.
`byte_size` is on the 484 distributions (1.4%) whose publisher states a size.
A distribution never carries both `broken` and `unverified`. A `broken` that
`catalog.verify()` found, rather than the registry, also carries
`"by": "local"`.

`kind` is always there: what the distribution is, read from its metadata with
no request.

```text
pxweb     a PxWeb statistical table                              10,468
kolada    a Kolada key figure                                     5,963
doi       a DOI, resolving to a research-data landing page        5,021
file      a file to download: CSV, Excel, JSON, PDF, a ZIP        4,647
huwise    a Huwise (Opendatasoft) dataset: records and exports    3,520
geodata   a map service or a geo file                             2,794
web_page  a web page; the data is behind it, not at it            1,405
rowstore  EntryScape's rowstore: a table behind an API              552
unknown   the metadata does not say                                 324
ckan      a CKAN resource or its API                                310
api       an API described by a data service                        144
```

Counted over all 35,148 distributions. A `download_url` whose format is `html`
and whose address has no file extension is a `web_page`, not a `file`.

A dataset or data service has one key of the same sort, there only when the
source catalogue it was harvested from failed its latest harvest. The record
is what the last good harvest left; it is marked, never removed.

```json
{
    "stale": { "reason": "harvest failed", "checked": "2026-10-04T02:46:11" }
}
```

## `catalog.data_services(...)`

Same signature as `datasets()`. Seven filters, not ten: `format`, `kind`,
`updated`, `language` and the four date filters are refused with `QueryError` naming the
ones that work, because a data service has no distributions, no
`accrual_periodicity`, one single `language` across all 599, and `modified` on
7.5% of them. The registry's link check never tests `endpointURL`, so whether
an API answers is not something this can tell you; `exclude_broken` leaves
data services alone.

```python
page = catalog.data_services(
    query="skola",
    limit=50,
    offset=0,
    facet_limit=None,

    publisher="skolverket",
    keyword="grundskola",
    publisher_type="national_authority",   # also: company | non_governmental_organisation
    service_type="rest",
        # rest | view_service | download_service | discovery_service
        # | transformation_service
    theme="education_culture_and_sport",
    license="cc0_1_0",
    access_rights="public",
)
```

```json
{
    "uri": "https://editera.dataportal.se/store/162/resource/13",
    "context_id": "124",
    "entry_id": "2971",
    "type": "data_service",
    "title": { "sv": "Skolenhetsregistret", "en": "School unit registry" },
    "description": {
        "sv": "Skolenhetsregistret innehåller ...",
        "en": "The school unit register ..."
    },
    "keywords": { "sv": ["grundskola", "skolor"], "en": ["school units"] },
    "service_type": "rest",
    "endpoint_url": "https://api.skolverket.se/skolenhetsregistret/swagger-ui/index.html",
    "endpoint_description": null,
    "serves_datasets": ["https://editera.dataportal.se/store/162/resource/26"],
    "conforms_to": [],
    "publisher": {
        "id": "skolverket",
        "uri": "http://dataportal.se/organisation/SE2021004185",
        "name": { "sv": "Skolverket" },
        "aliases": [],
        "type": "national_authority",
        "homepage": "https://www.skolverket.se/",
        "email": "support.oppnadata@skolverket.se",
        "identifiers": ["2021004185"]
    },
    "themes": ["education_culture_and_sport"],
    "license": {
        "id": "cc0_1_0",
        "label": {
            "en": "CC0 1.0 (Public Domain Dedication, No Copyright)"
        },
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
    ]
}
```

## `Results` — what both searches return

A `list` of those record dicts, carrying the totals.

```python
page.total                        # int, exact: how many matched altogether
page.offset                       # int, what you asked for
page.limit                        # int | None, what you asked for
page.has_more                     # bool: offset + len(page) < total
page.facets                       # Facets over everything that matched
len(page); page[0]; list(page)    # it is a list
```

```json
{ "total": 44, "offset": 0, "limit": 2, "has_more": true, "len": 2 }
```

## `catalog.facets(...)`

You filter with a value; a facet tells you which values exist. This is every
dataset facet before you search — the same `Facets` a search carries as
`page.facets`, counted over every dataset the catalogue holds. Data-service
facets are `catalog.data_services(limit=0).facets`, identical in structure over
its seven.

```python
facets = catalog.facets(
    limit=None,                   # int | None, caps each facet
)

facets["theme"]                   # Facet: a list of FacetValue, biggest first
facets["theme"][0].value          # 'government_and_public_sector'  <- feed back into datasets()
facets["theme"][0].count          # 6249
facets["theme"][0].label          # {"en": "Government and public sector", "sv": "Regeringen och ..."}
value, count = facets["theme"][0] # still a 2-tuple
facets["theme"].omitted           # int, values cut by limit
facets.omitted                    # {filter: cut}, only the non-zero ones
facets.top("theme")               # FacetValue | None
facets.to_dict()                  # {filter: {value: count}}, JSON-serializable
list(facets); "format" in facets
```

```json
{
    "publisher": 287,
    "publisher_type": 8,
    "theme": 31,
    "keyword": 16135,
    "format": 36,
    "kind": 10,
    "license": 9,
    "access_rights": 1,
    "updated": 18,
    "language": 55
}
```

Counted over the default catalogue — `public` only, so `access_rights` has one
value; `Catalog(access_rights=None, exclude_broken=False)` has 356 publishers,
21,359 keywords, 47 formats, 11 kinds, 3 access rights and 65 languages.

A `keyword` value is shown as the publisher spelt it — the commonest spelling
of each — and matched case-insensitively, so `Kommun`, `kommun` and `KOMMUN`
are one keyword and all three find the same 4,615 datasets.

## `catalog.publishers()`

Every publisher this catalogue holds something from, most datasets first. A
plain list; no arguments, no pages. `id` is what `publisher=` takes and what
the `publisher` facet reports.

```python
publishers = catalog.publishers()
```

```json
{
    "id": "radet_for_framjande_av_kommunala_analyser_kolada",
    "uri": "http://dataportal.se/organisation/SE2220000315",
    "name": {
        "sv": "Rådet för främjande av kommunala analyser - Kolada",
        "en": "The Council for Advocacy of Municipal Analysis - Kolada"
    },
    "aliases": ["kolada"],
    "type": "non_governmental_organisation",
    "homepage": "https://rka.nu/",
    "email": "rka@rka.nu",
    "identifiers": [],
    "dataset_count": 5863,
    "data_service_count": 1
}
```

287 of them by default, 356 unscoped. The counts are this catalogue's:
`dataset_count == catalog.datasets(publisher=id, limit=0).total`, for every
row.

## `catalog.publisher(...)`

One publisher, with what it publishes. `None` if it has nothing in this
catalogue; a value nobody knows raises `QueryError` with suggestions.

```python
publisher = catalog.publisher(
    "skolverket",                 # id | alias | name (either language)
                                  # | organisation number | URI -- the same
                                  # resolver publisher= uses
)
```

```json
{
    "id": "skolverket",
    "uri": "http://dataportal.se/organisation/SE2021004185",
    "name": { "sv": "Skolverket" },
    "aliases": [],
    "type": "national_authority",
    "homepage": "https://www.skolverket.se/",
    "email": "support.oppnadata@skolverket.se",
    "identifiers": ["2021004185"],
    "dataset_count": 6,
    "data_service_count": 5,
    "facets": {
        "theme": {
            "education_culture_and_sport": 5,
            "utility_and_governmental_services": 1
        },
        "format": {
            "json": 5,
            "html": 1,
            "iso_19139_xml": 1,
            "xml": 1,
            "zip": 1
        },
        "kind": { "file": 5, "pxweb": 1, "web_page": 1 },
        "license": { "cc0_1_0": 5, "nolicense": 1 },
        "access_rights": { "public": 6 },
        "updated": { "semiannual": 1 },
        "language": { "sv": 2 }
    }
}
```

The row from `publishers()` plus `facets`, which is exactly
`catalog.datasets(publisher=id, limit=0).facets.to_dict()` over those seven
filters — so every value in it can be fed back in beside `publisher=`.

## `catalog.get(...)`

```python
record = catalog.get(
    "https://metadata.boverket.se/store/1/resource/8",
    format="dict",                # "dict" (local, immediate) | "turtle" | "ttl"
                                  # | "rdf/xml" | "rdfxml" | "xml" | "n-triples"
                                  # | "ntriples" | "nt" | "json-ld" | "jsonld"
                                  # | "trig" | any media type
)
```

```text
dict      -> the dataset or data_service record above, or None if not in this copy
otherwise -> str of RDF, fetched live; the only request Catalog makes outside a
             download. None if the URI is not in this copy.
```

## `catalog.info()`

```python
catalog.info()
```

```json
{
    "database": "/home/you/.cache/dataportalen/catalog.sqlite",
    "first_retrieved": "2026-09-30T17:57:14",
    "last_refreshed": "2026-10-01T13:04:54",
    "downloaded": "2026-10-01T13:04:54",
    "age_days": 0,
    "bytes": 98951168,
    "datasets": 17555,
    "data_services": 578,
    "publishers": 287,
    "excluded": {
        "access_rights": 5921,
        "dead_distributions": 784,
        "dead_datasets": 127
    },
    "stale_datasets": 1,
    "unverified_distributions": 8220
}
```

`datasets` is what this `Catalog` holds, after `access_rights` and
`exclude_broken`; the database underneath holds all 23,582. `excluded` is what
those two left out when the object was built: records outside the access
scope, dead distributions, and the datasets that had nothing but dead ones.
`unverified_distributions` and `stale_datasets` count what is held and marked.

## `catalog.sources()`

The source catalogues the registry harvests, and how the latest harvest of
each went. One row per source, most datasets first; no arguments. Read from
the registry at the end of every download or refresh, so it is as old as the
copy; empty for a database written before this existed.

```python
sources = catalog.sources()
```

```text
context_id          str, what record["context_id"] holds
status              "success" | "failed"
harvested           "2026-10-04T02:23:44" | None, the latest harvest
title               str | None
dataset_count       int, what this catalogue holds from it
data_service_count  int
```

`status` is `success` or `failed`. The counts are what this catalogue holds
from the source. 666 sources, 427 of them failed (2026-10-04) — but 419 of
those are registrations that never yielded a dataset. The other 8 hold 81
datasets unscoped, 1 in the default catalogue, and each of those carries
`stale`.

## `catalog.verify(...)`

Asks the publishers' servers about the distributions the registry's checker
could not reach, and keeps what they say. The only thing in the package that
sends a request to a publisher, and it never happens unless this is called.

```python
summary = catalog.verify(
    "unverified",                 # which distributions, within access_rights:
                                  #   "unverified" | "broken" | "all"
    limit=None,                   # int | None: at most this many addresses,
                                  #   taken one per host in turn
)
```

```text
checked       int    distinct addresses asked about
alive         int
dead          int
unverified    int    still no verdict
invalid_cert  int    answered, but only with certificate verification off
requests      int
elapsed       float  seconds
```

```text
HEAD answers below 400            -> alive
HEAD answers an error             -> a one-byte ranged GET; its answer counts
404 | 410                         -> dead
a redirect to the site's page
  for every unknown path          -> dead, "soft 404"
no answer, host not in DNS        -> dead, "host not found"
no answer, host in DNS            -> unverified (after one retry)
429, or two addresses in a row
  with no answer                  -> unverified; that host is left alone
                                     for the rest of the run
any other error status            -> unverified, the status phrase as reason
                                     (a WMS endpoint answers 400 to a bare address)
a certificate error               -> asked again without verification;
                                     counted under invalid_cert
```

One request at a time per host with a 0.4 s pause, 8 hosts at once. A sample
of 200 addresses over all access levels on 2026-10-04: 91 alive, 19 dead (all
Not Found), 90 still unverified, 290 requests, 157 s. All 10,858 unverified is
about 11,000 requests and most of an hour, because 7,091 are one host
(api.scb.se).

A verdict is stored in the database and applied when a record is read: alive
removes the registry's mark, dead sets `broken` with `"by": "local"`. Against
the registry's `unverified` a local answer always wins; against its `broken`
the later look wins. Kept across refreshes, dropped by `rebuild=True`.

## `Catalog` properties and lifecycle

```python
catalog.database                  # str
catalog.max_age                   # int | None
catalog.exclude_broken            # bool
catalog.access_rights             # frozenset | None
catalog.first_retrieved           # "2026-09-30T17:57:14" | None, when built
catalog.last_refreshed            # "2026-10-01T13:04:54" | None, what a refresh asks from
catalog.downloaded                # datetime | None
catalog.age_days                  # int >= 0 | None

len(catalog)                      # datasets only
for record in catalog: ...        # datasets only
catalog.close()                   # releases the HTTP connection; never required
with Catalog() as catalog: ...    # same thing
```

## `LiveCatalog(...)`

The same searches asked of the registry itself: no database, nothing
downloaded, every call a request. The methods and arguments are `Catalog`'s and
the records are the same dicts, built by the same code — `kind` included,
less link health and `stale`, which the registry cannot filter on. About 0.1–0.3 s for a count, a few seconds
for a page of full records.

```python
live = LiveCatalog(
    access_rights=("public",),  # as for Catalog: "public" | "non_public"
                                #   | "restricted" | "none" | a list | None
)

live.datasets(
    query="cykel",              # the registry's full-text phrase search, not
                                #   Catalog's substring match: 251 vs 388
    limit=50,                   # 0..100 -- a page is all the registry serves.
                                #   None or >100 raises; 0 = count + facets
    offset=0,
    facet_limit=None,
    publisher="trafikverket",   # every filter Catalog takes, except:
    theme="transport",          #   publisher_type -> QueryError (not indexed)
                                #   kind -> QueryError (worked out after the
                                #   fetch; the records still carry it)
    keyword="kommun",           # case-insensitive, as locally; an unknown
                                #   keyword raises, without suggestions
    modified_after="2025",
)                               # -> Results[DatasetRecord], facets without
                                #    keyword, publisher_type and kind
live.data_services(service_type="rest")  # same, data-service filters
live.facets(limit=None)         # = datasets(limit=0).facets, one request
live.get("https://metadata.boverket.se/store/1/resource/8",
         format="dict")         # 4 requests; "turtle" etc. as for Catalog
live.publishers()               # ~20 requests the first time, then held
live.publisher("scb")           # row + facets, as for Catalog
live.info()                     # three counts only
live.close()                    # with LiveCatalog() as live: ... too
```

```json
{ "datasets": 17682, "data_services": 578, "publishers": 298 }
```

No `len()`, no iteration, no `database`/`max_age`/`exclude_broken`: each would
be a download. No `sources()` and no `verify()` either: both keep what they
find in the database. Nothing is excluded, so the default holds 17,682 datasets where
`Catalog()` holds 17,555.

How its answers compare with `Catalog(access_rights=None, exclude_broken=False)`, measured over every facet value of every filter on
2026-10-01:

```text
datasets
  publisher, total            equal (356 of 356 values)
  theme, license,             equal for most; a few more live, from nested nodes
  access_rights, language       and second values: theme 3 of 31 values (+1..+3),
                                license 4 of 9 (cc_by_nc_4_0 20 vs 17),
                                restricted 247 vs 244, sv 18,355 vs 18,349
  updated                     9 of 20 values more live (continuous 650 vs 634);
                                quadrennial and decennial exist only live
  format                      the registry's own index: microsoft_excel 63 vs 73,
                                zip facet 292 vs filter 291 (two spellings added)
  keyword                     equal for 199 of 200 sampled (Badplatser 20 vs 17)
  modified_*, issued_*        either way: modified_after="2025" 12,672 vs 12,584,
                                issued_before="2015-06" 1,887 vs 1,934
  query                       a different engine: "cykel" 251 vs 388,
                                "air quality" 15 vs 15
  publisher_type, kind        refused
data services
  every filter                equal (publisher 31, service_type 5, theme 12,
                                license 3, access_rights 3 values)
records                       identical, less broken/unverified/stale (50 of 50 compared)
publishers()                  identical, 356 of 356 rows
```

## `text(...)`

```python
text(
    {"sv": "Vägtrafiknät", "en": "Road traffic network"},
    prefer="sv",                  # "sv" | "en"
)                                 # -> 'Vägtrafiknät'
```

```text
{"en": "Road traffic network"}  -> 'Road traffic network'   (falls back)
{}                              -> None
"already a string"              -> 'already a string'       (passed through)
None                            -> None
```

## `default_catalog_path()`

```python
default_catalog_path()
```

```text
Windows -> %LOCALAPPDATA%\dataportalen\catalog.sqlite
else    -> $XDG_CACHE_HOME/dataportalen/catalog.sqlite
           or ~/.cache/dataportalen/catalog.sqlite
```

## `read_catalog(...)`

Every record in a database file without building a `Catalog` around it — both
types, in insertion order, each the dict shown above, unscoped: every
access_rights value and every distribution, the dead ones carrying `broken`. A path
that does not exist raises `FileNotFoundError`.

```python
records = read_catalog(default_catalog_path())
```

## Typed records

Every dict above has a `TypedDict`, so an editor completes `dataset["` and a
type checker catches `"licence"`. Nothing changes at run time: they are still
plain dicts.

```python
from typing import List, Optional

def files(page: Results[DatasetRecord]) -> List[DistributionRecord]:
    return [dist for dataset in page for dist in dataset["distributions"]]

row: Publisher = catalog.publishers()[0]
detail: Optional[PublisherDetail] = catalog.publisher("scb")
```

```text
DatasetRecord       catalog.datasets(), catalog.get()
DataServiceRecord   catalog.data_services()
DistributionRecord  dataset["distributions"][i]
PublisherRecord     record["publisher"]                  (8 keys)
Publisher           catalog.publishers()[i]              (+ 2 counts)
PublisherDetail     catalog.publisher(x)                 (+ facets)
SourceRecord        catalog.sources()[i]
LicenseRecord       record["license"]                    {id, label, uri}
ContactRecord       record["contact_points"][i]          {uri, name, email}
TemporalRecord      dataset["temporal"]                  {start, end}
LinkMark            dist["broken"], dist["unverified"],  {reason, checked}, and
                    record["stale"]                      by: "local" from verify()
LanguageMap         title, description, name, label      {sv, en}, either or both
KeywordMap          record["keywords"]                   {sv: [...], en: [...]}
```

## `enable_logging(...)` and `logger`

```python
import logging, sys

enable_logging(
    level=logging.INFO,           # int | "debug" | "info" | "warning" | "error"
    stream=sys.stderr,            # any file-like; None -> stderr
    fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)                                 # -> the "dataportalen" logger

logger.setLevel(logging.WARNING)  # the same logger, for apps that configure their own
```

## Publisher aliases

Short names for publishers with long ids, shipped with the package. An alias
is accepted wherever a publisher is, and shows in the publisher's `aliases`.

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

## Errors

```text
DataportalError                   # base; catch this one
  TransportError                  #   the request never completed
    TimeoutError                  #     it took too long
  HTTPError                       #   the registry answered with a status
    NotFoundError                 #     404
    RateLimitError                #     429
    ServerError                   #     5xx
  ParseError                      #   the answer, or the database, was not what it claims
  QueryError                      #   the call was wrong: bad filter, value, window, or
                                  #     Catalog argument

FileNotFoundError                 # read_catalog() on a path that is not there
```
