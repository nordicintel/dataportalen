# dataportalen

Python access to [dataportal.se](https://www.dataportal.se) that gives you
**plain dicts, not RDF**.

Sweden's open-data registry describes its 23,500 datasets in RDF, which means
every value arrives as a web address: a dataset about roads is filed under
`http://publications.europa.eu/resource/authority/data-theme/TRAN` rather than
under "transport". Working out that it means "transport" is, per the
[registry's own documentation](https://docs.dataportal.se/registry/api/), your
problem.

This package makes it not your problem. You search with words and you get
dictionaries back.

## Install

```bash
pip install dataportalen
```

Distribution and import name are both `dataportalen`. The unrelated
`dataportal` package on PyPI is a Korean public-data client — not this.

## Search

```python
from dataportalen import Dataportal

with Dataportal(language="sv") as dp:      # "en", or "all" for every language
    page = dp.datasets(theme="transport", publisher="trafikverket",
                       updated_after="2024-01-01")
    print(page.total)
    for dataset in page:
        print(dataset.to_dict())
```

```json
{
  "uri": "https://example.org/data/roads",
  "title": "Vägtrafiknät",
  "themes": ["transport"],
  "license": "cc_by_4_0",
  "access_rights": "public",
  "accrual_periodicity": "annual",
  "publisher": {"name": "Trafikverket", "type": "national_authority"},
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

- **[docs/guide.md](docs/guide.md)** — how to use it: searching, what comes
  back, the catalogue download, settings, async.
- **[docs/internals.md](docs/internals.md)** — how it is built: where the short
  values come from, module layout, development, releasing.

## Licence

Code is MIT. The bundled label table is third-party data redistributed under
CC BY 4.0 and EU Decision 2011/833/EU; both notices are in
[LICENSE](LICENSE), with the details in
[docs/internals.md](docs/internals.md).

Metadata you retrieve is published by its respective publishers, each under its
own licence — check the `license` field on the dataset.
