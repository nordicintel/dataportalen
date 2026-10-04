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

catalog = Catalog()      # downloads the catalogue the first time: ~7 min, ~95 MB
print(catalog.info())    # {'datasets': 17601, 'data_services': 578, ...}

page = catalog.datasets(theme="transport", format="csv")
print(page.total)                    # 44
print(page.facets["publisher"][:3])  # who publishes them

for dataset in page:
    print(text(dataset["title"]), text(dataset["publisher"]["name"]))
    for dist in dataset["distributions"]:
        print("   ", dist["format"], dist["access_url"])
```

**That first line takes about seven minutes.** It downloads the whole catalogue
once, and every search after that is local and takes hundredths of a second.
When the copy is over a week old, the next `Catalog()` catches up in under a
minute.

By default the catalogue holds the datasets that say `public`, minus the
files the registry's nightly link check got an HTTP error for.
`Catalog(access_rights=None, exclude_broken=False)` holds everything.

Publishers are there too: `catalog.publishers()` lists them, and
`catalog.publisher("scb")` is one of them with what it publishes.

No room for a download, or one question to ask? `LiveCatalog()` has the same
methods and asks the registry directly, a page at a time.

## Documentation

- **[docs/cheatsheet.md](docs/cheatsheet.md)** — every public name once, every argument
  spelled out, the output shape underneath.
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
