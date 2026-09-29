# Guide

Everything here is ordinary Python. You ask for datasets, you get dictionaries.

1. [Connect](#connect)
2. [Find datasets](#find-datasets)
3. [Narrow the search](#narrow-the-search)
4. [Filter by date](#filter-by-date)
5. [See what a filter accepts](#see-what-a-filter-accepts)
6. [Read more than one page](#read-more-than-one-page)
7. [What a dataset looks like](#what-a-dataset-looks-like)
8. [Download the whole catalogue](#download-the-whole-catalogue)
9. [Work from a local copy](#work-from-a-local-copy)
10. [Other things in the registry](#other-things-in-the-registry)
11. [Settings](#settings)
12. [When something goes wrong](#when-something-goes-wrong)
13. [Async](#async)
14. [Going deeper](#going-deeper)

## Connect

```python
from dataportalen import Dataportal

dp = Dataportal()              # reads Swedish
dp = Dataportal(language="en") # reads English
```

That is the client. Every example below uses `dp`.

Swedish is the default because the registry is Swedish: most datasets are
described in Swedish only. Ask for another language and you get it where the
publisher wrote one, and the Swedish text where they did not — text you can use
beats a blank.

It holds an open connection, so close it when you are finished — or let a `with`
block do it:

```python
with Dataportal() as dp:
    ...
```

## Find datasets

`dp.datasets()` searches. It returns one page of results, which you can loop
over:

```python
page = dp.datasets(text="cykel")

print(page.total)          # 251 — how many datasets matched
for dataset in page:
    print(dataset.title)
```

Every result is a `Dataset`. `dataset.to_dict()` turns it into a plain
dictionary you can save as JSON, put in a dataframe, or hand to something else.

To fetch one dataset whose address you already have:

```python
dataset = dp.dataset(uri="https://catalog.skane.se/rowstore/dataset/9f0e...")
```

That address is the publisher's own identifier for the dataset — the `uri` field
of any result. A URI nothing matches gives you `None` rather than an error, so
check before using it.

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

## See what a filter accepts

`dp.values(...)` answers "what can I put in `theme=`?", counted against the
live registry so you see what publishers actually use:

```python
for value, count in dp.values("theme"):
    print(count, value)
```

```text
6455 population_and_society
6343 government_and_public_sector
3811 education_culture_and_sport
2964 environment
2322 health
...
```

Every value it prints is one you can filter on directly:

```python
dp.datasets(theme="population_and_society")
```

It works for `theme`, `format`, `license`, `access_rights`, `updated`,
`language`, `place` and `publisher`, and takes one request. (Not `keyword` —
the registry indexes keywords by fragment, so counting them returns pieces of
words rather than keywords. A [local copy](#work-from-a-local-copy) can count
those.)

Without the network, `known_values` lists the same names from the table
shipped in the package:

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

## Read more than one page

A search returns at most 100 datasets at a time. That is the registry's cap,
not this package's — ask for 1000 and it answers with 100. `page` tells you
where you are:

```python
page = dp.datasets(theme="transport", limit=100)
page.total       # how many matched in total
page.has_more    # whether anything follows this page
```

Rather than asking for pages yourself, let the client do it:

```python
for dataset in dp.iter_datasets(theme="transport"):
    print(dataset.title)
```

That keeps fetching until the results run out. Pass `limit=` to stop early.

One caveat worth knowing: `total` is the registry's own estimate and can be a
little optimistic, and results shift as publishers update their data. If you are
walking a large result and need each dataset exactly once, add
`sort="created asc"` so the order cannot change underneath you.

## What a dataset looks like

```python
dataset.to_dict()
```

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

`dataset.to_json()` gives you the same thing as a JSON string.

### Every language at once

Some datasets really are described in several languages. To keep all of them,
ask for `"all"`, and the text fields become maps instead of strings:

```python
dp = Dataportal(language="all")
dataset = next(iter(dp.datasets(text="cykel")))
dataset.to_dict()["title"]
# {"sv": "Cykelstråk", "en": "Cycle routes"}
```

That applies to `title`, `description`, `keywords` and the publisher's `name` —
the fields publishers write themselves. A single dataset can also be switched
without a second client:

```python
dataset.with_language("all").to_dict()
```

## Download the whole catalogue

One call writes every dataset in Sweden's registry to a file:

```python
from dataportalen import download_catalog

download_catalog("catalog.jsonl")
```

It takes about six minutes and draws a progress line while it runs. The last
full run wrote 23,580 datasets and 35,151 distributions: one JSON object per
line, 58 MB.

Each line is complete on its own — distributions, publisher and contacts are
already inside it, so there is nothing left to look up:

```python
import json

with open("catalog.jsonl", encoding="utf-8") as f:
    for line in f:
        dataset = json.loads(line)
```

Useful variations:

```python
download_catalog("catalog.jsonl.gz")               # compressed, from the suffix
download_catalog("sample.jsonl", limit=500)        # just 500, to try it out
download_catalog("catalog.jsonl", progress=None)   # no progress line
download_catalog("catalog.jsonl", progress=print)  # your own progress handler

summary = download_catalog("catalog.jsonl")
summary.datasets, summary.distributions, summary.elapsed
```

## Work from a local copy

The registry answers about **two requests a second**, and more requests in
flight does not help — 8, 16 and 32 at once all come back at the same rate. At
100 datasets a request that is a ceiling of roughly 200 datasets a second, so
reading any large slice of the catalogue over the API takes minutes.

If you are going to touch more than a few thousand datasets, work from the
file instead:

```python
from dataportalen import LocalCatalog

catalog = LocalCatalog("catalog.jsonl")     # downloads it the first time

len(catalog)                                 # 23580
catalog.datasets(theme="transport", format="csv")
```

Same filters, same short values, same dicts — and the whole corpus is scanned
in milliseconds rather than minutes:

| | over the API | over the file |
| --- | --- | --- |
| `theme="transport"` | ~1s for the first page, minutes for all 545 | 0.03s for all 545 |
| `access_rights="public"` | ~90s for all 17,682 | 0.02s |
| counting every keyword | not possible | 0.4s |

```python
catalog.datasets(publisher="trafikverket", updated_after="2024-01-01")
catalog.values("keyword")        # what the live index cannot count
catalog.downloaded               # when the file was written
catalog.refresh()                # download it again
```

Two differences from the live search, both in your favour:

- `text=` is a plain substring match here, so it finds a few more than the
  registry's word-based index does.
- `published_after=` compares the dataset's own date. The registry's index
  covers every date in the entry, including each distribution's, so it returns
  datasets whose distributions are recent even when the dataset is from 2013.

Pass `download=False` to a program that must not reach the network; a missing
file then raises instead of fetching 58 MB.

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
dataset.publisher()         # the organisation behind one dataset
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
[paging](#read-more-than-one-page).

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
            print(dataset.title)

asyncio.run(main())
```

Requests are awaited and the loops are `async for`. Nothing else changes.

## Going deeper

Skip this section unless a plain dictionary is genuinely not enough.

The registry describes everything in RDF — a graph format where values are web
addresses instead of words. This package exists so you do not have to deal with
that, but it does not hide it either.

**The original data behind a dataset:**

```python
dataset.raw_json()      # exactly what the registry sent
dataset.to_rdf()        # the same, as a graph
dataset.to_rdf_dict()   # every field the publisher supplied, including ones
                        # to_dict() leaves out
```

**A file in another format**, when you want to feed it to a proper RDF library:

```python
dp.entry_raw(dataset.context_id, dataset.entry_id, format="text/turtle").text
```

**A search this package cannot express.** The registry's index is Solr, and `Q`
writes Solr queries for you, escaping as needed:

```python
from dataportalen import Q

dp.search(Q.text("cykel") & ~Q.tag("historisk"))
```

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
