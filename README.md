# dataportalen

[![PyPI](https://img.shields.io/pypi/v/dataportalen)](https://pypi.org/project/dataportalen/)
[![CI](https://github.com/nordicintel/dataportalen/actions/workflows/ci.yml/badge.svg)](https://github.com/nordicintel/dataportalen/actions/workflows/ci.yml)
[![Licence](https://img.shields.io/pypi/l/dataportalen)](LICENSE)

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

## Quick start

```python
from dataportalen import Catalog, text

cat = Catalog()          # downloads the catalogue the first time: ~7 min, 64 MB
print(cat.info())        # {'datasets': 23576, 'data_services': 599, ...}

page = cat.datasets(theme="transport", format="csv")
print(page.total)                               # 67

for dataset in page:
    print(text(dataset["title"]), text(dataset["publisher"]["name"]))
    for dist in dataset["distributions"]:
        print("   ", dist["format"], dist["access_url"])
```

**That first line takes about seven minutes.** It downloads the whole catalogue
once, and every search after that is local and takes hundredths of a second.
Run it deliberately the first time — it prints a progress line.

## Documentation

- **[docs/guide.md](docs/guide.md)** — how to use it, in the order you hit it:
  searching, discovering filters, reading a result, recipes.
- **[docs/reference.md](docs/reference.md)** — every method, filter, record key
  and error, as tables.
- **[docs/internals.md](docs/internals.md)** — how it is built: the short
  values, the module layout, development, releasing.

## Licence

Code is [MIT](LICENSE). The bundled label table is third-party data
redistributed under CC BY 4.0 and EU Decision 2011/833/EU; the attributions are
in [NOTICE](NOTICE), with the details in
[docs/internals.md](docs/internals.md#attribution).

Metadata you retrieve is published by its respective publishers, each under its
own licence — check the `license` field on the dataset.

## Status

Most of the code here was written with an AI coding assistant. The design,
features and scope are my decisions, and everything shipped has been reviewed
and tested against the live registry.

This package exists to support NordicIntel's main project, so expect it to be
maintained rather than extended. Bug reports are welcome; I am not looking for
co-maintainers.
