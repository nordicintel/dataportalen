# Reference

Everything the package exposes, as tables. [guide.md](guide.md) is the part
you read; this is the part you look up.

Percentages are measured over the whole corpus — every dataset and every data
service, not a sample. The registry grows by a handful a day, so treat the
absolute counts as "as of 2026-09-30" and the percentages as stable.

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
cat.datasets(**filters, limit=50, offset=0, breakdown_limit=None)      -> Results
cat.data_services(**filters, limit=50, offset=0, breakdown_limit=None) -> Results
cat.filters(limit=None)                                               -> Breakdown
cat.get(uri, format="dict")                                           -> dict | str | None
cat.info()                                                            -> dict
cat.close()                                                           -> None
```

| | |
| --- | --- |
| `datasets()` | Search the datasets — 23,576 of them. `limit=None` for every match, `limit=0` for the count and breakdown with no rows. |
| `data_services()` | Search the 599 data services. Same signature; refuses `format`, `updated`, `place`, `language` and the date filters. |
| `filters()` | Every dataset filter, every value present, count-descending, with labels. ~0.5 s over the whole corpus. |
| `get()` | One record by its URI, from the file. `format="dict"` is local; any other format is one live request for the entry's RDF as text. `None` if nothing matches. |
| `info()` | The file: path, when it was written, how old, how big, and what it holds. |
| `close()` | Release the HTTP connection. Never required; nothing leaks without it. |

`len(cat)` and iterating a `Catalog` give the datasets. Data services come from
`data_services()`.

`get(uri, format=)` accepts `turtle`, `ttl`, `rdf/xml`, `rdfxml`, `xml`,
`n-triples`, `ntriples`, `nt`, `json-ld`, `jsonld`, `trig`, or any media type
passed straight through.

## Building a Catalog

```python
Catalog(path=None, *, refresh="if_missing", stale_after=7, progress="auto",
        workers=8, base_url=DEFAULT_BASE_URL, transport=None)
```

| Argument | Default | |
| --- | --- | --- |
| `path` | the cache directory | The JSONL file. A `.gz` suffix reads and writes gzip. |
| `refresh` | `"if_missing"` | When the file may be written. See below. |
| `stale_after` | `7` | Days before a copy counts as stale. |
| `progress` | `"auto"` | Progress line on a terminal, periodic log otherwise. `None` is silent; a callable gets `(done, total)`. |
| `workers` | `8` | Parallel requests while downloading. |
| `base_url` | `https://admin.dataportal.se` | Another EntryStore registry. |
| `transport` | `requests` | Your own `BaseTransport`, from `dataportalen.core`. |

Everything that writes the file is here. No method rewrites it.

| `refresh=` | Behaviour |
| --- | --- |
| `"if_missing"` | Download only when the file is absent. An old copy is used and reported, never replaced. |
| `"if_stale"` | Also download when it is older than `stale_after` days. |
| `"always"` | Download now, whatever is there. |
| `"never"` | Never download. A missing file raises `FileNotFoundError`. |

Default path: `%LOCALAPPDATA%\dataportalen\catalog.jsonl` on Windows,
`$XDG_CACHE_HOME/dataportalen/catalog.jsonl` or
`~/.cache/dataportalen/catalog.jsonl` elsewhere. `default_catalog_path()`
returns it.

## Dataset filters

Eleven, plus free text and four dates. All combine; all must match. A list
value means any of them will do, except `keyword`, where all must be present.

| Filter | Matches | On |
| --- | --- | --- |
| `publisher=` | the organisation that put it on the portal | 100% |
| `license=` | the licence it is released under | 100% |
| `keyword=` | one of the publisher's own tags, by substring | 94.8% |
| `publisher_type=` | what kind of organisation published it | 92.6% |
| `language=` | the language of the data: `swedish` or `english` | 88.5% |
| `access_rights=` | `public`, `non_public` or `restricted` | 82.3% |
| `theme=` | the subject it is filed under | 78.2% |
| `format=` | a format one of its distributions is in | 69.7% |
| `updated=` | how often the publisher refreshes it | 63.3% |
| `creator=` | the organisation that produced the data | 30.1% |
| `place=` | the area it covers | 19.0% |
| `text=` | title, description or keywords, either language | — |

| Date filter | Which date | On |
| --- | --- | --- |
| `updated_after=`, `updated_before=` | `modified` — when the publisher last changed it | 89.0% |
| `published_after=`, `published_before=` | `issued` — when they first released it | 41.6% |

Dates take `"2024-01-01"`, `"2024-01"`, `"2024"`, or a `date`/`datetime`.

How many distinct values each has in the corpus, and what the commonest is:

| Filter | Values | Commonest |
| --- | --- | --- |
| `keyword` | 23,371 | `Rådet för främjande av kommunala analyser - Kolada` |
| `place` | 542 | `kingdom_of_sweden` |
| `publisher` | 356 | `radet_for_framjande_av_kommunala_analyser_kolada` |
| `creator` | 138 | `statistikmyndigheten_scb_statistiska_centralbyran` |
| `format` | 47 | `json` |
| `theme` | 31 | `population_and_society` |
| `updated` | 18 | `annual` |
| `license` | 9 | `cc0_1_0` |
| `publisher_type` | 8 | `national_authority` |
| `access_rights` | 3 | `public` |
| `language` | 2 | `swedish` |

`cat.filters()` lists them all, live against your copy, with labels.

## Data service filters

Eight, plus free text. **Four dataset filters are refused**, with an error
naming the ones that work.

| Filter | Matches | On |
| --- | --- | --- |
| `access_rights=` | `public`, `non_public` or `restricted` | 97.8% |
| `publisher=` | the organisation that published it | 97.3% |
| `publisher_type=` | what kind of organisation that is | 97.2% |
| `keyword=` | one of the publisher's own tags | 83.5% |
| `service_type=` | what kind of service | 55.9% |
| `theme=` | the subject | 53.8% |
| `license=` | the licence | 51.8% |
| `creator=` | who produced it | 7.8% |
| `text=` | title, description or keywords | — |

| Refused | Why |
| --- | --- |
| `format=` | a data service has no distributions |
| `updated=` | nor an accrual periodicity |
| `place=` | set on 7.8% of the 599 |
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

22 keys, always present. An empty value is `null`, `[]` or `{}` — never a
missing key, so you never need `.get()`.

| Key | Type | Filled |
| --- | --- | --- |
| `uri` | str | 100% |
| `type` | `"dataset"` | 100% |
| `context_id`, `entry_id` | str | 100% |
| `title` | `{"sv": str, "en": str}` | 100% |
| `description` | `{"sv": str, "en": str}` | 100% |
| `publisher` | dict, see below — always a dict, with empty values where no publisher is named (10 datasets) | 100% |
| `license` | short name | 100% |
| `keywords` | `{"sv": [str], "en": [str]}` | 94.8% |
| `distributions` | `[dict]`, see below | 93.0% |
| `languages` | `[short name]` | 89.3% |
| `modified` | ISO date or timestamp | 89.0% |
| `access_rights` | short name | 82.3% |
| `themes` | `[short name]` | 78.2% |
| `contact_points` | `[dict]` | 63.5% |
| `accrual_periodicity` | short name | 63.3% |
| `identifier` | str | 61.0% |
| `landing_page` | url | 56.2% |
| `issued` | ISO date | 41.6% |
| `creators` | `[dict]`, shaped like `publisher` — but one of the 146 is a web page rather than a registry entry, so that one is `{"uri": …}` alone | 30.1% |
| `temporal` | `{"start": …, "end": …}` | 19.0% |
| `spatial` | `[short name]` | 19.0% |

**`publisher` and each of `creators`**

| Key | |
| --- | --- |
| `name` | `{"sv": str, "en": str}` |
| `type` | short name: `national_authority`, `local_authority`, … |
| `identifiers` | `[str]`, usually the organisation number |
| `email`, `homepage` | str or `null` |
| `uri`, `context_id`, `entry_id` | the registry's own identifiers |

**`contact_points`** — `name`, `email`, `uri`.

**Each entry in `distributions`** — 15 keys:

| Key | |
| --- | --- |
| `title`, `description` | `{"sv": str, "en": str}` |
| `format` | short name: `csv`, `json`, `geopackage`, … |
| `access_url` | `[url]` — a page or service to get it through. **Read this one.** |
| `download_url` | `[url]` — a direct file link, on only 7.4% |
| `license`, `availability`, `status` | short names |
| `languages` | `[short name]` |
| `issued`, `modified` | ISO dates |
| `access_service_uris` | `[uri]` — the data service that serves it, where declared |
| `uri`, `context_id`, `entry_id` | the registry's own identifiers |

Of 35,133 distributions: 32,536 carry only `access_url`, 2,592 carry both, 5
carry neither, and none carries `download_url` alone.

## A data service record

20 keys. The same keys mean the same things as on a dataset; `distributions`,
`accrual_periodicity`, `spatial`, `temporal`, `modified` and `issued` are
absent.

| Key | Type | Filled |
| --- | --- | --- |
| `uri`, `type`, `context_id`, `entry_id` | | 100% |
| `title` | `{"sv": str, "en": str}` | 100% |
| `endpoint_url` | url — where to call it | 99.8% |
| `endpoint_urls` | `[url]` — all of them | 99.8% |
| `access_rights` | short name | 97.8% |
| `description` | `{"sv": str, "en": str}` | 97.3% |
| `publisher` | dict | 97.3% |
| `keywords` | `{"sv": [str], "en": [str]}` | 83.5% |
| `contact_points` | `[dict]` | 64.1% |
| `service_type` | short name | 55.9% |
| `themes` | `[short name]` | 53.8% |
| `landing_page` | url | 52.6% |
| `license` | short name | 51.8% |
| `endpoint_descriptions` | `[url]` | 47.6% |
| `conforms_to` | `[url]` — the spec it follows | 29.5% |
| `serves_dataset_uris` | `[uri]` | 8.0% |
| `creators` | `[dict]` | 7.8% |

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
| `QueryError` | your filters were wrong: a value that does not exist, or one that does not apply to what you searched |
| `NotFoundError` | there is no such entry (`get()` returns `None` instead) |
| `RateLimitError` | too many requests; it already retried |
| `TimeoutError` | the registry did not answer in time |
| `TransportError` | the connection failed |
| `ServerError` | the registry itself broke |
| `ParseError` | the registry sent something unreadable |
| `HTTPError` | any other HTTP failure |

`FileNotFoundError` — Python's own — is raised by `refresh="never"` when the
file is not there.

## Everything exported

19 names.

| | |
| --- | --- |
| `Catalog` | the whole surface |
| `default_catalog_path` | where the file goes by default |
| `text` | read a language map |
| `Results`, `Breakdown`, `ValueList`, `ValueCount` | what a search gives you |
| `DataportalError` + the 7 subclasses above | errors |
| `logger`, `enable_logging` | logging |
| `__version__` | |

```python
from dataportalen import enable_logging
enable_logging("DEBUG")       # every request, with status and duration
```

The package logs to a logger named `dataportalen` and never touches your
configuration, so `logging.getLogger("dataportalen")` works if your application
already sets logging up.

The RDF layer — `Q`, `Graph`, `Entry`, `Dataset`, `DataService` — is internal
and not exported; the download is built on it. `BaseTransport` and `Response`
live in `dataportalen.core` for anyone supplying their own HTTP.
[internals.md](internals.md#public-and-internal) says why.
