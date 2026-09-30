# Guide

Everything here is ordinary Python. You ask for datasets, you get dictionaries.

1. [Start](#start)
2. [Find datasets](#find-datasets)
3. [Narrow the search](#narrow-the-search)
4. [Filter by date](#filter-by-date)
5. [What you can filter by](#what-you-can-filter-by)
6. [What a result is made of](#what-a-result-is-made-of)
7. [How much comes back](#how-much-comes-back)
8. [What a dataset looks like](#what-a-dataset-looks-like)
9. [Data services](#data-services)
10. [One record at a time](#one-record-at-a-time)
11. [The file](#the-file)
12. [Settings](#settings)
13. [When something goes wrong](#when-something-goes-wrong)

## Start

```python
from dataportalen import Catalog

cat = Catalog()
```

That first line downloads the catalogue — about seven minutes and 64 MB, once
— and every search after that runs against the copy on disk, in hundredths of
a second.

**The copy is how this package works.** It is built that way because the
registry is slow in a way you cannot work around: it caps a page at 100
entries and answers about two requests a second no matter how many you have in
flight. Reading anything substantial over the API takes minutes every time.
Reading it once takes minutes once.

The file holds every dataset and every data service, each with its
distributions, publisher, creators and contacts already nested. Searching,
counting and every breakdown come from it without touching the network.

There is one exception, and you have to ask for it:
[`get(uri, format="turtle")`](#one-record-at-a-time) fetches an entry's raw RDF
from the registry, because that is not in the file.

## Find datasets

```python
from dataportalen import text

page = cat.datasets(text="cykel")

print(page.total)          # 388 — how many matched
for dataset in page:
    print(text(dataset["title"]))
```

It is an ordinary list, so it slices, and it goes straight into anything that
takes records:

```python
import pandas
pandas.DataFrame(cat.datasets(theme="transport", limit=None))
```

`limit` defaults to 50 so a stray search cannot print twenty thousand
dictionaries. `limit=None` gives you everything that matched, and `limit=0`
gives you the count and the breakdown with no rows at all.

## Narrow the search

Add any of these. They combine, so all of them have to match:

```python
page = cat.datasets(theme="transport", format="csv", publisher="trafikverket")
```

Values are short and lowercase. You never type a web address.

| Filter | What it matches | On how many datasets |
| --- | --- | --- |
| `text="cykel"` | anywhere in the title, description or keywords, either language | — |
| `publisher="trafikverket"` | the organisation that put it on the portal | 100% |
| `license="cc_by_4_0"` | the licence it is released under | 100% |
| `keyword="geodata"` | one of the keywords the publisher attached | 94.8% |
| `language="swedish"` | the language of the data itself | 89.3% |
| `access_rights="public"` | whether it is open to everyone | 82.3% |
| `theme="transport"` | the subject it is filed under | 78.2% |
| `format="csv"` | a format you can download it in | 69.7% |
| `updated="annual"` | how often the publisher refreshes it | 63.3% |
| `publisher_type="local_authority"` | what kind of organisation published it | 100% |
| `creator="statistikmyndigheten_scb"` | the organisation that produced the data | 30.1% |
| `place="kingdom_of_sweden"` | the area it covers | 23.6% |

The percentages are measured over all 23,576 datasets, and they are the reason
this list is this list — nothing rarer got in. A filter with no value on a
dataset simply does not match it.

**`publisher` and `creator` are different questions.** The publisher put it on
the portal; the creator produced the data. Most datasets name only a
publisher, but where both are there they often differ — Statistikmyndigheten
SCB is the creator of 3,807 datasets published by others.

Give a list instead of one value and any of them will do:

```python
cat.datasets(format=["csv", "xlsx"])        # either format
cat.datasets(keyword=["geodata", "trafik"]) # but keywords must all be present
```

`language` is `swedish` or `english` and nothing else. The registry holds 67
distinct values in that field, but 65 of them cover about 106 datasets between
them — language corpora and multilingual dictionaries. They are still on each
dataset's own `languages` list; they are just not worth a filter.

## Filter by date

```python
cat.datasets(updated_after="2024-01-01")
```

| Filter | Which date | Present on |
| --- | --- | --- |
| `updated_after`, `updated_before` | when the publisher last changed the data | 89.1% |
| `published_after`, `published_before` | when the publisher first released it | 42.1% |

Write the date however is convenient — `"2024-01-01"`, `"2024-01"`, `"2024"`,
or Python's own `date` and `datetime` objects.

Both dates are the publisher's. The registry also records when it last copied a
dataset in, but it does that nightly for nearly everything, so filtering on it
would tell you about the registry's schedule rather than about the data. It is
deliberately not offered.

## What you can filter by

`cat.filters()` is the one thing a search cannot tell you: the options, with
counts, before you search.

```python
for row in cat.filters()["publisher"][:3]:
    print(row.dataset_count, row.value, "-", row.label["sv"])
```

```text
5863 radet_for_framjande_av_kommunala_analyser_kolada - Rådet för främjande av kommunala analyser - Kolada
4306 statistikmyndigheten_scb_statistiska_centralbyran - Statistikmyndigheten SCB
2246 goteborgs_universitet - Göteborgs universitet
```

`row.value` is what you filter with, `row.label` is what you show a person, and
`row.dataset_count` is how many there are. A row is still a plain
`(value, count)` pair, so it unpacks in a loop.

```python
list(cat.filters())
# ['publisher', 'publisher_type', 'creator', 'theme', 'keyword',
#  'format', 'license', 'access_rights', 'updated', 'language', 'place']
```

Some of these lists are long — 23,371 distinct keywords, 542 places. Cap them,
and what was cut is counted rather than dropped quietly:

```python
options = cat.filters(limit=10)
options["keyword"]           # the top 10
options["keyword"].omitted   # 23361
options.omitted              # {'keyword': 23361, 'place': 532, ...}
```

It reads the whole catalogue, which takes about half a second. If you already
have a result in hand, its own breakdown is free — see next.

And if you just guess a value, the error corrects you:

```python
cat.datasets(theme="transprot")
# QueryError: unknown theme 'transprot'. Did you mean: transport?
```

## What a result is made of

Every search comes back knowing what it matched. `page.breakdown` is the same
structure `filters()` gives you, counted over **everything that matched** —
not just the rows you are holding:

```python
page = cat.datasets(text="cykel")
page.total                        # 388

page.breakdown["publisher"]       # [('kolada', 213), ('trafikverket', 51), ...]
page.breakdown["theme"]           # [('population_and_society', 233), ...]
page.breakdown["format"]          # [('json', 246), ('html', 51), ...]
page.breakdown["access_rights"]   # [('public', 366), ...]
```

Every value goes straight back in to narrow the search:

```python
cat.datasets(text="cykel", publisher="trafikverket")
```

So one structure answers both "what can I filter by?" and "what is in this
result?". Counts are per dataset — one with three CSV files counts once under
`format` → `csv`.

```python
page.breakdown.to_dict()      # {filter: {value: count}}, for JSON
page.breakdown.top("theme")   # the commonest value
```

`breakdown_limit=` caps each list, exactly as `filters(limit=)` does.

## How much comes back

There are no pages. A search scans the file and `limit` says how much of the
result you want to hold:

```python
cat.datasets(theme="transport")              # the first 50
cat.datasets(theme="transport", limit=None)  # all 545
cat.datasets(theme="transport", limit=10, offset=100)
cat.datasets(theme="transport", limit=0)     # the count and breakdown, no rows
```

`page.total` is how many matched and `page.has_more` says whether you are
holding all of them. `total` is exact, not an estimate, and the file does not
move while you read it.

That last line is how you count things:

```python
cat.datasets(publisher="trafikverket", limit=0).total   # 338
```

## What a dataset looks like

Every result, and everything `get()` returns, is the same dictionary:

```json
{
  "uri": "https://example.org/data/roads",
  "type": "dataset",
  "context_id": "50",
  "entry_id": "28672",

  "title": {"sv": "Vägtrafiknät", "en": "Road traffic network"},
  "description": {"sv": "Nationell vägdatabas ..."},
  "keywords": {"sv": ["vägnät", "trafik"], "en": ["road network"]},
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
    "name": {"sv": "Trafikverket"},
    "type": "national_authority",
    "identifiers": ["2021006297"],
    "email": "data@trafikverket.se",
    "homepage": "https://trafikverket.se",
    "uri": "http://dataportal.se/organisation/SE2021006297",
    "context_id": "1", "entry_id": "5"
  },
  "creators": [{"name": {"sv": "Trafikverket"}, "type": "national_authority",
                "uri": "http://dataportal.se/organisation/SE2021006297"}],
  "contact_points": [{"name": "Datasupport", "email": "data@example.org"}],

  "distributions": [
    {"title": {"sv": "Vägnät CSV"},
     "download_url": ["https://...csv"],
     "access_url": [],
     "format": "csv",
     "license": "cc_by_4_0",
     "availability": "stable"}
  ]
}
```

Four things worth knowing.

**Text is a map of languages, always.** `{"sv": ...}`, `{"en": ...}`, or both.
A key is there only when that language is. Over the whole catalogue: 53% of
datasets are Swedish only, 36% carry both, 10% are English only. There is no
language setting to get wrong — you get what the publisher wrote, and you pick
when you read. Anything the registry tagged as undetermined, or in a third
language, is filed under `"sv"`; it is a Swedish registry.

Because that 10% is real, do not write `record["title"]["sv"]` — it raises for
one dataset in ten. `text()` takes the best there is:

```python
from dataportalen import text

text(dataset["title"])              # Swedish if there is any, else English
text(dataset["title"], "en")        # the other way round
text(dataset["publisher"]["name"])  # works on any of them
```

It returns `None` for a field with nothing in it, and passes a plain string
through, so it is safe wherever you are unsure of the shape.

**Anything from a fixed list of options is one short English word.** `themes`,
`license`, `access_rights`, `languages`, `publisher.type` — these come from
standard vocabularies, and you get `"transport"` rather than the web address
the registry publishes. The same word works as a filter, which is the point.
They stay English whatever the text says, because `"annual"` is more useful to
build on than *årligen*.

**The filter is singular, the field is plural.** You search `theme="transport"`
and read `dataset["themes"]`. Four differ this way:

| Filter | Field on the record |
| --- | --- |
| `theme=` | `themes` |
| `language=` | `languages` |
| `place=` | `spatial` |
| `updated=` | `accrual_periodicity` |

The rest read as you would guess. A breakdown is keyed by the *filter* name,
because that is what you feed back in.

**The files live under `distributions`.** One entry per download the publisher
offers. `download_url` is the file itself; `access_url` is a page or service
you go through to get it. Both are lists, because publishers give more than one
often enough that a scalar would lie.

Anything the publisher left out is `null` or `[]`, never missing.

## Data services

The registry holds 599 `dcat:DataService` entries — APIs rather than files.
They are in the same file, and search the same way:

```python
from dataportalen import text

page = cat.data_services(service_type="view_service")
for service in page:
    print(text(service["title"]), service["endpoint_url"])
```

A record looks like a dataset's, minus what a service does not have and plus
what it does:

```python
service["service_type"]        # 'rest', 'view_service', 'download_service', ...
service["endpoint_url"]        # where to call it
service["serves_dataset_uris"] # which datasets it serves
service["conforms_to"]         # the specification it follows, where declared
```

`service_type` takes `rest` (288 of them), `view_service` (31),
`download_service` (14), `transformation_service` and `discovery_service` (one
each). The last four are INSPIRE spatial service types, so that is how you find
the WMS endpoints.

**Four filters do not apply, and saying so is the point:**

```python
cat.data_services(format="csv")
# QueryError: data services have no 'format'. Available: publisher,
# publisher_type, creator, service_type, theme, keyword, license,
# access_rights, text
```

A data service has no distributions, so no `format`; no accrual periodicity, so
no `updated`; `place` is set on 8% of the 599 and `language` has a single value
across all of them. The date filters are out for the same reason — `modified`
is on 7.5% and `issued` on 0.8%. Returning zero rows instead would read as "no
CSV APIs" when the truth is "wrong question".

Its breakdown carries the eight keys it has, not empty lists for the four it
does not:

```python
cat.data_services(limit=0).breakdown["service_type"]
```

## One record at a time

```python
cat.get("https://catalog.skane.se/rowstore/dataset/9f0e...")
```

That address is the publisher's own identifier — the `uri` field of any result.
It comes straight out of the file, works for datasets and data services alike,
and gives you `None` rather than an error when nothing matches.

For the original RDF, which is not in the file, name a format. That is one
request to the registry:

```python
cat.get(uri, format="turtle")      # also "rdf/xml", "n-triples", "json-ld",
                                   # "trig", or any media type
```

Four of the 23,576 dataset URIs are shared by two records, because the same
dataset was harvested into two catalogues. `get()` returns the first.

## The file

```python
cat.info()
```

```python
{'path': 'C:\\Users\\you\\AppData\\Local\\dataportalen\\catalog.jsonl',
 'downloaded': '2026-09-30T03:06:34',
 'age_days': 0,
 'stale': False,
 'bytes': 66805627,
 'datasets': 23576,
 'data_services': 599,
 'publishers': 365}
```

It lives in the usual cache directory — `%LOCALAPPDATA%\dataportalen\` on
Windows, `~/.cache/dataportalen/` elsewhere — so one copy serves every project
and nothing lands in a repository.

**Everything that writes the file is an argument you pass when you build the
`Catalog`.** There is no method that quietly replaces 64 MB on disk.

```python
Catalog("my-copy.jsonl")                    # somewhere else
Catalog(refresh="if_stale")                 # refresh when it is over a week old
Catalog(refresh="always")                   # refresh now
Catalog(refresh="never")                    # never download; raise if missing
Catalog(stale_after=1)                      # your definition of old
Catalog(progress=None)                      # no progress line
Catalog(progress=print)                     # your own progress handler
Catalog("catalog.jsonl.gz")                 # gzip, from the suffix
```

`refresh="if_missing"` is the default: download it the first time and then
leave it alone. The registry re-harvests nightly, so after a week you get a
warning saying how old your copy is — but nothing is re-downloaded on its own,
because a script that answered in a second yesterday should not block for
seven minutes today.

`refresh="never"` is for a program that must not reach the network: a missing
file raises instead of fetching.

Short names are resolved when the file is written, so a copy also keeps the
vocabulary of the version that downloaded it. Refresh after upgrading to pick
up renamed or newly labelled values.

It is ordinary JSONL, so nothing stops you reading it yourself:

```python
import json

with open(cat.info()["path"], encoding="utf-8") as f:
    for line in f:
        record = json.loads(line)          # record["type"] says which kind
```

## Settings

All optional:

```python
cat = Catalog(
    base_url="https://admin.dataportal.se",  # another EntryStore registry
    transport=None,                          # your own HTTP layer
    workers=8,                               # parallel requests while downloading
)
```

To see what it is doing:

```python
from dataportalen import enable_logging
enable_logging("DEBUG")     # every request, with status and duration
```

If your application already configures logging, use it — this package logs to a
logger named `dataportalen` and never touches your setup:

```python
import logging
logging.getLogger("dataportalen").setLevel(logging.DEBUG)
```

`cat.close()` releases the HTTP connection. You do not have to call it and
nothing leaks if you don't.

## When something goes wrong

Every error here comes from `DataportalError`, so one `except` catches all of
them:

```python
from dataportalen import DataportalError

try:
    page = cat.datasets(theme="transport")
except DataportalError as error:
    print(error)
```

When you want to react differently to different failures:

| Error | Means |
| --- | --- |
| `QueryError` | your filters were wrong — usually a value that does not exist, or one that does not apply to what you were searching |
| `FileNotFoundError` | `refresh="never"` and there is no file |
| `NotFoundError` | there is no such entry (`get()` gives you `None` instead) |
| `RateLimitError` | too many requests; it already retried |
| `TimeoutError` | the registry did not answer in time |
| `TransportError` | the connection failed |
| `ServerError` | the registry itself broke |
| `ParseError` | the registry sent something unreadable |

One thing that is not a bug here: the registry rebuilds its search index
nightly, so your copy can be a day behind what a publisher has actually
published — and a week behind if you have not refreshed it. `cat.info()["stale"]`
tells you.
