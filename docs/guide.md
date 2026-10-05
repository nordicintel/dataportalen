# Guide

How to use it, in the order you run into things. Every method, filter and
model field is listed in [reference.md](reference.md), and
[cheatsheet.md](cheatsheet.md) shows every call with its arguments and output;
this page is the part worth reading once. Coming from 0.12, the
[CHANGELOG](../CHANGELOG.md) has a table of what each old call is now.

1. [The first run](#the-first-run)
2. [What the catalogue holds](#what-the-catalogue-holds)
3. [Searching](#searching)
4. [Facets: finding out what you can filter by](#facets-finding-out-what-you-can-filter-by)
5. [Reading a result](#reading-a-result)
6. [Publishers](#publishers)
7. [Data services](#data-services)
8. [Recipes](#recipes)
9. [What it does not do](#what-it-does-not-do)
10. [Why it works this way](#why-it-works-this-way)
11. [Asking the registry: `live=True`](#asking-the-registry-livetrue)
12. [When something goes wrong](#when-something-goes-wrong)

## The first run

```python
from dataportalen import Catalog

catalog = Catalog()
```

**That takes about seven minutes the first time** and writes ~95 MB to disk. Do
it deliberately rather than inside something you expect to be quick — it prints
a progress line, so it never looks like a hang.

Every call after that is local: no network, works on a plane. Reading the copy
when a `Catalog` is built takes about two and a half seconds; a filtered
search after that takes hundredths of a second. That is the whole design, and
[why](#why-it-works-this-way) is worth two minutes if anything about it
surprises you.

The copy is a SQLite database in the usual cache directory —
`%LOCALAPPDATA%\dataportalen\catalog.sqlite` on Windows,
`~/.cache/dataportalen/catalog.sqlite` elsewhere — so one copy serves every
project and nothing lands in a repository.

```python
catalog.info()
# {'database': 'C:\\Users\\you\\AppData\\Local\\dataportalen\\catalog.sqlite',
#  'live': False,
#  'first_retrieved': '2026-09-30T17:57:14',
#  'last_refreshed': '2026-10-01T13:04:54',
#  'downloaded': '2026-10-01T13:04:54', 'age_days': 0,
#  'bytes': 98951168, 'datasets': 23422, 'data_services': 599,
#  'publishers': 346,
#  'excluded': {'dead_distributions': 903, 'dead_datasets': 160},
#  'stale_datasets': 81, 'unverified_distributions': 10858,
#  'sources': {'total': 666, 'succeeded': 239, 'failed': 427,
#              'failed_holding_records': [...]}}
```

`excluded` is what `exclude_broken` left out, the two counts after it are
what is held but marked, and `sources` is how the registry's latest harvest
of each source catalogue went.
[What the catalogue holds](#what-the-catalogue-holds) explains each.

**It keeps itself current, cheaply.** The registry re-harvests nightly. When
your copy is older than `max_age` days — seven by default — building the
`Catalog` asks the registry only for the entries it has touched since you last
looked and replaces those rows. A day's churn is about 630 datasets, seven
pages: **under a minute, against seven for a rebuild.** It logs when it does.

```python
Catalog()                       # refresh if over a week old; download if missing
Catalog(max_age=1)              # refresh if over a day old
Catalog(max_age=None)           # use what is there; download only if missing
Catalog(rebuild=True)           # fetch everything again now
Catalog(database="my-copy.sqlite")
```

A refresh uses the registry's own timestamp for an entry, not the publisher's
`modified`, because a publisher can leave that empty or set it to 2100 and
neither says whether the entry changed. One thing a refresh cannot see is a
deletion: a dataset withdrawn from the registry keeps its row until
`rebuild=True`, which is also what a schema change asks you for.

`max_age=None` is for anywhere that must not spend a minute on the network
without being asked. It still downloads if there is nothing there at all; if
even that is unacceptable, check `os.path.exists(default_catalog_path())`
first.

A `Catalog` holds a connection to the registry for the calls that use it.
`catalog.close()` releases it, and `with Catalog() as catalog:` does that for
you; neither is required.

## What the catalogue holds

Every dataset and data service in the registry, at every access level, less
one thing decided when you build the `Catalog`.

**Dead distributions are left out.** The registry checks every distribution's
URL nightly and calls 11,761 of 35,148 broken — but its message says two
different things:

| The registry's message                                                        | Distributions |                                                |
| ----------------------------------------------------------------------------- | ------------- | ---------------------------------------------- |
| an HTTP error: Not Found, Forbidden, Internal Server Error, …                 | 718           | a server said no:**dead**                      |
| a host that is not in DNS                                                     | 185           | no server to ask:**dead**                      |
| nothing usable: no message, a failed connection, a timeout, Too Many Requests | 10,858        | the checker did not get through:**unverified** |

By default the dead distributions are gone from the datasets, and so are the
160 datasets whose every distribution is dead. An unverified distribution
stays, and says so:

```python
dist.unverified       # <LinkMark 'timeout' 2026-10-01T02:52:59>
dist.unverified.reason, dist.unverified.checked
```

The difference matters. Statistics Sweden's API resets the registry's
checker, so 7,091 of its links are "broken" while every one of them answers an
ordinary request. Treating those as dead removed 4,270 of SCB's 4,306 datasets.

```python
Catalog(exclude_broken=False)   # keep every distribution; the dead ones carry `broken`
```

That is 23,582 datasets against 23,422, and 356 publishers against 346. A
dataset that never had distributions (1,647 of them: APIs, registers) is not
dead and stays either way. Both verdicts are the registry's own data; this
package sends no request to a publisher's server, so an unverified
distribution stays unverified until the registry's checker gets through.

**A stale record is marked, not removed.** The registry harvests each source
catalogue nightly, and the download reads how the latest harvest of each went.
Of 666 sources, 427 failed — but 419 of those never yielded a dataset. The
other 8 hold 81 datasets, and each says so:

```python
dataset.stale                                  # <LinkMark 'harvest failed' 2026-10-04T02:46:11>
catalog.info()["sources"]["failed_holding_records"]
# one row per failed source that still holds records, most datasets first:
# context_id, title, status, harvested, dataset_count, data_service_count
```

The data may be fine. What is stale is the record: it is what the last good
harvest left behind. `stale` is on data services too, and `None` on every
record whose source harvested cleanly. A database written before 0.12 has no
harvest status, and there `info()["sources"]` is `None`.

**Access rights are a filter, not a scope.** The default catalogue holds every
access level. 17,555 datasets say `public`, 1,489 `non_public`, 241
`restricted`, and 4,137 — 17.7%, mostly universities — say nothing at all.
Narrow any call to what you want:

```python
catalog.search(access_rights="public")              # published openly
catalog.search(access_rights=["public", "none"])    # plus the ones that set nothing
```

`"none"` is how you ask for a record that sets no access rights; it is not a
value of the facet, which lists the three a record can say.

`info()["datasets"]` is what this `Catalog` holds — 23,422 by default. The
database underneath always holds every record, so changing `exclude_broken`
is a new `Catalog`, not a new download.

## Searching

```python
result = catalog.search("cykel")

result.total                    # 386 — how many matched
for dataset in result.datasets:
    print(dataset.title.text())
```

`result` is a `SearchResult`: the matching datasets, how many there were, and
the [facets](#facets-finding-out-what-you-can-filter-by) — what every match is
made of. Iterating it iterates the datasets, and `len(result)` is how many it
holds.

`query`, the first argument, is a phrase: it matches where those words appear
together, ignoring case, in the title, the description or the keywords, in
either language. `search("air quality")` finds the 15 datasets where those
words sit together, not the hundreds that have both somewhere.

**Filters combine, and all of them have to match:**

```python
catalog.search(theme="transport", format="csv", publisher="transportstyrelsen")
```

Values are short and lowercase; you never type a web address. There are eight,
plus `query` and one date, and [reference.md](reference.md#dataset-filters)
lists each with how much of the corpus it covers:

```python
catalog.search("cykel")                            # a phrase, anywhere in the text
catalog.search(publisher="scb")                    # who put it on the portal
catalog.search(publisher_type="local_authority")   # what kind of organisation that is
catalog.search(theme="transport")                  # the subject
catalog.search(format="csv")                       # a format you can download
catalog.search(kind="file")                        # what a distribution is
catalog.search(keyword="geodata")                  # the publisher's own tags
catalog.search(access_rights="public")             # who may use it
catalog.search(accrual_periodicity="monthly")      # how often it is updated
catalog.search(modified_after="2024-01-01")        # changed since
```

A list means any of them will do, for every filter:

```python
catalog.search(format=["csv", "xlsx"])             # either format
catalog.search(keyword=["geodata", "trafik"])      # either keyword
```

`keyword` is exact and ignores case — `Kommun`, `kommun` and `KOMMUN` are one
keyword, 4,617 datasets — and a keyword nobody uses is an error with
suggestions, like any other filter. For a part of a word, use `query`.

`modified_after` takes `"2024-01-01"`, `"2024-01"`, `"2024"`, a `date` or a
`datetime`. It reads the publisher's `modified`, and `issued` for a dataset
that has no `modified` (651 of them). It is the one date filter.

`license`, `language`, `issued_after`, `issued_before` and `modified_before`
were filters before 0.13 and are not now. Every record still carries the
fields, and asking for one is a `QueryError` that says what to do instead —
usually a line of Python over the list ([recipes](#recipes)).

**How much comes back.** With no `limit`, a search holds every match.
`limit` and `offset` take a window of it:

```python
catalog.search(theme="transport")                    # all 526
catalog.search(theme="transport", limit=10)          # the first 10
catalog.search(theme="transport", limit=10, offset=100)
catalog.search(theme="transport", limit=0)           # the count and facets, no datasets
```

`result.total` is exactly how many matched — not an estimate — whatever the
window, and `result.has_more` says whether there are more past it. The facets
always count every match, not the window. So the last line is how you count
things:

```python
catalog.search(publisher="trafikverket", limit=0).total      # 337
```

A filtered search takes hundredths of a second. One with no filter and no
`limit` is about two seconds, most of it spent making 23,422 `Dataset`
objects; `limit=0` makes none.

**Listing is `datasets()`.** When you want the matches rather than a
question about them:

```python
catalog.datasets(publisher="scb", kind="pxweb")      # a list of 4,284 Dataset objects
catalog.datasets()                                   # every dataset the catalogue holds
```

The same filters as `search()`, without `query`, a window or facets: a plain
list of every match. Passing `limit`, `offset`, `facet_limit` or `query` is a
`QueryError` that points at `search()`. `for dataset in catalog` walks every
dataset too, and `len(catalog)` is how many there are.

## Facets: finding out what you can filter by

You filter with a value; a facet tells you which values exist.
`catalog.facets()` is every facet, with counts, before you search.

```python
for row in catalog.facets()["publisher"][:3]:
    print(row.count, row.value, "-", row.label.text())
```

```text
5863 radet_for_framjande_av_kommunala_analyser_kolada - Rådet för främjande av kommunala analyser - Kolada
4306 statistikmyndigheten_scb_statistiska_centralbyran - Statistikmyndigheten SCB - Statistiska centralbyrån
2246 goteborgs_universitet - Göteborgs universitet
```

`row.value` is what you filter with, `row.label` is what you show a person (a
`MultilingualText`, empty for a keyword or a kind, which are their own label),
and `row.count` is how many datasets carry it. A row is also a plain
`(value, count)` pair, so it unpacks in a loop.

```python
list(catalog.facets())
# ['publisher', 'publisher_type', 'theme', 'keyword', 'format', 'kind',
#  'access_rights', 'accrual_periodicity']
```

The facets are exactly the filters, and `catalog.facets()` is exactly
`catalog.search(limit=0).facets`.

**Every value round-trips.** `len(catalog.datasets(theme=value))` is exactly
the count the row claimed, for every filter and every value. Checked on the
real catalogue for all 21,566 values, keywords included: no exceptions.

Some facets are long — 21,105 distinct keywords. Cap them, and what was cut is
counted rather than dropped quietly:

```python
facets = catalog.facets(limit=10)
facets["keyword"]            # the top 10
facets["keyword"].omitted    # 21095
facets.omitted               # {'publisher': 335, 'theme': 21, 'keyword': 21095, ...}
```

`search()` takes the same cap as `facet_limit=`. A search carries its own
facets, and they are the same structure counted over **what matched** rather
than over everything:

```python
result = catalog.search("cykel")
result.facets["publisher"][:2]   # [FacetValue(value='radet_..._kolada', count=213),
                                 #  FacetValue(value='trafikverket', count=51)]
result.facets["theme"][0]        # FacetValue(value='population_and_society', count=233)
result.facets.top("format")      # the commonest value: json, on 246
result.facets.counts()           # {filter: {value: count}}
result.facets.to_dict()          # {filter: [{'value', 'count', 'label'}, ...]}, for JSON
```

So one structure answers both "what can I filter by?" and "what is in this
result?", and every value in it narrows the search you already have. Counts
are per dataset: one with three CSV files counts once under `csv`.

If you guess a value, the error corrects you:

```python
catalog.search(theme="transprot")
# QueryError: unknown theme 'transprot'. Did you mean: transport?
```

## Reading a result

What comes back are four models — `Dataset`, `DataService`, `Distribution`
and `Publisher` — read by attribute, so your editor completes them.
[reference.md](reference.md) lists every field with how often it is filled.
The names are the ones the 0.12 dicts had: `dataset.title`,
`dataset.publisher.id`, `dist.access_url`. A few things are worth knowing
before you write any of it down.

**Text is a `MultilingualText`, always.** It holds `{"sv": ...}`,
`{"en": ...}`, or both. A language is there only when the publisher wrote it:
53% of datasets are Swedish only, 36% carry both, 10% are English only.

So **do not write `dataset.title["sv"]`** — it raises `KeyError` for one
dataset in ten. `.text()` takes the best there is:

```python
dataset.title.text()                 # Swedish if there is any, else English
dataset.title.text("en")             # the other way round, this once
dataset.publisher.name.text()        # every text field works the same way

Catalog(language="en")               # English first, for every .text()
```

`.text()` returns `None` for a field with nothing in it. A `MultilingualText`
is still a read-only mapping and compares equal to its dict, so
`dataset.title == {"sv": "Planbestämmelser"}` is true. Anything the registry
tagged as undetermined, or in a third language, is filed under `"sv"` — it is
a Swedish registry.

Keywords are per language too, in a `Keywords`:

```python
dataset.keywords.list()        # in the catalogue's language, else the other
dataset.keywords.list("en")
dataset.keywords.all()         # every keyword in either language, each once
```

**Anything from a fixed list of options is one short English word.** `themes`,
`access_rights`, `accrual_periodicity`, `publisher.type` — these come from
standard vocabularies, and you get `"transport"` rather than the web address the
registry publishes. The same word works as a filter, which is the point. They
stay English whatever the prose says, because `"annual"` is more useful to build
on than _årligen_.

**Except a licence, which is the one value nobody can read.** `cc_by_nc_sa_4_0`
tells you nothing without the page, so a licence is a small object and the
page comes with it:

```python
dataset.license                # <License cc_by_4_0>
dataset.license.id             # 'cc_by_4_0'
dataset.license.label.text()   # 'CC BY 4.0 (Attribution)'
dataset.license.uri            # 'http://creativecommons.org/licenses/by/4.0/'
```

`license` is `None` where the publisher named none. 39% of datasets say
`nolicense` or `otherlicense` — DIGG's own categories — and those have a label
too. It is not a filter: filter on `dataset.license.id` in Python.

**A language is its ISO code.** `languages` is `["sv"]`, `["sv", "en"]`, or
one of 63 others down to `fit` (Tornedalen Finnish, which has no two-letter
code). It is read, not searched by: `"en" in dataset.languages`.

**A filter is named for what you ask, a field for what is there.** You search
`theme="transport"` and read `dataset.themes`; `keyword=` reads
`dataset.keywords`; `format=` and `kind=` read the distributions;
`publisher=` matches `dataset.publisher.id` (or anything else the publisher
shows) and `publisher_type=` reads `dataset.publisher.type`. A facet is keyed
by the _filter_ name, because that is what you feed back in.

**The data is under `distributions`, and `access_url` is the one to read.**

```python
for dist in dataset.distributions:
    dist.kind          # 'file' — what it is
    dist.format        # 'csv'
    dist.access_url    # a page or service to get it through — nearly all
    dist.download_url  # a direct file link — only 7.4% have one
```

Each is one URL or `None`. Of 35,148 distributions, 32,548 carry **only**
`access_url` and 2,595 carry both; none carries `download_url` alone. Reading
only `download_url` would miss nine distributions in ten.

**A distribution is usually not a file.** `kind` says what it is, read from
the metadata with no request: `file`, `rowstore`, `ckan`, `huwise`, `pxweb`,
`kolada`, `doi`, `geodata`, `api`, `web_page` or `unknown`. Only 4,647 of the
35,148 are a `file`; 10,468 are PxWeb tables and 5,963 Kolada key figures,
which you call rather than download. Format `html` makes it a `web_page`
unless the address says otherwise — a file extension on a `download_url`, or
an address shaped like one of the APIs above. It is always there,
and `kind=` filters on it:

```python
catalog.datasets(kind="file")       # datasets with a file to download
catalog.facets()["kind"][:2]        # [FacetValue(value='pxweb', count=6704),
                                    #  FacetValue(value='kolada', count=5952)]
```

Nothing is ever missing. A field the publisher left empty is `None`, `[]` or
an empty text, so you never need `getattr` with a default. Four fields are
`None` unless they have something to say: `unverified`, `broken` and
`byte_size` on a distribution — `byte_size` is on the 1.4% whose publisher
states a size — and `stale` on a dataset or a data service.

**Every model turns into plain JSON.**

```python
dataset.to_dict()                                  # a dict, the same field names
print(dataset)                                     # that dict as indented JSON
catalog.datasets(theme="transport", as_dict=True)  # a list of dicts
catalog.search("cykel", as_dict=True)
# {'total': 386, 'offset': 0, 'limit': None, 'datasets': [...],
#  'facets': {...}, 'facets_omitted': {}}
```

Every method that returns models takes `as_dict=True`, which is exactly
`to_dict()` of what it would have returned. In `to_dict()` every field is
there: `broken`, `unverified`, `byte_size` and `stale` are `None` when
unset, and the publisher inside a dataset has `dataset_count`,
`data_service_count` and `facets` set to `None`, because only
`publishers()` and `publisher()` count them. Printing works on everything —
a `MultilingualText`, a facet, a whole `Facets` — and always prints the JSON
of `to_dict()`.

## Publishers

A publisher is the third kind of thing in the catalogue, next to datasets and
data services.

```python
for publisher in catalog.publishers()[:3]:
    print(publisher.dataset_count, publisher.id, publisher.name.text())
```

`publishers()` is a plain list of `Publisher`, most datasets first — 346 by
default. Each is who they are and how much of theirs is here:

```python
catalog.publisher("skolverket").to_dict()
# {'id': 'skolverket',
#  'uri': 'http://dataportal.se/organisation/SE2021004185',
#  'name': {'sv': 'Skolverket'}, 'alias': None,
#  'type': 'national_authority',
#  'homepage': 'https://www.skolverket.se/',
#  'email': 'support.oppnadata@skolverket.se',
#  'identifiers': ['2021004185'],
#  'dataset_count': 6, 'data_service_count': 5,
#  'facets': {'theme': [{'value': 'education_culture_and_sport', 'count': 5,
#                        'label': {...}}, ...],
#             'format': [...], 'kind': [...], 'access_rights': [...],
#             'accrual_periodicity': [...]}}
```

**`id` is the thing to hold on to.** It is what `publisher=` takes, what the
`publisher` facet reports, and what is on `dataset.publisher.id` — so it
joins the three. Not the URI: eight organisations have two.

**You can name a publisher any way it shows itself** — to `publisher()` and to
the `publisher=` filter alike:

```python
catalog.publisher("scb")                                             # its alias
catalog.publisher("statistikmyndigheten_scb_statistiska_centralbyran")  # its id
catalog.publisher("Statistikmyndigheten SCB - Statistiska centralbyrån")
catalog.publisher("2021000837")                                      # organisation number
catalog.publisher("http://dataportal.se/organisation/SE2021000837")  # URI
```

An alias is the one short name a publisher goes by, and most have none:
`scb`, `fohm`, `slu`, `smhi`, `uhr`, `kolada` and `energimyndigheten` are the
ones there are. They are kept by hand in
[publishers.json](../src/dataportalen/publishers.json), one entry per
publisher id with its name, URI and at most one alias, and show as the
publisher's `alias` — a string, or `None`. Add your own there; an alias that
would collide with an id, an organisation number or another alias is left out,
with a warning in the log.

`publisher()` adds `facets`: what that publisher's datasets are made of. It is
exactly `catalog.search(publisher=id, limit=0).facets` for five filters —
`theme`, `format`, `kind`, `access_rights` and `accrual_periodicity` — so any
value in it narrows a search:

```python
scb = catalog.publisher("scb")
scb.facets["accrual_periodicity"][:2]    # [FacetValue(value='annual', count=3001),
                                         #  FacetValue(value='monthly', count=547)]
catalog.datasets(publisher="scb", accrual_periodicity="monthly")   # those 547
```

**`publishers()` takes filters too**, the ones datasets and data services
share: `publisher`, `publisher_type`, `theme`, `keyword` and `access_rights`.
The counts are then what the filters select, and a publisher with nothing
that matches is not listed:

```python
catalog.publishers(publisher_type="local_authority")   # the 73 local authorities
catalog.publishers(theme="transport")[0]               # trafikverket: 335 datasets,
                                                       #   34 data services
```

The counts are this catalogue's. A publisher with nothing in it is not listed,
and `publisher()` returns `None` for it; a name nobody knows is an error with
suggestions.

For "who publishes the most CSV?", ask a search rather than the list —
`format` is not something a data service has, so `publishers()` does not take
it: `catalog.search(format="csv", limit=0).facets["publisher"]`.

## Data services

The registry holds 599 `dcat:DataService` entries — APIs rather than files. They
are in the same database and list the same way:

```python
for service in catalog.data_services(service_type="view_service")[:2]:
    print(service.title.text(), service.endpoint_url)
```

```text
Railway Transport Network - View Service (INSPIRE) http://geo-inspire.trafikverket.se/...
Road Transport Network - View Service (INSPIRE)    http://geo-inspire.trafikverket.se/...
```

`service_type` is `rest` (288 of them), `view_service` (31),
`download_service` (14), `transformation_service` and `discovery_service` (one
each); 264 say nothing. The last four are INSPIRE spatial service types, so
that is how you find WMS endpoints.

A `DataService` looks like a `Dataset`, minus what a service does not have and
plus what it does: `service_type`, `endpoint_url`, `endpoint_description`,
`serves_datasets`, `conforms_to`, and no `distributions`.
`data_services()` is a complete list like `datasets()`; `search()` is for
datasets only.

**Some filters do not apply, and saying so is the point:**

```python
catalog.data_services(format="csv")
# QueryError: data services have no 'format'. Available: publisher,
# publisher_type, service_type, theme, keyword, access_rights
```

A data service has no distributions, so no `format` or `kind`; no accrual
periodicity; and `modified` is on 7.5% of them, so no date filter. Coming
back with zero rows instead would read as "no CSV APIs" when the truth is
"wrong question".

The registry's link check never tests an `endpointURL` — only distributions,
landing pages and documentation links — so whether an API answers is not
something this package can tell you, and `exclude_broken` leaves data services
alone.

## Recipes

**Every CSV in a theme, with somewhere to get it**

```python
for dataset in catalog.datasets(theme="transport", format="csv"):
    for dist in dataset.distributions:
        if dist.format == "csv":
            print(dataset.title.text(), dist.access_url)
```

**What changed since a date, newest first**

```python
recent = catalog.datasets(modified_after="2026-09-01")
recent.sort(key=lambda d: d.modified or d.issued or "", reverse=True)
```

`modified` is the publisher's own date, and 63 of them are in the future. Sort by
it, but do not trust it as a fact about the world.

**Who publishes a subject**

```python
for row in catalog.search(theme="environment", limit=0).facets["publisher"][:3]:
    print(row.count, row.label.text())
```

**Only what is published openly**

```python
catalog.search("cykel", access_rights="public")
catalog.datasets(theme="transport", access_rights=["public", "none"])
```

**Only distributions the registry actually reached**

```python
sure = [dist for dataset in catalog.datasets(theme="transport")
        for dist in dataset.distributions if dist.unverified is None]
```

**By licence or language, which are not filters**

```python
cc_by = [d for d in catalog.datasets(theme="transport")
         if d.license and d.license.id == "cc_by_4_0"]
english = [d for d in catalog.datasets() if "en" in d.languages]
```

**Into a dataframe**

```python
import pandas
frame = pandas.DataFrame(catalog.datasets(theme="transport", as_dict=True))
```

Nested columns stay nested. One row per distribution is usually more useful:

```python
rows = [
    {"dataset": d.title.text(),
     "publisher": d.publisher.id,
     "kind": dist.kind,
     "format": dist.format,
     "url": dist.access_url}
    for d in catalog.datasets(theme="transport")
    for dist in d.distributions
]
frame = pandas.DataFrame(rows)
```

**Into a JSON file**

```python
import json

with open("cykel.json", "w", encoding="utf-8") as out:
    json.dump(catalog.search("cykel", as_dict=True), out, ensure_ascii=False)
```

**In CI, or anywhere that must not spend a minute on the network**

```python
catalog = Catalog("catalog.sqlite", max_age=None)
```

Cache or commit the database. It is used as it is, however old.

**Read the database yourself**

```python
from dataportalen import read_catalog

for record in read_catalog(catalog.database):
    record["type"]            # 'dataset' or 'data_service'
```

That is every record as a plain dict, unscoped — every distribution, the dead
ones carrying `broken`. It is the stored shape rather than `to_dict()`'s: a
mark such as `broken`, `unverified` or `stale` is a key only where it is set.
Or open the database with anything that speaks SQLite — the records are JSON
text in a `doc` column, keyed by the registry's own `context_id` and
`entry_id`:

```sql
SELECT type, count(*) FROM record GROUP BY type;
SELECT doc FROM record WHERE uri = 'https://example.org/data/roads';
SELECT key, value FROM meta;     -- first_retrieved, last_refreshed, schema
SELECT status, count(*) FROM source GROUP BY status;   -- the latest harvests
```

The stored JSON is what the registry said. `publisher["id"]`, `alias`,
`kind`, `stale` and the split of `broken` into `broken` and `unverified` are
added when a record is read, so raw SQL shows a slightly plainer record than
`read_catalog` does.

## What it does not do

It reads. There is nothing here that writes to the registry.

Things the registry has that this deliberately does not surface, with the
reason, because they are the likeliest things to go looking for:

- **Catalogues.** 656 are registered and only 157 hold a dataset; of the 152
  live ones, 127 have exactly one publisher, so a catalogue listing mostly
  duplicates `publisher`. Where a dataset came in through an aggregator,
  `dataset.context_id` identifies it, and `catalog.info()["sources"]` says
  how many sources failed their latest harvest and which of those still hold
  records — something to read, not a filter.
- **Agents.** 7,609 exist and 365 publish anything; 4,916 are individual
  researchers who reach no dataset. `catalog.publishers()` is the ones that
  matter.
- **Creators.** `dcterms:creator` is on 7,104 datasets, and on 6,174 of those
  it names the publisher a second time. The ~930 that name someone else are
  citing a source. Not a search axis, so not a field.
- **Place.** 4,471 datasets set a spatial coverage and 3,068 of those say
  "Sweden"; the rest are mostly municipalities tagging themselves, which
  `publisher=` already finds. `dataset.spatial` is still on the model; it
  is not a filter.
- **Licence and language as filters.** Both are on every record
  (`dataset.license`, `dataset.languages`) and filter in a line of Python
  ([recipes](#recipes)). They stopped being filters in 0.13, and asking for
  one says so.
- **A link verdict on every distribution.** Only a problem is marked; a
  distribution the registry reached carries nothing.
- **The registry's own reports** — nightly dataset counts, link checks, DCAT-AP
  quality scores. They describe the registry, not the data.

For raw RDF, `catalog.get(uri, format="turtle")` hands you an entry's graph as
text; `catalog.get(uri)` is the model. That and anything asked with
`live=True` are the only calls that reach the network after the download, and
none of them reaches a publisher.

## Why it works this way

The registry caps a page at **100 entries** and answers about **two requests a
second** no matter how many you have in flight — one thread gets 0.84, two get
2.26, and four, eight, sixteen and thirty-two get the same 2.26. So reading the
whole corpus costs about seven minutes whenever you do it, and the package uses
two threads to do it, because that is where the whole gain is.

That leaves two designs: pay seven minutes once, or pay a slice of it on every
question. This package pays once by default, and then keeps the copy current
for seconds a week. The consequences are worth knowing:

- Searching, counting and every facet are local and immediate. Facets over
  every dataset take under a second, which is not something the registry's
  index can do at all for keywords.
- Your copy is a snapshot that catches up when you build a `Catalog` and it
  is over `max_age` days old. Between those, it drifts;
  `catalog.info()["age_days"]` says by how much.
- Counts here can differ slightly from dataportal.se. That is what a snapshot
  means, and what `exclude_broken` means, rather than a bug.

The other design is there when you want it:
[`live=True`](#asking-the-registry-livetrue).
[internals.md](internals.md#what-the-registry-can-do) has the measurements.

## Asking the registry: `live=True`

For a quick question, or where 95 MB on disk is not welcome, a `Catalog` can
ask the registry directly. Same methods, same arguments, same models:

```python
from dataportalen import Catalog

live = Catalog(live=True)      # warns: every call is a request
result = live.search(theme="transport", format="csv", limit=10)
result.total                   # the registry's own count
result.facets["publisher"]     # in the same request
```

Building one warns with a `DataportalWarning`, because nothing about the
calls says they are now requests. Nothing is downloaded and `database` is
`None`.

On a local catalogue, `search()`, `datasets()`, `data_services()` and
`publishers()` take `live=True` for one call:

```python
catalog.search("cykel", limit=10, live=True)   # this one asks the registry
```

What you give up:

- **Speed.** A count (`limit=0`) takes about a tenth of a second and a page of
  full records a few seconds. The registry serves 100 to a page, and a call
  with no `limit` — every `datasets()`, `data_services()` and unpaged
  `search()` — fetches every page, one after another. Above 1,000 records it
  warns first, with how long it expects to take. Pass `limit` to `search()`.
- **Link health.** Nothing dead is excluded, no distribution says `broken` or
  `unverified`, nothing says `stale`, and `info()` is three counts:
  `{'datasets', 'data_services', 'publishers', 'live': True, 'sources': None}`.
- **Two filters.** `kind` and `publisher_type` are refused as filters on
  datasets and data services: the registry's index has neither.
  `publisher_type` still narrows `publishers()`, which reads it off each
  publisher. A distribution still carries its `kind`.
- **Three facets.** There is no `keyword`, `publisher_type` or `kind` facet.
- **The same `query`.** It is the registry's full-text search, a phrase of
  whole words anywhere in the entry, where the local catalogue matches part
  of a word in the title, the description and the keywords.
- **Exact agreement.** The registry's index covers every node of an entry's
  graph, so a filter can find a few more records than it does locally, and
  `modified_after` a few more again. Results come in URI order.

[cheatsheet.md](cheatsheet.md) has the whole comparison.

## When something goes wrong

Every error comes from `DataportalError`, so one `except` catches all of them:

```python
from dataportalen import DataportalError

try:
    result = catalog.search(theme="transport")
except DataportalError as error:
    print(error)
```

[reference.md](reference.md#errors) lists each one. A `DataportalWarning` is
not an error: it says a call is about to cost requests — building
`Catalog(live=True)`, or a live call that will fetch more than 1,000 records.
`warnings.simplefilter("ignore", DataportalWarning)` silences it. The
situations that are not really errors:

**"It spent a minute on the network and I did not ask."** Your copy was over
`max_age` days old — seven by default — so it caught up. `max_age=None` turns
that off.

**"`QueryError: updated is called accrual_periodicity now`."** A filter
changed in 0.13. Each removed one says what to use instead: `updated` is
`accrual_periodicity`, `text` is `query`, `issued_after` is covered by
`modified_after`, and `license`, `language` and `modified_before` are fields
to filter on in Python. The [CHANGELOG](../CHANGELOG.md) has the full table.

**"`datasets() returns every match and takes no limit`."** `datasets()`,
`data_services()` and `publishers()` are complete lists. A window, a query or
facets is `search()`.

**"`'Dataset' object is not subscriptable`."** Results are models, read by
attribute: `dataset.title`, not `dataset["title"]`. For dicts, pass
`as_dict=True` or call `to_dict()`.

**"`KeyError: 'sv'`."** 10% of datasets have no Swedish text. Use `.text()`.

**"`unknown keyword`."** `keyword=` is exact. For part of a word, use `query`.

**"My counts differ from dataportal.se."** Two reasons, in order of
likelihood: the default leaves out distributions that are dead and the
datasets left with none, and your copy is a snapshot
(`catalog.info()["age_days"]`). The registry's own counts are estimates too.
`Catalog(exclude_broken=False, rebuild=True)` is the whole registry as of now.

**"It came back empty and I expected something."** If the filter does not apply
to what you searched you get a `QueryError` naming the ones that do, so an empty
result really is an empty result. Check the value against `catalog.facets()`,
and remember `exclude_broken`.

**"It says the database is the wrong schema."** The record shape changed in a
release. `Catalog(rebuild=True)` once, and it is seven minutes. A database
written by 0.12 is read by 0.13 as it is.
