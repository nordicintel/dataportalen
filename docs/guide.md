# Guide

How to use it, in the order you run into things. Every method, filter and
record key is listed in [reference.md](reference.md); this page is the part
worth reading once.

1. [The first run](#the-first-run)
2. [Searching](#searching)
3. [Finding out what you can filter by](#finding-out-what-you-can-filter-by)
4. [Reading a result](#reading-a-result)
5. [Data services](#data-services)
6. [Recipes](#recipes)
7. [What it does not do](#what-it-does-not-do)
8. [Why it works this way](#why-it-works-this-way)
9. [When something goes wrong](#when-something-goes-wrong)

## The first run

```python
from dataportalen import Catalog

cat = Catalog()
```

**That takes about seven minutes the first time** and writes 64 MB to disk. Do
it deliberately rather than inside something you expect to be quick — it prints
a progress line, so it never looks like a hang.

Every search after that is local: hundredths of a second, no network, works on
a plane. That is the whole design, and [why](#why-it-works-this-way) is worth
two minutes if anything about it surprises you.

The file goes in the usual cache directory — `%LOCALAPPDATA%\dataportalen\` on
Windows, `~/.cache/dataportalen/` elsewhere — so one copy serves every project
and nothing lands in a repository.

```python
cat.info()
# {'path': 'C:\\Users\\you\\AppData\\Local\\dataportalen\\catalog.jsonl',
#  'downloaded': '2026-09-30T03:17:40', 'age_days': 0, 'stale': False,
#  'bytes': 67527943, 'datasets': 23576, 'data_services': 599,
#  'publishers': 356}
```

**Nothing is ever downloaded behind a call that looked like a search.** When the
file may be rewritten is something you say when you build the `Catalog`:

```python
Catalog(refresh="if_missing")   # the default: fetch it once, then leave it
Catalog(refresh="if_stale")     # also refresh when it is over a week old
Catalog(refresh="always")       # refresh now
Catalog(refresh="never")        # never fetch; raise if the file is not there
Catalog("my-copy.jsonl")        # somewhere of your own
```

The registry re-harvests nightly, so after a week you get a warning telling you
how old your copy is. It is still used — a script that answered in a second
yesterday should not block for seven minutes today. `cat.info()["stale"]` is
the same fact without the log.

`refresh="never"` is what you want in CI, or anywhere that must not reach the
network.

## Searching

```python
from dataportalen import text

page = cat.datasets(text="cykel")

page.total                                 # 388 — how many matched
for dataset in page:
    print(text(dataset["title"]))
```

`page` is an ordinary list of dictionaries that also knows its own total, so it
slices and goes straight into anything that takes records.

**Filters combine, and all of them have to match:**

```python
cat.datasets(theme="transport", format="csv", publisher="trafikverket")
```

Values are short and lowercase; you never type a web address. There are twelve,
and [reference.md](reference.md#dataset-filters) lists each with how much of the
corpus it covers. The ones you will reach for:

```python
cat.datasets(text="cykel")                     # title, description, keywords
cat.datasets(publisher="trafikverket")         # who put it on the portal
cat.datasets(creator="statistikmyndigheten_scb_statistiska_centralbyran")
cat.datasets(theme="transport")                # the subject
cat.datasets(format="csv")                     # a format you can download
cat.datasets(keyword="geodata")                # the publisher's own tags
cat.datasets(updated_after="2024-01-01")       # changed since
cat.datasets(link="success")                   # its files actually resolve
```

`publisher` and `creator` are different questions — the publisher put it on the
portal, the creator produced the data. Statistikmyndigheten SCB is the creator
of 3,807 datasets that others publish.

A list means any of them will do:

```python
cat.datasets(format=["csv", "xlsx"])           # either format
cat.datasets(keyword=["geodata", "trafik"])    # but keywords must all match
```

**How much comes back.** There are no pages. A search scans the file and `limit`
says how much of the result you want to hold:

```python
cat.datasets(theme="transport")               # the first 50 (the default)
cat.datasets(theme="transport", limit=None)   # all 545
cat.datasets(theme="transport", limit=10, offset=100)
cat.datasets(theme="transport", limit=0)      # the count and breakdown, no rows
```

`page.total` is exactly how many matched — not an estimate — and that last line
is how you count things:

```python
cat.datasets(publisher="trafikverket", limit=0).total     # 338
```

## Finding out what you can filter by

`cat.filters()` is the one thing a search cannot tell you: the options, with
counts, before you search.

```python
for row in cat.filters()["publisher"][:3]:
    print(row.dataset_count, row.value, "-", text(row.label))
```

```text
5863 radet_for_framjande_av_kommunala_analyser_kolada - Rådet för främjande av kommunala analyser - Kolada
4306 statistikmyndigheten_scb_statistiska_centralbyran - Statistikmyndigheten SCB - Statistiska centralbyrån
2246 goteborgs_universitet - Göteborgs universitet
```

`row.value` is what you filter with, `row.label` is what you show a person, and
`row.dataset_count` is how many there are. A row is also a plain
`(value, count)` pair, so it unpacks in a loop.

```python
list(cat.filters())
# ['publisher', 'publisher_type', 'creator', 'theme', 'keyword',
#  'format', 'license', 'access_rights', 'updated', 'language', 'place']
```

**Every value round-trips.** `cat.datasets(theme=value, limit=0).total` is
exactly the count the row claimed, for every filter and every value. That is
tested, not hoped.

Some lists are long — 23,371 distinct keywords, 542 places. Cap them, and what
was cut is counted rather than dropped quietly:

```python
options = cat.filters(limit=10)
options["keyword"]           # the top 10
options["keyword"].omitted   # 23361
options.omitted              # {'keyword': 23361, 'place': 532, ...}
```

Reading the whole catalogue takes about half a second. If you already have a
result in hand its own breakdown is free, and it is the same structure counted
over **what matched** rather than over everything:

```python
page = cat.datasets(text="cykel")
page.breakdown["publisher"]       # [('radet_..._kolada', 213), ('trafikverket', 51), ...]
page.breakdown["theme"]           # [('population_and_society', 233), ...]
page.breakdown.top("format")      # the commonest value
page.breakdown.to_dict()          # {filter: {value: count}}, for JSON
```

So one structure answers both "what can I filter by?" and "what is in this
result?", and every value in it narrows the search you already have.

If you guess a value, the error corrects you:

```python
cat.datasets(theme="transprot")
# QueryError: unknown theme 'transprot'. Did you mean: transport?
```

## Reading a result

Every result is the same dictionary.
[reference.md](reference.md#a-dataset-record) lists all 22 keys with how often
each is filled. Four things are worth knowing before you write any of it down.

**Text is a map of languages, always.** `{"sv": ...}`, `{"en": ...}`, or both. A
key is there only when that language is: 53% of datasets are Swedish only, 36%
carry both, 10% are English only.

So **do not write `record["title"]["sv"]`** — it raises for one dataset in ten.
`text()` takes the best there is:

```python
from dataportalen import text

text(dataset["title"])               # Swedish if there is any, else English
text(dataset["title"], "en")         # the other way round
text(dataset["publisher"]["name"])   # works on any of them
```

It returns `None` for a field with nothing in it and passes a plain string
through, so it is safe wherever you are unsure of the shape. Anything the
registry tagged as undetermined, or in a third language, is filed under `"sv"` —
it is a Swedish registry.

**Anything from a fixed list of options is one short English word.** `themes`,
`license`, `access_rights`, `languages`, `publisher["type"]` — these come from
standard vocabularies, and you get `"transport"` rather than the web address the
registry publishes. The same word works as a filter, which is the point. They
stay English whatever the prose says, because `"annual"` is more useful to build
on than *årligen*.

**The filter is singular, the field is plural.** You search `theme="transport"`
and read `dataset["themes"]`. Four differ this way — `theme`/`themes`,
`language`/`languages`, `place`/`spatial`, `updated`/`accrual_periodicity`. The
rest read as you would guess, and a breakdown is keyed by the *filter* name,
because that is what you feed back in.

**A third of the files do not resolve, and the record says which.** The
registry runs a link check nightly and publishes the result per URL; that
verdict is on every file:

```python
for dist in dataset["distributions"]:
    dist["link"]["status"]    # 'success', 'broken' or 'excluded'
    dist["link"]["message"]   # 'OK', 'Not Found', 'Too Many Requests', ...
    dist["link"]["checked"]   # when the registry last tried
```

Over the corpus: **18,228 success, 11,886 broken, 5,021 excluded** by the
registry's own configuration. `link` is an ordinary filter, so it is in every
breakdown with counts:

```python
cat.datasets(link="success")                 # 16,584 have a file that resolves
cat.datasets(link="broken")                  # 6,069 have one that does not
cat.datasets(limit=0).breakdown["link"]
```

The status is the registry's, passed through as it stands — `broken` means its
checker could not fetch the URL, whatever the reason it gave. To drop those
files entirely, say so when you build the `Catalog`:

```python
Catalog(exclude_broken_links=True)      # 23,254 files instead of 35,140
```

A dataset whose every file is broken keeps its metadata and an empty
`distributions` list; `datasets(link="success")` leaves it out of a search too.

**The files are under `distributions`, and `access_url` is the one to read.**

```python
for dist in dataset["distributions"]:
    dist["format"]        # 'csv'
    dist["access_url"]    # a page or service to get it through — nearly all
    dist["download_url"]  # a direct file link — only 7.4% have one
```

Both are lists, because publishers give more than one often enough that a scalar
would lie. Of 35,133 distributions, 32,536 carry **only** `access_url` and 2,592
carry both; none carries `download_url` alone. Reading only `download_url` would
miss nine files in ten.

Anything the publisher left out is `null` or `[]`, never missing — a key is
always there, so you never need `.get()`.

## Data services

The registry holds 599 `dcat:DataService` entries — APIs rather than files. They
are in the same file and search the same way:

```python
for service in cat.data_services(service_type="view_service", limit=2):
    print(text(service["title"]), service["endpoint_url"])
```

```text
Railway Transport Network - View Service (INSPIRE) http://geo-inspire.trafikverket.se/...
Road Transport Network - View Service (INSPIRE)    http://geo-inspire.trafikverket.se/...
```

`service_type` is `rest` (288 of them), `view_service` (31),
`download_service` (14), `transformation_service` and `discovery_service` (one
each). The last four are INSPIRE spatial service types, so that is how you find
WMS endpoints.

A record looks like a dataset's, minus what a service does not have and plus
what it does: `endpoint_url`, `serves_dataset_uris`, `conforms_to`, and no
`distributions`.

**Four filters do not apply, and saying so is the point:**

```python
cat.data_services(format="csv")
# QueryError: data services have no 'format'. Available: publisher,
# publisher_type, creator, service_type, theme, keyword, license,
# access_rights, text
```

A data service has no distributions, so no `format`; no accrual periodicity, so
no `updated`; `place` is set on 8% of the 599 and `language` has one single value
across all of them. The date filters are out for the same reason. Coming back
with zero rows instead would read as "no CSV APIs" when the truth is "wrong
question".

## Recipes

**Every CSV in a theme, with somewhere to get it**

```python
for dataset in cat.datasets(theme="transport", format="csv", limit=None):
    for dist in dataset["distributions"]:
        if dist["format"] == "csv":
            print(text(dataset["title"]), dist["access_url"])
```

**What changed since a date, newest first**

```python
recent = cat.datasets(updated_after="2026-09-01", limit=None)
recent.sort(key=lambda r: r["modified"] or "", reverse=True)
```

`modified` is the publisher's own date, and 63 of them are in the future. Sort by
it, but do not trust it as a fact about the world.

**Who publishes a subject**

```python
for row in cat.datasets(theme="environment", limit=0).breakdown["publisher"][:3]:
    print(row.dataset_count, text(row.label))
```

```text
471 Sveriges Lantbruksuniversitet (SLU)
291 Rådet för främjande av kommunala analyser - Kolada
246 Statistikmyndigheten SCB - Statistiska centralbyrån
```

**Into a dataframe**

```python
import pandas
frame = pandas.DataFrame(cat.datasets(theme="transport", limit=None))
```

Nested columns stay nested. One row per file is usually more useful:

```python
rows = [
    {"dataset": text(d["title"]),
     "publisher": text(d["publisher"]["name"]),
     "format": dist["format"],
     "url": (dist["access_url"] or [None])[0]}
    for d in cat.datasets(theme="transport", limit=None)
    for dist in d["distributions"]
]
```

**In CI, or anywhere that must not reach the network**

```python
cat = Catalog("catalog.jsonl", refresh="never")
```

Cache or commit the file, and a missing one raises instead of spending seven
minutes. `Catalog("catalog.jsonl.gz")` reads and writes gzip from the suffix,
which is about a third of the size.

**Read the file yourself**

```python
import json

with open(cat.info()["path"], encoding="utf-8") as handle:
    for line in handle:
        record = json.loads(line)       # record["type"] says which kind
```

It is ordinary JSONL: one record per line, complete on its own, both kinds of
record in the one file.

## What it does not do

It reads. There is nothing here that writes to the registry.

Three things the registry has that this deliberately does not surface, with the
reason, because they are the likeliest things to go looking for:

- **Catalogues.** 656 are registered and only 157 hold a dataset; of the 152
  live ones, 127 have exactly one publisher, so a catalogue listing mostly
  duplicates `publisher`. Where a dataset came in through an aggregator,
  `record["context_id"]` identifies it.
- **Agents.** 7,609 exist and 365 publish anything; 4,916 are individual
  researchers who reach no dataset. `cat.filters()["publisher"]` and
  `["creator"]` are the ones that matter, and they come with names.
- **The registry's own reports** — nightly dataset counts, link checks, DCAT-AP
  quality scores. They describe the registry, not the data.

For raw RDF, `cat.get(uri, format="turtle")` hands you an entry's graph as text.
That is the only call that reaches the network after the download.

## Why it works this way

The registry caps a page at **100 entries** and answers about **two requests a
second** no matter how many you have in flight — measured at 8, 16 and 32
workers, all the same. So reading the whole corpus costs about seven minutes
whenever you do it.

That leaves two designs: pay seven minutes once, or pay a slice of it on every
question. This package pays once. The consequences are worth knowing:

- Searching, counting and every breakdown are local and immediate. A
  whole-corpus breakdown over every one of the 23,500-odd datasets takes half
  a second, which is not
  something the registry's index can do at all.
- Your copy is a snapshot. The registry re-harvests nightly, so it drifts, and
  `cat.info()["stale"]` tells you when it has drifted more than a week.
- Counts here can differ slightly from dataportal.se. That is what a snapshot
  means, rather than a bug.

[internals.md](internals.md#what-the-registry-can-do) has the measurements.

## When something goes wrong

Every error comes from `DataportalError`, so one `except` catches all of them:

```python
from dataportalen import DataportalError

try:
    page = cat.datasets(theme="transport")
except DataportalError as error:
    print(error)
```

[reference.md](reference.md#errors) lists each one. The four situations that are
not really errors:

**"It is downloading again and I did not ask."** It only does that for
`refresh="always"`, or `refresh="if_stale"` with a copy over a week old. The
default never refetches.

**"My counts differ from dataportal.se."** Your copy is a snapshot; check
`cat.info()["age_days"]`. Build with `refresh="always"` when you need today's.

**"`KeyError: 'sv'`."** 10% of datasets have no Swedish text. Use `text()`.

**"It came back empty and I expected something."** If the filter does not apply
to what you searched you get a `QueryError` naming the ones that do, so an empty
result really is an empty result. Check the value against `cat.filters()`.
