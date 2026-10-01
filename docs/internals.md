# Internals

For working on the package. Using it is [guide.md](guide.md), and every method,
filter and record key is in [reference.md](reference.md).

1. [Where the short values come from](#where-the-short-values-come-from)
2. [Label coverage](#label-coverage)
3. [Rebuilding the table](#rebuilding-the-table)
4. [Module layout](#module-layout)
5. [The store](#the-store)
6. [What the registry can do](#what-the-registry-can-do)
7. [Development](#development)
8. [Releasing](#releasing)
9. [Measured, not assumed](#measured-not-assumed)
10. [Attribution](#attribution)

## Where the short values come from

The registry states controlled values as URIs:

```
http://publications.europa.eu/resource/authority/data-theme/TRAN
http://purl.org/adms/publishertype/LocalAuthority
http://creativecommons.org/licenses/by/4.0/
```

Each one is mapped to a short name derived from its official **English** label —
`transport`, `local_authority`, `cc_by_4_0` — and that name is the only form the
package accepts or returns. There is no code, URI and label to choose between.

English is deliberate for standardised terms; a Swedish rendering of "annual" or
"public" helps nobody building on this. Publisher-authored text is a different
thing, and is never translated or chosen for you: `title`, `description`,
`keywords` and an organisation's `name` are maps of whatever languages the
publisher wrote.

The mapping lives in two generated data files shipped inside the package:
`vocabulary.json` (1,037 terms, with 1,036 English and 935 Swedish labels) and
`organisations.json` (538 publishers, keyed by both slugged name and
organisation number). Nothing is fetched at runtime.

`label_for()` is the reverse, and is what puts a readable name beside a
breakdown value:

```python
from dataportalen.rdf import label_for

label_for("national_authority")
# {'en': 'National authority', 'sv': 'Nationell myndighet'}
```

An organisation has no vocabulary entry, so its label and its slug both come
from the records: the URI table first, then the name the publisher wrote.
Without that fallback 13 of the 365 publishers would have no filter value at
all, because they mint URIs the table never saw
(`fohm-app.folkhalsomyndigheten.se/...`, `myndighetsregistret.scb.se/...`).

```python
from dataportalen.rdf import slug_for, resolve

slug_for("http://publications.europa.eu/resource/authority/data-theme/TRAN")
# 'transport'
resolve("transport")
# ['http://publications.europa.eu/resource/authority/data-theme/TRAN']
```

A URI with no label falls back to its last path segment, slugified, so output is
always a short string and never a URI.

## Label coverage

Measured over a seeded random sample of 5,000 datasets drawn from random
positions across the corpus (25,038 vocabulary values):

| Field | Labelled |
| --- | --- |
| `themes` | 5,560 / 5,560 |
| `access_rights` | 4,110 / 4,110 |
| `accrual_periodicity` | 3,047 / 3,047 |
| `hvd_categories` | 57 / 57 |
| `languages` | 5,431 / 5,435 |
| `spatial` | 1,629 / 1,637 |
| `license` | 2,913 / 5,000 |
| `subjects` | 89 / 192 |
| **total** | **22,836 / 25,038 — 91%** |

95% of what is unlabelled is one vocabulary:
`https://dataportal.se/concepts/licensecategories/{nolicense,otherlicense}`,
2,087 of the 2,202 misses. Those URIs do not dereference (the host answers
`426`) and appear in neither DIGG's templates nor the dataportal.se frontend
translations, so there is no authoritative label to ship. Real licence URLs
resolve fine. The remainder is mostly GEMET concepts, whose host is unreachable
over plain HTTP.

Sampling is offset-based because the index has no random sort, so it is mildly
clustered — a good estimate rather than a census.

```bash
python tools/build_vocabulary.py --skip-build --sample 5000
python tools/build_vocabulary.py --skip-build --sample all   # exact, slower
```

## Rebuilding the table

```bash
python tools/build_vocabulary.py --extra-only   # apply EXTRA_LABELS offline
python tools/build_vocabulary.py                # the full rebuild (see below)
```

Built from DIGG's own [DCAT-AP-SE templates](https://github.com/diggsweden/DCAT-AP-SE),
the authority tables the remaining URIs dereference to, and the GeoNames bulk
exports for place names. The script measures coverage against the URIs
publishers are actually using in the live registry and prints what it could not
resolve, so gaps stay visible rather than assumed. Commit the regenerated
`vocabulary.json` and release it as a patch version.

`EXTRA_LABELS` in the script is ours: labels the sources lack or get wrong,
and they win. `--extra-only` applies them to the existing table with no
network, which is how DIGG's `nolicense` and `otherlicense` got their labels
in 0.10.0. A term in `EXTRA_LABELS` whose short name must not follow its
label -- those two, whose slugs were their URI tails before they had a label
-- is pinned in `rdf._FIXED_SLUGS`.

**The full rebuild has not worked since 0.7.0.** It calls `Dataportal.facet`
and `iter_datasets`, which went with the public client; it now says so
instead of failing on import. Growing a facet call and a dataset iterator
back onto `_Registry` is what it needs, and that is its own job.

## Module layout

Six modules; callers import from the package root.

| Module | Holds |
| --- | --- |
| `core.py` | version, exceptions, logging and progress, the HTTP transport |
| `rdf.py` | namespaces, the RDF/JSON parser, the label table, the short-name layer |
| `models.py` | `Dataset`, `DataService`, `Distribution`, `Agent` and friends, `to_dict()`, and the result types |
| `query.py` | the `Q` Solr query builder |
| `client.py` | `Catalog` (the whole public surface), `_Registry` (HTTP and Solr), the store, the download |
| `__init__.py` | 19 exports, and nothing else |

### Public and internal

`Catalog` is the public surface: `datasets()`, `data_services()`, `filters()`,
`get()`, `info()`, `close()`. Five methods where 0.6.0 had 32 on `Dataportal`
plus 7 on `LocalCatalog`. Plus `text()`, `default_catalog_path()` and the
result types -- see [reference.md](reference.md#everything-exported).

Everything that talks to the registry sits behind `_Registry`, and the RDF layer
-- `Q`, `Graph`, `Entry`, `Dataset`, `SearchPage` -- is internal but very much
alive: the download is built on it. `_crawl` pages through
`_Registry._search(Q.rdf_type(...))` and parses each hit into a model before
calling `to_dict()`. None of it is exported, because 36 of the 42 `rdfType`
values in the registry are EntryStore bookkeeping and a public `search()` mostly
opened a door onto that.

`_transport=` is the test seam: `tests/conftest.py` subclasses
`BaseTransport` and the suite passes it at a few hundred call sites, which is
how the whole offline suite runs with no network. It is underscored because
it is not a feature; a user has no reason to touch it.

## What the registry can do

Measured, because both numbers shape the package's design:

| | |
| --- | --- |
| Page size | capped at 100 by the registry; asking for 1000 returns 100 |
| Throughput | ~2.2 requests/second, and **concurrency does not help** — 8, 16 and 32 workers all measure the same |
| Rate limiting | none observed: no `Retry-After`, no rate-limit headers, no 429s in a 64-request burst |
| Deep paging | flat — offset 23,000 costs the same as offset 0 |
| Paging stability | walking 2,000 datasets gives 2,000 distinct entries under `created asc` and under `uri asc`. The crawl uses `uri asc` because it is unique per entry, so a page boundary cannot move |
| Duplicate URIs | a few datasets are published into two catalogues, so one `uri` can appear twice with different `context_id`/`entry_id`. In a full export: 23,576 entries, 23,572 distinct URIs |

So the ceiling is roughly 200 datasets a second whatever you do, which is why a
full download takes ~7 minutes -- and why the package reads the catalogue once
and searches it locally.

### What is in the registry, and what got into the file

Every `rdfType` in the registry, counted in full:

| Kind | Count | In the file? |
| --- | --- | --- |
| `dcat:Dataset` | 23,576 | yes |
| `dcat:Distribution` | 34,916 | nested in its dataset |
| `dcat:DataService` | 599 | yes |
| `dcat:Catalog` | 656 | no -- only 157 hold a dataset, and 127 of 152 live ones have exactly one publisher, so it duplicates `publisher`. `context_id` on the record identifies the harvest source |
| `foaf:Agent` + `foaf:Organization` + `prov:Agent` | 7,609 | nested as `publisher`. 4,916 of them are `private_individual` and reach no dataset |
| vcard contact types (8 of them) | ~48,000 | nested as `contact_points` |
| EntryStore/EntryScape internals (17 types) | -- | no. `PipelineResult`, `List`, `CatalogContext`, `CatalogStatistics`, `LinkCheckReport`, `MQA`, `User`... the CMS talking to itself |
| `dcterms:Standard`, `prof:Profile`, `schema:Question` and friends | <400 | no |

Of 42 distinct types, six are DCAT and 36 are platform bookkeeping. That ratio
is why there is no public `search()`.

One asymmetry worth knowing if you join the two types: `servedByDataService`
appears on **29% of datasets** while `servesDataset` appears on **8% of data
services**. The link is recorded far more often from the dataset side.

### The link check

The registry checks every URL it holds, nightly, and publishes the result per
catalogue as an `entryscape:LinkCheckReport`. The report's **metadata** is five
counters; the detail is its **resource**, a JSON array with one object per link:
the URL, the entry it belongs to, `status`, `statusMessage`, `checkedAt` and
`attempts`. That resource is the only place the per-link verdict exists, and it
is easy to miss if you only read the metadata graph.

159 catalogues, one latest report each, ~29 MiB, about ten seconds -- 165
requests on top of 691. The download reads them and marks every file the
check found broken: `{"reason", "checked"}` under `broken`, and nothing on a
file that passed or that the check never reached. Until 0.10.0 every file
carried a `link` dict -- 18,360 successes and 5,021 the check skipped, for the
11,762 that mattered -- and every record carried a landing-page verdict that
was `None` on 10,331 datasets and `broken` on 277 whose files were all fine.

What the check covers, counted over 40 reports: `dcat:downloadURL` 3,072,
`dcat:accessURL` 1,426, `dcat:landingPage` 130, `foaf:page` 78,
`dcterms:conformsTo` 42, `dcat:endpointDescription` 9 -- and
`dcat:endpointURL` never. So whether a data service's API answers is
something the registry does not know, and `exclude_broken` leaves data
services alone rather than pretending.

The verdict is passed through exactly as stated. `broken` covers everything the
checker could not fetch, and its `statusMessage` is whatever reason it gave --
`Not Found`, `Too Many Requests`, `No Content`, or nothing at all for 5,990 of
them. `Catalog(exclude_broken=True)`, the default, acts on it: the broken
files go, and so does a dataset whose every file was broken (5,331), because
metadata with nothing to fetch is what dead means. A dataset that never had
files (1,647) is not dead and stays.

A catalogue keeps about three days of reports; only the newest is read. Verdicts
are current: of 12,718 broken records, 12,711 were checked this year.

### Where each call goes

Everything but one reads the file. `datasets()`, `data_services()`, `filters()`,
`get()` and `info()` never touch the network; `get(uri, format=...)` is the
single exception, and you have to name a format to get it. The download is the
only other thing that makes requests. There is no mode switch, so there is
nothing to get wrong.

Searches return `Results`, a `list` of dicts carrying `total`, `offset`,
`limit`, `has_more` and `breakdown`.

The download writes one file with both kinds of line, each tagged `type`. A
record written before 0.7.0 has no `type` and is read as a dataset, which is all
those files held.

The agent crawl asks for all three agent types. `foaf:Agent` alone (5,847)
misses the 69 entries typed `foaf:Organization` and the 1,693 typed
`prov:Agent`, which was found by checking a real download rather than a
fixture. The reason it was found was `creators`, now dropped: those 69 carried
2,073 of the 7,151 creator references. Whether publishers alone still need all
three has not been measured, and the superset costs 18 pages of an 856-request
build against the cost of silently naming a publisher absent.

A value that the vocabulary table does not know but the file does contain --
`parquet`, a bare GeoNames id -- is still a valid filter value: `_local_slugs`
falls back to what the catalogue actually holds, so everything a breakdown
reports can be filtered on. A value that is neither known nor present is
still an error, with suggestions.

### Two things that will bite

**A local filter matches its value exactly when the file contains it.** Only
then does it fall back to vocabulary expansion. That ordering is load-bearing:
`json` also resolves to `application/json+zip`, whose own slug is
`json_in_a_zip`, so expanding first made `format="json"` return 14,173 where the
breakdown said 14,119 -- and the breakdown's counts stopped agreeing with the
searches its values produce. `tests/test_filters.py` round-trips every value of
every filter, which is the invariant to keep.

**An organisation's slug comes from the URI table first and its own name
second.** Dropping the fallback silently loses 13 of the 365 publishers,
because they mint URIs the table never saw. Dropping the table and using only
names would break every slug users have written down. An alias
(`aliases.json`) is resolved before either, to the canonical slug; the
records and the breakdown never show the alias.

**A URI is looked up in every spelling it comes in, not just as written.**
`rdf._variants` knew that `.../by/4.0/deed.sv`, `.../legalcode` and the
http/https pair all name one licence, but `slug_for` never asked it, so a
publisher who wrote the Swedish deed got the licence `deed_sv`. 2,578
distributions were filed under licences that do not exist -- `deed_sv` 1,486,
`1_0` 686, `4_0` 406 -- next to datasets filed correctly under `cc_by_4_0`.

**A language's short name is its ISO code, and the table maps to it.** The
registry names a language by its EU authority URI, whose tail is the ISO
639-3 code; `_ISO_639_1` maps that to the two-letter code where one exists.
Deriving the name from the label gave `swedish`, which read as an English word
describing a Swedish dataset, and hid the 183 datasets whose publisher wrote
`id.loc.gov/vocabulary/iso639-1/sv` directly as a second value `sv`.

**`creators` is read off the graph and thrown away.** `dcterms:creator` is on
7,104 datasets, and on 6,174 of those it names the publisher again: the same
agent URI on 5,292, the publisher's name plus a survey or system suffix on 634
(`Folkhalsomyndigheten, Sminet`), an internal department on 150
(`geodataenheten` under Örebro kommun). The ~930 that name someone else are
citing a source -- Socialstyrelsen under Folkhälsomyndigheten, Medlingsinstitutet
under SCB -- which is provenance, not a second publisher. A filter whose two
largest values were 4,468 datasets pointing at their own publisher was not a
search axis, so the field went with it in 0.9.0 and `SCHEMA_VERSION` went to
`2` to force a rebuild of files that still hold it.

**A row is keyed on `context_id`/`entry_id`, not on `uri`.** Four datasets in
the corpus share a resource URI with another, because the same dataset was
harvested into two catalogues. Keying on the URI merged those pairs and lost
four records outright -- and context/entry is what the registry itself
identifies an entry by, so it is also the right thing for a refresh to replace.

## The store

The catalogue is a SQLite database, because the point of it is to be brought up
to date cheaply and an upsert does that with no bookkeeping:

```sql
CREATE TABLE meta   (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE record (
    context_id TEXT NOT NULL,
    entry_id   TEXT NOT NULL,
    uri        TEXT,
    type       TEXT NOT NULL,      -- dataset | data_service
    harvested  TEXT,               -- the REGISTRY's timestamp for the entry
    doc        TEXT NOT NULL,      -- the record, as JSON
    PRIMARY KEY (context_id, entry_id)
);
```

`harvested` is the registry's own `modified` from the entry envelope, not the
publisher's `dcterms:modified`. The publisher's is 89% filled, and 63 datasets
date themselves in the future, so it cannot answer "did this change since I
looked". There is no index on it: the registry filters by it server-side and we
never do.

A refresh asks `modified:[<last_refreshed> TO *]` and upserts what comes back.
Measured on the real registry: **38 s for a day's churn against 302 s for a
rebuild**, 630 datasets instead of 23,581.

`doc` is plain JSON text rather than a compressed blob. Measured over the whole
corpus: plain is 94 MiB and loads in ~1.4 s, zlib is 36 MiB at ~2.1 s, and the
JSONL this replaced was 70 MiB at ~1.4 s. `json.loads` accounts for ~1.2 s of
every one of them, so the store itself barely moves the needle -- and plain text
means the records are readable with any SQLite browser, which a blob would take
away. The database costs 24 MiB more than the JSONL and no measurable time.

What a refresh cannot see is a deletion. A withdrawn dataset keeps its row until
`rebuild=True` rebuilds, and there is no cheap way to notice: the search
returns full graphs, so listing the registry's URIs costs the same 236 pages as
copying it. A full build clears `record` inside the same transaction as the
write, so it is a real rebuild -- it was an upsert until 0.9.0, and a
"rebuild" left 27 rows carrying a field the schema had dropped -- and a
download that dies still leaves the old catalogue untouched.

## Development

```bash
pip install -e ".[dev]"
git config core.hooksPath .githooks     # once per clone: lint before commits

pytest                                  # offline, against recorded fixtures
DATAPORTAL_LIVE=1 pytest -m network     # against the real registry
ruff check src tests tools
```

## Releasing

**Pushing to `main` never publishes anything.** The only workflow that uploads is
`Release`, and it runs only when a GitHub Release is published, or when you run
it by hand with the dry-run switch turned off.

1. Bump `__version__` in `src/dataportalen/core.py` — the single source, read by
   `pyproject.toml` and by the User-Agent.
2. Merge to `main`.
3. `gh release create v0.3.0 --title "v0.3.0" --notes "What changed"`
   (or **Releases → Draft a new release**, tag `v0.3.0`, target `main`).
4. Watch **Actions → Release**. It checks the tag against `core.py`, tests,
   builds and uploads.

To rehearse: **Actions → Release → Run workflow**, `dry_run` on by default —
builds, validates the metadata, checks the wheel imports, uploads nothing.

Requires a `PYPI_TOKEN` repository secret (a PyPI API token, starting `pypi-`)
under **Settings → Secrets and variables → Actions**.

If the tag and the version disagree the workflow stops before uploading. If an
upload fails partway, **do not retry the same version** — PyPI refuses
re-uploads of a version even after deletion; bump the patch and release again.

## Measured, not assumed

The numbers this design rests on, and where they came from. Everything here was
measured against the live registry rather than estimated, because several of
them overturned a guess. Counted on 2026-09-30; the registry gains a handful of
datasets a day, so the absolute figures drift and the ratios do not.

| Fact | Value |
| --- | --- |
| Datasets / data services / distributions | 23,582 / 599 / 35,148 in the file (2026-10-01) |
| The default `Catalog`: public, with a working file | 12,653 datasets |
| `access_rights`: public / non_public / restricted / unset | 17,682 / 1,489 / 244 / 4,167 |
| Link verdicts on files: success / broken / excluded | 18,360 / 11,762 / 5,021 |
| Datasets with every file broken / with no files at all | 5,331 / 1,647 |
| Landing-page verdicts: none / success / excluded / broken | 10,331 / 7,791 / 5,021 / 439 |
| ...of the broken landing pages, with every file fine | 277 |
| What the link check covers (40 reports) | downloadURL 3,072, accessURL 1,426, landingPage 130, foaf:page 78, conformsTo 42, endpointDescription 9, endpointURL 0 |
| Link-check reports: requests / time | 165 / ~10 s |
| Registry throughput by threads: 1 / 2 / 4 / 8 | 0.84 / 2.26 / 2.46 / 2.31 req/s |
| Full build: requests / time / size | 856 / ~6 min with 2 threads (17 with 1) / 94 MiB |
| Incremental refresh, one day of churn | 630 datasets, ~38 s |
| Loading the database | ~1.4 s (of which json.loads ~1.2 s) |
| Distributions with >1 access URL / >1 download URL | 147 / 130 of 35,148 |
| Data services with >1 endpoint URL / description / served dataset | 4 / 1 / 8 of 599 |
| Distribution licences mis-slugged before 0.10.0 | 2,578 (`deed_sv` 1,486, `1_0` 686, `4_0` 406) |
| Datasets saying nolicense / otherlicense | 8,231 / 913 |
| Distinct languages on datasets / with a two-letter code | 66 / 50 |
| Datasets with a spatial coverage / of those, "Sweden" | 4,471 / 3,068 |
| Sub-national place values / on exactly one dataset | 540 / 223 |
| A filtered search | ~0.06 s |
| `filters()` over the whole corpus | ~0.5 s |
| `data_services(limit=0)` | 0.005 s |
| Distinct `rdfType` values in the registry | 42 (6 DCAT, 36 platform) |
| Catalogs registered / holding a dataset | 656 / 157 |
| Live catalogs with exactly one publisher | 127 of 152 |
| Widest aggregator catalog | context `818`: 6,607 datasets from **110 publishers** |
| Agents / of which publish anything | 7,609 / 365 |
| Agents that are `private_individual` | 4,916 (65%) |
| Datasets naming a creator / of those, the publisher again | 7,104 / 6,174 |
| Duplicate dataset URIs | 4 of 23,576 |
| Datasets Swedish-only / both / English-only | 53% / 36% / 10% |
| Publisher organisations with an English name | 149 of 392 |
| ...of those, sv and en genuinely differ | 105 (70%) |
| `FakeTransport` call sites in the test suite | 31 |

Four of these changed a decision:

**Concurrency has one useful step.** One thread gets 0.84 requests a second
and two get 2.26; four and eight get the same 2.26. So the pool is fixed at
two and the `workers=` argument went: the only choice it offered was between
six minutes and seventeen.

**The counter was slower than the thing it replaced.** `count_datasets(theme=
"transport")` measured 53.1 ms against 43.9 ms for `datasets(theme="transport",
limit=0)` -- and the cheap version threw away the breakdown it got for free. It
made sense against the API, where a count was one request and a list was 236.
Locally the scan *is* the count, so it went.

**A language setting was the wrong shape.** 53% of datasets are Swedish only,
36% carry both languages and 10% are English only, so a single-language read had
to fall back for a tenth of the registry and silently discarded the English of
another third. And of the 149 organisations that have both names, 105 genuinely
differ -- `Svensk nationell datatjänst` / `Swedish National Data Service` -- so
collapsing them would have lost a real translation in a quarter of cases.

**Browse-lists over registry internals were mostly noise.** 656 catalogs of
which 499 hold nothing; 7,609 agents of which 365 publish anything and 4,916 are
individual researchers. Both listings went, and `context_id` on the record
covers the one case the catalog listing answered.

## Attribution

`vocabulary.json` is data, not code, compiled from third-party sources and
redistributed under their terms:

| Source | Used for | Licence |
| --- | --- | --- |
| [DIGG DCAT-AP-SE](https://github.com/diggsweden/DCAT-AP-SE) | Swedish/English vocabulary labels | CC BY 4.0 |
| [GeoNames](https://www.geonames.org/) | place names for `spatial` | CC BY 4.0 |
| [EU Vocabularies](https://op.europa.eu/en/web/eu-vocabularies) | themes, file types, frequencies, languages | Decision 2011/833/EU |
| [INSPIRE registry](https://inspire.ec.europa.eu/registry) | INSPIRE themes and code lists | Decision 2011/833/EU |

The same notice ships with the package, at the bottom of [LICENSE](../LICENSE)
and in the `_comment` key of `vocabulary.json` itself.
