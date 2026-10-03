# Internals

For working on the package. Using it is [guide.md](guide.md), and every method,
filter and record key is in [reference.md](reference.md).

1. [Where the short values come from](#where-the-short-values-come-from)
2. [Label coverage](#label-coverage)
3. [Rebuilding the table](#rebuilding-the-table)
4. [Module layout](#module-layout)
5. [What the registry can do](#what-the-registry-can-do)
6. [Two invariants](#two-invariants)
7. [Development](#development)
8. [Releasing](#releasing)
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
from dataportalen.rdf import label_for, resolve, slug_for

slug_for("http://publications.europa.eu/resource/authority/data-theme/TRAN")
# 'transport'
resolve("transport")
# ['http://publications.europa.eu/resource/authority/data-theme/TRAN']
label_for("national_authority")
# {'en': 'National authority', 'sv': 'Nationell myndighet'}
```

An organisation has no vocabulary entry, so its label and its slug both come
from the records: the URI table first, then the name the publisher wrote. The
fallback matters because some publishers mint URIs the table never saw
(`fohm-app.folkhalsomyndigheten.se/...`, `myndighetsregistret.scb.se/...`).

A URI with no label falls back to its last path segment, slugified, so output is
always a short string and never a URI.

## Label coverage

Measured over a seeded random sample of 5,000 datasets (25,038 vocabulary
values):

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
resolve, so gaps stay visible. Commit the regenerated `vocabulary.json` and
release it as a patch version.

## Module layout

Six modules; callers import from the package root.

| Module | Holds |
| --- | --- |
| `core.py` | version, exceptions, logging and progress, the HTTP transport |
| `rdf.py` | namespaces, the RDF/JSON parser, the label table, the short-name layer |
| `models.py` | `Dataset`, `DataService`, `Distribution`, `Agent` and friends, `to_dict()`, and the result types |
| `query.py` | the `Q` Solr query builder |
| `client.py` | `Catalog` (the whole public surface), `_Registry` (HTTP and Solr), the download |
| `__init__.py` | 19 exports, and nothing else |

### Public and internal

`Catalog` is the public surface: `datasets()`, `data_services()`, `filters()`,
`get()`, `info()`, `close()`. Plus `text()`, `default_catalog_path()` and the
result types — see [reference.md](reference.md#everything-exported).

Everything that talks to the registry sits behind `_Registry`, and the RDF layer
— `Q`, `Graph`, `Entry`, `Dataset`, `SearchPage` — is internal but very much
alive: the download is built on it. `_crawl` pages through
`_Registry._search(Q.rdf_type(...))` and parses each hit into a model before
calling `to_dict()`. None of it is exported, because 36 of the 42 `rdfType`
values in the registry are EntryStore bookkeeping and a public `search()` mostly
opened a door onto that.

`transport=` on `Catalog` takes any `BaseTransport` from `dataportalen.core`.
The test suite uses a fake one that replays recorded responses, which is how
the whole offline suite runs with no network.

### Where each call goes

Everything but one reads the file. `datasets()`, `data_services()`, `filters()`,
`get()` and `info()` never touch the network; `get(uri, format=...)` is the
single exception, and you have to name a format to get it. The download is the
only other thing that makes requests.

The download writes one JSONL file with both kinds of record, each tagged
`type`. A record with no `type` is read as a dataset, for files written by
older versions.

The agent crawl asks for all three agent types (`foaf:Agent`,
`foaf:Organization`, `prov:Agent`). The 69 entries typed `foaf:Organization`
carry about a third of all creator references, so leaving them out turns those
creators into bare URIs.

A value that the vocabulary table does not know but the file does contain —
`parquet`, a bare GeoNames id — is still a valid filter value: `_local_slugs`
falls back to what the catalogue actually holds, so everything a breakdown
reports can be filtered on. A value that is neither known nor present is an
error, with suggestions.

## What the registry can do

Measured, because these numbers shape the design:

| | |
| --- | --- |
| Page size | capped at 100 by the registry; asking for 1000 returns 100 |
| Throughput | ~2.2 requests/second, and **concurrency does not help** — 8, 16 and 32 workers all measure the same |
| Rate limiting | none observed: no `Retry-After`, no rate-limit headers, no 429s in a 64-request burst |
| Deep paging | flat — offset 23,000 costs the same as offset 0 |
| Paging stability | the crawl pages under `uri asc` because it is unique per entry, so a page boundary cannot move |
| Duplicate URIs | a few datasets are published into two catalogues, so one `uri` can appear twice with different `context_id`/`entry_id`. In a full export: 23,576 entries, 23,572 distinct URIs |

So the ceiling is roughly 200 datasets a second whatever you do, which is why a
full download takes ~7 minutes — and why the package reads the catalogue once
and searches it locally.

### What is in the registry, and what got into the file

Every `rdfType` in the registry, counted in full:

| Kind | Count | In the file? |
| --- | --- | --- |
| `dcat:Dataset` | 23,576 | yes |
| `dcat:Distribution` | 34,916 | nested in its dataset |
| `dcat:DataService` | 599 | yes |
| `dcat:Catalog` | 656 | no — only 157 hold a dataset, and 127 of 152 live ones have exactly one publisher, so it duplicates `publisher`. `context_id` on the record identifies the harvest source |
| `foaf:Agent` + `foaf:Organization` + `prov:Agent` | 7,609 | nested as `publisher` and `creators`. 4,916 of them are `private_individual` and reach no dataset |
| vcard contact types (8 of them) | ~48,000 | nested as `contact_points` |
| EntryStore/EntryScape internals (17 types) | — | no. `PipelineResult`, `List`, `CatalogContext`, `CatalogStatistics`, `LinkCheckReport`, `MQA`, `User`... the CMS talking to itself |
| `dcterms:Standard`, `prof:Profile`, `schema:Question` and friends | <400 | no |

Of 42 distinct types, six are DCAT and 36 are platform bookkeeping. That ratio
is why there is no public `search()`.

One asymmetry worth knowing if you join the two types: `servedByDataService`
appears on **29% of datasets** while `servesDataset` appears on **8% of data
services**. The link is recorded far more often from the dataset side.

## Two invariants

**A local filter matches its value exactly when the file contains it, and only
then falls back to vocabulary expansion.** The order matters: `json` also
resolves to `application/json+zip`, whose own slug is `json_in_a_zip`, so
expanding first makes `format="json"` return more than the breakdown row for
`json` claims. `tests/test_filters.py` round-trips every value of every filter,
which is the invariant to keep: every value a breakdown reports filters back to
exactly its own count.

**An organisation's slug comes from the URI table first and its own name
second.** Dropping the fallback silently loses the publishers and creators that
mint URIs the table never saw. Dropping the table and using only names would
break every slug users have written down.

## Development

```bash
pip install -e ".[dev]"

pytest                                  # offline, against recorded fixtures
DATAPORTAL_LIVE=1 pytest -m network     # against the real registry
ruff check src tests tools
```

CI runs the offline suite and ruff on every push, on Python 3.9 to 3.13. The
live tests run weekly in a separate workflow so that a registry outage or a
changed harvest never fails a pull request.

## Releasing

**Pushing to `main` never publishes anything.** The only workflow that uploads is
`Release`, and it runs only when a GitHub Release is published, or when you run
it by hand with the dry-run switch turned off.

1. Bump `__version__` in `src/dataportalen/core.py` — the single source, read by
   `pyproject.toml` and by the User-Agent. Add the entry to `CHANGELOG.md`.
2. Merge to `main`.
3. **Releases → Draft a new release**, tag `v<version>`, target `main`, paste
   the changelog entry as the notes.
4. Watch **Actions → Release**. It checks the tag against `core.py`, tests,
   builds and uploads.

To rehearse: **Actions → Release → Run workflow**, `dry_run` on by default —
builds, validates the metadata, checks the wheel imports, uploads nothing.

Requires a `PYPI_TOKEN` repository secret (a PyPI API token, starting `pypi-`)
under **Settings → Secrets and variables → Actions**.

If the tag and the version disagree the workflow stops before uploading. If an
upload fails partway, **do not retry the same version** — PyPI refuses
re-uploads of a version even after deletion; bump the patch and release again.

## Attribution

`vocabulary.json` is data, not code, compiled from third-party sources and
redistributed under their terms:

| Source | Used for | Licence |
| --- | --- | --- |
| [DIGG DCAT-AP-SE](https://github.com/diggsweden/DCAT-AP-SE) | Swedish/English vocabulary labels | CC BY 4.0 |
| [GeoNames](https://www.geonames.org/) | place names for `spatial` | CC BY 4.0 |
| [EU Vocabularies](https://op.europa.eu/en/web/eu-vocabularies) | themes, file types, frequencies, languages | Decision 2011/833/EU |
| [INSPIRE registry](https://inspire.ec.europa.eu/registry) | INSPIRE themes and code lists | Decision 2011/833/EU |

The same notice ships with the package, in [NOTICE](../NOTICE) and in the
`_comment` key of `vocabulary.json` itself.
