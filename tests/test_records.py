"""The TypedDicts are a claim about data; this holds them to it.

`records.py` says what every dict the package hands out looks like. A type
checker believes it, so it has to be true -- of the keys, and of the shapes
under them. Keys alone are not enough: `keywords` was `[]` on 5.5% of records
and a `{sv, en}` map on the rest, and a keys-only check passes on that.
"""

from __future__ import annotations

from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

import pytest

from conftest import load_fixture
from dataportalen import (
    Catalog,
    DataServiceRecord,
    DatasetRecord,
    Publisher,
    PublisherDetail,
    read_catalog,
)
from dataportalen.client import _present
from dataportalen.models import Dataset, wrap_entry


def problems(value: Any, hint: Any, where: str = "record") -> list:
    """Every way `value` fails to be a `hint`, as readable strings."""
    if hint is Any:
        return []
    if hasattr(hint, "__required_keys__"):                  # a TypedDict
        if not isinstance(value, dict):
            return ["%s: %r is not a dict" % (where, type(value).__name__)]
        keys, out = set(value), []
        missing = hint.__required_keys__ - keys
        extra = keys - hint.__required_keys__ - hint.__optional_keys__
        if missing:
            out.append("%s: missing %s" % (where, sorted(missing)))
        if extra:
            out.append("%s: undeclared %s" % (where, sorted(extra)))
        hints = get_type_hints(hint)
        for key in keys & set(hints):
            out += problems(value[key], hints[key], "%s[%r]" % (where, key))
        return out
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


def shape_of(record):
    return DatasetRecord if record["type"] == "dataset" else DataServiceRecord


# -- the checker itself is not a rubber stamp ---------------------------------


def test_the_checker_catches_the_bug_that_prompted_it(cat):
    record = dict(cat.datasets()[0], keywords=[])
    assert problems(record, DatasetRecord) == [
        "record['keywords']: 'list' is not a dict"]


def test_the_checker_catches_a_missing_and_an_undeclared_key(cat):
    record = dict(cat.datasets()[0], licence=None)
    del record["license"]
    found = " ".join(problems(record, DatasetRecord))
    assert "missing ['license']" in found and "undeclared ['licence']" in found


def test_the_checker_looks_inside_nested_dicts(cat):
    record = cat.datasets()[0]
    record["distributions"][0]["access_url"] = ["https://example.org/a"]
    record["publisher"]["aliases"] = None
    found = " ".join(problems(record, DatasetRecord))
    assert "access_url" in found and "aliases" in found


# -- what the Catalog hands out ------------------------------------------------


def test_every_record_the_catalogue_holds_is_its_typeddict(tmp_path, transport):
    from conftest import write_catalog

    catalog = Catalog(write_catalog(tmp_path), max_age=None, _transport=transport,
                      access_rights=None, exclude_broken=False)
    records = list(catalog.datasets(limit=None)) + list(catalog.data_services(limit=None))
    assert len(records) == 4
    for record in records:
        assert problems(record, shape_of(record)) == []


def test_read_catalog_hands_out_the_same_shapes(tmp_path):
    from conftest import write_catalog

    for record in read_catalog(write_catalog(tmp_path)):
        assert problems(record, shape_of(record)) == []


def test_get_returns_a_record_of_the_declared_shape(cat):
    record = cat.get("https://example.org/roads")
    assert problems(record, DatasetRecord) == []


def test_a_broken_an_unverified_and_a_sized_file_are_still_the_shape(
        tmp_path, transport):
    """The three keys a distribution has only when they say something."""
    from conftest import CATALOG_RECORDS, write_catalog

    files = [dict(d) for d in CATALOG_RECORDS[0]["distributions"]]
    files[0]["broken"] = {"reason": "Not Found", "checked": "2026-09-28T02:44:17"}
    files[1]["broken"] = {"reason": None, "checked": None}
    files[1]["byte_size"] = 200704
    record = dict(CATALOG_RECORDS[0], distributions=files)
    catalog = Catalog(write_catalog(tmp_path, [record]), max_age=None,
                      _transport=transport, exclude_broken=False)
    found = catalog.datasets()[0]
    assert problems(found, DatasetRecord) == []
    assert [sorted(set(d) & {"broken", "unverified", "byte_size"})
            for d in found["distributions"]] == [["broken"], ["byte_size", "unverified"]]


def test_publishers_and_publisher_are_their_typeddicts(cat):
    rows = cat.publishers()
    assert rows
    for row in rows:
        assert problems(row, Publisher) == []
        assert problems(cat.publisher(row["id"]), PublisherDetail) == []


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
        default=Dataset,
    )
    record = _present(entry.to_dict())
    assert problems(record, DatasetRecord) == []
    assert record["distributions"], "the fixture has files, so they were checked"


@pytest.mark.parametrize("name", ["Results", "DatasetRecord", "DataServiceRecord",
                                  "DistributionRecord", "PublisherRecord",
                                  "Publisher", "PublisherDetail", "LicenseRecord",
                                  "ContactRecord", "TemporalRecord", "LinkMark",
                                  "LanguageMap", "KeywordMap"])
def test_the_shapes_are_exported(name):
    import dataportalen

    assert name in dataportalen.__all__
    assert getattr(dataportalen, name)


def test_results_is_generic_and_still_a_list(cat):
    from dataportalen import Results

    page = cat.datasets()
    assert isinstance(page, list)
    assert Results[DatasetRecord]                      # subscriptable for a checker
    hints = get_type_hints(Catalog.datasets)
    assert get_args(hints["return"]) == (DatasetRecord,)
    assert get_args(get_type_hints(Catalog.data_services)["return"]) == (
        DataServiceRecord,)
