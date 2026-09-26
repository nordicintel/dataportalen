# Where the short names come from

The registry states controlled values as URIs:

```
http://publications.europa.eu/resource/authority/data-theme/TRAN
http://purl.org/adms/publishertype/LocalAuthority
http://creativecommons.org/licenses/by/4.0/
```

This package never shows you one. Each URI is mapped to a short name derived
from its official **English** label — `transport`, `local_authority`,
`cc_by_4_0` — and that name is the only form: filters take it, `to_dict()`
returns it. There is no code, URI and label to choose between.

Standardised terms are English on purpose; a Swedish rendering of "annual" or
"public" helps nobody building on this. Publisher-authored text (title,
description, keywords) keeps every language the publisher supplied.

The table ships in the package as `vocabulary.json` — 1,017 terms, no network
call. It is looked up directly if you need it:

```python
from dataportalen.rdf import slug_for, resolve
from dataportalen import known_values, known_publishers

slug_for("http://publications.europa.eu/resource/authority/data-theme/TRAN")
# 'transport'
resolve("transport")
# ['http://publications.europa.eu/resource/authority/data-theme/TRAN']
known_values("trans")       # every short name matching "trans"
known_publishers("traf")    # every publisher name matching "traf"
```

A URI with no label falls back to its last path segment, slugified, so output is
always a short string and never a URI.

## Coverage

Measured over a seeded random sample of **5,000 datasets** drawn from 225 random
positions across the corpus (25,109 vocabulary values):

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

Sampling is offset-based because the index has no random sort, so it is mildly
clustered — a good estimate rather than a census.

```bash
python tools/build_vocabulary.py --skip-build --sample 5000
python tools/build_vocabulary.py --skip-build --sample all   # exact, slower
```

## Regenerating

```bash
python tools/build_vocabulary.py
```

Built from DIGG's own [DCAT-AP-SE templates](https://github.com/diggsweden/DCAT-AP-SE),
the authority tables the remaining URIs dereference to, and the GeoNames bulk
exports for place names. The script measures coverage against the URIs
publishers are _actually_ using in the live registry and prints what it could
not resolve, so gaps stay visible rather than assumed.

The publisher table (`organisations.json`, 538 entries) is generated from the
registry's own organisation list, keyed by both slugged name and organisation
number.

## Attribution

`vocabulary.json` is **data, not code**, compiled from third-party sources and
redistributed under their terms:

| Source | Used for | Licence |
| --- | --- | --- |
| [DIGG DCAT-AP-SE](https://github.com/diggsweden/DCAT-AP-SE) | Swedish/English vocabulary labels | CC BY 4.0 |
| [GeoNames](https://www.geonames.org/) | place names for `spatial` | CC BY 4.0 |
| [EU Vocabularies](https://op.europa.eu/en/web/eu-vocabularies) | themes, file types, frequencies, languages | Decision 2011/833/EU |
| [INSPIRE registry](https://inspire.ec.europa.eu/registry) | INSPIRE themes and code lists | Decision 2011/833/EU |

Full notices: [NOTICE](../NOTICE).
