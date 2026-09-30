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
from dataportalen import Catalog, text

cat = Catalog()
page = cat.datasets(theme="transport", publisher="trafikverket",
                    updated_after="2024-01-01")

print(page.total)
for dataset in page:
    print(text(dataset["title"]), dataset["distributions"])
```

The first use downloads the whole catalogue — about seven minutes and 64 MB,
once — and every search after that runs against that copy in hundredths of a
second. That copy is how the package works: the registry caps a page at 100
entries and answers about two requests a second whatever you do, so reading it
once beats reading it every time.

A result is a plain dictionary. Text comes back in every language the
publisher wrote — `text()` picks the best one, because 10% of datasets have no
Swedish — and anything from a fixed list of options as one short English word
you can filter on:

```json
{
  "uri": "https://example.org/data/roads",
  "type": "dataset",
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

## What you can filter by

```python
cat.filters()["publisher"][0]
# ValueCount(value='radet_for_framjande_av_kommunala_analyser_kolada',
#            dataset_count=5863)   .label → {'sv': 'Rådet för främjande av ...'}

list(cat.filters())
# ['publisher', 'publisher_type', 'creator', 'theme', 'keyword',
#  'format', 'license', 'access_rights', 'updated', 'language', 'place']
```

Every value goes straight back in, so browsing leads into a search. Each
result carries the same thing for what it matched:

```python
page = cat.datasets(text="cykel")
page.total                        # 388
page.breakdown["publisher"]       # [('kolada', 213), ('trafikverket', 51), ...]
page.breakdown["theme"]           # [('population_and_society', 233), ...]
```

`breakdown_limit=10` caps each list and counts what it cut.

## The rest of it

```python
cat.data_services(service_type="view_service")   # the APIs, not the files
cat.get("https://example.org/data/roads")        # one record by URI
cat.get(uri, format="turtle")                    # its raw RDF, from the registry
cat.info()                                       # the file: path, age, counts
```

The file is ordinary JSONL — one record per line, distributions, publisher and
creators already nested — kept in the usual cache directory. Put it where you
like, and say when it may be rewritten:

```python
Catalog("catalog.jsonl", refresh="if_stale")   # or "if_missing", "always", "never"
```

Nothing is ever downloaded behind a call that looked like a search.

## Documentation

- **[docs/guide.md](docs/guide.md)** — how to use it: searching, what comes
  back, the file, settings.
- **[docs/internals.md](docs/internals.md)** — how it is built: where the short
  values come from, what the registry can and cannot do, development.

## Licence

Code is MIT. The bundled label table is third-party data redistributed under
CC BY 4.0 and EU Decision 2011/833/EU; both notices are in
[LICENSE](LICENSE), with the details in
[docs/internals.md](docs/internals.md).

Metadata you retrieve is published by its respective publishers, each under its
own licence — check the `license` field on the dataset.
