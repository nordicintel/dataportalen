# dataportal

A complete Python wrapper for the **Sveriges dataportal registry API** —
[`admin.dataportal.se`](https://docs.dataportal.se/registry/api/), the
EntryScape Registry / EntryStore instance behind
[dataportal.se](https://www.dataportal.se).

Everything the registry exposes is covered: the Solr-backed search index,
single-entry lookup by registry id *or* by the publisher's own URI, harvest
reports, the nightly statistics series, the organisation chart and the nightly
RDF dump — with DCAT-AP-SE 2.0.0 metadata parsed into typed objects.

- **No required dependencies.** Works on the standard library alone; uses
  `httpx` or `requests` automatically when either is installed.
- **Sync and async** clients with the same API surface.
- **Typed models** for datasets, distributions, data services, catalogs,
  agents, contact points, harvest reports and statistics — plus raw RDF access
  whenever the typed view is not enough.
- **A query builder** that handles Solr's escaping rules and EntryStore's
  MD5-hashed predicate fields for you.
- **A CLI** for searching, inspecting and monitoring from the terminal.

## Install

```bash
pip install dataportal
```

Optional extras:

```bash
pip install "dataportal[httpx]"    # pooled HTTP/2-capable transport
pip install "dataportal[async]"    # the asyncio client (httpx)
```

## Quick start

```python
from dataportal import Dataportal

with Dataportal() as dp:
    page = dp.datasets(title="bidrag", title_lang="sv", limit=10)
    print(page.total, "matching datasets")

    for dataset in page:
        print(dataset.title, "—", dataset.publisher_uri)
```

Fetch one dataset with everything attached (distributions, publisher, contact
points) in a single recursive request:

```python
dataset = dp.dataset(uri="https://example.org/data/roads")

print(dataset.title, dataset.license, dataset.keywords)
for distribution in dataset.distributions():
    print(distribution.format, distribution.download_url, distribution.byte_size)

publisher = dataset.publisher()
print(publisher.name, publisher.identifiers)
```

Iterate across pages without thinking about offsets:

```python
for dataset in dp.iter_datasets(theme="http://publications.europa.eu/resource/authority/data-theme/TRAN"):
    print(dataset.title)
```

## How the registry is shaped

Worth knowing, because it explains the API:

- Datasets are identified by **the publisher's own URI**, not by anything the
  registry mints. The registry additionally has a `contextId`/`entryId` pair
  that is stable in practice but can change if a dataset is removed and
  re-added.
- A **search hit carries only that entry's own metadata**. Referenced managed
  entities — distributions, publishers, contact points, data services — appear
  as bare URIs and need a separate lookup. `lookup_many()` batches those, and
  `recursive=True` on a single-entry fetch avoids them entirely.
- The Solr index is **rebuilt nightly**, so counts are estimates and content is
  at most 24 hours old.

## Searching

`Q` builds Solr queries, escaping colons, slashes and the rest for you:

```python
from dataportal import Q
from dataportal.namespaces import DCAT

dp.search(Q.rdf_type(DCAT.Dataset) & Q.title("cykel", "sv") & ~Q.language("eng"))
dp.search(Q.publisher("http://dataportal.se/organisation/SE2021005521"))
dp.search(Q.modified("2024-01-01T00:00:00Z") & Q.tag("geodata"))
```

Combine with `&` (AND), `|` (OR) and `~` (NOT). Anything the builder does not
cover goes through `Q.raw("...")` verbatim.

Negation is handled carefully: Lucene returns nothing for a purely negative
query and silently drops everything for `a AND (NOT b)`, so `Q` anchors
negative fragments to `*:*` wherever a positive clause is required. `~` does
what it looks like it does.

The keyword form is equivalent and usually shorter:

```python
dp.datasets(
    text="cykel",
    keyword=["geodata", "trafik"],
    publisher="http://dataportal.se/organisation/SE2021005521",
    modified_after="2024-01-01T00:00:00Z",
    limit=50,
)
```

Supported filters: `text`, `title` (+ `title_lang`), `description`, `keyword`
(+ `keyword_lang`), `publisher`, `theme`, `format`, `license`, `hvd_category`,
`language`, `context`, `resource`, `modified_after`/`modified_before`,
`created_after`/`created_before`, and `query` for a raw fragment.

### Predicate queries and facets

EntryStore indexes predicate/object pairs under a field whose name embeds an
MD5 of the predicate URI. `Q.predicate` and `predicate_field` compute it:

```python
from dataportal import Q, predicate_field

predicate_field("dcterms:subject")            # metadata.predicate.literal.256bd150
Q.predicate("dcterms:accessRights", "http://.../PUBLIC", kind="uri")
Q.predicate_range("dcterms:issued", "2023-01-01T00:00:00Z", kind="date")
```

Facets count distinct values of any indexed field:

```python
counts = dp.facet("rdfType", limit=20).as_dict()
```

## Typed entities

| Method | Returns |
| --- | --- |
| `dp.datasets(...)` / `dp.iter_datasets(...)` | `Dataset` |
| `dp.distributions(...)` | `Distribution` |
| `dp.data_services(...)` | `DataService` |
| `dp.dataset_series(...)` | `DatasetSeries` |
| `dp.catalogs(...)` | `Catalog` |
| `dp.agents(...)` / `dp.agent(uri)` | `Agent` |
| `dp.standards(...)` | `Standard` |
| `dp.harvest_reports()` | `HarvestReport` |
| `dp.link_check_reports()` | `LinkCheckReport` |
| `dp.metadata_quality()` | `MetadataQuality` |
| `dp.catalog_statistics()` | `CatalogStatistics` |
| `dp.lookup(uri)` / `dp.lookup_many(uris)` | whichever model fits the hit |

Localized values follow a language preference (`sv`, then `en`, by default):

```python
dp = Dataportal(languages=["en", "sv"])
dataset.title              # the English title when there is one
dataset.titles             # {"sv": "...", "en": "..."}
dataset.value("dcterms:title", ["de"])
```

### Dropping to RDF

Every entry keeps its graph, so nothing is hidden:

```python
dataset.resource.uris("dcat:theme")
dataset.resource.value("http://purl.org/dc/terms/provenance")
dataset.resource.to_dict()          # everything, with CURIE keys
dataset.metadata.to_json()          # the original RDF/JSON

for subject, predicate, obj in dataset.metadata.triples():
    ...
```

Other serializations come straight from the server:

```python
turtle = dp.entry_raw(547, 28672, recursive=True, format="text/turtle").text
jsonld = dp.entry_raw(547, 28672, recursive=True, format="application/ld+json").json()
```

## Registry operations

```python
dp.organisations()              # dataset counts per publisher (the org chart)
dp.organisation_summary()       # registry totals
dp.harvest_reports()            # newest harvest run per source
dp.catalog_statistics(limit=30) # the nightly series, newest first
dp.context_names()              # contextId -> organisation name
dp.datasets_per_organisation()  # (contextId, name, count) for one day
dp.link_check_reports()         # nightly link check per catalog
dp.metadata_quality()           # DCAT-AP MQA scores per catalog
```

Harvest reports are how you see whether a source is actually being ingested:

```python
for report in dp.harvest_reports():
    if not report.all_succeeded:
        print(report.title, report.validation_errors, "errors")
```

Link checks tell you whether the distributions still resolve, and the MQA
scores how well a catalog follows DCAT-AP:

```python
for report in dp.link_check_reports(failing_only=True):
    print(report.context_id, report.failed, "of", report.checked, "links dead")

for score in dp.metadata_quality():
    print(score.title, score.percentage, score.rating)
```

## The nightly dump

`all.rdf` is the only place where datasets arrive together with their related
entities in one document. It is large, so it is always streamed:

```python
dp.download_dump("all.rdf", progress=lambda n: print(n, "bytes"))

for chunk in dp.iter_dump():     # or handle it yourself
    ...
```

## Async

```python
import asyncio
from dataportal.aio import AsyncDataportal

async def main():
    async with AsyncDataportal() as dp:
        page = await dp.datasets(title="bidrag", limit=10)
        async for dataset in dp.iter_datasets(limit=100):
            print(dataset.title)

asyncio.run(main())
```

Same names and arguments as the sync client; requests are coroutines and the
iterators are async generators. `entry()` and `lookup_many()` fan out
concurrently, bounded by `max_concurrency`. Entries returned by the async
client carry no client reference, so use `dp.lookup(...)` instead of
`dataset.publisher()`.

## Command line

```bash
dataportal search "cykel" --limit 5
dataportal search --publisher http://dataportal.se/organisation/SE2021005521
dataportal dataset --id 547/28672
dataportal dataset https://example.org/data/roads --json
dataportal organisations --top 10
dataportal harvest --failed
dataportal stats --days 7
dataportal contexts --top 20
dataportal links --failed
dataportal quality
dataportal facet rdfType
dataportal raw 'title.sv:cykel AND public:true'
dataportal dump ./all.rdf
```

Every subcommand accepts `--json` for the raw response.

## Configuration

```python
Dataportal(
    base_url="https://admin.dataportal.se",
    languages=["sv", "en"],   # preference for localized values
    timeout=30.0,
    max_retries=3,            # on 429/5xx and connection errors, with backoff
    public_only=True,         # add public:true to every search
    default_sort="modified desc",
    cache_size=512,           # memoized URI lookups
    transport=None,           # httpx / requests / urllib, auto-selected
)
```

Set `DATAPORTAL_USER_AGENT` to identify your client to the registry.

## Errors

All exceptions derive from `DataportalError`:

`TransportError` (and `TimeoutError`) for connection failures; `HTTPError` with
the `NotFoundError`, `RateLimitError` and `ServerError` subclasses for non-2xx
responses; `ParseError` for malformed bodies; `QueryError` for bad query
construction.

## Testing

```bash
pytest                                  # offline, against recorded responses
DATAPORTAL_LIVE=1 pytest -m network     # against the real registry
```

## Caveats from upstream

- Solr result counts are estimates and may exceed the number of entries you can
  actually read; `SearchPage.has_more` is a hint, not a guarantee.
- The index lags the triple store, so it should not be a source of truth for
  anything critical.
- `limit` is capped at 100 per request.
- Deep paging over a changing index can skip or repeat entries; sort by
  something stable (e.g. `created asc`) when exactness matters.

## License

MIT.
