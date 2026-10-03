# Changelog

All notable changes to this package. Versions are on
[PyPI](https://pypi.org/project/dataportalen/); the headings link to the
GitHub release.

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
