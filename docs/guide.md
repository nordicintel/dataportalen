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
12. [Async](#async)
13. [Going deeper](#going-deeper)

## Connect

```python
from dataportalen import Dataportal

dp = Dataportal()
```

The first search downloads the whole catalogue — about six minutes and 58 MB,
once — and every search after that runs against that copy in milliseconds.
This is the normal way to use the package.

It is done that way because the registry is slow: it answers about two
requests a second and does not go faster with more of them in flight, at 100
datasets a request. Reading anything substantial over the API takes minutes
every time; reading it once and keeping it takes minutes once.

```python
dp = Dataportal(language="en")   # English where the publisher wrote it
dp = Dataportal(local=False)     # search the registry instead, no download
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
`%LOCALAPPDATA%\dataportalen\catalog.jsonl` on Windows,
`~/.cache/dataportalen/catalog.jsonl` elsewhere — so one copy serves every
project and nothing lands in a repository. Pass `catalog_path=` to put it
somewhere of your choosing.

The registry re-harvests nightly. After a week the client logs a warning
saying how old the copy is; it never re-downloads on its own, because a
script that answered in a second yesterday should not block for six minutes
today.

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

### What runs locally, and what does not

| | |
| --- | --- |
| Local | `datasets()`, `iter_datasets()`, `dataset(uri=...)`, `count_datasets()` |
| Registry | `distributions()`, `data_services()`, `catalogs()`, `agents()`, `standards()`, statistics, link checks, quality scores, and any search using `query=` or `facet_fields=` |

The file holds datasets with their distributions, publisher and contacts
nested inside them, so a dataset search never needs the network and anything
else still does. You do not have to remember which is which — the results are
the same dicts either way.

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
| `theme="transport"`         | the subject it is filed under        |
| `format="csv"`              | a file format you can download it in |
| `license="cc_by_4_0"`       | the licence it is released under     |
| `access_rights="public"`    | whether it is open to everyone       |
| `updated="annual"`          | how often the publisher refreshes it |
| `language="swedish"`        | the language of the data itself      |
| `place="kingdom_of_sweden"` | the area it covers                   |

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

Against the registry (`local=False`) the counts arrive with the search in the
same request, except `keyword`: the index stores keywords by fragment, so it
can only count pieces of words. Locally there is no such limit.

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

Against the registry (`local=False`) the same two calls page for you, 100 at a
time — that is the registry's cap, not this package's; ask for 1000 and it
answers with 100. There `total` is the index's estimate and can be slightly
optimistic, and the underlying data shifts as publishers update it: add
`sort="uri asc"` if you are walking a large result and need each entry
exactly once: it is unique per entry, so a page boundary cannot move. Locally neither caveat applies — the file does not move while
you read it.

## What a dataset looks like

Every search result, and every dataset you fetch, is one of these:

```json
{
    "uri": "https://example.org/data/roads",
    "title": "Vägtrafiknät",
    "description": "Nationell vägdatabas ...",
    "keywords": ["vägnät", "trafik"],
    "themes": ["transport"],
    "license": "cc_by_4_0",
    "access_rights": "public",
    "accrual_periodicity": "annual",
    "languages": ["swedish"],
    "publisher": {
        "name": "Trafikverket",
        "type": "national_authority",
        "identifiers": ["2021006297"]
    },
    "issued": "2020-03-04",
    "distributions": [
        {
            "title": "Vägnät CSV",
            "download_url": ["https://...csv"],
            "format": "csv"
        }
    ],
    "contact_points": [{ "name": "Datasupport", "email": "data@example.org" }]
}
```

Strings are strings — in the language you asked the client for.

Two things worth knowing:

**Everything from a fixed list of options is one short English word.** `themes`,
`license`, `access_rights`, `languages`, the publisher's `type` — these come
from standard vocabularies, and this package gives you `"transport"` rather than
the web address the registry actually publishes. The same word works as a
filter, which is the point. They stay English whatever language you chose,
because `"annual"` is more useful to build on than _årligen_.

**The files live under `distributions`.** One entry per download the publisher
offers, each with its own format and URL. `download_url` is the file itself;
`access_url` is a page or service you go through to get it.

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

### Where local and live results differ

Two small differences, both in the file's favour:

- `text=` is a plain substring match here, so it finds a few more than the
  registry's word-based index does.
- `published_after=` compares the dataset's own date. The registry's index
  covers every date in the entry including each distribution's, so it returns
  datasets whose *distributions* are recent even when the dataset is from 2013.

Everything else matches: transport 545 either way, `access_rights="public"`
17,682, `publisher="trafikverket"` 338.

## Other things in the registry

**Who publishes what.** `dp.organisations()` lists every publisher with its
dataset count, biggest first:

```python
for org in dp.organisations()[:3]:
    print(org.dataset_count, org.publisher, "-", org.name)
```

```text
5920 radet_for_framjande_av_kommunala_analyser_kolada - Rådet för främjande av kommunala analyser - Kolada
4315 statistikmyndigheten_scb_statistiska_centralbyran - Statistikmyndigheten SCB
2248 goteborgs_universitet - Göteborgs universitet
```

`org.publisher` is the value you filter with, so a listing leads straight into a
search:

```python
biggest = dp.organisations()[0]
page = dp.datasets(publisher=biggest.publisher)
```

The counts come from a chart the registry rebuilds nightly, so they can be a
little ahead of what a search returns today. `org.to_dict()` gives you the same
four fields as a dictionary, and `dp.organisation_summary()` gives registry-wide
totals.

```python
dataset["publisher"]        # the organisation behind one dataset, inline
```

Beyond datasets, the registry publishes catalogues (a publisher's collection),
data services (APIs rather than files), and dataset series. Each has its own
search, taking the same filters:

```python
dp.catalogs()
dp.data_services()
dp.dataset_series()
dp.distributions()
dp.agents()                 # organisations and people
```

And some housekeeping data the registry produces about itself:

```python
dp.catalog_statistics(limit=30)  # dataset counts per night, newest first
dp.link_check_reports()          # which download links still work
dp.metadata_quality()           # the registry's own quality scores
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

## Async

If your program is already async, use `AsyncDataportal`. Same method names,
same arguments:

```python
import asyncio
from dataportalen import AsyncDataportal

async def main():
    async with AsyncDataportal() as dp:
        page = await dp.datasets(theme="transport", limit=10)
        async for dataset in dp.iter_datasets(limit=100):
            print(dataset["title"])

asyncio.run(main())
```

Requests are awaited and the loops are `async for`; the results are the same
dicts. The async client always asks the registry — a local catalogue is a
file, and reading a file is not what `await` is for.

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

`search()` is the only search that hands back models rather than dicts, and it
always asks the registry.

Combine with `&` (and), `|` (or) and `~` (not). `Q.raw("...")` passes a
fragment through untouched if you know the query language.

**The rest of the surface**, in one place, for when you need it:

```python
dp.datasets(catalog=50)              # one catalogue, by its number
dp.datasets(uri="https://...")       # a dataset by its own address
dp.datasets(query="title.en:*")      # a raw index query, as above

dp.lookup("https://...")             # any entry, whatever type it turns out to be
dp.lookup_many([...])                # several at once, batched into few requests
dp.standards()                       # the standards datasets declare conformance to
dp.context_names()                   # catalogue number -> catalogue title
dp.context_publishers()              # catalogue number -> publisher
dp.download_dump("all.rdf")          # the registry's nightly RDF file, streamed

Dataportal(
    base_url="https://admin.dataportal.se",  # point at another EntryStore registry
    public_only=True,                        # only entries the public can read
    default_sort="modified desc",            # default ordering for searches
    cache_size=512,                          # how many lookups to remember
    transport=None,                          # the HTTP library to use
)
```

Set the `DATAPORTAL_USER_AGENT` environment variable to identify your program to
the registry, which is polite if you are making a lot of requests.

---

Contributing to this package, or curious where the short values come from?
See [internals.md](internals.md).
