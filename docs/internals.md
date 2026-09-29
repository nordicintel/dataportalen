# Internals

For working on the package. Using it is covered in [guide.md](guide.md).

1. [Where the short values come from](#where-the-short-values-come-from)
2. [Label coverage](#label-coverage)
3. [Rebuilding the table](#rebuilding-the-table)
4. [Module layout](#module-layout)
5. [What the registry can do](#what-the-registry-can-do)
6. [Development](#development)
7. [Releasing](#releasing)
8. [Attribution](#attribution)

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
"public" helps nobody building on this, and they stay English whatever the
client's `language` is. Publisher-authored text follows `language` instead:
one string, or every language the publisher supplied when `language="all"`.

The mapping lives in two generated data files shipped inside the package:
`vocabulary.json` (1,017 vocabulary terms) and `organisations.json` (538
publishers, keyed by both slugged name and organisation number). Nothing is
fetched at runtime.

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

Seven modules; callers import from the package root.

| Module | Holds |
| --- | --- |
| `core.py` | version, exceptions, logging and progress, the three HTTP transports |
| `rdf.py` | namespaces, the RDF/JSON parser, the label table, the short-name layer |
| `models.py` | `Dataset`, `Distribution`, `Agent` and friends, and `to_dict()` |
| `query.py` | the `Q` Solr query builder |
| `client.py` | `Dataportal` (local-first), the catalogue export, `LocalCatalog` |
| `aio.py` | `AsyncDataportal` |
| `__init__.py` | the public surface |

## What the registry can do

Measured, because both numbers shape the package's design:

| | |
| --- | --- |
| Page size | capped at 100 by the registry; asking for 1000 returns 100 |
| Throughput | ~2.2 requests/second, and **concurrency does not help** — 8, 16 and 32 workers all measure the same |
| Rate limiting | none observed: no `Retry-After`, no rate-limit headers, no 429s in a 64-request burst |
| Deep paging | flat — offset 23,000 costs the same as offset 0 |
| Paging stability | walking 2,000 datasets gives 2,000 distinct entries under `created asc` and under `uri asc`. The crawl uses `uri asc` because it is unique per entry, so a page boundary cannot move |
| Duplicate URIs | a few datasets are published into two catalogues, so one `uri` can appear twice with different `context_id`/`entry_id`. In a full export: 23,575 entries, 23,571 distinct URIs |

So the ceiling is roughly 200 datasets a second whatever you do, which is why
`download_catalog` takes ~6 minutes — and why the client reads the whole
catalogue once and searches it locally by default.

### How the client decides

`Dataportal(local=True)` (the default) serves a dataset search from the file
and sends everything else to the registry. `_serve_locally()` holds the rule
in one place: local unless `local=False`, unless the call uses `query=` (a
raw index expression) or `facet_fields=` (counting the index). A client also
keeps an API-only twin of itself, so downloading the catalogue cannot recurse
into the catalogue it is downloading.

Searches return `Results`, a `list` of dicts carrying `total`, `offset`,
`limit` and `has_more`, so local and live results are the same type. The
model objects (`Dataset`, `Entry`) are still there behind `lookup()`,
`entry()` and `search()`, which is where the RDF lives.

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
