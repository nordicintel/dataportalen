"""The models are a claim about data; this holds them to it.

`models.py` says what every object the package hands out looks like, and
`to_dict()` what its JSON looks like. A type checker believes the
annotations, so they have to be true -- of the fields, and of the shapes
under them. Fields alone are not enough: `keywords` was `[]` on 5.5% of
records and a `{sv, en}` map on the rest, and a names-only check passes on
that.

The checker reads both forms: a model, field by field, and its `to_dict()`,
key by key -- where a key missing or a key nobody declared is a problem too.
"""

from __future__ import annotations

import dataclasses
from typing import (
    Any,
    Dict,
    List,
    Literal,
    Union,
    get_args,
    get_origin,
    get_overloads,
    get_type_hints,
)

import pytest

from conftest import load_fixture
from dataportalen import (
    Catalog,
    DataService,
    Dataset,
    Facets,
    Keywords,
    MultilingualText,
    Publisher,
    SearchResult,
    read_catalog,
)
from dataportalen.client import _present
from dataportalen.entries import DatasetEntry, wrap_entry
from dataportalen.models import _dataset, _record

LANGUAGES = {"sv", "en"}


def _language_map(value: Any, item: Any, hint: Any, where: str) -> list:
    """A MultilingualText or Keywords, or the dict it turns into."""
    if isinstance(value, hint):
        value = value.to_dict()
    elif not isinstance(value, dict):
        return ["%s: %r is not a dict" % (where, type(value).__name__)]
    out = ["%s: %r is not a language" % (where, key) for key in value
           if key not in LANGUAGES]
    return out + problems(value, Dict[str, item], where)


def _facets(value: Any, where: str) -> list:
    """Facets, or ``{filter: [{"value", "count", "label"}, ...]}``."""
    if isinstance(value, Facets):
        value = value.to_dict()
    elif not isinstance(value, dict):
        return ["%s: %r is not Facets" % (where, type(value).__name__)]
    out = []
    for name, rows in value.items():
        for i, row in enumerate(rows):
            here = "%s[%r][%d]" % (where, name, i)
            if not isinstance(row, dict) or set(row) != {"value", "count", "label"}:
                out.append("%s: %r is not a facet row" % (here, row))
                continue
            out += problems(row["value"], str, here + "['value']")
            out += problems(row["count"], int, here + "['count']")
            out += _language_map(row["label"], str, MultilingualText,
                                 here + "['label']")
    return out


def problems(value: Any, hint: Any, where: str = "record") -> list:
    """Every way `value` fails to be a `hint`, as readable strings.

    `hint` is a model class: `value` is either that model or its `to_dict()`.
    """
    if hint is Any:
        return []
    if isinstance(hint, type) and dataclasses.is_dataclass(hint):
        names = [field.name for field in dataclasses.fields(hint)]
        hints = get_type_hints(hint)
        if isinstance(value, dict):                     # the to_dict() form
            out = []
            missing = set(names) - set(value)
            extra = set(value) - set(names)
            if missing:
                out.append("%s: missing %s" % (where, sorted(missing)))
            if extra:
                out.append("%s: undeclared %s" % (where, sorted(extra)))
            for name in names:
                if name in value:
                    out += problems(value[name], hints[name],
                                    "%s[%r]" % (where, name))
            return out
        if not isinstance(value, hint):
            return ["%s: %r is not %s" % (where, type(value).__name__, hint.__name__)]
        return [p for name in names
                for p in problems(getattr(value, name), hints[name],
                                  "%s.%s" % (where, name))]
    if hint is MultilingualText:
        return _language_map(value, str, hint, where)
    if hint is Keywords:
        return _language_map(value, List[str], hint, where)
    if hint is Facets:
        return _facets(value, where)
    origin = get_origin(hint)
    if origin is Union:
        tries = [problems(value, arg, where) for arg in get_args(hint)]
        return [] if any(not t for t in tries) else min(tries, key=len)
    if origin is Literal:
        return [] if value in get_args(hint) else [
            "%s: %r is not one of %r" % (where, value, get_args(hint))]
    if origin is list:
        if not isinstance(value, list):
            return ["%s: %r is not a list" % (where, type(value).__name__)]
        (item,) = get_args(hint)
        return [p for i, v in enumerate(value)
                for p in problems(v, item, "%s[%d]" % (where, i))]
    if origin is dict:
        if not isinstance(value, dict):
            return ["%s: %r is not a dict" % (where, type(value).__name__)]
        _, item = get_args(hint)
        return [p for k, v in value.items()
                for p in problems(v, item, "%s[%r]" % (where, k))]
    if hint is type(None):
        return [] if value is None else ["%s: %r is not None" % (where, value)]
    return [] if isinstance(value, hint) and not (hint is int and isinstance(value, bool)) \
        else ["%s: %r is not %s" % (where, value, getattr(hint, "__name__", hint))]


def both(model: Any) -> list:
    """Problems with a model and with its to_dict(), together."""
    shape = type(model)
    return problems(model, shape) + problems(model.to_dict(), shape)


def within(small: Any, big: Any) -> bool:
    """Whether everything in `small` is in `big`, unchanged."""
    if isinstance(small, dict):
        return isinstance(big, dict) and all(
            key in big and within(value, big[key]) for key, value in small.items())
    if isinstance(small, list):
        return isinstance(big, list) and len(small) == len(big) and all(
            within(a, b) for a, b in zip(small, big))
    return small == big


# -- the checker itself is not a rubber stamp ---------------------------------


def test_the_checker_catches_the_bug_that_prompted_it(cat):
    record = dict(cat.datasets(as_dict=True)[0], keywords=[])
    assert problems(record, Dataset) == [
        "record['keywords']: 'list' is not a dict"]
    model = dataclasses.replace(cat.datasets()[0], keywords=[])
    assert problems(model, Dataset) == ["record.keywords: 'list' is not a dict"]


def test_the_checker_catches_a_missing_and_an_undeclared_key(cat):
    record = dict(cat.datasets(as_dict=True)[0], licence=None)
    del record["license"]
    found = " ".join(problems(record, Dataset))
    assert "missing ['license']" in found and "undeclared ['licence']" in found


def test_the_checker_looks_inside_nested_dicts(cat):
    record = cat.datasets(as_dict=True)[0]
    record["distributions"][0]["access_url"] = ["https://example.org/a"]
    record["publisher"]["alias"] = ["scb"]
    found = " ".join(problems(record, Dataset))
    assert "access_url" in found and "alias" in found


def test_the_checker_looks_inside_nested_models(cat):
    model = cat.datasets()[0]
    model.distributions[0].access_url = ["https://example.org/a"]
    model.title = MultilingualText({"de": "Straßen"})
    found = " ".join(problems(model, Dataset))
    assert "record.distributions[0].access_url" in found
    assert "'de' is not a language" in found


# -- what the Catalog hands out ------------------------------------------------


def test_every_record_the_catalogue_holds_is_its_model(tmp_path, transport):
    from conftest import write_catalog

    catalog = Catalog(write_catalog(tmp_path), max_age=None, _transport=transport,
                      exclude_broken=False)
    records = catalog.datasets() + catalog.data_services()
    assert len(records) == 4
    assert {type(record) for record in records} == {Dataset, DataService}
    for record in records:
        assert both(record) == []


def test_as_dict_is_the_to_dict_of_the_models(cat):
    assert cat.datasets(as_dict=True) == [d.to_dict() for d in cat.datasets()]
    assert cat.data_services(as_dict=True) == [
        s.to_dict() for s in cat.data_services()]


def test_read_catalog_hands_out_what_the_models_are_built_from(tmp_path):
    """read_catalog stays dicts; every one becomes a model losing nothing."""
    from conftest import write_catalog

    for record in read_catalog(write_catalog(tmp_path)):
        model = _record(record, "sv")
        assert both(model) == []
        assert within(record, model.to_dict()), record["uri"]


def test_get_returns_a_model_of_the_declared_shape(cat):
    record = cat.get("https://example.org/roads")
    assert isinstance(record, Dataset)
    assert both(record) == []
    assert cat.get("https://example.org/roads", as_dict=True) == record.to_dict()


def test_a_broken_an_unverified_and_a_sized_file_are_still_the_shape(
        tmp_path, transport):
    """The three fields a distribution fills only when they say something."""
    from conftest import CATALOG_RECORDS, write_catalog

    files = [dict(d) for d in CATALOG_RECORDS[0]["distributions"]]
    files[0]["broken"] = {"reason": "Not Found", "checked": "2026-09-28T02:44:17"}
    files[1]["broken"] = {"reason": None, "checked": None}
    files[1]["byte_size"] = 200704
    record = dict(CATALOG_RECORDS[0], distributions=files)
    catalog = Catalog(write_catalog(tmp_path, [record]), max_age=None,
                      _transport=transport, exclude_broken=False)
    found = catalog.datasets()[0]
    assert both(found) == []
    assert [(d.broken is not None, d.unverified is not None, d.byte_size)
            for d in found.distributions] == [(True, False, None),
                                              (False, True, 200704)]
    for dist in found.to_dict()["distributions"]:
        assert {"broken", "unverified", "byte_size"} <= set(dist)   # always there


def test_publishers_and_publisher_are_their_model(cat):
    rows = cat.publishers()
    assert rows
    for row in rows:
        assert both(row) == []
        assert isinstance(row.dataset_count, int) and row.facets is None
        detail = cat.publisher(row.id)
        assert both(detail) == []
        assert isinstance(detail.facets, Facets)


def test_a_nested_publisher_leaves_its_counts_none(cat):
    publisher = cat.datasets()[0].publisher
    assert both(publisher) == []
    assert (publisher.dataset_count, publisher.data_service_count,
            publisher.facets) == (None, None, None)


def test_a_search_result_is_its_shape(cat):
    result = cat.search()
    assert isinstance(result, SearchResult)
    for dataset in result:
        assert both(dataset) == []
    assert problems(result.facets, Facets) == []
    assert set(result.to_dict()) == {"total", "offset", "limit", "datasets",
                                     "facets", "facets_omitted"}


# -- what the download writes --------------------------------------------------


def test_a_dataset_parsed_from_the_registry_s_rdf_is_the_shape():
    """Not a hand-written fixture: a real entry, through the real parser."""
    entry = wrap_entry(
        {
            "metadata": load_fixture("dataset_recursive.json"),
            "info": load_fixture("dataset_entry.json"),
            "contextId": "547",
            "entryId": "28672",
        },
        default=DatasetEntry,
    )
    record = _present(entry.to_dict())
    model = _dataset(record, "sv")
    assert both(model) == []
    assert model.distributions, "the fixture has files, so they were checked"
    assert within(record, model.to_dict())


@pytest.mark.parametrize("name", ["Dataset", "DataService", "Distribution",
                                  "Publisher", "SearchResult", "MultilingualText",
                                  "Keywords", "License", "Contact", "Temporal",
                                  "LinkMark", "Facets", "Facet", "FacetValue"])
def test_the_shapes_are_exported(name):
    import dataportalen

    assert name in dataportalen.__all__
    assert getattr(dataportalen, name)


@pytest.mark.parametrize("name", ["Results", "DatasetRecord", "DataServiceRecord",
                                  "DistributionRecord", "PublisherRecord",
                                  "PublisherDetail", "LicenseRecord",
                                  "ContactRecord", "TemporalRecord",
                                  "LanguageMap", "KeywordMap", "text"])
def test_the_dict_shapes_are_gone(name):
    """The models replaced them; a stale import fails loudly, not oddly."""
    import dataportalen

    assert name not in dataportalen.__all__
    assert not hasattr(dataportalen, name)


def test_the_listings_are_plain_lists_and_say_of_what(cat):
    assert type(cat.datasets()) is list
    assert type(cat.data_services()) is list

    def returns(method):
        return [get_type_hints(f)["return"] for f in get_overloads(method)]

    assert List[Dataset] in returns(Catalog.datasets)
    assert List[DataService] in returns(Catalog.data_services)
    assert List[Publisher] in returns(Catalog.publishers)
    assert SearchResult in returns(Catalog.search)
