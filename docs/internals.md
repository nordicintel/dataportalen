# Internals

For working on the package. Using it is covered in [guide.md](guide.md).

1. [Where the short values come from](#where-the-short-values-come-from)
2. [Label coverage](#label-coverage)
3. [Rebuilding the table](#rebuilding-the-table)
4. [Module layout](#module-layout)
5. [What the registry can do](#what-the-registry-can-do)
6. [Development](#development)
7. [Releasing](#releasing)
8. [Measured, not assumed](#measured-not-assumed)
9. [Attribution](#attribution)

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
Without that fallback 13 of the 365 publishers and 98 of the 146 creators would
have no filter value at all, because they mint URIs the table never saw
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
python tools/build_vocabulary.py
```

Built from DIGG's own [DCAT-AP-SE templates](https://github.com/diggsweden/DCAT-AP-SE),
the authority tables the remaining URIs dereference to, and the GeoNames bulk
exports for place names. The script measures coverage against the URIs
publishers are actually using in the live registry and prints what it could not
resolve, so gaps stay visible rather than assumed. Commit the regenerated
`vocabulary.json` and release it as a patch version.

## Module layout

Six modules; callers import from the package root.

| Module | Holds |
| --- | --- |
| `core.py` | version, exceptions, logging and progress, the HTTP transport |
| `rdf.py` | namespaces, the RDF/JSON parser, the label table, the short-name layer |
| `models.py` | `Dataset`, `DataService`, `Distribution`, `Agent` and friends, `to_dict()`, and the result types |
| `query.py` | the `Q` Solr query builder |
| `client.py` | `Catalog` (the whole public surface), `_Registry` (HTTP and Solr), the download |
| `__init__.py` | 18 exports, and nothing else |

### Public and internal

`Catalog` is the public surface: `datasets()`, `data_services()`, `filters()`,
`get()`, `info()`, `close()`. Five methods where 0.6.0 had 32 on `Dataportal`
plus 7 on `LocalCatalog`.

Everything that talks to the registry sits behind `_Registry`, and the RDF layer
-- `Q`, `Graph`, `Entry`, `Dataset`, `SearchPage` -- is internal but very much
alive: the download is built on it. `_crawl` pages through
`_Registry._search(Q.rdf_type(...))` and parses each hit into a model before
calling `to_dict()`. None of it is exported, because 36 of the 42 `rdfType`
values in the registry are EntryStore bookkeeping and a public `search()` mostly
opened a door onto that.

`transport=` survives as a constructor argument for one reason worth knowing:
`tests/conftest.py` subclasses `BaseTransport` and uses it at 31 call sites,
which is how the whole offline suite runs with no network. Import it from
`dataportalen.core`.

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
| `foaf:Agent` + `foaf:Organization` + `prov:Agent` | 7,609 | nested as `publisher` and `creators`. 4,916 of them are `private_individual` and reach no dataset |
| vcard contact types (8 of them) | ~48,000 | nested as `contact_points` |
| EntryStore/EntryScape internals (17 types) | -- | no. `PipelineResult`, `List`, `CatalogContext`, `CatalogStatistics`, `LinkCheckReport`, `MQA`, `User`... the CMS talking to itself |
| `dcterms:Standard`, `prof:Profile`, `schema:Question` and friends | <400 | no |

Of 42 distinct types, six are DCAT and 36 are platform bookkeeping. That ratio
is why there is no public `search()`.

One asymmetry worth knowing if you join the two types: `servedByDataService`
appears on **29% of datasets** while `servesDataset` appears on **8% of data
services**. The link is recorded far more often from the dataset side.

### Which filters each type supports

Measured by crawling every metadata graph of both types -- all 23,576 datasets
and all 599 data services -- and counting predicates. Nothing was sampled.

Datasets take eleven, at these fill rates: `publisher`, `license` and
`publisher_type` 100%, `keyword` 94.8%, `language` 89.3%, `access_rights`
82.3%, `theme` 78.2%, `format` 69.7%, `updated` 63.3%, `creator` 30.1%, `place`
23.6%. Plus `text` and the date pair (`modified` 89.1%, `issued` 42.1%).

Data services take eight -- `access_rights` 97.8%, `publisher` 97.3%,
`publisher_type`, `keyword` 83.5%, `service_type` 55.9%, `theme` 53.8%,
`license` 51.8%, `creator`, plus `text` -- and **refuse** four with an error
naming the ones that work: `format` and `updated` are entirely absent from the
599, `place` is on 7.8% and `language` has one single value across all of them.
No date filters either: `modified` 7.5%, `issued` 0.8%.

Rejected for datasets, each with the number that rejected it:
`qualifiedAttribution` 28.7% and `provenance` 28.6% (free text, not values),
`temporal` 19% (a range, so it stays a field), `conformsTo` 3.6%, `subject`
2.1%, `hvdCategory` 1.5% with 11 clean values, `applicableLegislation` 1.5%
with **one** distinct value, and `dataQuality`/`byteSize`/`version` under 1%.
Nothing above 30% fill was left out.

The raw-to-slug collapse is where the vocabulary earns its keep: `theme` has 530
distinct URIs in the wild collapsing to 31 slugs, `language` 67 to 2, `place`
2,704 to 542.

`service_type` comes from `dcterms:type`: `rest` 288 (the Wikidata term),
`view_service` 31, `download_service` 14, `transformation_service` 1,
`discovery_service` 1 -- the last four INSPIRE. All of them already had slugs in
`vocabulary.json`, INSPIRE themes included.

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

The agent crawl asks for all three agent types. `foaf:Agent` alone (5,847) misses
the 69 entries typed `foaf:Organization`, and those 69 carry 2,073 of the 7,151
creator references -- a third of them, which landed as bare URIs until this was
found by checking a real download rather than a fixture.

A value that the vocabulary table does not know but the file does contain --
`parquet`, a bare GeoNames id -- is still a valid filter value: `_local_slugs`
falls back to what the catalogue actually holds, so everything a breakdown
reports can be filtered on. A value that is neither known nor present is
still an error, with suggestions.

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
them overturned a guess.

| Fact | Value |
| --- | --- |
| Datasets / data services / distributions | 23,576 / 599 / 34,916 |
| Full download | 673 requests, ~7 minutes, 64 MiB |
| Loading the file | ~1.3 s |
| A filtered search | 0.02-0.06 s |
| `filters()` over the whole corpus | ~0.47 s |
| `data_services(limit=0)` | 0.005 s |
| Distinct `rdfType` values in the registry | 42 (6 DCAT, 36 platform) |
| Catalogs registered / holding a dataset | 656 / 157 |
| Live catalogs with exactly one publisher | 127 of 152 |
| Widest aggregator catalog | context `818`: 6,607 datasets from **110 publishers** |
| Agents / of which publish anything | 7,609 / 365 |
| Agents that are `private_individual` | 4,916 (65%) |
| Distinct creator URIs / resolvable | 146 / 145 |
| Duplicate dataset URIs | 4 of 23,576 |
| Datasets Swedish-only / both / English-only | 53% / 36% / 10% |
| Publisher+creator organisations with an English name | 149 of 392 |
| ...of those, sv and en genuinely differ | 105 (70%) |
| `FakeTransport` call sites in the test suite | 31 |

Three of these changed a decision:

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
