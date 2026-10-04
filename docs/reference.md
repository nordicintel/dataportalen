# Reference

Everything the package exposes, as tables. [guide.md](guide.md) is the part
you read; this is the part you look up. [cheatsheet.md](cheatsheet.md) is the
same surface as code: every argument once, the output underneath.

Percentages are measured over the whole corpus — every dataset and every data
service, not a sample. The registry grows by a handful a day, so treat the
absolute counts as "as of 2026-10-01" and the percentages as stable.

1. [The methods](#the-methods)
2. [Building a Catalog](#building-a-catalog)
3. [Dataset filters](#dataset-filters)
4. [Data service filters](#data-service-filters)
5. [A dataset record](#a-dataset-record)
6. [A data service record](#a-data-service-record)
7. [A publisher](#a-publisher)
8. [What a search returns](#what-a-search-returns)
9. [text()](#text)
10. [Typed records](#typed-records)
11. [Errors](#errors)
12. [Everything exported](#everything-exported)

## The methods

```python
catalog.datasets(query=None, limit=50, offset=0, facet_limit=None, **filters)      -> Results
catalog.data_services(query=None, limit=50, offset=0, facet_limit=None, **filters) -> Results
catalog.facets(limit=None)                                                         -> Facets
catalog.publishers()                                                               -> list[dict]
catalog.publisher(value)                                                           -> dict | None
catalog.get(uri, format="dict")                                                    -> dict | str | None
catalog.info()                                                                     -> dict
catalog.sources()                                                                  -> list[dict]
catalog.verify(which="unverified", *, limit=None)                                  -> dict
catalog.close()                                                                    -> None
```

Every argument is keyword-only except `value`, `uri` and `which`.

| | |
| --- | --- |
| `datasets()` | Search the datasets the catalogue holds — 17,555 by default, 23,582 unscoped. `limit=None` for every match, `limit=0` for the count and facets with no rows. |
| `data_services()` | Search the data services — 578 by default, 599 unscoped. Same signature; refuses `format`, `updated`, `language` and the date filters. |
| `facets()` | Every dataset facet: each filter's values, count-descending, with labels. ~0.3 s over the default catalogue. |
| `publishers()` | Every publisher this catalogue holds something from — 287 by default, 356 unscoped — most datasets first. |
| `publisher()` | One publisher by id, alias, name, organisation number or URI, with the facets of its datasets. `None` if it has nothing here. |
| `get()` | One record by its URI, from the file. `format="dict"` is local; any other format is one live request for the entry's RDF as text. `None` if nothing matches. |
| `info()` | The database: `database`, `first_retrieved`, `last_refreshed`, age, size, what this `Catalog` holds, and what its scope left out: `excluded` (`access_rights`, `dead_distributions`, `dead_datasets`), `stale_datasets`, `unverified_distributions`. |
| `sources()` | The source catalogues the registry harvests, one row each, with how the latest harvest went. Most datasets first. See [below](#building-a-catalog). |
| `verify()` | Ask the publishers' servers about the distributions the registry's checker could not reach, and keep the answers. Opt-in: the only call that sends a request to a publisher, and nothing calls it for you. See [below](#building-a-catalog). |
| `close()` | Release the HTTP connection. Never required; nothing leaks without it. |

`len(catalog)` and iterating a `Catalog` give the datasets. Data services come
from `data_services()`.

`LiveCatalog(access_rights=("public",))` has the same methods with the same
arguments, asked of the registry instead of a file: `limit` is 0 to 100,
`publisher_type` and `kind` are refused, facets have no `keyword`,
`publisher_type` or `kind`, nothing carries `broken`/`unverified`/`stale`,
`info()` is the three counts, and there is no `sources()`, `verify()`, `len()`
or iteration. Its distributions do carry `kind`. [cheatsheet.md](cheatsheet.md#livecatalog) compares
its answers with `Catalog`'s, value by value.

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
| `exclude_broken` | `True` | Drop every dead distribution, and every dataset left with none. See below. `False` keeps everything and marks each dead distribution `broken`. |
| `access_rights` | `("public",)` | Which `access_rights` values the catalogue holds: `public`, `non_public`, `restricted`, and `none` for the 4,167 that set nothing. A list, one string, or `None` for all. |

Everything that writes the records is here. No method rewrites them;
`verify()` stores its verdicts beside them.

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

**What dead means.** The registry checks every distribution's URL nightly and
calls 11,761 of 35,148 `broken`. It records no status code for those, only a
message, and the message is one of two things:

| The registry's message | Distributions | Here |
| --- | --- | --- |
| an HTTP error: Not Found 395, Forbidden 212, Internal Server Error 40, Bad Request 33, Unauthorized 14, Access Denied 8, … | 718 | **dead** — `broken`; dropped by `exclude_broken` |
| a host that is not in DNS: `request to … failed, reason: getaddrinfo ENOTFOUND …` | 185 | **dead** — `broken`; dropped by `exclude_broken` |
| no usable answer: no message 5,263, `request to … failed` for any other reason 2,698, Too Many Requests 2,624, `timeout` 255, `maximum redirect` 11, an `ftp://` URL with credentials 7 | 10,858 | **`unverified`** — kept, always |

Only the first is a server saying no. The second is the registry's checker
failing to get through — 7,091 of Statistics Sweden's 14,228 links, whose
server resets the checker's connection while answering 200 to anyone else.
The list of HTTP errors is explicit, and so is the one DNS message; any other
message is unverified.

Under the default, 127 public datasets go because every distribution they have
is dead. A dataset that never had distributions (1,647: APIs, registers) is not
dead and stays.

**Asking again: `verify()`.** The 10,858 are the registry's checker failing to
get through, so `catalog.verify()` asks the servers from here. It is opt-in
and never called implicitly.

```python
catalog.verify(which="unverified", *, limit=None)
# {"checked", "alive", "dead", "unverified", "invalid_cert", "requests", "elapsed"}
```

| Argument | Default | |
| --- | --- | --- |
| `which` | `"unverified"` | Which distributions to ask about, within the catalogue's `access_rights`: `"unverified"`, `"broken"` or `"all"`. Anything else is a `QueryError`. |
| `limit` | `None` | At most this many addresses, taken one per host in turn, so a sample is spread over the publishers. |

| What the server does | Verdict |
| --- | --- |
| answers `HEAD` below 400 | **alive** |
| answers `HEAD` with an error | asked again with a one-byte ranged `GET`; only what the `GET` says counts |
| `404`, `410` | **dead** — reason `Not Found`, `Gone` |
| redirects to the page the site sends every unknown path to | **dead** — reason `soft 404` |
| no answer after one retry, and the host is not in DNS | **dead** — reason `host not found` |
| no answer after one retry, and the host is in DNS | **unverified** |
| `429` | **unverified**, and nothing more is asked of that host in this run; nor after two addresses in a row that got no answer |
| any other error status: `400`, `401`, `403`, `503`, … | **unverified**, with the status phrase as the reason |
| a certificate error | asked again without verification; the verdict stands and is counted under `invalid_cert` |

Dead is narrower here than for the registry's verdicts: a WMS endpoint answers
`400` to a bare address, and that is a server that is there. One request at a
time per host with a 0.4 s pause, 8 hosts at once.

A verdict is applied when a record is read. Alive removes the registry's mark.
Dead sets `broken` with `by: "local"`, and `exclude_broken` drops it like any
other. Against the registry's `unverified` a local answer always wins; against
its `broken` the later look wins. A local answer that is itself unverified
changes nothing. Verdicts are kept across refreshes and dropped by
`rebuild=True`.

Measured on a sample of 200 addresses over all access levels, 2026-10-04: 91
alive, 19 dead (all Not Found), 90 still unverified, 290 requests, 157 s. All
10,858 is about 11,000 requests and most of an hour, because 7,091 of them are
one host (api.scb.se).

**Stale is not dead.** At the end of every download or refresh the registry's
latest harvest result for each source catalogue is read. A dataset or data
service from a source whose latest harvest failed carries
`stale: {"reason": "harvest failed", "checked": …}`. It is marked, never
removed: the data may be fine; the record is what the last good harvest left.
Of 666 sources, 427 failed their latest harvest (2026-10-04), but only 8 of
those have datasets in the registry — 81 datasets unscoped, 1 in the default
catalogue.

`sources()` returns one row per source, most datasets first:

| Key | |
| --- | --- |
| `context_id` | the source — what `record["context_id"]` holds |
| `status` | `"success"` or `"failed"` |
| `harvested` | when the registry last harvested it, or tried to |
| `title` | the source catalogue's name, or `null` |
| `dataset_count`, `data_service_count` | what this `Catalog` holds from it; zero for most failed sources, which never yielded a dataset |

Empty for a database written before the harvest status was read; the next
refresh fills it.

## Dataset filters

Ten, plus `query` and four dates. All combine; all must match. A list value
means any of them will do.

| Filter | Matches | On |
| --- | --- | --- |
| `publisher=` | the organisation that put it on the portal — id, alias, name, organisation number or URI | 100% |
| `license=` | the licence's `id` | 100% |
| `keyword=` | one of the publisher's own tags, exactly, ignoring case | 94.8% |
| `kind=` | what one of its distributions is: a file, an API, a web page | 93.0% |
| `publisher_type=` | what kind of organisation published it | 92.6% |
| `language=` | the language of the data, as an ISO code: `sv`, `en`, … | 89.3% |
| `access_rights=` | `public`, `non_public` or `restricted` | 82.3% |
| `theme=` | the subject it is filed under | 78.2% |
| `format=` | a format one of its distributions is in | 69.7% |
| `updated=` | how often the publisher refreshes it | 63.3% |
| `query=` | a phrase in the title, description or keywords, either language, ignoring case | — |

| Date filter | Which field | On |
| --- | --- | --- |
| `modified_after=`, `modified_before=` | `modified` — when the publisher last changed it | 89.0% |
| `issued_after=`, `issued_before=` | `issued` — when they first released it | 41.6% |

Dates take `"2024-01-01"`, `"2024-01"`, `"2024"`, or a `date`/`datetime`.

`query` is a phrase, not a bag of words: `query="air quality"` finds the 14
datasets with those two words together, not the hundreds that have both
somewhere.

How many distinct values each has, over the whole registry and over the
default catalogue, and what the commonest is:

| Filter | Values (all) | Values (default) | Commonest |
| --- | --- | --- | --- |
| `keyword` | 21,359 | 16,053 | `Rådet för främjande av kommunala analyser - Kolada` |
| `publisher` | 356 | 287 | `radet_for_framjande_av_kommunala_analyser_kolada` |
| `language` | 65 | 55 | `sv` |
| `format` | 47 | 36 | `json` |
| `kind` | 11 | 10 | `pxweb` |
| `theme` | 31 | 31 | `government_and_public_sector` |
| `updated` | 18 | 18 | `annual` |
| `license` | 9 | 9 | `cc0_1_0` |
| `publisher_type` | 8 | 8 | `national_authority` |
| `access_rights` | 3 | 1 | `public` |

`catalog.facets()` lists them all, live against your copy, with labels.

**`keyword`** is an ordinary filter since 0.11.0. `Kommun`, `kommun` and
`KOMMUN` are one keyword (the registry has 23,373 spellings of 21,359
keywords). A facet shows each as the publisher spelt it — the commonest
spelling, the first alphabetically on a tie. An unknown keyword raises; use
`query=` for a substring.

**`kind`** is what a distribution is, read from its metadata with no request.
Any distribution matches, as with `format=`. Always one of eleven values;
anything else raises.

| Value | Is | Distributions (all) | Datasets (default) |
| --- | --- | --- | --- |
| `pxweb` | a PxWeb statistical table | 10,468 | 6,704 |
| `kolada` | a Kolada key figure | 5,963 | 5,952 |
| `doi` | a DOI, resolving to a research-data landing page | 5,021 | 1,734 |
| `file` | a file to download: CSV, Excel, JSON, PDF, a ZIP | 4,647 | 2,103 |
| `huwise` | a Huwise (Opendatasoft) dataset: records and exports | 3,520 | — |
| `geodata` | a map service or a geo file | 2,794 | 945 |
| `web_page` | a web page; the data is behind it, not at it | 1,405 | 850 |
| `rowstore` | EntryScape's rowstore: a table behind an API | 552 | 421 |
| `unknown` | the metadata does not say | 324 | 225 |
| `ckan` | a CKAN resource or its API | 310 | 78 |
| `api` | an API described by a data service | 144 | 2 |

Read strongest signal first: a geodata format; the shape of the address
(rowstore, CKAN, Huwise, PxWeb, Kolada, DOI); a `download_url`, which is a
`file` unless its format is `html` and the address has no file extension, when
it is a `web_page`; the format, for an `access_url`; an access service with
nothing else to go on. It says what the publisher described, not what the
server returns.

**`publisher`** takes whatever a publisher object shows: its `id`, an alias
(`scb`, `fohm`, `slu`, `smhi`, `uhr`, `kolada`, `energimyndigheten` — kept in
[aliases.json](../src/dataportalen/aliases.json)), a name in either language,
an organisation number as written, or a URI.

**`publisher_type`** reads the type on each record's own publisher. Two
agents for one organisation can disagree — the one on 1,668 of
Folkhälsomyndigheten's datasets states no type — so it is not always
`publisher(id)["type"]`.

## Data service filters

Seven, plus `query`. **Four dataset filters and the dates are refused**, with
an error naming the ones that work.

| Filter | Matches | On |
| --- | --- | --- |
| `access_rights=` | `public`, `non_public` or `restricted` | 97.8% |
| `publisher=` | the organisation that published it | 97.3% |
| `publisher_type=` | what kind of organisation that is | 97.2% |
| `keyword=` | one of the publisher's own tags | 83.5% |
| `service_type=` | what kind of service | 55.9% |
| `theme=` | the subject | 53.8% |
| `license=` | the licence's `id` | 51.8% |
| `query=` | a phrase in the title, description or keywords | — |

| Refused | Why |
| --- | --- |
| `format=`, `kind=` | a data service has no distributions |
| `updated=` | nor an accrual periodicity |
| `language=` | one single value across all 599 |
| the four date filters | `modified` on 7.5%, `issued` on 0.8% |

`service_type=` takes five values (unscoped counts):

| Value | Count | |
| --- | --- | --- |
| `rest` | 288 | a web API |
| `view_service` | 31 | INSPIRE view, i.e. WMS |
| `download_service` | 14 | INSPIRE download |
| `transformation_service` | 1 | INSPIRE |
| `discovery_service` | 1 | INSPIRE |

A keyword or publisher that only datasets carry is not an error here; it
matches nothing.

## A dataset record

21 keys, always present. An empty value is `null`, `[]` or `{}` — never a
missing key, so you never need `.get()`. One more, `stale`, is there only when
it says something.

| Key | Type | Filled |
| --- | --- | --- |
| `uri` | str | 100% |
| `type` | `"dataset"` | 100% |
| `context_id`, `entry_id` | str — the registry's own id, what `get(format="turtle")` sends | 100% |
| `title` | `{"sv": str, "en": str}` | 100% |
| `description` | `{"sv": str, "en": str}` | 100% |
| `publisher` | dict, see below — always a dict, with empty values where no publisher is named | 100% |
| `license` | `{"id", "label", "uri"}`, see below | 100% |
| `keywords` | `{"sv": [str], "en": [str]}`; `{}` when there are none | 94.8% |
| `distributions` | `[dict]`, see below | 93.0% |
| `languages` | `[ISO code]` — `sv`, `en`, or one of 63 others | 89.3% |
| `modified` | ISO date or timestamp | 89.0% |
| `access_rights` | short name | 82.3% |
| `themes` | `[short name]` | 78.2% |
| `contact_points` | `[{"uri", "name", "email"}]` | 63.5% |
| `accrual_periodicity` | short name | 63.3% |
| `identifier` | str | 61.0% |
| `landing_page` | url | 56.2% |
| `issued` | ISO date | 41.6% |
| `temporal` | `{"start": …, "end": …}` or `null` | 19.0% |
| `spatial` | `[short name]` — not a filter | 19.0% |
| `stale` | `{"reason": "harvest failed", "checked"}` — only when its source catalogue failed its latest harvest | 81 datasets |

**`license`**

| Key | |
| --- | --- |
| `id` | short name, what `license=` takes: `cc_by_4_0`, `nolicense`, … |
| `label` | `{"sv": str, "en": str}` — `nolicense` and `otherlicense` are DIGG's own categories, on 39% of datasets, and have labels too |
| `uri` | the licence page |

**`publisher`** — 8 keys

| Key | |
| --- | --- |
| `id` | what `publisher=` takes and the `publisher` facet reports; `null` on the 27 records that name no publisher |
| `uri` | the registry's URI for the agent on this record |
| `name` | `{"sv": str, "en": str}` |
| `aliases` | `[str]` — its short names from `aliases.json`; `[]` for most |
| `type` | short name: `national_authority`, `local_authority`, … |
| `homepage`, `email` | str or `null` |
| `identifiers` | `[str]`, usually the organisation number |

`id` and `aliases` are added when a record is read, not stored, so a new alias
shows up without a rebuild. The rest is that record's own agent.

**Each entry in `distributions`** — 14 keys, plus up to two of three more:

| Key | | Filled |
| --- | --- | --- |
| `uri` | | 100% |
| `title`, `description` | `{"sv": str, "en": str}` | 97.8%, 30.0% |
| `format` | short name: `csv`, `json`, `geopackage`, … | 82.9% |
| `kind` | what it is: `file`, `pxweb`, `web_page`, … — one of the eleven under [Dataset filters](#dataset-filters) | 100% |
| `access_url` | url or `null` — a page or service to get it through. **Read this one.** | 100% |
| `download_url` | url or `null` — a direct file link | 7.4% |
| `license` | `{"id", "label", "uri"}` or `null` | 67.8% |
| `availability`, `status` | short names | 43.6%, 9.5% |
| `languages` | `[ISO code]` | 44.2% |
| `issued`, `modified` | ISO dates | 16.6%, 17.7% |
| `access_service_uris` | `[uri]` — the data service that serves it, where declared | 21.6% |
| `broken` | `{"reason", "checked"}` — a dead distribution; only in a `Catalog(exclude_broken=False)` or from `read_catalog`. Also `"by": "local"` when the verdict is `verify()`'s and not the registry's | 9.5% |
| `unverified` | `{"reason", "checked"}` — a distribution the registry's checker could not reach | 24.0% |
| `byte_size` | int — only where the publisher states one | 1.4% |

Of 35,148 distributions: 32,548 carry only `access_url`, 2,595 carry both, 5
carry neither, and none carries `download_url` alone. 147 name more than one
access URL and 130 more than one download URL; the first is what you get.

`byte_size` is on 484 distributions. The registry holds a size for 485; one of them is
a date. Sizes written `5 420 000` are read.

## A data service record

18 keys. The same keys mean the same things as on a dataset, the optional
`stale` included; `distributions`, `accrual_periodicity`, `spatial`,
`temporal`, `modified` and `issued` are absent.

| Key | Type | Filled |
| --- | --- | --- |
| `uri`, `type`, `context_id`, `entry_id` | | 100% |
| `title` | `{"sv": str, "en": str}` | 100% |
| `publisher` | dict, 8 keys as above | 100% |
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
description; the first is what you get.

The registry's link check never tests `endpointURL`, so a data service carries
no `broken` or `unverified`, `exclude_broken` leaves them alone, and `verify()`
does not ask about them.

## A publisher

`publishers()` returns rows of 10 keys: the 8 of a nested `publisher` plus

| Key | |
| --- | --- |
| `dataset_count` | `== datasets(publisher=id, limit=0).total` |
| `data_service_count` | `== data_services(publisher=id, limit=0).total` |

`publisher(value)` returns that row plus

| Key | |
| --- | --- |
| `facets` | `{filter: {value: count}}` over `theme`, `format`, `kind`, `license`, `access_rights`, `updated`, `language` — exactly `datasets(publisher=id, limit=0).facets.to_dict()` for those seven |

| | |
| --- | --- |
| Identity | `id`, not `uri`: 8 of 356 publishers have two URIs, and both find the same publisher. |
| Attributes | Those of one real registry agent — the URI the package's table knows, then the one on most records — never a merge of two. |
| Scope | Who a publisher is does not depend on `access_rights` or `exclude_broken`; the two counts and the facets do. A publisher with nothing in this catalogue is not listed. |
| Not found | `None` for an organisation with nothing here; `QueryError`, with suggestions, for a value nobody knows. |
| Order | Most datasets first, then by `id`. The rows that have datasets are `facets()["publisher"]`, row for row. |

## What a search returns

`Results` is a `list` of dicts with four extras:

| | |
| --- | --- |
| `.total` | how many matched, exactly — not an estimate |
| `.offset`, `.limit` | what you asked for |
| `.has_more` | whether you are holding all of them |
| `.facets` | a `Facets` over everything that matched |

`Facets` is a mapping keyed by filter name:

| | |
| --- | --- |
| `facets["theme"]` | a `Facet`: a list of `FacetValue`, count-descending |
| `facets["theme"].omitted` | how many values `facet_limit` cut from that one |
| `facets.omitted` | `{filter: how many values were cut}`, for the ones that were |
| `facets.top("theme")` | the commonest value, or `None` |
| `facets.to_dict()` | `{filter: {value: count}}`, JSON-ready |
| `"format" in facets` | whether that filter applies here |

`FacetValue` is a `(value, count)` pair — it unpacks and compares equal to a
plain tuple — with `.label` alongside as `{"sv": …, "en": …}`: the vocabulary's
name for a controlled value, the publisher's name for a publisher, `{}` for a
keyword or a `kind`, which is its own label. The label is not part of the tuple, so
`_replace` and pickling drop it.

Counts are per record: a dataset with three CSV distributions counts once under
`format` → `csv`. On a data-service search they count data services.

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

## Typed records

Every dict has a `TypedDict`, importable from `dataportalen`. They change
nothing at run time; an editor and a type checker read them.

| Type | Is |
| --- | --- |
| `DatasetRecord` | what `datasets()` and `get()` return; `stale` optional |
| `DataServiceRecord` | what `data_services()` returns; `stale` optional |
| `DistributionRecord` | one of `dataset["distributions"]`; `broken`, `unverified` and `byte_size` optional |
| `SourceRecord` | a `sources()` row |
| `PublisherRecord` | `record["publisher"]` |
| `Publisher`, `PublisherDetail` | a `publishers()` row; what `publisher()` returns |
| `LicenseRecord`, `ContactRecord`, `TemporalRecord`, `LinkMark` | the small ones; `LinkMark` is `{reason, checked}`, with `by` only on a local verdict |
| `LanguageMap`, `KeywordMap` | `{sv, en}` text and `{sv: [...], en: [...]}` keywords, either key or both |

`Results` is generic: `datasets()` is `Results[DatasetRecord]`. The shapes are
checked against every record of the real catalogue — keys and the values under
them — on Python 3.9 and up.

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

34 names.

| | |
| --- | --- |
| `Catalog` | the catalogue, from a local copy |
| `LiveCatalog` | the same methods, asked of the registry |
| `default_catalog_path` | where the database goes by default |
| `read_catalog` | every record in a database, unscoped, without building a `Catalog` |
| `text` | read a language map |
| `Results`, `Facets`, `Facet`, `FacetValue` | what a search gives you |
| the 13 record types above | shapes, for editors and type checkers |
| `DataportalError` + the 8 subclasses above | errors |
| `logger`, `enable_logging` | logging |
| `__version__` | |
