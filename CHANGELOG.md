# Changelog

All notable changes to this package. Versions are on
[PyPI](https://pypi.org/project/dataportalen/); the headings link to the
GitHub release.

## [0.13.0](https://github.com/nordicintel/dataportalen/releases/tag/v0.13.0) — unreleased

Breaking. The API is built around four models, and one `Catalog` answers
from the database or from the registry. A database written by 0.12 is read
as it is; nothing downloads again.

- **Models, not dicts.** `Dataset`, `DataService`, `Distribution` and
  `Publisher` read by attribute — `dataset.title.text()`,
  `dataset.publisher.id`, `dist.kind` — and `SearchResult` carries datasets
  and facets. Every model has `to_dict()`, with the field names the dicts
  had, and printing one prints that JSON. Every method that returns models
  takes `as_dict=True` for the dicts instead.
- **`search()` is the search.** `catalog.search(query=None, *, limit=None,
  offset=0, facet_limit=None, **filters)` finds datasets and returns
  `SearchResult(datasets, facets, total, offset, limit)`. With no `limit` it
  holds every match; facets always count every match.
- **The lists are complete.** `datasets()`, `data_services()` and
  `publishers()` take filters and return every match — no query, no window,
  no facets. `publishers(**filters)` counts what the filters select.
- **`MultilingualText`** replaces the `{"sv", "en"}` dict and the `text()`
  helper: `.text()` picks the catalogue's language, set with
  `Catalog(language="en")`, and falls back to the other. It still compares
  equal to the dict. Keywords are `Keywords`, with `.list()` and `.all()`.
- **`Catalog(live=True)`** replaces `LiveCatalog`, and warns when built.
  `search()`, `datasets()`, `data_services()` and `publishers()` also take
  `live=True` for one call. Live and local return the same shapes; an
  unpaged live call fetches every page, warning above 1,000 records.
- **The catalogue holds every access level.** `access_rights=` is no longer
  a `Catalog` argument; it is a filter, and `access_rights="none"` finds the
  records that set nothing. The default catalogue grows from 17,555 datasets
  to 23,422.
- **Filters.** `updated` is `accrual_periodicity`. `license`, `language`,
  `issued_after`, `issued_before` and `modified_before` are gone — every
  record still carries the fields — and each raises `QueryError` naming what
  to do instead. `modified_after` reads `issued` for a dataset with no
  `modified` (651 datasets). Facets follow the filters.
- `sources()` is gone: `info()["sources"]` has the totals and the failed
  sources that still hold records. `verify()` is no longer public.
- `Facets.to_dict()` is `{filter: [{"value", "count", "label"}]}`; the old
  `{filter: {value: count}}` is `Facets.counts()`. `FacetValue.label` is a
  `MultilingualText`.
- A publisher has one `alias` (`"scb"` or `None`) instead of an `aliases`
  list. `organisations.json` and `aliases.json` are one `publishers.json`.
- In `to_dict()`, `broken`, `unverified`, `byte_size` and `stale` are always
  present, `None` when unset; a publisher nested in a record has
  `dataset_count`, `data_service_count` and `facets` set to `None`.
- New `DataportalWarning`. Removed exports: `LiveCatalog`, `text`, `Results`
  and the `TypedDict` record types (`DatasetRecord`, `SourceRecord`, ...).

| 0.12 | 0.13 |
| --- | --- |
| `catalog.datasets(theme="transport").total` | `catalog.search(theme="transport", limit=0).total` |
| `catalog.datasets(limit=None, **f)` | `catalog.datasets(**f)` |
| `for d in catalog.datasets(query="cykel"): d["title"]` | `for d in catalog.search("cykel").datasets: d.title` |
| `text(d["title"])` | `d.title.text()` |
| `d["publisher"]["id"]` | `d.publisher.id` |
| `catalog.datasets(...)` as dicts | `catalog.datasets(..., as_dict=True)` |
| `Catalog()` (public only) | `Catalog()` plus `access_rights="public"` on each call |
| `Catalog(access_rights=None)` | `Catalog()` |
| `updated="annual"` | `accrual_periodicity="annual"` |
| `LiveCatalog().datasets(limit=10)` | `Catalog(live=True).search(limit=10)` |
| `catalog.sources()` | `catalog.info()["sources"]` |
| `publisher["aliases"]` | `publisher.alias` |
| `page.facets.to_dict()` | `result.facets.counts()` |

## [0.12.0](https://github.com/nordicintel/dataportalen/releases/tag/v0.12.0) — 2026-10-04

Breaking. Everything since 0.7.1; versions 0.8.0 to 0.11.0 were never
published, so this is the upgrade from 0.7.1. A catalogue file written by
0.7.x is not read: the first `Catalog()` downloads again.

- The catalogue is a SQLite database instead of a JSONL file, so keeping it
  current is an incremental refresh of under a minute rather than a
  seven-minute rebuild. The `.gz` path is gone. `read_catalog(path)` reads
  every record without building a `Catalog`.
- `Catalog(database=, max_age=, rebuild=, exclude_broken=, access_rights=)`.
  `max_age=` replaces `refresh=` and `stale_after=`; `rebuild=True` is the full
  fetch. `progress`, `workers`, `base_url`, `transport` and the `stale`
  property are removed.
- What the catalogue holds is decided when it is built. By default it is the
  datasets that say `public` (`access_rights=("public",)`), minus dead
  distributions (`exclude_broken=True`): 17,555 datasets of 23,582.
  `Catalog(access_rights=None, exclude_broken=False)` holds everything.
- Dead means the registry's nightly link check got an HTTP error for the
  distribution, or found its host is not in DNS.
  Those distributions are dropped, and so is a dataset left with none. A
  distribution the check could not reach, or was rate-limited on (429), stays
  and carries `unverified`; with
  `exclude_broken=False` a dead distribution carries `broken`. Both are
  `{"reason", "checked"}`.
- Every distribution carries `kind`: `file`, `rowstore`, `ckan`, `huwise`,
  `pxweb`, `kolada`, `doi`, `geodata`, `api`, `web_page` or `unknown` — what it
  is, read from its metadata with no request. Only 4,647 of 35,148 are a
  `file`. `kind=` is a tenth dataset filter and facet in `Catalog`, and one of
  the publisher detail facets; `LiveCatalog` records carry it, but it is
  refused there as a filter.
- `Catalog.verify(which="unverified", *, limit=None)` asks the publishers'
  servers about the distributions the registry's check could not reach, and
  stores what they say. Opt-in, and the only request the package sends to a
  publisher. Alive removes the registry's mark; dead — `404`, `410`, no such
  host, a soft 404 — sets `broken` with `by: "local"`; any other error status
  stays `unverified`. Verdicts are kept across refreshes and dropped by
  `rebuild=True`.
- The registry's latest harvest result per source catalogue is read at the end
  of every download and refresh. A dataset or data service from a source whose
  latest harvest failed carries `stale: {"reason", "checked"}`; it is marked,
  never removed. `Catalog.sources()` lists the sources: `context_id`,
  `status`, `harvested`, `title`, `dataset_count`, `data_service_count`.
  `SourceRecord` is its type.
- `Catalog.info()` gains `excluded` — `access_rights`, `dead_distributions`
  and `dead_datasets`, what the scope left out — and `stale_datasets` and
  `unverified_distributions`.
- `LiveCatalog` has the same methods and arguments as `Catalog` and asks the
  registry directly, with no download and no link health.
- `publishers()` and `publisher()` list and look up publishers. A publisher is
  accepted by id, alias (`scb`, `smhi`, ...), name, organisation number or URI.
- Facets replace breakdowns: `Results.facets`, `Catalog.facets()` and
  `facet_limit=` replace `Results.breakdown`, `Catalog.filters()` and
  `breakdown_limit=`. A row is `(value, count)`.
- `query=` replaces `text=` as the search input.
- `modified_after`/`modified_before` and `issued_after`/`issued_before`
  replace the `updated_*` and `published_*` filters.
- `keyword=` is case-insensitive, exact and any-of, like every other filter;
  an unknown keyword raises with suggestions.
- Removed the `creator` and `place` filters and `creators` on the record.
  `spatial` stays on the record.
- Record shape: a licence is `{"id", "label", "uri"}`; a language is its ISO
  code (`sv`, not `swedish`); a distribution has one `access_url` and one
  `download_url` rather than lists; `byte_size` is present where a
  distribution states one; `info()` gains `first_retrieved` and
  `last_refreshed`.
- Fixed: a licence URL ending in `deed.sv` was filed under a licence that does
  not exist; two datasets sharing a URI across catalogues were merged into
  one; `read_catalog` on a missing path returned `[]` instead of raising.

## [0.7.1](https://github.com/nordicintel/dataportalen/releases/tag/v0.7.1) — 2026-09-30

- An empty catalogue file now raises `ParseError` instead of reading as a
  catalogue of zero datasets.
- Removed `Entry.reload()`, which could only ever raise.
- Confirmed at full census that every breakdown value filters back to exactly
  the count its row claims.

## [0.7.0](https://github.com/nordicintel/dataportalen/releases/tag/v0.7.0) — 2026-09-30

Breaking. The package is now one object with five methods.

- `Catalog` replaces `Dataportal` and `LocalCatalog`. It owns the local file;
  when it may be rewritten is a constructor argument (`refresh=`), so no search
  ever downloads behind your back.
- `datasets()`, `data_services()`, `filters()`, `get()`, `info()`. Exports go
  from 33 to 19.
- Text is always a language map, `{"sv": ..., "en": ...}`, with a key present
  only when that language exists. `text()` reads the best available. The
  `language=` setting is gone.
- `data_services()` searches the 599 DCAT data services in the same file and
  refuses the four filters that cannot apply to a service, naming the ones that
  can.
- Every dataset carries resolved `creators`; `creator=` and `service_type=`
  are filters; `publisher=` accepts an organisation number.
- Breakdown rows carry `.label`.
- Removed `catalogs()`, `agents()`, the registry report methods, the counters,
  the iterators and the public `search()`/`Q`.
- Fixed: a failed download left an empty file that was then read as valid
  (writes are atomic now); an organisation number as `publisher=` matched
  nothing; `publisher` was `None` on some records; keyword filters did not
  agree with their breakdown counts; empty filter values were coerced instead
  of refused; a negative `limit` sliced from the wrong end; thread safety of
  the cache and HTTP sessions.

Versions 0.4.0 to 0.6.0 were intermediate steps towards this surface and were
not published.

## [0.3.0](https://github.com/nordicintel/dataportalen/releases/tag/v0.3.0) — 2026-09-26

- Controlled values are short names in and out (`theme="transport"`,
  `"license": "cc_by_4_0"`). URIs are no longer accepted; an unknown value
  raises with the nearest match.
- Date filters: `updated_after`/`updated_before` and
  `published_after`/`published_before`, accepting `"2024-01-01"`, `"2024-01"`,
  `"2024"`, `date` or `datetime`.
- Fixed: `format="csv"` returned nothing; a bare date produced HTTP 400; three
  `Q` type helpers silently matched nothing.
- Internal modules consolidated from 14 to 7.

## [0.2.0](https://github.com/nordicintel/dataportalen/releases/tag/v0.2.0) — 2026-09-22

Breaking. The output schema changed.

- `title`, `description`, `keywords` and `publisher.name` are language maps;
  `access_url` and `download_url` are lists.
- Fields empty for 96.5% or more of the registry are no longer carried in
  `to_dict()`.
- Vocabulary labels are English.
- Download progress is reported by default; logging goes to the `dataportalen`
  logger.
- Fixed: the vocabulary builder ignored redirects, leaving `availability`
  unlabelled on 15,304 distributions.

## [0.1.0](https://github.com/nordicintel/dataportalen/releases/tag/v0.1.0) — 2026-09-22

First release: dataportal.se as plain dicts, a bundled label table for 1,013
vocabulary terms, a whole-catalogue download to JSONL, and a Solr query
builder.
