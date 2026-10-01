# Reference

Everything the package exposes, as tables. [guide.md](guide.md) is the part
you read; this is the part you look up. [SPEEDRUN.md](../SPEEDRUN.md) is the
same surface as code: every argument once, the output underneath.

Percentages are measured over the whole corpus — every dataset and every data
service, not a sample. The registry grows by a handful a day, so treat the
absolute counts as "as of 2026-10-01" and the percentages as stable.

1. [The five methods](#the-five-methods)
2. [Building a Catalog](#building-a-catalog)
3. [Dataset filters](#dataset-filters)
4. [Data service filters](#data-service-filters)
5. [A dataset record](#a-dataset-record)
6. [A data service record](#a-data-service-record)
7. [What a search returns](#what-a-search-returns)
8. [text()](#text)
9. [Errors](#errors)
10. [Everything exported](#everything-exported)

## The five methods

```python
catalog.datasets(**filters, limit=50, offset=0, breakdown_limit=None)      -> Results
catalog.data_services(**filters, limit=50, offset=0, breakdown_limit=None) -> Results
catalog.filters(limit=None)                                               -> Breakdown
catalog.get(uri, format="dict")                                           -> dict | str | None
catalog.info()                                                            -> dict
catalog.close()                                                           -> None
```

| | |
| --- | --- |
| `datasets()` | Search the datasets the catalogue holds — 12,653 by default, 23,582 unscoped. `limit=None` for every match, `limit=0` for the count and breakdown with no rows. |
| `data_services()` | Search the data services — 578 by default, 599 unscoped. Same signature; refuses `format`, `updated`, `language` and the date filters. |
| `filters()` | Every dataset filter, every value present, count-descending, with labels. ~0.5 s over the whole corpus. |
| `get()` | One record by its URI, from the file. `format="dict"` is local; any other format is one live request for the entry's RDF as text. `None` if nothing matches. |
| `info()` | The database: `database`, `first_retrieved`, `last_refreshed`, age, size, and what this `Catalog` holds. |
| `close()` | Release the HTTP connection. Never required; nothing leaks without it. |

`len(catalog)` and iterating a `Catalog` give the datasets. Data services come
from `data_services()`.

`get(uri, format=)` accepts `turtle`, `ttl`, `rdf/xml`, `rdfxml`, `xml`,
`n-triples`, `ntriples`, `nt`, `json-ld`, `jsonld`, `trig`, or any media type
passed straight through.

## Building a Catalog

```python
Catalog(database=None, *, max_age=7, rebuild=False,
        exclude_broken=True, access_rights=("public",))
```

| Argument | Default | |
| --- | --- | --- |
| `database` | the cache directory | The SQLite file. |
| `max_age` | `7` | Days. A copy older than this — or missing — is brought up to date when the object is built: a download if there is nothing, otherwise an incremental refresh of what the registry has touched since, under a minute. `None`: use what is there, download only if there is nothing. |
| `rebuild` | `False` | Fetch everything again now. What a schema change asks for, and the only thing that drops a dataset the registry has withdrawn. |
| `exclude_broken` | `True` | Drop every file the registry's nightly check found broken (11,762 of 35,148), and every dataset whose every file was broken (5,331). A dataset that never had files (1,647) stays. `False` keeps everything and marks each broken file. |
| `access_rights` | `("public",)` | Which `access_rights` values the catalogue holds: `public`, `non_public`, `restricted`, and `none` for the 4,167 that set nothing. A list, one string, or `None` for all. |

Everything that writes the file is here. No method rewrites it.

| What is there | `max_age=7` | `max_age=None` | `rebuild=True` |
| --- | --- | --- | --- |
| nothing | download | download | download |
| a copy under 7 days old | use it | use it | fetch everything |
| a copy over 7 days old | refresh what changed, ~40 s | use it | fetch everything |

Default path: `%LOCALAPPDATA%\dataportalen\catalog.sqlite` on Windows,
`$XDG_CACHE_HOME/dataportalen/catalog.sqlite` or
`~/.cache/dataportalen/catalog.sqlite` elsewhere. `default_catalog_path()`
returns it.

The database always holds the whole registry; `exclude_broken` and
`access_rights` decide what this `Catalog` holds of it, so changing either is
a new `Catalog`, not a new download. `info()["datasets"]` is the scoped count.

## Dataset filters

Nine, plus free text and four dates. All combine; all must match. A list
value means any of them will do, except `keyword`, where all must be present.

| Filter | Matches | On |
| --- | --- | --- |
| `publisher=` | the organisation that put it on the portal — slug, name, organisation number, URI or alias | 100% |
| `license=` | the licence's `id` | 100% |
| `keyword=` | one of the publisher's own tags, by substring | 94.8% |
| `publisher_type=` | what kind of organisation published it | 92.6% |
| `language=` | the language of the data, as an ISO code: `sv`, `en`, … | 89.3% |
| `access_rights=` | `public`, `non_public` or `restricted` | 82.3% |
| `theme=` | the subject it is filed under | 78.2% |
| `format=` | a format one of its distributions is in | 69.7% |
| `updated=` | how often the publisher refreshes it | 63.3% |
| `text=` | title, description or keywords, either language | — |

| Date filter | Which field | On |
| --- | --- | --- |
| `modified_after=`, `modified_before=` | `modified` — when the publisher last changed it | 89.0% |
| `issued_after=`, `issued_before=` | `issued` — when they first released it | 41.6% |

Dates take `"2024-01-01"`, `"2024-01"`, `"2024"`, or a `date`/`datetime`.

How many distinct values each has, over the whole registry and over the
default catalogue, and what the commonest is:

| Filter | Values (all) | Values (default) | Commonest |
| --- | --- | --- | --- |
| `keyword` | 23,373 | 15,592 | `Rådet för främjande av kommunala analyser - Kolada` |
| `publisher` | 356 | 209 | `radet_for_framjande_av_kommunala_analyser_kolada` |
| `language` | 65 | 55 | `sv` |
| `format` | 47 | 34 | `json` |
| `theme` | 31 | 30 | `population_and_society` |
| `updated` | 18 | 18 | `annual` |
| `license` | 9 | 8 | `cc0_1_0` |
| `publisher_type` | 8 | 8 | `national_authority` |
| `access_rights` | 3 | 1 | `public` |

`catalog.filters()` lists them all, live against your copy, with labels.

Publisher aliases ship in [aliases.json](../src/dataportalen/aliases.json):
`scb`, `fhm`, `slu`, `smhi`, `uhr`, `kolada`, `energimyndigheten`. An alias is
accepted as input and never appears in output.

## Data service filters

Eight, plus free text. **Three dataset filters and the dates are refused**,
with an error naming the ones that work.

| Filter | Matches | On |
| --- | --- | --- |
| `access_rights=` | `public`, `non_public` or `restricted` | 97.8% |
| `publisher=` | the organisation that published it | 97.3% |
| `publisher_type=` | what kind of organisation that is | 97.2% |
| `keyword=` | one of the publisher's own tags | 83.5% |
| `service_type=` | what kind of service | 55.9% |
| `theme=` | the subject | 53.8% |
| `license=` | the licence's `id` | 51.8% |
| `text=` | title, description or keywords | — |

| Refused | Why |
| --- | --- |
| `format=` | a data service has no distributions |
| `updated=` | nor an accrual periodicity |
| `language=` | one single value across all 599 |
| the four date filters | `modified` on 7.5%, `issued` on 0.8% |

`service_type=` takes five values:

| Value | Count | |
| --- | --- | --- |
| `rest` | 288 | a web API |
| `view_service` | 31 | INSPIRE view, i.e. WMS |
| `download_service` | 14 | INSPIRE download |
| `transformation_service` | 1 | INSPIRE |
| `discovery_service` | 1 | INSPIRE |

## A dataset record

21 keys, always present. An empty value is `null`, `[]` or `{}` — never a
missing key, so you never need `.get()`.

| Key | Type | Filled |
| --- | --- | --- |
| `uri` | str | 100% |
| `type` | `"dataset"` | 100% |
| `context_id`, `entry_id` | str — the registry's own id, what `get(format="turtle")` sends | 100% |
| `title` | `{"sv": str, "en": str}` | 100% |
| `description` | `{"sv": str, "en": str}` | 100% |
| `publisher` | dict, see below — always a dict, with empty values where no publisher is named (10 datasets) | 100% |
| `license` | `{"id", "label", "uri"}`, see below | 100% |
| `keywords` | `{"sv": [str], "en": [str]}` | 94.8% |
| `distributions` | `[dict]`, see below | 93.0% |
| `languages` | `[ISO code]` — `sv`, `en`, or one of 63 others | 89.3% |
| `modified` | ISO date or timestamp | 89.0% |
| `access_rights` | short name | 82.3% |
| `themes` | `[short name]` | 78.2% |
| `contact_points` | `[dict]` | 63.5% |
| `accrual_periodicity` | short name | 63.3% |
| `identifier` | str | 61.0% |
| `landing_page` | url | 56.2% |
| `issued` | ISO date | 41.6% |
| `temporal` | `{"start": …, "end": …}` | 19.0% |
| `spatial` | `[short name]` — not a filter | 19.0% |

**`license`**

| Key | |
| --- | --- |
| `id` | short name, what `license=` takes: `cc_by_4_0`, `nolicense`, … |
| `label` | `{"sv": str, "en": str}` — `nolicense` and `otherlicense` are DIGG's own categories, on 39% of datasets, and have labels too |
| `uri` | the licence page |

**`publisher`**

| Key | |
| --- | --- |
| `uri` | the registry's URI for the organisation |
| `name` | `{"sv": str, "en": str}` |
| `type` | short name: `national_authority`, `local_authority`, … |
| `identifiers` | `[str]`, usually the organisation number |
| `email`, `homepage` | str or `null` |

**`contact_points`** — `name`, `email`, `uri`.

**Each entry in `distributions`** — 13 keys, 14 when broken:

| Key | | Filled |
| --- | --- | --- |
| `uri` | | 100% |
| `title`, `description` | `{"sv": str, "en": str}` | 97.8%, 30.0% |
| `format` | short name: `csv`, `json`, `geopackage`, … | 82.9% |
| `access_url` | url or `null` — a page or service to get it through. **Read this one.** | 100% |
| `download_url` | url or `null` — a direct file link | 7.4% |
| `license` | `{"id", "label", "uri"}` or `null` | 67.8% |
| `availability`, `status` | short names | 43.6%, 9.5% |
| `languages` | `[ISO code]` | 44.2% |
| `issued`, `modified` | ISO dates | 16.6%, 17.7% |
| `access_service_uris` | `[uri]` — the data service that serves it, where declared | 21.6% |
| `broken` | `{"reason": str, "checked": ISO timestamp}` — only on a file the registry's check found broken, only in a `Catalog(exclude_broken=False)` | 33.5% |

Of 35,148 distributions: 32,548 carry only `access_url`, 2,595 carry both, 5
carry neither, and none carries `download_url` alone. 147 name more than one
access URL and 130 more than one download URL; the first is what you get.

## A data service record

18 keys. The same keys mean the same things as on a dataset; `distributions`,
`accrual_periodicity`, `spatial`, `temporal`, `modified` and `issued` are
absent.

| Key | Type | Filled |
| --- | --- | --- |
| `uri`, `type`, `context_id`, `entry_id` | | 100% |
| `title` | `{"sv": str, "en": str}` | 100% |
| `publisher` | dict | 100% |
| `endpoint_url` | url — where to call it | 99.8% |
| `access_rights` | short name | 97.8% |
| `description` | `{"sv": str, "en": str}` | 97.3% |
| `keywords` | `{"sv": [str], "en": [str]}` | 83.5% |
| `contact_points` | `[dict]` | 64.1% |
| `service_type` | short name | 55.9% |
| `themes` | `[short name]` | 53.8% |
| `landing_page` | url | 52.6% |
| `license` | `{"id", "label", "uri"}` or `null` | 51.8% |
| `endpoint_description` | url — the API documentation | 47.6% |
| `conforms_to` | `[url]` — the spec it follows | 29.5% |
| `serves_datasets` | `[uri]` | 8.0% |

Four of the 599 name more than one endpoint URL and one more than one
description; the first is what you get. `conforms_to` (36 of 177 plural) and
`serves_datasets` (8 of 48) stay lists.

The registry's link check never tests `endpointURL`, so a data service carries
no `broken` and `exclude_broken` leaves them alone.

## What a search returns

`Results` is a `list` of dicts with four extras:

| | |
| --- | --- |
| `.total` | how many matched, exactly — not an estimate |
| `.offset`, `.limit` | what you asked for |
| `.has_more` | whether you are holding all of them |
| `.breakdown` | a `Breakdown` over everything that matched |

`Breakdown` is a mapping keyed by filter name:

| | |
| --- | --- |
| `breakdown["theme"]` | a `ValueList` of `ValueCount`, count-descending |
| `breakdown.omitted` | `{filter: how many values were cut}`, when `breakdown_limit` was given |
| `breakdown.top("theme")` | the commonest value, or `None` |
| `breakdown.to_dict()` | `{filter: {value: count}}`, JSON-ready |
| `"format" in breakdown` | whether that filter applies here |

`ValueList` is a `list` with `.omitted`. `ValueCount` is a
`(value, dataset_count)` pair — it unpacks and compares equal to a plain tuple
— with `.label` alongside as `{"sv": …, "en": …}`, `{}` where the value is its
own label. The label is not part of the tuple, so `_replace` and pickling drop
it.

Counts are per record: a dataset with three CSV files counts once under
`format` → `csv`.

## text()

```python
text(value, prefer="sv") -> str | None
```

One string out of a language map, because 10% of datasets have no Swedish and
`record["title"]["sv"]` raises for them.

| Input | Result |
| --- | --- |
| `{"sv": "Vägtrafiknät", "en": "Road"}` | `"Vägtrafiknät"` |
| `{"sv": "Vägtrafiknät", "en": "Road"}`, `prefer="en"` | `"Road"` |
| `{"en": "Road"}` | `"Road"` |
| `{}` | `None` |
| `None` | `None` |
| `"already a string"` | `"already a string"` |

## Errors

Every one derives from `DataportalError`.

| Error | Means |
| --- | --- |
| `QueryError` | your call was wrong: a filter value that does not exist, a filter that does not apply to what you searched, a bad window, or a `Catalog` argument out of range |
| `NotFoundError` | there is no such entry (`get()` returns `None` instead) |
| `RateLimitError` | too many requests; it already retried |
| `TimeoutError` | the registry did not answer in time |
| `TransportError` | the connection failed |
| `ServerError` | the registry itself broke |
| `ParseError` | the registry sent something unreadable, or the database is from another schema (`rebuild=True`) |
| `HTTPError` | any other HTTP failure |

`FileNotFoundError` — Python's own — is raised by `read_catalog()` on a path
that is not there.

## Everything exported

19 names.

| | |
| --- | --- |
| `Catalog` | the whole surface |
| `default_catalog_path` | where the database goes by default |
| `read_catalog` | every record in a database, unscoped, without building a `Catalog` |
| `text` | read a language map |
| `Results`, `Breakdown`, `ValueList`, `ValueCount` | what a search gives you |
| `DataportalError` + the 7 subclasses above | errors |
| `logger`, `enable_logging` | logging |
| `__version__` | |
