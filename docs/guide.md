# Guide

How to use it, in the order you run into things. Every method, filter and
record key is listed in [reference.md](reference.md), and
[cheatsheet.md](cheatsheet.md) shows every call with its arguments and output;
this page is the part worth reading once.

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
11. [When something goes wrong](#when-something-goes-wrong)

## The first run

```python
from dataportalen import Catalog

catalog = Catalog()
```

**That takes about seven minutes the first time** and writes ~95 MB to disk. Do
it deliberately rather than inside something you expect to be quick — it prints
a progress line, so it never looks like a hang.

Every search after that is local: hundredths of a second, no network, works on
a plane. That is the whole design, and [why](#why-it-works-this-way) is worth
two minutes if anything about it surprises you.

The copy is a SQLite database in the usual cache directory —
`%LOCALAPPDATA%\dataportalen\catalog.sqlite` on Windows,
`~/.cache/dataportalen/catalog.sqlite` elsewhere — so one copy serves every
project and nothing lands in a repository.

```python
catalog.info()
# {'database': 'C:\\Users\\you\\AppData\\Local\\dataportalen\\catalog.sqlite',
#  'first_retrieved': '2026-09-30T17:57:14',
#  'last_refreshed': '2026-10-01T13:04:54',
#  'downloaded': '2026-10-01T13:04:54', 'age_days': 0,
#  'bytes': 98951168, 'datasets': 17601, 'data_services': 578,
#  'publishers': 294}
```

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

## What the catalogue holds

Two decisions are made when you build the `Catalog`, because otherwise they
would get in the way of every search.

**Dead files are left out.** The registry checks every file URL nightly and
calls 11,761 of 35,148 broken — but its message says two different things:

| The registry's message | Files | |
| --- | --- | --- |
| an HTTP error: Not Found, Forbidden, Too Many Requests, … | 3,342 | a server said no: **dead** |
| nothing usable: no message, a failed connection, a timeout | 8,419 | the checker did not get through: **unverified** |

By default the dead files are gone from the records, and so are the 81 public
datasets whose every file is dead. An unverified file stays, and says so:

```python
dist["unverified"]    # {'reason': 'timeout', 'checked': '2026-10-01T02:52:59'}
```

The difference matters. Statistics Sweden's API resets the registry's
checker, so 7,091 of its links are "broken" while every one of them answers an
ordinary request. Treating those as dead removed 4,270 of SCB's 4,306 datasets.

```python
Catalog(exclude_broken=False)   # keep every file; the dead ones say `broken`
```

A dataset that never had files (1,647 of them: APIs, registers) is not dead
and stays either way. Both verdicts are the registry's own data; this package
tests no link itself.

**Only public datasets are held.** 17,682 say `access_rights="public"`. 1,489
say `non_public`, 244 `restricted`, and 4,167 — 17.7%, mostly universities —
say nothing at all. The last group is a choice rather than a default, so it has
a name:

```python
Catalog(access_rights=["public", "none"])   # public, plus the ones that set nothing
Catalog(access_rights=None)                  # everything
```

`info()["datasets"]` is what the `Catalog` holds after both — 17,601 by
default; the database underneath always holds all 23,582, so changing either is
a new `Catalog`, not a new download.

## Searching

```python
from dataportalen import text

page = catalog.datasets(query="cykel")

page.total                                 # 330 — how many matched
for dataset in page:
    print(text(dataset["title"]))
```

`page` is an ordinary list of dictionaries that also knows its own total, so it
slices and goes straight into anything that takes records.

`query` is a phrase: it matches where those words appear together, ignoring
case, in the title, the description or the keywords, in either language.
`query="air quality"` finds the 14 datasets where those words sit together,
not the hundreds that have both somewhere.

**Filters combine, and all of them have to match:**

```python
catalog.datasets(theme="transport", format="csv", publisher="trafikverket")
```

Values are short and lowercase; you never type a web address. There are nine,
plus `query` and four dates, and [reference.md](reference.md#dataset-filters)
lists each with how much of the corpus it covers. The ones you will reach for:

```python
catalog.datasets(query="cykel")                    # a phrase, anywhere in the text
catalog.datasets(publisher="scb")                  # who put it on the portal
catalog.datasets(theme="transport")                # the subject
catalog.datasets(format="csv")                     # a format you can download
catalog.datasets(keyword="geodata")                # the publisher's own tags
catalog.datasets(language="en")                    # the language of the data
catalog.datasets(modified_after="2024-01-01")      # changed since
```

A list means any of them will do, for every filter:

```python
catalog.datasets(format=["csv", "xlsx"])           # either format
catalog.datasets(keyword=["geodata", "trafik"])    # either keyword
```

`keyword` is exact and ignores case — `Kommun`, `kommun` and `KOMMUN` are one
keyword — and a keyword nobody uses is an error with suggestions, like any
other filter. For a part of a word, use `query`.

**How much comes back.** There are no pages. A search scans the file and `limit`
says how much of the result you want to hold:

```python
catalog.datasets(theme="transport")               # the first 50 (the default)
catalog.datasets(theme="transport", limit=None)   # all 429
catalog.datasets(theme="transport", limit=10, offset=100)
catalog.datasets(theme="transport", limit=0)      # the count and facets, no rows
```

`page.total` is exactly how many matched — not an estimate — and that last line
is how you count things:

```python
catalog.datasets(publisher="trafikverket", limit=0).total     # 294
```

## Facets: finding out what you can filter by

You filter with a value; a facet tells you which values exist. `catalog.facets()`
is every facet, with counts, before you search.

```python
for row in catalog.facets()["publisher"][:3]:
    print(row.count, row.value, "-", text(row.label))
```

```text
5863 radet_for_framjande_av_kommunala_analyser_kolada - Rådet för främjande av kommunala analyser - Kolada
4303 statistikmyndigheten_scb_statistiska_centralbyran - Statistikmyndigheten SCB - Statistiska centralbyrån
1676 folkhalsomyndigheten - Folkhälsomyndigheten
```

`row.value` is what you filter with, `row.label` is what you show a person, and
`row.count` is how many there are. A row is also a plain `(value, count)` pair,
so it unpacks in a loop.

```python
list(catalog.facets())
# ['publisher', 'publisher_type', 'theme', 'keyword', 'format',
#  'license', 'access_rights', 'updated', 'language']
```

**Every value round-trips.** `catalog.datasets(theme=value, limit=0).total` is
exactly the count the row claimed, for every filter and every value. Checked
on the real catalogue for all 22,401 values, keywords included: no exceptions.

Some facets are long — 16,135 distinct keywords. Cap them, and what was cut is
counted rather than dropped quietly:

```python
facets = catalog.facets(limit=10)
facets["keyword"]            # the top 10
facets["keyword"].omitted    # 16125
facets.omitted               # {'keyword': 16125, 'publisher': 284, ...}
```

A search carries its own facets, and they are the same structure counted over
**what matched** rather than over everything:

```python
page = catalog.datasets(query="cykel")
page.facets["publisher"]       # [('radet_..._kolada', 213), ('sodertalje_kommun', 18), ...]
page.facets["theme"]           # [('government_and_public_sector', 214), ...]
page.facets.top("format")      # the commonest value
page.facets.to_dict()          # {filter: {value: count}}, for JSON
```

So one structure answers both "what can I filter by?" and "what is in this
result?", and every value in it narrows the search you already have.

If you guess a value, the error corrects you:

```python
catalog.datasets(theme="transprot")
# QueryError: unknown theme 'transprot'. Did you mean: transport?
```

## Reading a result

Every result is the same dictionary.
[reference.md](reference.md#a-dataset-record) lists every key with how often it
is filled, and each has a `TypedDict` your editor can complete from. Five things
are worth knowing before you write any of it down.

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
`access_rights`, `accrual_periodicity`, `publisher["type"]` — these come from
standard vocabularies, and you get `"transport"` rather than the web address the
registry publishes. The same word works as a filter, which is the point. They
stay English whatever the prose says, because `"annual"` is more useful to build
on than _årligen_.

**Except a licence, which is the one value nobody can read.** `cc_by_nc_sa_4_0`
tells you nothing without the page, so the licence is a small dict and the
page comes with it:

```python
dataset["license"]
# {'id': 'cc_by_4_0',
#  'label': {'en': 'CC BY 4.0 (Attribution)'},
#  'uri': 'http://creativecommons.org/licenses/by/4.0/'}
```

`id` is what `license=` filters on. 39% of datasets say `nolicense` or
`otherlicense` — DIGG's own categories — and those have a label too.

**A language is its ISO code.** `languages` is `["sv"]`, `["sv", "en"]`, or
one of 63 others down to `fit` (Tornedalen Finnish, which has no two-letter
code), and `language="sv"` filters on it.

**The filter is singular, the field is plural.** You search `theme="transport"`
and read `dataset["themes"]`. Three differ this way — `theme`/`themes`,
`language`/`languages`, `updated`/`accrual_periodicity`. The rest read as you
would guess, and a facet is keyed by the _filter_ name, because that is what you
feed back in.

**The files are under `distributions`, and `access_url` is the one to read.**

```python
for dist in dataset["distributions"]:
    dist["format"]        # 'csv'
    dist["access_url"]    # a page or service to get it through — nearly all
    dist["download_url"]  # a direct file link — only 7.4% have one
```

Each is one URL or `None`. Of 35,148 distributions, 32,548 carry **only**
`access_url` and 2,595 carry both; none carries `download_url` alone. Reading
only `download_url` would miss nine files in ten.

Anything the publisher left out is `null`, `[]` or `{}`, never missing — a key
is always there, so you never need `.get()`. Three keys on a file are the
exception, present only when they have something to say: `unverified`,
`broken`, and `byte_size`, which is on the 1.4% of files whose publisher states
a size.

## Publishers

A publisher is the third kind of thing in the catalogue, next to datasets and
data services.

```python
for publisher in catalog.publishers()[:3]:
    print(publisher["dataset_count"], publisher["id"], text(publisher["name"]))
```

`publishers()` is a plain list, most datasets first — 294 by default. Each row
is who they are and how much of theirs is here:

```python
catalog.publisher("skolverket")
# {'id': 'skolverket',
#  'uri': 'http://dataportal.se/organisation/SE2021004185',
#  'name': {'sv': 'Skolverket'}, 'aliases': [],
#  'type': 'national_authority',
#  'homepage': 'https://www.skolverket.se/',
#  'email': 'support.oppnadata@skolverket.se',
#  'identifiers': ['2021004185'],
#  'dataset_count': 6, 'data_service_count': 5,
#  'facets': {'theme': {'education_culture_and_sport': 5, ...},
#             'format': {'json': 5, 'html': 1, ...},
#             'license': {'cc0_1_0': 5, 'nolicense': 1}, ...}}
```

**`id` is the thing to hold on to.** It is what `publisher=` takes, what the
`publisher` facet reports, and what is on `dataset["publisher"]["id"]` — so it
joins the three. Not the URI: eight organisations have two.

**You can name a publisher any way it shows itself** — to `publisher()` and to
the `publisher=` filter alike:

```python
catalog.publisher("scb")                                             # an alias
catalog.publisher("statistikmyndigheten_scb_statistiska_centralbyran")  # its id
catalog.publisher("Statistikmyndigheten SCB - Statistiska centralbyrån")
catalog.publisher("2021000837")                                      # organisation number
catalog.publisher("http://dataportal.se/organisation/SE2021000837")  # URI
```

Aliases — `scb`, `fohm`, `slu`, `smhi`, `uhr`, `kolada`, `energimyndigheten` —
are kept by hand in [aliases.json](../src/dataportalen/aliases.json) and show in
the publisher's `aliases`. Add your own there.

`publisher()` adds `facets`: what that publisher's datasets are made of. It is
exactly `catalog.datasets(publisher=id, limit=0).facets` for six filters, so
any value in it narrows a search:

```python
scb = catalog.publisher("scb")
scb["facets"]["updated"]                           # {'annual': 3001, 'monthly': 547, ...}
catalog.datasets(publisher="scb", updated="monthly")
```

The counts are this catalogue's. A publisher with nothing in it is not listed,
and `publisher()` returns `None` for it; a name nobody knows is an error with
suggestions.

For "who publishes the most CSV?", ask a search rather than the list:
`catalog.datasets(format="csv", limit=0).facets["publisher"]`.

## Data services

The registry holds 599 `dcat:DataService` entries — APIs rather than files. They
are in the same file and search the same way:

```python
for service in catalog.data_services(service_type="view_service", limit=2):
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
what it does: `endpoint_url`, `endpoint_description`, `serves_datasets`,
`conforms_to`, and no `distributions`.

**Some filters do not apply, and saying so is the point:**

```python
catalog.data_services(format="csv")
# QueryError: data services have no 'format'. Available: publisher,
# publisher_type, service_type, theme, keyword, license, access_rights, query
```

A data service has no distributions, so no `format`; no accrual periodicity, so
no `updated`; `language` has one single value across all 599; and `modified` is
on 7.5% of them, so no date filters. Coming back with zero rows instead would
read as "no CSV APIs" when the truth is "wrong question".

The registry's link check never tests an `endpointURL` — only files, landing
pages and documentation links — so whether an API answers is not something this
package can tell you, and `exclude_broken` leaves data services alone.

## Recipes

**Every CSV in a theme, with somewhere to get it**

```python
for dataset in catalog.datasets(theme="transport", format="csv", limit=None):
    for dist in dataset["distributions"]:
        if dist["format"] == "csv":
            print(text(dataset["title"]), dist["access_url"])
```

**What changed since a date, newest first**

```python
recent = catalog.datasets(modified_after="2026-09-01", limit=None)
recent.sort(key=lambda r: r["modified"] or "", reverse=True)
```

`modified` is the publisher's own date, and 63 of them are in the future. Sort by
it, but do not trust it as a fact about the world.

**Who publishes a subject**

```python
for row in catalog.datasets(theme="environment", limit=0).facets["publisher"][:3]:
    print(row.count, text(row.label))
```

**Only files the registry actually reached**

```python
sure = [dist for dataset in catalog.datasets(theme="transport", limit=None)
        for dist in dataset["distributions"] if "unverified" not in dist]
```

**Into a dataframe**

```python
import pandas
frame = pandas.DataFrame(catalog.datasets(theme="transport", limit=None))
```

Nested columns stay nested. One row per file is usually more useful:

```python
rows = [
    {"dataset": text(d["title"]),
     "publisher": d["publisher"]["id"],
     "format": dist["format"],
     "url": dist["access_url"]}
    for d in catalog.datasets(theme="transport", limit=None)
    for dist in d["distributions"]
]
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

That is every record, unscoped — every access_rights value and every file, the
dead ones carrying `broken`. Or open it with anything that speaks SQLite — the
records are JSON text in a `doc` column, keyed by the registry's own
`context_id` and `entry_id`:

```sql
SELECT type, count(*) FROM record GROUP BY type;
SELECT doc FROM record WHERE uri = 'https://example.org/data/roads';
SELECT key, value FROM meta;     -- first_retrieved, last_refreshed, schema
```

The stored JSON is what the registry said. `publisher["id"]`, `aliases` and the
split of `broken` into `broken` and `unverified` are added when a record is
read, so raw SQL shows a slightly plainer record than `read_catalog` does.

## What it does not do

It reads. There is nothing here that writes to the registry.

Things the registry has that this deliberately does not surface, with the
reason, because they are the likeliest things to go looking for:

- **Catalogues.** 656 are registered and only 157 hold a dataset; of the 152
  live ones, 127 have exactly one publisher, so a catalogue listing mostly
  duplicates `publisher`. Where a dataset came in through an aggregator,
  `record["context_id"]` identifies it.
- **Agents.** 7,609 exist and 365 publish anything; 4,916 are individual
  researchers who reach no dataset. `catalog.publishers()` is the ones that
  matter.
- **Creators.** `dcterms:creator` is on 7,104 datasets, and on 6,174 of those
  it names the publisher a second time. The ~930 that name someone else are
  citing a source. Not a search axis, so not a field.
- **Place.** 4,471 datasets set a spatial coverage and 3,068 of those say
  "Sweden"; the rest are mostly municipalities tagging themselves, which
  `publisher=` already finds. `dataset["spatial"]` is still on the record; it
  is not a filter.
- **A link verdict on every file.** Only a problem is marked; a file the
  registry reached carries nothing.
- **The registry's own reports** — nightly dataset counts, link checks, DCAT-AP
  quality scores. They describe the registry, not the data.

For raw RDF, `catalog.get(uri, format="turtle")` hands you an entry's graph as
text. That is the only call that reaches the network after the download.

## Why it works this way

The registry caps a page at **100 entries** and answers about **two requests a
second** no matter how many you have in flight — one thread gets 0.84, two get
2.26, and four, eight, sixteen and thirty-two get the same 2.26. So reading the
whole corpus costs about seven minutes whenever you do it, and the package uses
two threads to do it, because that is where the whole gain is.

That leaves two designs: pay seven minutes once, or pay a slice of it on every
question. This package pays once, and then keeps the copy current for seconds a
week. The consequences are worth knowing:

- Searching, counting and every facet are local and immediate. Facets over
  every dataset take a third of a second, which is not something the
  registry's index can do at all for keywords.
- Your copy is a snapshot that catches up when you build a `Catalog` and it
  is over `max_age` days old. Between those, it drifts;
  `catalog.info()["age_days"]` says by how much.
- Counts here can differ slightly from dataportal.se. That is what a snapshot
  means, and what `access_rights` and `exclude_broken` mean, rather than a bug.

[internals.md](internals.md#what-the-registry-can-do) has the measurements.

## Without a download: `LiveCatalog`

For a quick question, or where 95 MB on disk is not welcome,
`LiveCatalog` asks the registry directly. Same methods, same arguments, same
records:

```python
from dataportalen import LiveCatalog

live = LiveCatalog()
page = live.datasets(theme="transport", format="csv", limit=10)
page.total                     # the registry's own count
page.facets["publisher"]       # one request for both
```

What you give up: `limit` is at most 100 (a page is all the registry serves),
there is no link health so nothing dead is excluded, `publisher_type` is
refused, and the facets have no `keyword`. A count takes about a tenth of a
second, a page of full records a few seconds. The counts match the local
ones for publishers and every data service filter; for a few dataset values
the registry's index finds a few more, and the date filters can differ either
way. [cheatsheet.md](cheatsheet.md#livecatalog) has the whole comparison.

## When something goes wrong

Every error comes from `DataportalError`, so one `except` catches all of them:

```python
from dataportalen import DataportalError

try:
    page = catalog.datasets(theme="transport")
except DataportalError as error:
    print(error)
```

[reference.md](reference.md#errors) lists each one. The situations that are not
really errors:

**"It spent a minute on the network and I did not ask."** Your copy was over
`max_age` days old — seven by default — so it caught up. `max_age=None` turns
that off.

**"My counts differ from dataportal.se."** Three reasons, in order of
likelihood: the default holds only `public` datasets with a file that is not
dead; your copy is a snapshot (`catalog.info()["age_days"]`); and the
registry's own counts are estimates. `Catalog(access_rights=None,
exclude_broken=False, rebuild=True)` is the whole registry as of now.

**"`KeyError: 'sv'`."** 10% of datasets have no Swedish text. Use `text()`.

**"`unknown keyword`."** `keyword=` is exact. For part of a word, use `query=`.

**"It came back empty and I expected something."** If the filter does not apply
to what you searched you get a `QueryError` naming the ones that do, so an empty
result really is an empty result. Check the value against `catalog.facets()`,
and remember what the `Catalog` holds.

**"It says the database is the wrong schema."** The record shape changed in a
release. `Catalog(rebuild=True)` once, and it is seven minutes.
