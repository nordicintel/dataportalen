"""Keep the examples in the docstrings honest."""

from __future__ import annotations

import doctest

import pytest

from dataportalen import core, query, rdf


@pytest.mark.parametrize("module", [query, rdf, core], ids=lambda m: m.__name__)
def test_module_doctests(module):
    results = doctest.testmod(module, verbose=False)
    assert results.failed == 0, "%d doctest failure(s) in %s" % (results.failed, module.__name__)
