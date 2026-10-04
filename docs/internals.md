# Internals

For working on the package. Using it is [guide.md](guide.md), and every method,
filter and record key is in [reference.md](reference.md). Every call with its
arguments and output is in [cheatsheet.md](cheatsheet.md), which changes
whenever the public interface does.

1. [Where the short values come from](#where-the-short-values-come-from)
2. [Label coverage](#label-coverage)
3. [Rebuilding the table](#rebuilding-the-table)
4. [Module layout](#module-layout)
5. [What the registry can do](#what-the-registry-can-do)
6. [The store](#the-store)
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
facet value:

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
from the records: the URI table first, then the name the publisher wrote.
Without that fallback 13 of the 365 publishers would have no filter value at
all, because they mint URIs the table never saw
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
python tools/build_vocabulary.py --extra-only   # apply EXTRA_LABELS offline
python tools/build_vocabulary.py                # the full rebuild (see below)
```

Built from DIGG's own [DCAT-AP-SE templates](https://github.com/diggsweden/DCAT-AP-SE),
the authority tables the remaining URIs dereference to, and the GeoNames bulk
exports for place names. The script measures coverage against the URIs
publishers are actually using in the live registry and prints what it could not
resolve, so gaps stay visible. Commit the regenerated `vocabulary.json` and
release it as a patch version.

`EXTRA_LABELS` in the script is ours: labels the sources lack or get wrong,
and they win. `--extra-only` applies them to the existing table with no
network, which is how DIGG's `nolicense` and `otherlicense` got their labels
in 0.10.0. A term in `EXTRA_LABELS` whose short name must not follow its
label -- those two, whose slugs were their URI tails before they had a label
-- is pinned in `rdf._FIXED_SLUGS`.

The full rebuild was broken from 0.7.0 to 0.12.0, which put its facet call
and dataset pages back onto `_Registry`. `--skip-build --sample 500` runs in
about five seconds: 2,438 of 2,463 vocabulary values labelled, the rest GEMET
subjects.

## Module layout

Ten modules; callers import from the package root.

| Module | Holds |
| --- | --- |
| `core.py` | version, exceptions, logging and progress, the HTTP transport |
| `rdf.py` | namespaces, the RDF/JSON parser, the label table, the short-name layer |
| `models.py` | `Dataset`, `DataService`, `Distribution`, `Agent` and friends, `to_dict()`, and the result types |
| `query.py` | the `Q` Solr query builder |
| `client.py` | `Catalog`, `_Registry` (HTTP and Solr), the store, the download, the publisher resolver |
| `live.py` | `LiveCatalog`: filters compiled to Solr, facet responses folded back into the package's values |
| `retrieval.py` | `classify` and `KINDS`: what a distribution is, read from its metadata; no request |
| `verify.py` | `check_links`: asking a distribution's server whether it is there. Reached only through `Catalog.verify` |
| `__init__.py` | 34 exports, and nothing else |
| `records.py` | `TypedDict`s for every dict handed out; no behaviour |

### Public and internal

`Catalog` is the public surface: `datasets()`, `data_services()`, `facets()`,
`publishers()`, `publisher()`, `get()`, `info()`, `close()`. Seven methods
where 0.6.0 had 32 on `Dataportal` plus 7 on `LocalCatalog`. Plus `text()`,
`default_catalog_path()`, the result types and the record `TypedDict`s -- see
[reference.md](reference.md#everything-exported). `LiveCatalog` has the same
seven methods with the same arguments; a test holds the signatures equal.
`sources()` and `verify()` are `Catalog`'s alone: both keep what they find in
the database, and `LiveCatalog` has none.

**`LiveCatalog` is built from the download's own parts.** A search hit is
assembled by `_targeted_indexes` and `_assemble`, the code a limited export
uses, and read through `_present` -- which is why its records equal the
database's (50 of 50 compared; only link health differs) and why they carry
`kind`, though the registry cannot be asked for it and `kind=` is refused. Publishers go
through the same `_Publishers`, fed `(agent, count)` pairs from the registry's
publisher facet instead of records, so an id means the same organisation in
both classes (356 of 356 rows identical). What is new is translation in both
directions: a filter value becomes every raw value the registry holds for it
-- `sv` is three URIs, `zip` two media types, learned from one facet request
and folded through `slug_for` -- and a facet response is folded back into
short names. Keywords are the exception: the exact index is case-sensitive,
so each search first asks which spellings exist with a `(?iu)` `facetMatches`
pattern, then matches those as quoted phrases.

Everything that talks to the registry sits behind `_Registry`, and the RDF layer
— `Q`, `Graph`, `Entry`, `Dataset`, `SearchPage` — is internal but very much
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
| `dcat:Catalog` | 656 | no -- only 157 hold a dataset, and 127 of 152 live ones have exactly one publisher, so it duplicates `publisher`. `context_id` on the record identifies the harvest source |
| `foaf:Agent` + `foaf:Organization` + `prov:Agent` | 7,609 | nested as `publisher`. 4,916 of them are `private_individual` and reach no dataset |
| vcard contact types (8 of them) | ~48,000 | nested as `contact_points` |
| EntryStore/EntryScape internals (17 types) | — | no. `PipelineResult`, `List`, `CatalogContext`, `CatalogStatistics`, `LinkCheckReport`, `MQA`, `User`... the CMS talking to itself |
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
requests on top of 691. The download reads them and stores, on every
distribution the check called broken, the registry's message and when it
looked: `{"reason", "checked"}` under `broken`. Nothing goes on a distribution
that passed or that the check never reached. Until 0.10.0 every distribution
carried a `link` dict -- 18,360 successes and 5,021 the check skipped, for the
11,761 that mattered -- and every record carried a landing-page verdict that
was `None` on 10,331 datasets and `broken` on 277 whose distributions were all
fine.

What the check covers, counted over 40 reports: `dcat:downloadURL` 3,072,
`dcat:accessURL` 1,426, `dcat:landingPage` 130, `foaf:page` 78,
`dcterms:conformsTo` 42, `dcat:endpointDescription` 9 -- and
`dcat:endpointURL` never. So whether a data service's API answers is
something the registry does not know, and `exclude_broken` leaves data
services alone rather than pretending.

**`broken` is two different things, and only one of them is dead.** The
registry records no status code for a broken link -- `statusCode` is null on
all 11,900 -- only a message. On 718 distributions the message is an HTTP error
(Not Found 395, Forbidden 212, Internal Server Error
40, Bad Request 33, Unauthorized 14, Access Denied 8, Gone 4, `404` 4, File
not found 3, Service Unavailable 2, Method Not Allowed 2, `400` 1): a server
answered and said no. On 185 it is `request to ... failed, reason:
getaddrinfo ENOTFOUND ...`: the host is not in DNS, so there is no server to
ask, and that is dead too. On 10,858 it is no usable answer (no message 5,263,
`request to ... failed` for any other reason 2,698, Too Many Requests 2,624,
`timeout` 255, `maximum redirect` 11, an `ftp://` URL with credentials in it
7): the registry's checker did not get through, which says nothing about the
distribution.
Too Many Requests is an HTTP status, but it is a server telling the checker
to slow down, so it counts as not getting through.

0.10.0 treated both as dead, and Statistics Sweden went from 4,306 datasets
to 33: api.scb.se resets the checker's connection, so 7,091 of its 14,228
links are "broken", and each answers 200 to an ordinary GET. Since 0.11.0 the
stored `broken` is split when a record is read (`client._present`, which both
`Catalog` and `read_catalog` go through): a reason on the HTTP-error list
stays `broken`, anything else becomes `unverified` with the same two keys.
`Catalog(exclude_broken=True)` drops `broken` distributions and a dataset left
with none -- 127 public datasets -- and never an unverified one. A dataset that
never had distributions (1,647) is not dead and stays.

`_DEAD_REASONS` is an allow-list on purpose: every standard HTTP reason phrase
from 400 up except 429, the bare numbers, and three phrasings the checker has been seen
to use. A message it invents next year is unverified until someone adds it,
so the default keeps distributions rather than dropping them. Written the
other way round -- a list of non-answers, everything else dead -- it would have
dropped the seven `ftp://` ones.

A catalogue keeps about three days of reports; only the newest is read. Verdicts
are current: of 12,718 broken records, 12,711 were checked this year.

### Asking the servers: `verify()`

The 10,858 unverified are the registry's checker failing, not the publishers'
servers, so the only way to know is to ask again from here.
`Catalog.verify(which="unverified", *, limit=None)` does, through
`verify.check_links`, and it is the only thing in the package that sends a
request to a publisher's server. Nothing calls it implicitly.

It reads the database through `read_catalog`, so `exclude_broken` does not
narrow it and `access_rights` does, takes the distributions carrying `which`
(`unverified`, `broken`, or `all` of them), and asks about the first address
of each -- `download_url` before `access_url`. How an answer becomes a verdict:

| | |
| --- | --- |
| `HEAD` first | a server that answers it with an error (other than 429) is asked again with a one-byte ranged `GET`, because plenty refuse `HEAD` and serve the address. Only what the `GET` says counts |
| A lost connection or a timeout | tried once more; one lost packet must not become a claim about a publisher |
| A certificate error | retried without verification. The verdict stands and carries `invalid_cert`, which the summary counts |
| No answer at all | DNS decides: a host that does not exist is dead (`host not found`), a host that exists and did not answer is unverified |
| `429` | unverified, and nothing more is asked of that host in this run. Nor after two addresses in a row that got no answer |
| A redirect | compared with where the site sends a path that cannot exist (one extra `HEAD` per site, for a random path). The same place is a soft 404 |
| Pace | one request at a time per host with a 0.4 s pause, 8 hosts at once, a 15 s timeout. api.scb.se allows 30 requests in 10 seconds; this stays under it |
| `limit=` | applied after the addresses are interleaved by host, so the first N are N hosts and not one |

**Local dead is narrower than the registry's dead.** `404`, `410`, no such
host, a soft 404 -- and nothing else. For the registry's verdicts every HTTP
error is dead, because its message is all there is to go on. Here the status
is known, and any other error status is a server that is there and would not
serve this request as asked: a WMS endpoint answers `400` to an address with
no parameters, a login wall `401`, an overloaded server `503`. Measured on 200
addresses, half of what the wider rule called dead was exactly that. Those
stay unverified, with the status phrase as the reason.

Verdicts go into the `verified` table, keyed by URL, and are applied when a
record is read (`client._apply_verdict`, called from `_present`), for the same
reason the `broken`/`unverified` split is: the stored record stays what the
registry said. Alive removes the registry's mark. Dead sets `broken` with
`by: "local"`, so `exclude_broken` drops it like any other. Against
`unverified` a local answer always wins -- the registry's checker got none, so
there is nothing to weigh it against. Against `broken` the later look wins. A
local answer that is itself unverified changes nothing. `verify()` re-reads
the database before it returns, so the catalogue it was called on already
agrees with what it found.

The summary is `{checked, alive, dead, unverified, invalid_cert, requests,
elapsed}`. A sample of 200 addresses over all access levels (2026-10-04): 91
alive, 19 dead (all Not Found), 90 still unverified, 290 requests, 157 s. All
10,858 is about 11,000 requests and most of an hour, because 7,091 of them
are api.scb.se and one host is asked one thing at a time.

### Harvest status

A record can also be out of date without any link being dead. The registry
harvests each source catalogue nightly and keeps the result as a
`PipelineResult` entry. `client._harvest_status` reads the latest of each --
`graphType:PipelineResult AND tag.literal:latest`, 7 requests -- at the end of
every download and refresh, in its own transaction after the records are
safe. It is never fatal: an unreadable answer is logged and the stored table
is left as it was. A download under a `limit` skips it.

`_write_sources` replaces the `source` table: it is a snapshot, not something
to merge. Measured 2026-10-04: 666 sources, 239 success, 427 failed. Only 8 of
the failed ones have datasets in the registry -- 81 datasets unscoped (STUNS
35, Sandvikens kommun 15, Trafiklab 13, Hack4heritage 6, Falu kommun 5,
National Museums of World Culture 4, Rymdstyrelsen 2, Kommun Kartan 1), 1 in
the default catalogue. The other 419 are registrations that never yielded a
dataset.

`_present` puts `stale: {"reason": "harvest failed", "checked": <harvested>}`
on a dataset or data service whose source failed its latest harvest.
`Catalog.sources()` is the table joined with this catalogue's counts per
`context_id`, most datasets first; it is empty for a database written before
the table existed.

**Stale is a mark, not an exclusion.** A failed harvest says the registry
could not read the publisher's catalogue last night. It says nothing about
the data: the record is what the last good harvest left, and the distributions
it points at may all answer. Dropping it would remove 81 datasets for a
failure that is the source catalogue's. So nothing is removed, and
`info()["stale_datasets"]` counts them.

### What a distribution is

DCAT-AP-SE says only that `accessURL` is a web address, and one distribution
in fourteen has a `downloadURL`, so "a file" is the wrong word for most of
them. `retrieval.classify` reads `kind` out of what a distribution carries,
strongest signal first: a geodata format; the shape of the address (rowstore,
CKAN, Huwise, PxWeb, Kolada, DOI); `downloadURL`, which the profile says is a
file -- unless the format says `html` and the address has no file extension,
because publishers do point it at web pages; the format, for an `accessURL`;
an access service with nothing else to go on. No request is made, so it is a
statement about what the publisher described, not about what the server
returns.

`_present` adds it on reading, like the split above, so the rules can change
without anybody downloading anything. `kind=` is a dataset filter and facet
whose values are exactly `retrieval.KINDS`; a facet row has no label, because
`file` and `api` are the package's words and must not borrow a vocabulary
term's.

### Where each call goes

Everything but two reads the file. `datasets()`, `data_services()`, `facets()`,
`publishers()`, `publisher()`, `get()`, `info()` and `sources()` never touch the network; `get(uri, format=...)` is
one exception, and you have to name a format to get it. `verify()` is the
other, and the only call that goes to a publisher's server rather than the
registry. The download is the only other thing that makes requests. `LiveCatalog` is the other way round:
every method is a request, and it is a different class rather than a mode, so
which one you hold says which you get.

Searches return `Results`, a `list` of dicts carrying `total`, `offset`,
`limit`, `has_more` and `facets`.

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
falls back to what the catalogue actually holds, so everything a facet
reports can be filtered on. A value that is neither known nor present is
still an error, with suggestions.

### Two things that will bite

**A local filter matches its value exactly when the file contains it.** Only
then does it fall back to vocabulary expansion. That ordering is load-bearing:
`json` also resolves to `application/json+zip`, whose own slug is
`json_in_a_zip`, so expanding first made `format="json"` return 14,173 where the
facet said 14,119 -- and the facet's counts stopped agreeing with the
searches its values produce. `tests/test_filters.py` round-trips every value of
every filter, which is the invariant to keep. On the real catalogue (unscoped,
2026-10-01) that is 22,401 values, 21,807 of them keywords: 0 mismatches,
49 minutes.

**An organisation's slug comes from the URI table first and its own name
second.** Dropping the fallback silently loses 13 of the 365 publishers,
because they mint URIs the table never saw. Dropping the table and using only
names would break every slug users have written down.

**A publisher is its `id`, and one resolver finds it.** `client._Publishers` is
built in `Catalog._read` from every record in the database, before
`access_rights` and `exclude_broken` narrow anything, so who a publisher is
does not depend on scope -- only its counts do. 8 of the 356 have two URIs; the
entity is one real agent (the URI the table knows, then the one on most
records, then the lowest URI), never a merge. Its resolver is tiered -- id,
alias, URI, identifier, slugified name -- and the first tier to claim a key
keeps it, which settles the one collision in the registry (a museum whose
Swedish name slugifies to another museum's id). `publisher=` and
`publisher()` both go through it. Before 0.11.0 a URI, a bare organisation
number and 24 names raised, though the docs said they worked, and
`data_services(publisher=id)` raised for a publisher with only datasets
because ids were vouched for per kind of record.

`id` and `aliases` on the nested `publisher` dict are added by `_present`
when a record is read, not stored: the organisation table and `aliases.json`
change between releases and the database does not.

**A keyword is matched folded and shown as written.** Strip and casefold --
23,373 spellings of 21,359 keywords -- but not `slugify`, which would merge
169 groups that are different words. A facet row shows the commonest spelling
(first alphabetically on a tie), because showing the folded form would rewrite
60% of all values into spellings no record carries. "Known" is checked across
datasets and data services together: a keyword has no vocabulary to vouch for
it, and per kind `data_services(keyword="kommun")` would raise. Facet labels
are looked up per filter; by bare value, 45 keywords wore a vocabulary term's
label.

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
CREATE TABLE verified (            -- what Catalog.verify() found
    url          TEXT PRIMARY KEY,
    status       TEXT NOT NULL,    -- alive | dead | unverified
    reason       TEXT,
    checked      TEXT,
    invalid_cert INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE source (              -- the latest harvest of each source catalogue
    context_id TEXT PRIMARY KEY,
    status     TEXT,               -- success | failed
    harvested  TEXT,
    title      TEXT
);
```

`verified` and `source` came after schema 4 and did not bump `SCHEMA_VERSION`:
both are created if missing when the database is opened, and neither changes
the shape of `doc`. An older database reads as it did, with no verdicts and no
sources, until the next refresh fills `source`. `verified` is upserted by URL
and kept across refreshes; `source` is replaced whole.

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
copying it. A full build clears `record` -- and `verified`, whose verdicts
belong to the build they were asked of -- inside the same transaction as the
write, so it is a real rebuild -- it was an upsert until 0.9.0, and a
"rebuild" left 27 rows carrying a field the schema had dropped -- and a
download that dies still leaves the old catalogue untouched.

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

## Measured, not assumed

The numbers this design rests on, and where they came from. Everything here was
measured against the live registry rather than estimated, because several of
them overturned a guess. Counted on 2026-09-30; the registry gains a handful of
datasets a day, so the absolute figures drift and the ratios do not.

| Fact | Value |
| --- | --- |
| Datasets / data services / distributions | 23,582 / 599 / 35,148 in the file (2026-10-01) |
| The default `Catalog`: public, minus the dead | 17,555 datasets, 578 data services, 287 publishers |
| Unscoped | 23,582 / 599 / 356 |
| `access_rights`: public / non_public / restricted / unset | 17,682 / 1,489 / 244 / 4,167 |
| Link verdicts on distributions: success / broken / excluded | 18,360 / 11,761 / 5,021 |
| ...of the broken: an HTTP error or a host not in DNS (dead) / no usable answer (unverified) | 903 / 10,858 |
| Datasets with every distribution broken / every distribution dead / with no distributions at all | 5,330 / 127 public / 1,647 |
| The default scope left out: records by `access_rights` / dead distributions / datasets with nothing else | 5,921 / 784 / 127 |
| Unverified distributions held by the default `Catalog` | 8,220 |
| `verify()` on 200 addresses, all access levels (2026-10-04): alive / dead / still unverified | 91 / 19 (all Not Found) / 90 |
| ...requests / time | 290 / 157 s |
| `verify()` over all 10,858 unverified | ~11,000 requests, most of an hour; 7,091 are api.scb.se |
| Source catalogues: all / latest harvest failed / success (2026-10-04) | 666 / 427 / 239 |
| ...failed sources with datasets in the registry / that never yielded one | 8 / 419 |
| Stale datasets: unscoped / in the default `Catalog` | 81 / 1 |
| Harvest status: requests | 7 |
| Distributions by `kind` (all 35,148) | pxweb 10,468, kolada 5,963, doi 5,021, file 4,647, huwise 3,520, geodata 2,794, web_page 1,405, rowstore 552, unknown 324, ckan 310, api 144 |
| Datasets by `kind`, the default `Catalog` | pxweb 6,704, kolada 5,952, file 2,103, doi 1,734, geodata 945, web_page 850, rowstore 421, unknown 225, ckan 78, api 2 |
| SCB under 0.10.0's rule / under this one | 33 / 4,303 of 4,306 |
| Distributions stating `dcat:byteSize` / readable as a size | 485 / 484, from 13 catalogues |
| Records whose `keywords` was `[]` rather than `{}` before schema 4 | 1,324 (5.5%) |
| Keyword spellings / keywords once folded / groups with >1 spelling | 23,373 / 21,359 / 1,765 |
| Publishers with two URIs / two names / two types | 8 / 4 / 6 of 356 |
| Records naming no publisher | 27 |
| Landing-page verdicts: none / success / excluded / broken | 10,331 / 7,791 / 5,021 / 439 |
| ...of the broken landing pages, with every distribution fine | 277 |
| What the link check covers (40 reports) | downloadURL 3,072, accessURL 1,426, landingPage 130, foaf:page 78, conformsTo 42, endpointDescription 9, endpointURL 0 |
| Link-check reports: requests / time | 165 / ~10 s |
| Registry throughput by threads: 1 / 2 / 4 / 8 | 0.84 / 2.26 / 2.46 / 2.31 req/s |
| Full build: requests / time / size | 856 / 441 s with 2 threads (about 17 min with 1) / 94 MiB |
| Incremental refresh, one day of churn | 630 datasets, ~38 s |
| Loading the database | ~1.4 s (of which json.loads ~1.2 s) |
| Distributions with >1 access URL / >1 download URL | 147 / 130 of 35,148 |
| Data services with >1 endpoint URL / description / served dataset | 4 / 1 / 8 of 599 |
| Distribution licences mis-slugged before 0.10.0 | 2,578 (`deed_sv` 1,486, `1_0` 686, `4_0` 406) |
| Datasets saying nolicense / otherlicense | 8,231 / 913 |
| Distinct languages on datasets / with a two-letter code | 66 / 50 |
| Datasets with a spatial coverage / of those, "Sweden" | 4,471 / 3,068 |
| Sub-national place values / on exactly one dataset | 540 / 223 |
| A filtered search / an unfiltered one (facets over everything) | ~0.07 s / ~0.5 s |
| `facets()` over the default catalogue | ~0.35 s |
| `publishers()` / `publisher("scb")` | under a millisecond once built / ~0.06 s |
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
| Facet values round-tripped on the real catalogue / mismatches | 22,401 / 0 |
| `LiveCatalog`: a count / a page of 50 full records | ~0.1 s / a few seconds |
| ...its filter-value index / publisher index | 1 request / ~20 requests, held per object |
| URIs one entry lookup batch of 20 can return | up to 22 (a URI can belong to two entries) |
| Publisher URIs of every national authority, as a request | 12,703 characters; the registry stops near 8,190 |

Four of these changed a decision:

**Concurrency has one useful step.** One thread gets 0.84 requests a second
and two get 2.26; four and eight get the same 2.26. So the pool is fixed at
two and the `workers=` argument went: the only choice it offered was between
seven minutes and seventeen.

**The counter was slower than the thing it replaced.** `count_datasets(theme=
"transport")` measured 53.1 ms against 43.9 ms for `datasets(theme="transport",
limit=0)` -- and the cheap version threw away the facets it got for free. It
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

The same notice ships with the package, in [NOTICE](../NOTICE) and in the
`_comment` key of `vocabulary.json` itself.
