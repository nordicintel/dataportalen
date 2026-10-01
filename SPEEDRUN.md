# Speedrun

Every public name in `dataportalen`, once, with every argument spelled out and
every output shape printed underneath. One Python snippet per function; a JSON
snippet under it when the shape has not been shown yet.

Written against 0.9.0 and the corpus as of 2026-09-30 (23,581 datasets, 599
data services). When the public interface changes, this file changes with it.

```python
from dataportalen import (
    Catalog, default_catalog_path, read_catalog, text,
    Results, Breakdown, ValueList, ValueCount,
    DataportalError, TransportError, TimeoutError, HTTPError, NotFoundError,
    RateLimitError, ServerError, ParseError, QueryError,
    logger, enable_logging, __version__,
)
```

## `Catalog(...)`

```python
cat = Catalog(
    path=None,                    # str | None -> default_catalog_path()
    refresh="if_missing",         # "if_missing" | "if_stale" | "always" | "never"
    exclude_broken_links=False,   # True drops every distribution the registry's
                                  #   nightly check found broken (11,871 of 35,140)
    stale_after=7,                # int days
    progress="auto",              # "auto" | None | callable(done, total)
    workers=8,                    # int, parallel requests while downloading
    base_url="https://admin.dataportal.se",
    transport=None,               # BaseTransport | None
)
```

## `cat.datasets(...)`

Filters AND together. A list is OR within one filter — except `keyword`, which
is AND. An unknown filter name, an unknown value, or a negative window raises
`QueryError` rather than matching nothing.

`creator` was a filter until 0.9.0 and is now an unknown name: of the 7,104
datasets that named one, 6,174 named their own publisher again.

```python
page = cat.datasets(
    limit=50,                     # int | None (every match) | 0 (counts only)
    offset=0,                     # int >= 0
    breakdown_limit=None,         # int | None; what it cuts lands in .breakdown.omitted

    text="cykel",                 # substring, both languages, title + description + keywords
    publisher="trafikverket",     # slug | name | org.nr "2021000639" | URI; 356 values
    keyword="Kommun",             # case-sensitive exact when known, substring otherwise; 23,370
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
    license="cc0_1_0",
        # cc0_1_0 | nolicense | cc_by_4_0 | otherlicense | cc_by_nc_4_0
        # | cc_by_nc_sa_4_0 | cc_by_sa_4_0 | cc_by_nc_nd_4_0 | cc_by_nd_4_0
    access_rights="public",       # public | non_public | restricted
    updated="annual",
        # annual | monthly | continuous | other | quarterly | irregular | unknown
        # | weekly | daily | semiannual | every_two_weeks | continuously_updated
        # | never | biennial | as_needed | triennial | every_two_months
        # | three_times_a_year                                        (18 values)
    language="swedish",           # swedish | english
    place="kingdom_of_sweden",    # slug | name; 542 values
    link="success",               # success | broken | excluded  (the worst verdict
                                  #   across the dataset's own files)

    updated_after="2025-01-01",   # dcterms:modified; "YYYY-MM-DD" | date | datetime
    updated_before="2026-01-01",
    published_after="2020-01-01", # dcterms:issued
    published_before="2026-01-01",
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
        "uri": "http://dataportal.se/organisation/SE2021006487",
        "context_id": "827",
        "entry_id": "208",
        "name": {
            "sv": "Universitets- och högskolerådet",
            "en": "Swedish Council for Higher Education"
        },
        "type": "national_authority",
        "homepage": "https://www.uhr.se/",
        "email": "registrator@uhr.se",
        "identifiers": ["2021006487"]
    },
    "themes": ["education_culture_and_sport"],
    "license": "cc0_1_0",
    "access_rights": "public",
    "accrual_periodicity": null,
    "languages": ["swedish"],
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
            "context_id": "110",
            "entry_id": "2544",
            "title": {},
            "description": {},
            "access_url": [
                "https://www.uhr.se/.../ikvt23_antagna_urval1_kurser.xlsx"
            ],
            "download_url": [],
            "format": "microsoft_excel_xml",
            "license": "cc0_1_0",
            "status": null,
            "availability": "stable",
            "languages": [],
            "issued": null,
            "modified": null,
            "access_service_uris": [],
            "link": {
                "status": "broken",
                "message": "Not Found",
                "checked": "2026-09-30T02:52:17",
                "attempts": 2
            }
        }
    ],
    "link": {
        "status": "success",
        "message": "OK",
        "checked": "2026-09-30T02:52:17",
        "attempts": null
    }
}
```

## `cat.data_services(...)`

Same signature as `datasets()`. Eight filters, not fifteen: `format`,
`updated`, `place`, `language` and the four date filters are refused with
`QueryError` naming the ones that work, because a data service has no
distributions, no `accrual_periodicity`, `place` on 8% and one single
`language` across all 599.

```python
page = cat.data_services(
    limit=50,
    offset=0,
    breakdown_limit=None,

    text="skola",
    publisher="skolverket",
    keyword="grundskola",
    publisher_type="national_authority",   # also: company | non_governmental_organisation
    service_type="rest",
        # rest | view_service | download_service | discovery_service
        # | transformation_service
    theme="education_culture_and_sport",
    license="cc0_1_0",
    access_rights="public",
    link="success",                        # success | broken
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
    "endpoint_urls": [
        "https://api.skolverket.se/skolenhetsregistret/swagger-ui/index.html"
    ],
    "endpoint_descriptions": [],
    "serves_dataset_uris": [
        "https://editera.dataportal.se/store/162/resource/26"
    ],
    "conforms_to": [],
    "publisher": {
        "uri": "http://dataportal.se/organisation/SE2021004185",
        "context_id": "827",
        "entry_id": "207",
        "name": { "sv": "Skolverket" },
        "type": "national_authority",
        "homepage": "https://www.skolverket.se/",
        "email": "support.oppnadata@skolverket.se",
        "identifiers": ["2021004185"]
    },
    "themes": ["education_culture_and_sport"],
    "license": "cc0_1_0",
    "access_rights": "public",
    "landing_page": "https://www.skolverket.se/om-oss/oppna-data/api-for-skolenhetsregistret",
    "contact_points": [
        {
            "uri": "https://editera.dataportal.se/store/162/resource/11",
            "name": "Centralsupport Skolverket",
            "email": "supporten@skolverket.se"
        }
    ],
    "link": {
        "status": "broken",
        "message": null,
        "checked": "2026-09-30T02:51:10",
        "attempts": 1
    }
}
```

## `Results` — what both searches return

A `list` of those record dicts, carrying the totals.

```python
page.total                        # int, exact: how many matched altogether
page.offset                       # int, what you asked for
page.limit                        # int | None, what you asked for
page.has_more                     # bool: offset + len(page) < total
page.breakdown                    # Breakdown over everything that matched
len(page); page[0]; list(page)    # it is a list
```

```json
{ "total": 67, "offset": 0, "limit": 2, "has_more": true, "len": 2 }
```

## `cat.filters(...)`

The dataset options before you search — the same `Breakdown` a search carries,
counted over every dataset. Data-service options are
`cat.data_services(limit=0).breakdown`, identical in structure over its nine.

```python
options = cat.filters(
    limit=None,                   # int | None, caps each value list
)

options["theme"]                  # ValueList of ValueCount, biggest first
options["theme"][0].value         # 'population_and_society'  <- feed back into datasets()
options["theme"][0].dataset_count # 6460
options["theme"][0].label         # {"sv": "Befolkning och samhälle", "en": "Population and society"}
value, count = options["theme"][0]        # still a 2-tuple
options["theme"].omitted          # int, values cut by limit
options.omitted                   # {filter: cut}, only the non-zero ones
options.top("theme")              # ValueCount | None
options.to_dict()                 # {filter: {value: count}}, JSON-serializable
list(options); "format" in options
```

```json
{
    "publisher": 356,
    "publisher_type": 8,
    "theme": 31,
    "keyword": 23370,
    "format": 47,
    "license": 9,
    "access_rights": 3,
    "updated": 18,
    "language": 2,
    "place": 542,
    "link": 3
}
```

## `cat.get(...)`

```python
record = cat.get(
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

## `cat.info()`

```python
cat.info()
```

```json
{
    "path": "C:\\Users\\ruben\\AppData\\Local\\dataportalen\\catalog.sqlite",
    "first_retrieved": "2026-09-30T17:57:14",
    "last_refreshed": "2026-09-30T17:58:16",
    "downloaded": "2026-09-30T17:58:16",
    "age_days": 0,
    "stale": false,
    "bytes": 98725888,
    "datasets": 23581,
    "data_services": 599,
    "publishers": 356
}
```

## `Catalog` properties and lifecycle

```python
cat.path                          # str
cat.first_retrieved               # "2026-09-30T17:57:14" | None, when built
cat.last_refreshed                # "2026-09-30T17:58:16" | None, what a refresh asks from
cat.downloaded                    # datetime | None
cat.age_days                      # int >= 0 | None
cat.stale                         # bool, age_days >= stale_after
cat.exclude_broken_links          # bool
cat.stale_after                   # int

len(cat)                          # 23581, datasets only
for record in cat: ...            # datasets only
cat.close()                       # releases the HTTP connection; never required
with Catalog() as cat: ...        # same thing
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
types, in insertion order, each the dict shown above.

```python
records = read_catalog("catalog.sqlite")
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
  QueryError                      #   the call was wrong: bad filter, value, or window

FileNotFoundError                 # refresh="never" and no file (not a DataportalError)
```

## Not built yet

`LiveCatalog()` — the agreed next change: same filters, every call straight to
the registry, no local copy. It cannot promise `total`, `breakdown` or
`filters()`, which is why it is a second class and not a flag on `Catalog`.
Nothing of it exists yet; this section is the placeholder it fills.

## IMPORTANT NOTES

- A list is OR for every filter except keyword, which is AND. theme=["transport","environment"] gives 3,485 (the union); keyword=["Kommun","Region"] gives 1,141 (the intersection of 4,611 and 2,397). That inconsistency was invisible until the arguments sat next to each other. Two meanings for one syntax, should be looked over, and possibly revised for clarity.
- datasets() takes 16 filters, data_services() takes 9. The refusal message is good, but the asymmetry is now plain to read, which is what you'll want when LiveCatalog has to decide which of those it can honour server-side.
