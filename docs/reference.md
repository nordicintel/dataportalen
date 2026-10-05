# Reference

Everything the package exposes, as tables. [guide.md](guide.md) is the part
you read; this is the part you look up. [cheatsheet.md](cheatsheet.md) is the
same surface as code: every argument once, the output underneath.

Percentages are measured over the whole registry — every dataset and every
data service, not a sample. The registry grows by a handful a day, so treat
the absolute counts as "as of 2026-10-05" and the percentages as stable.
What changed from 0.12 is in the [CHANGELOG](../CHANGELOG.md), with an
old-to-new table.

1. [The methods](#the-methods)
2. [Building a Catalog](#building-a-catalog)
3. [Dataset filters](#dataset-filters)
4. [Data service filters](#data-service-filters)
5. [Publisher filters](#publisher-filters)
6. [Dataset](#dataset)
7. [Distribution](#distribution)
8. [DataService](#dataservice)
9. [Publisher](#publisher)
10. [License, Contact, Temporal, LinkMark](#license-contact-temporal-linkmark)
11. [MultilingualText and Keywords](#multilingualtext-and-keywords)
12. [What a search returns](#what-a-search-returns)
13. [Errors](#errors)
14. [Everything exported](#everything-exported)

## The methods

```python
catalog.search(query=None, *, limit=None, offset=0, facet_limit=None,
               live=False, as_dict=False, **filters)       -> SearchResult
catalog.datasets(*, live=False, as_dict=False, **filters)      -> list[Dataset]
catalog.data_services(*, live=False, as_dict=False, **filters) -> list[DataService]
catalog.publishers(*, live=False, as_dict=False, **filters)    -> list[Publisher]
catalog.publisher(value, *, as_dict=False)                     -> Publisher | None
catalog.facets(limit=None, *, as_dict=False)                   -> Facets
catalog.get(uri, format="dict", *, as_dict=False)              -> Dataset | DataService | str | None
catalog.info()                                                 -> dict
catalog.close()                                                -> None
```

`query`, `value`, `uri`, `format` and the `limit` of `facets()` can be
passed by position; everything else is keyword-only.

| | |
| --- | --- |
| `search()` | Find datasets, and count what every match is made of. With no `limit` the result holds every match — all 23,422 with no filter, in ~2.2 s, most of it turning records into models; a filtered search is hundredths of a second. `limit=0` for the count and the facets alone. The facets always count every match, not the window. Datasets only. |
| `datasets()` | Every dataset the filters select, as a list: 23,422 with none, 23,582 under `exclude_broken=False`. No query, window or facets — a `query`, `limit`, `offset` or `facet_limit` is a `QueryError` pointing at `search()`. |
| `data_services()` | Every data service the filters select: 599. Its own [filters](#data-service-filters); the same refusal of `query` and a window. |
| `publishers()` | Every publisher with something that matches, most datasets first: 346 with no filter, 356 unscoped. The [filters](#publisher-filters) narrow what is counted. |
| `publisher()` | One publisher by id, alias, name, organisation number or URI, with its counts and the facets of its datasets. `None` if it has nothing here. |
| `facets()` | Every dataset facet: each filter's values, count-descending, with labels. Exactly `search(limit=0).facets`. About 0.7 s. |
| `get()` | One dataset or data service by its URI. `format="dict"` — the default, despite the name — is the model, from the database; any other format is one live request for the entry's RDF as text. `None` if nothing matches. Where two records share a URI, the first. |
| `info()` | The database and what this `Catalog` holds of it. See [below](#building-a-catalog). |
| `close()` | Release the HTTP connection. Never required; nothing leaks without it. A `Catalog` is also a context manager. |

`as_dict=True` returns `to_dict()` of what the call would have returned: a
dict for a model or a `SearchResult`, a list of dicts for a list, `None`
still `None`. `facets(as_dict=True)` is `Facets.to_dict()`. A type checker
sees which one you get.

`len(catalog)` is the number of datasets, and iterating a `Catalog` yields
`Dataset` models. Data services come from `data_services()`.

| Property | |
| --- | --- |
| `database` | the SQLite file; `None` on a live `Catalog` |
| `max_age`, `exclude_broken`, `language`, `live` | as given to the constructor |
| `first_retrieved` | when this copy was first built, ISO timestamp |
| `last_refreshed` | when it was last brought up to date — what a refresh asks from |
| `downloaded` | `last_refreshed` as a `datetime`; `None` live |
| `age_days` | whole days since then; `None` live |

`info()` on the default catalogue:

```python
{"database": "...\\dataportalen\\catalog.sqlite", "live": False,
 "first_retrieved": "2026-09-30T17:57:14",
 "last_refreshed": "2026-10-01T13:04:54",
 "downloaded": "2026-10-01T13:04:54", "age_days": 4, "bytes": 98951168,
 "datasets": 23422, "data_services": 599, "publishers": 346,
 "excluded": {"dead_distributions": 903, "dead_datasets": 160},
 "stale_datasets": ..., "unverified_distributions": 10858,
 "sources": {"total": 666, "succeeded": 239, "failed": 427,
             "failed_holding_records": [...]}}
```

| Key | |
| --- | --- |
| `datasets`, `data_services` | what this `Catalog` holds |
| `publishers` | `len(publishers())`: organisations, not URIs |
| `excluded` | what `exclude_broken` left out: `dead_distributions`, and `dead_datasets` that had nothing else. Both 0 under `exclude_broken=False` |
| `stale_datasets` | held datasets whose source catalogue failed its latest harvest |
| `unverified_distributions` | held distributions the registry's checker could not reach |
| `sources` | the registry's harvest status, [below](#building-a-catalog); `None` for a database written before it was read |

**Live.** `Catalog(live=True)` has the same methods with the same
arguments, asked of the registry instead of a database. Nothing is
downloaded, and it warns `DataportalWarning` when built. On a local
`Catalog`, `search()`, `datasets()`, `data_services()` and `publishers()`
take `live=True` for one call; `publisher()`, `facets()` and `get()` follow
the `Catalog`. Live and local return the same models. What differs:

| | Live |
| --- | --- |
| Paging | the registry serves 100 to a page, a few seconds each. An unpaged call fetches every page, and warns `DataportalWarning` above 1,000 records |
| Filters | `kind` and `publisher_type` are refused on datasets and data services; `publisher_type` works on `publishers()`, where it is read off the publisher |
| Facets | no `keyword`, `publisher_type` or `kind`; `publisher()` facets have no `kind` |
| `query` | the registry's full-text search: a phrase of whole words anywhere in the entry, not a substring of title, description and keywords |
| Link health | none: nothing is excluded, and nothing carries `broken`, `unverified` or `stale`. Distributions do carry `kind` |
| Order | URI order |
| `info()` | `{"datasets", "data_services", "publishers", "live": True, "sources": None}` |
| `len()`, iteration | one count request; every page |

`live=True` on a `Catalog(live=True)` changes nothing, and warns that it
does. [cheatsheet.md](cheatsheet.md) compares live answers with local ones,
value by value.

`get(uri, format=)` accepts `turtle`, `ttl`, `rdf/xml`, `rdfxml`, `xml`,
`n-triples`, `ntriples`, `nt`, `json-ld`, `jsonld`, `trig`, or any media type
passed straight through.

## Building a Catalog

```python
Catalog(database=None, *, max_age=7, rebuild=False,
        exclude_broken=True, language="sv", live=False)
```

| Argument | Default | |
| --- | --- | --- |
| `database` | the cache directory | The SQLite file. |
| `max_age` | `7` | Days. A copy older than this — or missing — is brought up to date when the object is built: a download if there is nothing, otherwise an incremental refresh of what the registry has touched since, under a minute. `None`: use what is there, download only if there is nothing. A negative number or a bool is a `QueryError`. |
| `rebuild` | `False` | Fetch everything again now. What a schema change asks for, and the only thing that drops a dataset the registry has withdrawn. |
| `exclude_broken` | `True` | Drop every dead distribution, and every dataset left with none. See below. `False` keeps everything and marks each dead distribution `broken`. |
| `language` | `"sv"` | Which language `MultilingualText.text()` and `Keywords.list()` pick first: `"sv"` or `"en"`, falling back to the other. Anything else is a `QueryError`. Nothing else changes. |
| `live` | `False` | Ask the registry for every call instead of reading a database. See [above](#the-methods). With `database=` or `rebuild=True` it is a `QueryError`. |

Everything that writes the records is here. No public method rewrites them.

| What is there | `max_age=7` | `max_age=None` | `rebuild=True` |
| --- | --- | --- | --- |
| nothing | download | download | download |
| a copy under 7 days old | use it | use it | fetch everything |
| a copy over 7 days old | refresh what changed, ~40 s | use it | fetch everything |

Building a `Catalog(max_age=None)` from a copy on disk takes ~2.4 s.

Default path: `%LOCALAPPDATA%\dataportalen\catalog.sqlite` on Windows,
`$XDG_CACHE_HOME/dataportalen/catalog.sqlite` or
`~/.cache/dataportalen/catalog.sqlite` elsewhere. `default_catalog_path()`
returns it.

The database always holds the whole registry, and the catalogue holds every
access level: `access_rights=` is a [filter](#dataset-filters) on each call.
`exclude_broken` decides what this `Catalog` holds of the database, so
changing it is a new `Catalog`, not a new download. `info()["datasets"]` is
the count it holds.

**What dead means.** The registry checks every distribution's URL nightly and
calls 11,761 of 35,148 `broken`. It records no status code for those, only a
message, and the message is one of two things:

| The registry's message | Distributions | Here |
| --- | --- | --- |
| an HTTP error: Not Found 395, Forbidden 212, Internal Server Error 40, Bad Request 33, Unauthorized 14, Access Denied 8, … | 718 | **dead** — `broken`; dropped by `exclude_broken` |
| a host that is not in DNS: `request to … failed, reason: getaddrinfo ENOTFOUND …` | 185 | **dead** — `broken`; dropped by `exclude_broken` |
| no usable answer: no message 5,263, `request to … failed` for any other reason 2,698, Too Many Requests 2,624, `timeout` 255, `maximum redirect` 11, an `ftp://` URL with credentials 7 | 10,858 | **`unverified`** — kept, always |

Only the first is a server saying no. The third is the registry's checker
failing to get through — 7,091 of Statistics Sweden's 14,228 links, whose
server resets the checker's connection while answering 200 to anyone else.
The list of HTTP errors is explicit, and so is the one DNS message; any other
message is unverified.

Under the default, 160 datasets go because every distribution they have is
dead. A dataset that never had distributions (1,647: APIs, registers) is not
dead and stays.

`broken` and `unverified` are the registry's verdicts. A maintainer tool,
`Catalog._verify()`, asks the servers themselves and stores the answers:
alive removes the mark, dead sets `broken` with `by: "local"`. It is not
public and nothing calls it; [internals.md](internals.md) describes it.

**Stale is not dead.** At the end of every download or refresh the registry's
latest harvest result for each source catalogue is read. A dataset or data
service from a source whose latest harvest failed carries
`stale = LinkMark(reason="harvest failed", checked=…)`. It is marked, never
removed: the data may be fine; the record is what the last good harvest left.
Of 666 sources, 427 failed their latest harvest (2026-10-04), but only 8 of
those have datasets in the registry — 81 datasets.

`info()["sources"]`:

| Key | |
| --- | --- |
| `total` | the source catalogues the registry harvests |
| `succeeded`, `failed` | how their latest harvest went |
| `failed_holding_records` | one row per failed source that still holds something in this `Catalog`, most datasets first: `context_id` (what `dataset.context_id` holds), `title` (or `None`), `status`, `harvested` (when the registry last tried), `dataset_count`, `data_service_count` |

`None` for a database written before the harvest status was read; the next
refresh fills it.

## Dataset filters

Eight, plus `modified_after` and, in `search()` only, `query`. All combine;
all must match. A list value means any of them will do. `search()` and
`datasets()` take the same filters.

| Filter | Matches | On |
| --- | --- | --- |
| `publisher=` | the organisation that put it on the portal — id, alias, name, organisation number or URI | 100% |
| `keyword=` | one of the publisher's own tags, exactly, ignoring case | 94.8% |
| `kind=` | what one of its distributions is: a file, an API, a web page | 93.0% |
| `publisher_type=` | what kind of organisation published it | 92.6% |
| `access_rights=` | `public`, `non_public` or `restricted` — or `none`, for a dataset that sets nothing | 82.3%; `none` the other 17.7% |
| `theme=` | the subject it is filed under | 78.2% |
| `format=` | a format one of its distributions is in | 69.7% |
| `accrual_periodicity=` | how often the publisher refreshes it: `annual`, `monthly`, … | 63.3% |
| `modified_after=` | `modified` on or after the date — or, for a dataset with no `modified`, `issued` | 91.8% |
| `query=` | `search()` only: a phrase in the title, description or keywords, either language, ignoring case | — |

`modified_after` takes `"2024-01-01"`, `"2024-01"`, `"2024"`, or a
`date`/`datetime`, and includes the day given. `modified` is on 89.0% of
datasets; the fallback to `issued` adds the 651 that were issued and never
modified. `modified_after="2025"` matches 12,708. A date that is not a real
date is a `QueryError`.

`query` is a phrase, not a bag of words: `query="air quality"` finds the 15
datasets with those two words together, not the hundreds that have both
somewhere. `search("cykel")` finds 386.

**Removed.** These were filters in 0.12. Each is now a `QueryError` that
says what to do instead; every record still carries the fields.

| Filter | Instead |
| --- | --- |
| `updated=` | `accrual_periodicity=` |
| `license=` | `dataset.license.id`, in Python |
| `language=` | `dataset.languages`, in Python |
| `issued_after=` | `modified_after=`, which reads `issued` where there is no `modified` |
| `issued_before=`, `modified_before=` | `dataset.modified`, in Python |
| `text=` | `query=` |

How many distinct values each has, unscoped (`exclude_broken=False`) and in
the default catalogue, and what the commonest is:

| Filter | Values (unscoped) | Values (default) | Commonest |
| --- | --- | --- | --- |
| `keyword` | 21,359 | 21,105 | `Rådet för främjande av kommunala analyser - Kolada` |
| `publisher` | 356 | 345 | `radet_for_framjande_av_kommunala_analyser_kolada` (5,863) |
| `format` | 47 | 45 | `json` |
| `theme` | 31 | 31 | `population_and_society` |
| `accrual_periodicity` | 18 | 18 | `annual` (11,591) |
| `kind` | 11 | 11 | `pxweb` (6,704) |
| `publisher_type` | 8 | 8 | `national_authority` |
| `access_rights` | 3 | 3 | `public` (17,555) |

`catalog.facets()` lists them all, live against your copy, with labels.

**`access_rights`** in the default catalogue: `public` 17,555, `non_public`
1,489, `restricted` 241, and `none` 4,137 — mostly universities. `none` is
not a facet value; it is asked for by name. `access_rights="public"` is what
0.12's default catalogue held.

**`keyword`** is an ordinary filter. `Kommun`, `kommun` and `KOMMUN` are one
keyword (the registry has 23,373 spellings of 21,359 keywords);
`keyword="Kommun"` matches 4,617. A facet shows each as the publisher spelt
it — the commonest spelling, the first alphabetically on a tie. An unknown
keyword raises; use `query=` for a substring.

**`kind`** is what a distribution is, read from its metadata with no request.
Any distribution matches, as with `format=`. Always one of eleven values;
anything else raises.

| Value | Is | Distributions (unscoped) | Datasets (default) |
| --- | --- | --- | --- |
| `pxweb` | a PxWeb statistical table | 10,468 | 6,704 |
| `kolada` | a Kolada key figure | 5,963 | 5,952 |
| `doi` | a DOI, resolving to a research-data landing page | 5,021 | 5,021 |
| `file` | a file to download: CSV, Excel, JSON, PDF, a ZIP | 4,647 | 2,479 |
| `huwise` | a Huwise (Opendatasoft) dataset: records and exports | 3,520 | 334 |
| `geodata` | a map service or a geo file | 2,794 | 1,098 |
| `web_page` | a web page; the data is behind it, not at it | 1,405 | 1,053 |
| `rowstore` | EntryScape's rowstore: a table behind an API | 552 | 460 |
| `unknown` | the metadata does not say | 324 | 297 |
| `ckan` | a CKAN resource or its API | 310 | 80 |
| `api` | an API described by a data service | 144 | 143 |

Read strongest signal first: a geodata format; the shape of the address
(rowstore, CKAN, Huwise, PxWeb, Kolada, DOI); a `download_url`, which is a
`file` unless its format is `html` and the address has no file extension, when
it is a `web_page`; the format, for an `access_url`; an access service with
nothing else to go on. It says what the publisher described, not what the
server returns.

**`publisher`** takes whatever a `Publisher` shows: its `id`, its `alias`
(`scb`, `fohm`, `slu`, `smhi`, `uhr`, `kolada`, `energimyndigheten` — kept in
[publishers.json](../src/dataportalen/publishers.json), at most one per
publisher), a name in either language, an organisation number as written, or
a URI. An alias that would collide with an id, an organisation number or
another alias is left out. `datasets(publisher="scb", kind="pxweb")` is 4,284
datasets.

**`publisher_type`** reads the type on each record's own publisher. Two
agents for one organisation can disagree — the one on 1,668 of
Folkhälsomyndigheten's datasets states no type — so it is not always
`publisher(id).type`.

## Data service filters

Six. **The distribution filters, `accrual_periodicity`, the date and
`query` are refused**, with an error naming the ones that work.

| Filter | Matches | On | Values |
| --- | --- | --- | --- |
| `access_rights=` | `public`, `non_public` or `restricted`, or `none` | 97.8% | 3 |
| `publisher=` | the organisation that published it | 97.3% | 31 |
| `publisher_type=` | what kind of organisation that is | 97.2% | 3 |
| `keyword=` | one of the publisher's own tags | 83.5% | 448 |
| `service_type=` | what kind of service | 55.9% | 5 |
| `theme=` | the subject | 53.8% | 12 |

| Refused | Why |
| --- | --- |
| `format=`, `kind=` | a data service has no distributions |
| `accrual_periodicity=` | nor an accrual periodicity |
| `modified_after=` | `modified` on 7.5%, `issued` on 0.8% |
| `query=` | `data_services()` is a list, not a search; `search()` searches datasets |

`service_type=` takes five values:

| Value | Count | |
| --- | --- | --- |
| `rest` | 288 | a web API |
| `view_service` | 31 | INSPIRE view, i.e. WMS |
| `download_service` | 14 | INSPIRE download |
| `transformation_service` | 1 | INSPIRE |
| `discovery_service` | 1 | INSPIRE |

A keyword or publisher that only datasets carry is not an error here; it
matches nothing.

## Publisher filters

Five: the filters datasets and data services share, so a publisher's two
counts answer the same question.

| Filter | Counts |
| --- | --- |
| `publisher=` | that publisher, or those |
| `publisher_type=` | publishers of that kind |
| `theme=` | datasets and data services filed under it |
| `keyword=` | datasets and data services tagged with it |
| `access_rights=` | datasets and data services with that level, or `none` |

`publishers(**filters)` counts what the filters select: `dataset_count` is
`search(publisher=id, limit=0, **filters).total` and `data_service_count` is
`len(data_services(publisher=id, **filters))`. A publisher with nothing that
matches is not listed. `publishers(publisher_type="local_authority")` is 73
publishers. Any other filter is a `QueryError` naming the five.

Locally, `publisher_type` reads each record's own publisher, as on datasets;
live, it reads the publisher.

## Dataset

22 fields, always present. Read by attribute — `dataset["title"]` is a
`TypeError`. An empty value is `None`, `[]` or an empty `MultilingualText` or
`Keywords` — never a missing attribute, and `to_dict()` has every key.
`stale` is `None` unless it says something.

| Field | Type | Filled |
| --- | --- | --- |
| `uri` | str | 100% |
| `context_id`, `entry_id` | str — the registry's own id, what `get(format="turtle")` sends | 100% |
| `type` | `"dataset"` | 100% |
| `title` | `MultilingualText` | 100% |
| `description` | `MultilingualText` | 100% |
| `keywords` | `Keywords`; empty when there are none | 94.8% |
| `identifier` | str or `None` | 61.0% |
| `landing_page` | url or `None` | 56.2% |
| `publisher` | `Publisher`, see [below](#publisher) — always one, with every field `None` where no publisher is named | 100% |
| `themes` | `[short name]` | 78.2% |
| `license` | `License` or `None`, see [below](#license-contact-temporal-linkmark) | 100% |
| `access_rights` | short name or `None` | 82.3% |
| `accrual_periodicity` | short name or `None` | 63.3% |
| `languages` | `[ISO code]` — `sv`, `en`, or one of 63 others. Not a filter | 89.3% |
| `spatial` | `[short name]`. Not a filter | 19.0% |
| `temporal` | `Temporal` or `None` | 19.0% |
| `issued` | ISO date or `None` | 41.6% |
| `modified` | ISO date or timestamp, or `None` | 89.0% |
| `contact_points` | `[Contact]` | 63.5% |
| `distributions` | `[Distribution]`, see [below](#distribution) | 93.0% |
| `stale` | `LinkMark(reason="harvest failed", checked)` — only when its source catalogue failed its latest harvest | 81 datasets |

## Distribution

17 fields, always present.

| Field | | Filled |
| --- | --- | --- |
| `uri` | | 100% |
| `title`, `description` | `MultilingualText` | 97.8%, 30.0% |
| `access_url` | url or `None` — a page or service to get it through. **Read this one.** | 100% |
| `download_url` | url or `None` — a direct file link | 7.4% |
| `format` | short name: `csv`, `json`, `geopackage`, … | 82.9% |
| `license` | `License` or `None` | 67.8% |
| `status`, `availability` | short names | 9.5%, 43.6% |
| `languages` | `[ISO code]` | 44.2% |
| `issued`, `modified` | ISO dates | 16.6%, 17.7% |
| `access_service_uris` | `[uri]` — the data service that serves it, where declared | 21.6% |
| `kind` | what it is: `file`, `pxweb`, `web_page`, … — one of the eleven under [Dataset filters](#dataset-filters) | 100% |
| `broken` | `LinkMark` — a dead distribution; only under `exclude_broken=False` or from `read_catalog`. `by` is `"local"` when the verdict is the maintainers' own and not the registry's | 2.6% |
| `unverified` | `LinkMark` — a distribution the registry's checker could not reach | 30.9% |
| `byte_size` | int — only where the publisher states one | 1.4% |

Of 35,148 distributions: 32,548 carry only `access_url`, 2,595 carry both, 5
carry neither, and none carries `download_url` alone. 147 name more than one
access URL and 130 more than one download URL; the first is what you get.

`byte_size` is on 484 distributions. The registry holds a size for 485; one
of them is a date. Sizes written `5 420 000` are read.

## DataService

19 fields. The same names mean the same things as on a dataset, `stale`
included; there are no `distributions`, `accrual_periodicity`, `languages`,
`spatial`, `temporal`, `identifier`, `modified` or `issued`.

| Field | Type | Filled |
| --- | --- | --- |
| `uri`, `context_id`, `entry_id` | str | 100% |
| `type` | `"data_service"` | 100% |
| `title` | `MultilingualText` | 100% |
| `publisher` | `Publisher` | 100% |
| `endpoint_url` | url — where to call it | 99.8% |
| `access_rights` | short name or `None` | 97.8% |
| `description` | `MultilingualText` | 97.3% |
| `keywords` | `Keywords` | 83.5% |
| `contact_points` | `[Contact]` | 64.1% |
| `service_type` | short name or `None` | 55.9% |
| `themes` | `[short name]` | 53.8% |
| `landing_page` | url or `None` | 52.6% |
| `license` | `License` or `None` | 51.8% |
| `endpoint_description` | url — the API documentation | 47.6% |
| `conforms_to` | `[url]` — the spec it follows | 29.5% |
| `serves_datasets` | `[uri]` | 8.0% |
| `stale` | `LinkMark` or `None` | |

Four of the 599 name more than one endpoint URL and one more than one
description; the first is what you get.

The registry's link check never tests `endpointURL`, so a data service
carries no `broken` or `unverified` and `exclude_broken` leaves them alone.

## Publisher

11 fields.

| Field | |
| --- | --- |
| `id` | what `publisher=` takes and the `publisher` facet reports; `None` on the 27 records that name no publisher |
| `uri` | the registry's URI for the agent |
| `name` | `MultilingualText` |
| `alias` | its one short name from [publishers.json](../src/dataportalen/publishers.json) (`"scb"`), or `None` for most |
| `type` | short name: `national_authority`, `local_authority`, … |
| `homepage`, `email` | str or `None` |
| `identifiers` | `[str]`, usually the organisation number |
| `dataset_count` | from `publishers()` and `publisher()`: `search(publisher=id, limit=0).total`, with the filters if any |
| `data_service_count` | likewise: `len(data_services(publisher=id))` |
| `facets` | from `publisher()` only: a `Facets` over `theme`, `format`, `kind`, `access_rights`, `accrual_periodicity` — exactly `search(publisher=id, limit=0).facets` for those five |

A publisher nested in a dataset or data service is that record's own agent,
with `dataset_count`, `data_service_count` and `facets` `None`; `to_dict()`
still has the three keys. `id` and `alias` are added when a record is read,
not stored, so a new alias shows up without a rebuild.

`publisher("skolverket")` has 6 datasets and 5 data services;
`publisher("scb")` is `statistikmyndigheten_scb_statistiska_centralbyran`,
4,306 and 25.

| | |
| --- | --- |
| Identity | `id`, not `uri`: 8 of 356 publishers have two URIs, and both find the same publisher. |
| Attributes | Those of one real registry agent — the URI the package's table knows, then the one on most records — never a merge of two. |
| Scope | Who a publisher is does not depend on `exclude_broken`; the two counts and the facets do. A publisher with nothing in this catalogue is not listed. |
| Not found | `None` for an organisation with nothing here; `QueryError`, with suggestions, for a value nobody knows. |
| Order | Most datasets first, then by `id`. The rows that have datasets are `facets()["publisher"]`, row for row. |

## License, Contact, Temporal, LinkMark

**`License`** — `dataset.license`, `distribution.license`

| Field | |
| --- | --- |
| `id` | short name: `cc_by_4_0`, `nolicense`, … |
| `label` | `MultilingualText` — `nolicense` and `otherlicense` are DIGG's own categories, on 39% of datasets, and have labels too |
| `uri` | the licence page |

`dataset.license` is `None` on 10 of 23,582 datasets. `license` is not a
filter: filter on `dataset.license.id` in Python.

**`Contact`** — each of `contact_points`

| Field | |
| --- | --- |
| `uri` | str or `None` |
| `name` | str or `None` — a person or a function |
| `email` | str or `None` |

**`Temporal`** — `dataset.temporal`

| Field | |
| --- | --- |
| `start`, `end` | ISO dates; either may be `None` |

**`LinkMark`** — `broken`, `unverified`, `stale`

| Field | |
| --- | --- |
| `reason` | the registry's message, `"harvest failed"` for `stale`, or a local verdict's reason |
| `checked` | when: the link check, or the harvest |
| `by` | `"local"` on a verdict the maintainers' own check stored; otherwise `None` |

Every model above, and `Dataset`, `Distribution`, `DataService`,
`Publisher` and `SearchResult`, has `to_dict()` — every field, in order, as
JSON-ready values, nested models too — and `str(model)` is
`json.dumps(model.to_dict(), indent=4, ensure_ascii=False)`. The models are
dataclasses, with typed fields for an editor and a type checker.

## MultilingualText and Keywords

```python
MultilingualText(values=None, lang="sv")
text.text(lang=None) -> str | None
```

Text a publisher wrote: `{"sv": ..., "en": ...}`, either or both. 53% of
datasets are Swedish only, 36% carry both, 10% are English only, so
`title["sv"]` raises `KeyError` for one in ten and `.text()` does not.

`.text()` takes `lang`, else the catalogue's language
(`Catalog(language=)`), else the other one, else whatever there is:

| Value | `.text()` | `.text("en")` |
| --- | --- | --- |
| `{"sv": "Vägtrafiknät", "en": "Road"}` | `"Vägtrafiknät"` | `"Road"` |
| `{"en": "Road"}` | `"Road"` | `"Road"` |
| `{"sv": "Vägtrafiknät"}` | `"Vägtrafiknät"` | `"Vägtrafiknät"` |
| `{}` | `None` | `None` |

Under `Catalog(language="en")`, `.text()` is `.text("en")`.

It is a read-only mapping of language to string: `["sv"]`, `in`, `len`,
iteration over the languages. It compares equal to the plain dict,
`to_dict()` is that dict, and `str()` is its JSON.

```python
Keywords(values=None, lang="sv")
keywords.list(lang=None) -> list[str]
keywords.all()           -> list[str]
```

| | |
| --- | --- |
| `.list()` | the keywords in `lang`, else the catalogue's language, else the other; `[]` when there are none |
| `.all()` | every keyword in either language, each once, in order |
| `["sv"]`, `to_dict()` | a mapping of language to list; equal to the plain dict |

## What a search returns

`SearchResult`:

| | |
| --- | --- |
| `.datasets` | `[Dataset]`: every match, or the window you asked for |
| `.facets` | a `Facets` over every match, not the window |
| `.total` | how many matched, exactly — not an estimate |
| `.offset`, `.limit` | what you asked for; `limit` is `None` for every match |
| `.has_more` | whether more matched than `datasets` holds past `offset` |
| `len(result)`, iteration | the datasets |
| `.to_dict()` | `{"total", "offset", "limit", "datasets", "facets", "facets_omitted"}` — what `as_dict=True` returns |

With no `limit`, `len(result) == result.total - result.offset`.
`search(theme="transport", format="csv").total` is 57.

`Facets` is a mapping keyed by filter name — exactly the dataset filters:
`publisher`, `publisher_type`, `theme`, `keyword`, `format`, `kind`,
`access_rights`, `accrual_periodicity`.

| | |
| --- | --- |
| `facets["theme"]` | a `Facet`: a list of `FacetValue`, count-descending |
| `facets["theme"].omitted` | how many values `facet_limit` cut from that one |
| `facets.omitted` | `{filter: how many values were cut}`, for the ones that were |
| `facets.top("theme")` | the commonest value, or `None` |
| `facets.to_dict()` | `{filter: [{"value", "count", "label"}]}`, JSON-ready, biggest first |
| `facets.counts()` | `{filter: {value: count}}`, the compact form without labels |
| `"format" in facets` | whether that filter has a facet here |
| `facets["nope"]` | a `QueryError` naming the facets there are |

Facet sizes over the default catalogue: `keyword` 21,105, `publisher` 345,
`format` 45, `theme` 31, `accrual_periodicity` 18, `kind` 11,
`publisher_type` 8, `access_rights` 3.

`FacetValue` is a `(value, count)` pair — it unpacks and compares equal to a
plain tuple — with `.label` alongside as a `MultilingualText`: the
vocabulary's name for a controlled value, the publisher's name for a
publisher, empty for a keyword or a `kind`, which is its own label. The label
is not part of the tuple, so `_replace` and pickling drop it.
`FacetValue.to_dict()` is `{"value", "count", "label"}`; `Facet.to_dict()` is
a list of those.

Counts are per dataset: a dataset with three CSV distributions counts once
under `format` → `csv`.

`str()` of a `SearchResult`, `Facets`, `Facet` or `FacetValue` is the JSON of
its `to_dict()`.

## Errors

Every error derives from `DataportalError`.

| Error | Means |
| --- | --- |
| `QueryError` | your call was wrong: a filter value that does not exist, a filter that does not apply to what you asked for, a [removed filter](#dataset-filters) (the message says what to use instead), a window on a list method, a bad window, or a `Catalog` argument out of range |
| `NotFoundError` | there is no such entry (`get()` returns `None` instead) |
| `RateLimitError` | too many requests; it already retried |
| `TimeoutError` | the registry did not answer in time |
| `TransportError` | the connection failed |
| `ServerError` | the registry itself broke |
| `ParseError` | the registry sent something unreadable, or the database is empty or from another schema (`rebuild=True`) |
| `HTTPError` | any other HTTP failure |

`TimeoutError` is a `TransportError`; `NotFoundError`, `RateLimitError` and
`ServerError` are `HTTPError`s.

`DataportalWarning` is a `UserWarning`, not an error: something worked, but
probably not the way you meant it to. It is warned when a
`Catalog(live=True)` is built, when a live call is about to fetch more than
1,000 records page by page, and when `live=True` is passed to a
`Catalog(live=True)`.

`FileNotFoundError` — Python's own — is raised by `read_catalog()` on a path
that is not there.

## Everything exported

30 names.

| | |
| --- | --- |
| `Catalog` | the catalogue, from a local copy or the registry |
| `default_catalog_path` | where the database goes by default |
| `read_catalog` | every record in a database as plain dicts, unscoped, without building a `Catalog`; `broken`, `unverified`, `byte_size` and `stale` only where set |
| `Dataset`, `DataService`, `Distribution`, `Publisher` | the four models |
| `SearchResult` | what `search()` returns |
| `MultilingualText`, `Keywords` | text and keywords in either language |
| `License`, `Contact`, `Temporal`, `LinkMark` | the small models |
| `Facets`, `Facet`, `FacetValue` | facets |
| `DataportalError` + the 8 subclasses above | errors |
| `DataportalWarning` | the warning |
| `logger`, `enable_logging` | logging |
| `__version__` | |
