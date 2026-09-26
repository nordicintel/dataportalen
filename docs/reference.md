# Reference

- [Search filters](#search-filters)
- [Dates](#dates)
- [Finding a value](#finding-a-value)
- [Paging](#paging)
- [Entities](#entities)
- [The catalogue export](#the-catalogue-export)
- [Async](#async)
- [Logging and progress](#logging-and-progress)
- [Configuration and errors](#configuration-and-errors)
- [Raw RDF and raw queries](#raw-rdf-and-raw-queries)
- [Upstream caveats](#upstream-caveats)

## Search filters

Every filter takes a short lowercase value. No URIs, no codes.

```python
page = dp.datasets(theme="transport", format="csv")
```

| Filter | What it matches | Example |
| --- | --- | --- |
| `text` | Free text across title, description and keywords | `text="cykel"` |
| `title` | Words in the title | `title="bidrag"` |
| `description` | Words in the description | `description="vägnät"` |
| `keyword` | A publisher's keyword. Several means all must match | `keyword=["geodata", "trafik"]` |
| `publisher` | The publishing organisation, by name | `publisher="trafikverket"` |
| `theme` | Subject category | `theme="transport"` |
| `format` | A format the data is available in | `format="csv"`, `format="xlsx"` |
| `license` | The licence | `license="cc_by_4_0"` |
| `access_rights` | Whether the data is open | `access_rights="public"` |
| `updated` | How often the publisher refreshes it | `updated="annual"` |
| `language` | Language of the data | `language="swedish"` |
| `place` | Geographic coverage | `place="kingdom_of_sweden"` |
| `catalog` | One catalogue, by its numeric id | `catalog=50` |
| `uri` | A specific dataset, by its own identifier | `uri="https://example.org/d1"` |
| `query` | A raw Solr fragment, for anything not covered above | `query="lang:eng"` |

Passing several values to `publisher`, `theme`, `format`, `license`, `language`
or `place` matches any of them. The same filters work on `distributions()`,
`data_services()`, `catalogs()` and the rest, where they apply.

## Dates

All of these accept `"2024-01-01"`, `"2024-01"`, `"2024"`, a `date` or a
`datetime`.

| Filter | Which date |
| --- | --- |
| `updated_after` / `updated_before` | When the **publisher** last changed the data |
| `published_after` / `published_before` | When the publisher **first released** it |

Both are the publisher's own dates. The registry's harvest timestamp is not a
filter: it changes nightly for nearly every dataset, so it would tell you about
the harvest job rather than about the data.

## Finding a value

Pass something that does not exist and the error tells you what you meant:

```python
>>> dp.datasets(theme="transprot")
QueryError: unknown theme 'transprot'.  Did you mean: transport, transportation, ...
```

Or list them:

```python
from dataportalen import known_values, known_publishers

known_values("transport")   # ['transport', 'transport_networks', 'transportation']
known_publishers("trafikv") # ['trafikverket']
```

## Paging

`datasets()` returns one page (`limit` is capped at 100 upstream);
`iter_datasets()` walks the whole result:

```python
page = dp.datasets(theme="transport", limit=100, offset=200)
page.total, page.has_more

for dataset in dp.iter_datasets(theme="transport"):
    ...
```

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

All of them have `.to_dict()` and `.to_json()`. A search hit names its
publisher and distributions by URI only; `dp.dataset(uri=...)` fetches the whole
closure, so that dict is self-contained.

Organisations and registry-wide data:

```python
dp.organisations()          # every publisher with its dataset count
dp.organisation_summary()   # registry totals
dp.context_names()          # contextId -> catalogue title
dp.context_publishers()     # contextId -> publisher URI
dataset.publisher()         # the Agent behind a dataset

dp.catalog_statistics(limit=30)  # nightly dataset counts, newest first
dp.link_check_reports()          # which distribution URLs still resolve
dp.metadata_quality()            # DCAT-AP MQA scores per catalogue
dp.download_dump("all.rdf")      # the full nightly RDF dump, streamed
```

Authored text (`title`, `description`, `keywords`, `publisher.name`) comes out
as a language map, so nothing is lost. Controlled values are one fixed English
short name regardless of preference. `Dataportal(languages=["en", "sv"])` sets
the preference used by the typed accessors.

## The catalogue export

```python
from dataportalen import download_catalog

summary = download_catalog("catalog.jsonl")
summary.datasets, summary.distributions, summary.elapsed

download_catalog("catalog.jsonl.gz")              # gzip from the suffix
download_catalog("sample.jsonl", limit=500)       # a quick smoke test
download_catalog("catalog.jsonl", progress=None)  # silence
download_catalog("catalog.jsonl", progress=print) # your own callback
download_catalog("catalog.jsonl", client=dp)      # reuse a client
```

Reading it back:

```python
import json
records = [json.loads(line) for line in open("catalog.jsonl", encoding="utf-8")]
```

The naive way would be one recursive fetch per dataset — 23,000+ requests.
Instead each referenced type is bulk-crawled once and joined locally, which is
665 requests and about five minutes. With `limit` set it skips the bulk crawl
and resolves only what that batch references, so `limit=500` takes seconds.

The nightly `all.rdf` dump would be a single request, but it has been seen
lagging the registry by a week, so this reads the live search index instead.

## Async

```python
import asyncio
from dataportalen import AsyncDataportal

async def main():
    async with AsyncDataportal() as dp:
        page = await dp.datasets(title="bidrag", limit=10)
        async for dataset in dp.iter_datasets(limit=100):
            print(dataset.to_dict()["title"])

asyncio.run(main())
```

Same names and arguments; requests are coroutines and the iterators are async
generators. `entry()` and `lookup_many()` fan out concurrently.

## Logging and progress

`download_catalog` draws a live progress line when stderr is a terminal, and
logs periodically when it is not:

```
datasets   47.3%  11,140/23,548  81/s  eta 2m33s
```

`progress="auto"` (default), `None` for silence, or your own
`progress(done, total)` callback.

Everything else logs to the standard `dataportalen` logger and never touches
your root logger:

```python
from dataportalen import Dataportal, enable_logging

enable_logging("INFO")
dp = Dataportal(log_level="DEBUG")   # same thing, as a shortcut

import logging
logging.getLogger("dataportalen").setLevel(logging.DEBUG)   # your app's way
```

`DEBUG` logs every request with its status, duration and size; retries and rate
limits are logged at `WARNING`, so a slow run explains itself.

## Configuration and errors

```python
Dataportal(
    base_url="https://admin.dataportal.se",
    languages=["sv", "en"],
    timeout=30.0,
    max_retries=3,            # on 429/5xx and connection errors, with backoff
    public_only=True,
    default_sort="modified desc",
    cache_size=512,
    log_level=None,
    transport=None,           # requests by default; HttpxTransport() for HTTP/2
)
```

Set `DATAPORTAL_USER_AGENT` to identify your client to the registry.

Errors all derive from `DataportalError`: `TransportError` / `TimeoutError` for
connection failures, `HTTPError` with `NotFoundError`, `RateLimitError` and
`ServerError` subclasses for non-2xx, `ParseError` for malformed bodies,
`QueryError` for bad queries and unknown filter values.

## Raw RDF and raw queries

The dict is the point, but nothing is hidden:

```python
dataset.to_rdf()                       # the metadata graph as RDF/JSON
dataset.to_rdf_dict()                  # every predicate, CURIE-keyed
dataset.raw_json()                     # the registry's own payload
dataset.resource.uris("dcat:theme")    # typed graph access
dp.entry_raw(547, 28672, recursive=True, format="text/turtle").text
```

`Q` builds arbitrary Solr queries and handles the escaping:

```python
from dataportalen import Q
from dataportalen.namespaces import DCAT

dp.search(Q.rdf_type(DCAT.Dataset) & Q.title("cykel", "sv") & ~Q.language("eng"))
```

Combine with `&`, `|`, `~`; `Q.raw("...")` passes a fragment through untouched.
Negation is handled for you — Lucene returns nothing for a purely negative query
and silently drops everything for `a AND (NOT b)`, so `Q` anchors negative
fragments to `*:*` where a positive clause is required.

## Upstream caveats

- Solr result counts are estimates and may exceed what you can actually read;
  `has_more` is a hint, not a guarantee.
- The index is rebuilt nightly, so data can be up to 24 hours old.
- `limit` is capped at 100 per request.
- Deep paging over a changing index can skip or repeat entries; sort by
  something stable (`sort="created asc"`) when exactness matters.

## Development

```bash
pip install -e ".[dev]"
pytest                                  # offline, against recorded fixtures
DATAPORTAL_LIVE=1 pytest -m network     # against the real registry
```
