# dataportalen

Python access to [dataportal.se](https://www.dataportal.se) that gives you
**plain dicts, not RDF**.

Sweden's open-data registry publishes everything as DCAT-AP-SE: RDF graphs,
blank nodes, PURLs, and controlled vocabularies expressed as bare URIs. The
[registry documentation](https://docs.dataportal.se/registry/api/) is explicit
that turning `http://publications.europa.eu/resource/authority/data-theme/TRAN`
into the word "Transport" is *your* problem.

This package makes it not your problem.

```python
from dataportalen import Dataportal

with Dataportal() as dp:
    dataset = dp.dataset(uri="https://example.org/data/roads")
    print(dataset.to_dict())
```

```json
{
  "uri": "https://example.org/data/roads",
  "title": "Vägtrafiknät",
  "keywords": ["vägnät", "trafik"],
  "themes": [
    {"uri": "http://publications.europa.eu/resource/authority/data-theme/TRAN",
     "label": "Transport"}
  ],
  "license": {"uri": "http://creativecommons.org/licenses/by/4.0/",
              "label": "Creative Commons Erkännande 4.0"},
  "accrual_periodicity": {"uri": ".../frequency/ANNUAL", "label": "Årligen"},
  "publisher": {"uri": "http://dataportal.se/organisation/SE2021006297",
                "name": "Trafikverket", "identifiers": ["2021006297"]},
  "issued": "2020-03-04",
  "distributions": [
    {"title": "Vägnät CSV", "download_url": "https://...csv",
     "format": {"uri": ".../file-type/CSV", "label": "CSV"}, "byte_size": 184320}
  ],
  "contact_points": [{"name": "Datasupport", "email": "data@example.org"}]
}
```

Every value above is `json.dumps`-able. No RDF terms, no graph traversal, no
URIs left unexplained.

## What it does for you

- **Resolves vocabulary URIs to labels.** Themes, file types, frequencies,
  languages, licences, access rights, HVD categories — Swedish and English,
  shipped in the package, no network call.
- **Flattens the graph.** Blank nodes, nested `PeriodOfTime`, `vcard` contact
  points and `spdx` checksums come out as ordinary keys.
- **Resolves references.** A search hit names its publisher and distributions
  by URI only; one recursive fetch pulls the whole closure into the dict.
- **Leaves the escape hatches open.** `to_rdf()` gives the raw graph,
  `raw_json()` the registry's own payload, and typed accessors sit underneath
  everything if you want them.

No required dependencies — the standard library is enough. `httpx` or
`requests` are used automatically if installed.

## Install

Not on PyPI yet.

```bash
pip install git+https://github.com/nordicintel/dataportal.git
```

Or from a checkout:

```bash
pip install -e .
```

Optional extras: `httpx` (pooled HTTP/2 transport), `async` (the asyncio
client), `requests`, `dev` (pytest plus both transports).

> Distribution and import name are both **`dataportalen`**. The unrelated
> `dataportal` package on PyPI is a Korean public-data client — not this.

## Searching

```python
page = dp.datasets(text="cykel", limit=10)
print(page.total)
page.to_dict()            # the whole page, ready for json.dumps
```

Keyword filters cover the common cases:

```python
dp.datasets(
    title="bidrag", title_lang="sv",
    keyword=["geodata", "trafik"],
    publisher="http://dataportal.se/organisation/SE2021005521",
    theme="http://publications.europa.eu/resource/authority/data-theme/TRAN",
    modified_after="2024-01-01T00:00:00Z",
)
```

Available: `text`, `title` (+ `title_lang`), `description`, `keyword`
(+ `keyword_lang`), `publisher`, `theme`, `format`, `license`, `hvd_category`,
`language`, `context`, `resource`, `modified_after` / `modified_before`,
`created_after` / `created_before`, and `query` for a raw fragment.

Iterate across pages without touching offsets:

```python
for dataset in dp.iter_datasets(theme="...TRAN"):
    print(dataset.to_dict()["title"])
```

### When you need the query language

`Q` builds Solr queries and handles the escaping — colons and slashes inside
URIs, and the MD5-hashed predicate fields EntryStore uses:

```python
from dataportalen import Q, predicate_field
from dataportalen.namespaces import DCAT

dp.search(Q.rdf_type(DCAT.Dataset) & Q.title("cykel", "sv") & ~Q.language("eng"))
dp.search(Q.predicate("dcterms:accessRights", "http://.../PUBLIC", kind="uri"))

predicate_field("dcterms:subject")    # metadata.predicate.literal.256bd150
```

Combine with `&`, `|`, `~`; drop to `Q.raw("...")` for anything else.
Negation is handled properly — Lucene returns nothing for a purely negative
query and silently drops everything for `a AND (NOT b)`, so `Q` anchors
negative fragments to `*:*` where a positive clause is required.

## The whole catalogue in one file

```python
from dataportalen import download_catalog

download_catalog("catalog.jsonl")
```

One dataset per line, each line a self-contained JSON object with its
distributions, publisher and contact points already nested — nothing to look
up afterwards.

```
23,548 datasets · 35,102 distributions · 117 MiB · 665 requests · ~5 minutes
```

```python
download_catalog("catalog.jsonl.gz")                 # gzip from the suffix
download_catalog("sample.jsonl", limit=500)          # a quick smoke test
download_catalog("catalog.jsonl", progress=print)    # progress(done, total)
summary = download_catalog("catalog.jsonl")
summary.datasets, summary.distributions, summary.elapsed
```

Reading it back is one line:

```python
import json
records = [json.loads(line) for line in open("catalog.jsonl", encoding="utf-8")]
```

The naive way to build this would be one recursive fetch per dataset — 23,000+
requests. Instead each referenced type is bulk-crawled once and joined locally,
which is 665 requests. With `limit` set it skips the bulk crawl entirely and
resolves only what that batch references, so `limit=500` takes seconds rather
than minutes.

The nightly `all.rdf` dump would be a single request, but it has been seen
lagging the registry by a week, so this reads the live search index instead.

## Vocabulary labels

The label table is available on its own:

```python
from dataportalen import label, term, VOCABULARY

label("http://publications.europa.eu/resource/authority/data-theme/TRAN")
# 'Transport'
label(".../data-theme/TRAN", ["sv"])
term(".../file-type/CSV")     # {"uri": "...", "label": "CSV"}
len(VOCABULARY)
```

An unknown URI gives `None` rather than a guess derived from the URI — a
missing label is a fact about coverage, not something to paper over.

### Coverage

Measured over a seeded random sample of **5,000 datasets** drawn from 225
random positions across the corpus (25,109 vocabulary values):

| Field | Labelled |
| --- | --- |
| `themes` | 100% |
| `access_rights` | 100% |
| `accrual_periodicity` | 100% |
| `hvd_categories` | 100% |
| `languages` | 100% |
| `spatial` | 99% |
| `license` | 57% |
| `subjects` | 44% |
| **total** | **91%** |

**94% of everything still unlabelled is two URIs**:
`https://dataportal.se/concepts/licensecategories/{nolicense,otherlicense}`,
which between them account for ~9,000 datasets. They do not dereference (the
host answers `426`) and appear in neither DIGG's templates nor the
dataportal.se frontend translations. Real licence URLs — Creative Commons and
friends — resolve fine. The rest is GEMET concepts, whose host is unreachable
over plain HTTP.

Reproduce or re-measure:

```bash
python tools/build_vocabulary.py --skip-build --sample 5000
python tools/build_vocabulary.py --skip-build --sample all   # exact, slower
```

Sampling is offset-based because the index has no random sort, so it is
mildly clustered — a good estimate rather than a census. `--sample all`
removes the sampling error when it matters.

### Regenerating

The table is built from DIGG's own
[DCAT-AP-SE templates](https://github.com/diggsweden/DCAT-AP-SE), the
authority tables the remaining URIs dereference to, and the GeoNames CC BY
bulk exports for place names:

```bash
python tools/build_vocabulary.py
```

The script measures coverage against the URIs publishers are *actually* using
in the live registry and prints what it could not resolve, so gaps stay
visible rather than assumed.

## Entities

| Method | Returns |
| --- | --- |
| `dp.datasets(...)` / `dp.iter_datasets(...)` | `Dataset` |
| `dp.dataset(uri=...)` / `dp.dataset(context_id=, entry_id=)` | `Dataset` |
| `dp.distributions(...)` | `Distribution` |
| `dp.data_services(...)` | `DataService` |
| `dp.dataset_series(...)` | `DatasetSeries` |
| `dp.catalogs(...)` | `Catalog` |
| `dp.agents(...)` / `dp.agent(uri)` | `Agent` |
| `dp.standards(...)` | `Standard` |
| `dp.lookup(uri)` / `dp.lookup_many(uris)` | whichever model fits |

All of them have `.to_dict()` and `.to_json()`.

### Organisations

```python
dp.organisations()          # every publisher with its dataset count
dp.organisation_summary()   # registry totals
dp.context_names()          # contextId -> catalog title
dp.context_publishers()     # contextId -> publisher URI
dataset.publisher()         # the Agent behind a dataset
```

### Language

Localized values follow a preference, `sv` then `en` by default:

```python
dp = Dataportal(languages=["en", "sv"])
dataset.to_dict()["title"]       # English when there is one
dataset.to_dict()["titles"]      # {"sv": ..., "en": ...}
```

### Dropping to RDF

```python
dataset.to_rdf()                       # the metadata graph as RDF/JSON
dataset.to_rdf_dict()                  # every predicate, CURIE-keyed
dataset.raw_json()                     # the registry's own payload
dataset.resource.uris("dcat:theme")    # typed graph access
dp.entry_raw(547, 28672, recursive=True, format="text/turtle").text
```

## Async

```python
import asyncio
from dataportalen.aio import AsyncDataportal

async def main():
    async with AsyncDataportal() as dp:
        page = await dp.datasets(title="bidrag", limit=10)
        async for dataset in dp.iter_datasets(limit=100):
            print(dataset.to_dict()["title"])

asyncio.run(main())
```

Same names and arguments; requests are coroutines and the iterators are async
generators. `entry()` and `lookup_many()` fan out concurrently.

## Other registry data

```python
dp.catalog_statistics(limit=30)  # nightly dataset counts, newest first
dp.link_check_reports()          # which distribution URLs still resolve
dp.metadata_quality()            # DCAT-AP MQA scores per catalog
dp.download_dump("all.rdf")      # the full nightly RDF dump, streamed
```

## Configuration

```python
Dataportal(
    base_url="https://admin.dataportal.se",
    languages=["sv", "en"],
    timeout=30.0,
    max_retries=3,            # on 429/5xx and connection errors, with backoff
    public_only=True,
    default_sort="modified desc",
    cache_size=512,
    transport=None,           # httpx / requests / urllib, auto-selected
)
```

Set `DATAPORTAL_USER_AGENT` to identify your client to the registry.

Errors all derive from `DataportalError`: `TransportError` / `TimeoutError`
for connection failures, `HTTPError` with `NotFoundError`, `RateLimitError`
and `ServerError` subclasses for non-2xx, `ParseError` for malformed bodies,
`QueryError` for bad queries.

## Development

```bash
pip install -e ".[dev]"
pytest                                  # offline, against recorded fixtures
DATAPORTAL_LIVE=1 pytest -m network     # against the real registry
```

### Releasing

**Pushing to `main` never publishes anything.** CI runs tests and builds the
distributions on every push and PR; that is all it does.

Publishing takes one file edit and one GitHub Release:

1. Bump `__version__` in `src/dataportalen/_version.py` — the single source of
   truth, which `pyproject.toml` and the client's User-Agent both read.
2. Merge to `main`.
3. `gh release create v0.2.0 --title "v0.2.0" --notes "..."`

Full instructions, including how to rehearse without uploading and what to do
when a release fails: **[RELEASING.md](RELEASING.md)**.

| Workflow | Trigger | Publishes |
| --- | --- | --- |
| `ci.yml` | every push and PR | no |
| `live.yml` | weekly schedule, manual | no |
| `release.yml` | published GitHub Release, manual | **yes** |

## Caveats from upstream

- Solr result counts are estimates and may exceed what you can actually read;
  `has_more` is a hint, not a guarantee.
- The index is rebuilt nightly, so data can be up to 24 hours old and should
  not be a source of truth for anything critical.
- `limit` is capped at 100 per request.
- Deep paging over a changing index can skip or repeat entries; sort by
  something stable (e.g. `created asc`) when exactness matters.

## License

MIT — see [LICENSE](LICENSE).
