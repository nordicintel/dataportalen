# Guide

Everything here is ordinary Python. You ask for datasets, you get dictionaries.

1. [Connect](#connect)
2. [Find datasets](#find-datasets)
3. [Narrow the search](#narrow-the-search)
4. [Filter by date](#filter-by-date)
5. [What is in a result](#what-is-in-a-result)
6. [How much comes back](#how-much-comes-back)
7. [What a dataset looks like](#what-a-dataset-looks-like)
8. [The catalogue file](#the-catalogue-file)
9. [Other things in the registry](#other-things-in-the-registry)
10. [Settings](#settings)
11. [When something goes wrong](#when-something-goes-wrong)
12. [Going deeper](#going-deeper)

## Connect

```python
from dataportalen import Dataportal

dp = Dataportal()
```

The first search downloads the whole catalogue — about six minutes and 58 MB,
once — and every search after that runs against that copy in milliseconds.
**That copy is how this package searches.** The registry is asked only for
what the file does not hold.

It is done that way because the registry is slow: it answers about two
requests a second and does not go faster with more of them in flight, at 100
datasets a request. Reading anything substantial over the API takes minutes
every time; reading it once and keeping it takes minutes once.

```python
dp = Dataportal(language="en")   # English where the publisher wrote it
```

Swedish is the default because the registry is Swedish: most datasets are
described in Swedish only. Ask for another language and you get it where the
publisher wrote one, and the Swedish text where they did not.

The client holds an open connection, so close it when you are finished — or
let a `with` block do it:

```python
with Dataportal() as dp:
    ...
```

### The copy on disk

```python
dp.catalog.path         # where it went
dp.catalog.downloaded   # when it was written
dp.catalog.age_days     # how old it is
dp.refresh_catalog()    # download it again
```

The file lives in the usual cache directory —
`%LOCALAPPDATA%\dataportalen\catalog-sv.jsonl` on Windows,
`~/.cache/dataportalen/catalog-sv.jsonl` elsewhere (the language is in the
name, because it is baked into the file) — so one copy serves every
project and nothing lands in a repository. Pass `catalog_path=` to put it
somewhere of your choosing.

The registry re-harvests nightly. After a week the client logs a warning
saying how old the copy is; it never re-downloads on its own, because a
script that answered in a second yesterday should not block for six minutes
today.

Short names are resolved when the file is written, so a copy also keeps the
vocabulary of the package that downloaded it. `dp.refresh_catalog()` after
upgrading picks up any renamed or newly labelled values.

## Find datasets

`dp.datasets()` searches. You get back a list of dictionaries that also knows
the total:

```python
page = dp.datasets(text="cykel")

print(page.total)          # 388 — how many matched
for dataset in page:
    print(dataset["title"])
```

It is an ordinary list, so it slices, and goes straight into anything that
takes records:

```python
import pandas
pandas.DataFrame(dp.datasets(theme="transport", limit=None))
```

`limit` defaults to 50 so a stray search cannot print twenty thousand
dictionaries; `limit=None` gives you everything that matched.

To fetch one dataset whose address you already have:

```python
dataset = dp.dataset(uri="https://catalog.skane.se/rowstore/dataset/9f0e...")
```

That address is the publisher's own identifier for the dataset — the `uri`
field of any result. It comes out of the local copy when it is there, and off
the registry when it is not. A URI nothing matches gives you `None` rather
than an error.

### What the registry is still for

| | |
| --- | --- |
| The catalogue | `datasets()`, `iter_datasets()`, `dataset(uri=...)`, `count_datasets()`, and every breakdown |
| The registry | `distributions()`, `data_services()`, `catalogs()`, `agents()`, `publishers()`, nightly statistics, link checks, quality scores, `lookup()`/`entry()` for the RDF, `search(Q...)` for a raw index query |

The file holds datasets with their distributions, publisher and contacts
nested inside them — so dataset search, and everything counted from it, never
touches the network.

## Narrow the search

Add any of these to `dp.datasets(...)`. They combine, so all of them must match:

```python
page = dp.datasets(theme="transport", format="csv", publisher="trafikverket")
```

Values are short and lowercase. You never type a web address.

**Searching text**

| Filter                 | What it matches                                |
| ---------------------- | ---------------------------------------------- |
| `text="cykel"`         | anywhere in the title, description or keywords |
| `title="bidrag"`       | the title only                                 |
| `description="vägnät"` | the description only                           |
| `keyword="geodata"`    | one of the keywords the publisher attached     |

**Picking a category**

| Filter                      | What it matches                      |
| --------------------------- | ------------------------------------ |
| `publisher="trafikverket"`  | the organisation that published it   |
| `publisher_type="local_authority"` | what kind of organisation that is |
| `theme="transport"`         | the subject it is filed under        |
| `format="csv"`              | a file format you can download it in |
| `license="cc_by_4_0"`       | the licence it is released under     |
| `access_rights="public"`    | whether it is open to everyone       |
| `updated="annual"`          | how often the publisher refreshes it |
| `language="swedish"`        | the language of the data: `swedish` or `english` |
| `place="kingdom_of_sweden"` | the area it covers                   |

`publisher_type` takes `national_authority`, `local_authority`,
`regional_authority`, `academia_scientific_organisation`,
`non_governmental_organisation`, `company` and a few more —
`known_values("publisher_type")` lists them.

`language` is `swedish` or `english` and nothing else. The registry holds 66
distinct language values, but 62 of them cover about 106 datasets between
them (multilingual dictionaries, language corpora); they stayed on each
dataset's own `languages` list and out of the filter.

Give a list instead of one value and any of them will do:

```python
dp.datasets(format=["csv", "xlsx"])        # either format
dp.datasets(keyword=["geodata", "trafik"]) # but keywords must all be present
```

The same filters work on `dp.distributions()`, `dp.data_services()` and the
other searches, wherever they make sense.

## Filter by date

```python
dp.datasets(updated_after="2024-01-01")
```

| Filter                                | Which date                               |
| ------------------------------------- | ---------------------------------------- |
| `updated_after`, `updated_before`     | when the publisher last changed the data |
| `published_after`, `published_before` | when the publisher first released it     |

Write the date however is convenient — `"2024-01-01"`, `"2024-01"`, `"2024"`,
or Python's own `date` and `datetime` objects.

Both dates are the publisher's. The registry also records when it last copied a
dataset in, but that happens nightly for nearly everything, so filtering on it
would tell you about the registry's schedule rather than about the data. It is
deliberately not offered.

## What is in a result

Every search comes back knowing what it is made of. `page.breakdown` counts
each filter over **everything that matched**, not just the rows you are
holding, biggest first:

```python
page = dp.datasets(text="cykel")
page.total                      # 388

for value, count in page.breakdown["publisher"][:3]:
    print(count, value)
```

```text
213 radet_for_framjande_av_kommunala_analyser_kolada
51 trafikverket
19 uppsala_universitet
```

Every filter is there, keyed by the name you would search with:

```python
page.breakdown["theme"]          # [('population_and_society', 233), ...]
page.breakdown["format"]         # [('json', 246), ('html', 51), ...]
page.breakdown["license"]        # [('cc0_1_0', 201), ...]
page.breakdown["access_rights"]  # [('public', 366), ...]
page.breakdown["updated"]        # [('annual', 140), ...]
page.breakdown["language"]       # [('swedish', 388), ...]
page.breakdown["place"]          # [('kingdom_of_sweden', 44), ...]
page.breakdown["keyword"]        # [('Kommun', 213), ...]
```

And every value is one you feed straight back in to narrow the search:

```python
dp.datasets(text="cykel", publisher="trafikverket")
```

So a breakdown is both the answer to "what can I filter by?" and the answer
to "what is in this result?". With no filters at all it describes the whole
catalogue:

```python
dp.datasets(limit=0).breakdown["theme"]   # every theme in the registry
```

Counts are per dataset — a dataset with three CSV files counts once under
`format` → `csv`. `page.breakdown.to_dict()` gives `{filter: {value: count}}`
for JSON, and `page.breakdown.top("theme")` the commonest value.

Some of these lists are long: 12,968 distinct keywords, 541 places. Cap them
with `breakdown_limit`, and what was cut is counted rather than dropped
silently:

```python
page = dp.datasets(limit=0, breakdown_limit=10)
page.breakdown["keyword"]           # the top 10
page.breakdown["keyword"].omitted   # 12958
page.breakdown.omitted              # {'keyword': 12958, 'place': 531, ...}
```

### Before you search

`known_values` lists the names the package knows without a search or a
network call:

```python
from dataportalen import known_values, known_publishers

known_values("access_rights")   # ['non_public', 'public', 'restricted']
known_values("theme", "trans")  # ['transport', 'transport_networks', 'transportation']
known_publishers("trafikv")     # ['trafikverket']
```

And if you just guess, the error corrects you:

```python
dp.datasets(theme="transprot")
# QueryError: unknown theme 'transprot'. Did you mean: transport?
```

## How much comes back

Locally there are no pages: a search scans the whole catalogue and `limit`
just says how much of the result you want to hold.

```python
dp.datasets(theme="transport")             # the first 50
dp.datasets(theme="transport", limit=None) # all 545
dp.datasets(theme="transport", limit=10, offset=100)
```

`page.total` is how many matched and `page.has_more` says whether you are
holding all of them.

`iter_datasets()` gives you the same records one at a time, which is the
better shape for a loop over thousands:

```python
for dataset in dp.iter_datasets(theme="transport"):
    print(dataset["title"])
```

There is no paging and no estimate: `total` is exactly how many matched, and
the file does not move while you read it. (The registry itself caps a page at
100 and reports an estimate, which is one more reason searches run against
the file.)

## What a dataset looks like

Every search result, and every dataset you fetch, is the same dictionary —
21 keys, all of them here:

```json
{
  "uri": "https://example.org/data/roads",
  "context_id": "50",
  "entry_id": "28672",

  "title": "Vägtrafiknät",
  "description": "Nationell vägdatabas ...",
  "keywords": ["vägnät", "trafik"],
  "identifier": "NVDB-2020",
  "landing_page": "https://example.org/roads",

  "themes": ["transport"],
  "license": "cc_by_4_0",
  "access_rights": "public",
  "accrual_periodicity": "annual",
  "languages": ["swedish"],
  "spatial": ["kingdom_of_sweden"],

  "issued": "2020-03-04",
  "modified": "2024-05-06T09:00:00+02:00",
  "temporal": {"start": "2015-01-01", "end": "2024-12-31"},

  "publisher": {
    "name": "Trafikverket",
    "type": "national_authority",
    "identifiers": ["2021006297"],
    "email": "data@trafikverket.se",
    "homepage": "https://trafikverket.se",
    "uri": "http://dataportal.se/organisation/SE2021006297",
    "context_id": "1", "entry_id": "5"
  },
  "creator_uris": [],
  "contact_points": [{"name": "Datasupport", "email": "data@example.org"}],

  "distributions": [
    {"title": "Vägnät CSV",
     "download_url": ["https://...csv"],
     "access_url": [],
     "format": "csv",
     "license": "cc_by_4_0",
     "availability": "stable"}
  ]
}
```

Anything the publisher left out is `null` or `[]`, never missing. Measured
over the whole catalogue, how often each is actually filled: `title`,
`publisher` and `license` 100%, `keywords` 95%, `distributions` 93%,
`modified` 89%, `themes` 78%, `contact_points` 64%, `identifier` 61%,
`landing_page` 56%, `issued` 42%, `spatial` and `temporal` 19%.

Four things worth knowing:

**The filter is singular, the field is plural.** You search `theme="transport"`
and read `dataset["themes"]`. Four of them differ this way:

| Filter | Field on the dataset |
| --- | --- |
| `theme=` | `themes` |
| `language=` | `languages` |
| `place=` | `spatial` |
| `updated=` | `accrual_periodicity` |

The rest — `publisher`, `license`, `access_rights`, `format`, `keyword` — read
as you would guess. A breakdown is keyed by the *filter* name, because that is
what you feed back in.

**Everything from a fixed list of options is one short English word.**
`themes`, `license`, `access_rights`, `languages`, the publisher's `type` —
these come from standard vocabularies, and you get `"transport"` rather than
the web address the registry publishes. The same word works as a filter, which
is the point. They stay English whatever language you chose, because
`"annual"` is more useful to build on than _årligen_.

**The files live under `distributions`.** One entry per download the publisher
offers. `download_url` is the file itself; `access_url` is a page or service
you go through to get it. Both are lists, because publishers give more than
one often enough that a scalar would lie.

**`modified` is the publisher's date**, matching the `updated_after` filter —
useful for "what changed since I last looked". `issued` is first publication.

It is a plain dictionary, so `json.dumps` it, put a list of them in a
dataframe, or pull out what you need.

### Every language at once

Some datasets really are described in several languages. To keep all of them,
ask for `"all"`, and the text fields become maps instead of strings:

```python
dp = Dataportal(language="all")
dataset = dp.datasets(text="cykel")[0]
dataset["title"]
# {"sv": "Cykelstråk", "en": "Cycle routes"}
```

That applies to `title`, `description`, `keywords` and the publisher's `name` —
the fields publishers write themselves. The language is fixed when you build the client, and the local copy is
written in it — switching languages on a client that has already downloaded
the catalogue means downloading it again.

## The catalogue file

The copy the client downloads is an ordinary JSONL file: one dataset per line,
each line complete on its own, with its distributions, publisher and contacts
already nested. Nothing stops you reading it yourself:

```python
import json

with open(dp.catalog.path, encoding="utf-8") as f:
    for line in f:
        dataset = json.loads(line)
```

To write one somewhere of your own — for another tool, a pipeline, or an
archive — call the download directly:

```python
from dataportalen import download_catalog

download_catalog("catalog.jsonl")                  # ~6 minutes, 58 MB
download_catalog("catalog.jsonl.gz")               # compressed, from the suffix
download_catalog("sample.jsonl", limit=500)        # just 500, to try it out
download_catalog("catalog.jsonl", progress=None)   # no progress line
download_catalog("catalog.jsonl", progress=print)  # your own progress handler

summary = download_catalog("catalog.jsonl")
summary.datasets, summary.distributions, summary.elapsed
```

The last full run wrote 23,580 datasets and 35,151 distributions in 354
seconds. It reads the live search index rather than the registry's nightly
RDF dump, which would be one request but has been seen lagging by a week.

To search a file you already have, without a client:

```python
from dataportalen import LocalCatalog

catalog = LocalCatalog("catalog.jsonl", download=False)
catalog.datasets(publisher="trafikverket", updated_after="2024-01-01")
catalog.datasets().breakdown["keyword"]   # what the registry cannot count
```

`download=False` is for a program that must not reach the network: a missing
file then raises instead of fetching 58 MB.

## Other things in the registry

**Every publisher, with the registry's own count.** `dp.publishers()` is the
one list the file cannot give you, because the registry counts differently
from your copy:

```python
for row in dp.publishers()[:3]:
    print(row.dataset_count, row.publisher, "-", row.name)
```

```text
5920 radet_for_framjande_av_kommunala_analyser_kolada - Rådet för främjande av kommunala analyser - Kolada
4315 statistikmyndigheten_scb_statistiska_centralbyran - Statistikmyndigheten SCB
2248 goteborgs_universitet - Göteborgs universitet
```

`row.publisher` is the value you filter with, so a listing leads straight into
a search. The counts come from a chart the registry rebuilds nightly, so they
run slightly ahead of your copy — `dp.datasets(limit=0).breakdown["publisher"]`
is the same list counted over the file. `dp.registry_totals()` gives the
whole-registry numbers.

**The other entity types.** Datasets are what the catalogue holds; these are
searched live, take the same filters, and return the same dicts:

```python
dp.distributions()          # the files, as entries in their own right
dp.data_services()          # APIs rather than files
dp.catalogs()               # one per harvested source
dp.agents()                 # organisations and people
```

**What the registry says about itself:**

```python
dp.catalog_statistics(limit=30)  # dataset counts per night, newest first
dp.link_check_reports()          # which download links still resolve
dp.metadata_quality()            # the registry's own DCAT-AP quality scores
```

## Settings

All optional:

```python
dp = Dataportal(
    language="sv",            # "sv", "en", any code, or "all" for every one
    timeout=30.0,             # seconds to wait for a response
    max_retries=3,            # retry a failed or rate-limited request
    log_level="INFO",         # see what the client is doing
)
```

`log_level="DEBUG"` prints every request with its status and duration, which is
the fastest way to find out why something is slow. If your application already
configures logging, use it — the client logs to a logger named `dataportalen`
and never touches your setup:

```python
import logging
logging.getLogger("dataportalen").setLevel(logging.DEBUG)
```

## When something goes wrong

Every error this package raises comes from `DataportalError`, so one `except`
catches all of them:

```python
from dataportalen import DataportalError

try:
    page = dp.datasets(theme="transport")
except DataportalError as error:
    print(error)
```

When you want to react differently to different failures:

| Error            | Means                                                              |
| ---------------- | ------------------------------------------------------------------ |
| `QueryError`     | your filters were wrong — a value that does not exist, usually     |
| `NotFoundError`  | there is no such entry (looking one up by URI gives`None` instead) |
| `RateLimitError` | too many requests; the client already retried                      |
| `TimeoutError`   | the registry did not answer in time                                |
| `TransportError` | the connection failed                                              |
| `ServerError`    | the registry itself broke                                          |
| `ParseError`     | the registry sent something unreadable                             |

Two things that are not bugs in this package: the registry rebuilds its search
index nightly, so data can be up to a day behind what a publisher has actually
published; and result counts are estimates, as described under
[how much comes back](#how-much-comes-back).

## Going deeper

Skip this section unless a plain dictionary is genuinely not enough.

The registry describes everything in RDF — a graph format where values are web
addresses instead of words. This package exists so you do not have to deal with
that, but it does not hide it either.

**The original data behind a dataset.** `dp.lookup()` gives you the model
object rather than a dict — it always asks the registry, because the graph is
not in the local file:

```python
entry = dp.lookup("https://example.org/data/roads")

entry.raw_json()      # exactly what the registry sent
entry.to_rdf()        # the same, as a graph
entry.to_rdf_dict()   # every field the publisher supplied, including ones
                      # the dict leaves out
entry.to_dict()       # back to the dict a search would have given you
```

**A file in another format**, when you want to feed it to a proper RDF library:

```python
dp.entry_raw(entry.context_id, entry.entry_id, format="text/turtle").text
```

**A search this package cannot express.** The registry's index is Solr, and `Q`
writes Solr queries for you, escaping as needed:

```python
from dataportalen import Q

dp.search(Q.text("cykel") & ~Q.tag("historisk"))   # returns model objects
```

`search()` is the only search that hands back model objects rather than dicts,
and it always asks the registry.

Combine with `&` (and), `|` (or) and `~` (not). `Q.raw("...")` passes a
fragment through untouched if you know the query language.

**The rest of the surface**, in one place, for when you need it:

```python
dp.datasets(catalog=50)              # one catalogue, by its number
dp.datasets(uri="https://...")       # a dataset by its own address
dp.datasets(query="title.en:*")      # a raw index query, as above

dp.lookup("https://...")             # any entry, whatever type it turns out to be
dp.lookup_many([...])                # several at once, batched into few requests
dp.count(Q.rdf_type(DCAT.Dataset))   # count anything Q can express
dp.download_catalog("mine.jsonl")    # a copy where you want it

Dataportal(
    base_url="https://admin.dataportal.se",  # another EntryStore registry
    public_only=True,                        # only entries the public can read
    cache_size=512,                          # how many lookups to remember
    transport=None,                          # your own BaseTransport
)
```

Set the `DATAPORTAL_USER_AGENT` environment variable to identify your program to
the registry, which is polite if you are making a lot of requests.

---

Contributing to this package, or curious where the short values come from?
See [internals.md](internals.md).
