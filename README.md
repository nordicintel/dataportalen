# dataportalen

Python access to [dataportal.se](https://www.dataportal.se) that gives you
**plain dicts, not RDF**.

Sweden's open-data registry publishes everything as DCAT-AP-SE: RDF graphs,
blank nodes, and controlled vocabularies expressed as bare URIs. The
[registry documentation](https://docs.dataportal.se/registry/api/) is explicit
that turning `http://publications.europa.eu/resource/authority/data-theme/TRAN`
into the word "transport" is _your_ problem. This package makes it not your
problem — short names go in, short names come out, and everything is
`json.dumps`-able.

## Install

```bash
pip install dataportalen
```

Distribution and import name are both `dataportalen`. The unrelated
`dataportal` package on PyPI is a Korean public-data client — not this.

## Search

```python
from dataportalen import Dataportal

with Dataportal() as dp:
    page = dp.datasets(theme="transport", publisher="trafikverket",
                       updated_after="2024-01-01")
    print(page.total)
    for dataset in page:
        print(dataset.to_dict())
```

```json
{
  "uri": "https://example.org/data/roads",
  "title": {"sv": "Vägtrafiknät", "en": "Road traffic network"},
  "themes": ["transport"],
  "license": "cc_by_4_0",
  "access_rights": "public",
  "accrual_periodicity": "annual",
  "publisher": {"name": {"sv": "Trafikverket"}, "type": "national_authority"},
  "distributions": [
    {"download_url": ["https://...csv"], "format": "csv"}
  ]
}
```

## The whole catalogue in one file

```python
from dataportalen import download_catalog

download_catalog("catalog.jsonl")
# 23,548 datasets · 35,102 distributions · 117 MiB · ~5 minutes
```

One dataset per line, distributions and publisher already nested, with a live
progress line while it runs.

## Documentation

- **[docs/reference.md](docs/reference.md)** — every filter, the date filters,
  paging, the catalogue export, entities, async, logging, configuration.
- **[docs/vocabulary.md](docs/vocabulary.md)** — where the short names come
  from, measured coverage, and how to regenerate the table.

## Licence

Code is MIT. The bundled label table is third-party data redistributed under
CC BY 4.0 and EU Decision 2011/833/EU; both notices are in
[LICENSE](LICENSE), with the details in
[docs/vocabulary.md](docs/vocabulary.md).

Metadata you retrieve is published by its respective publishers, each under its
own licence — check the `license` field on the dataset.
