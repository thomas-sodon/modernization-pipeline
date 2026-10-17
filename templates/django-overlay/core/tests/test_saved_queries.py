"""The @saved_query and @document registries and the not-implemented error."""

from __future__ import annotations

import importlib.util
import sys

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from equivalence.adapter import NotImplementedUnit

from .. import saved_queries as sq  # noqa: TID252

UNIT = "zz_core_test_unit"
DOC = "zz_core_test_document"


@pytest.fixture
def clean_registries():
    before = dict(sq.SAVED_QUERIES), dict(sq.DOCUMENTS)
    yield
    sq.SAVED_QUERIES.clear()
    sq.SAVED_QUERIES.update(before[0])
    sq.DOCUMENTS.clear()
    sq.DOCUMENTS.update(before[1])


def test_saved_query_registers_and_runs(clean_registries):
    @sq.saved_query(UNIT)
    def rows(params):
        return [[1, params.get("Name")], (2, None)]

    assert sq.SAVED_QUERIES[UNIT] is rows
    assert sq.run_saved_query(UNIT, {"Name": "x"}) == [(1, "x"), (2, None)]
    assert sq.registered_units()[UNIT] == "saved_query"


def test_document_registers_and_renders(clean_registries):
    @sq.document(DOC)
    def render(key, params):
        return {"invoice_number": key, "params": len(params)}

    assert sq.render_document(DOC, 15001, {}) == {
        "invoice_number": "15001",
        "params": 0,
    }
    assert sq.registered_units()[DOC] == "document"


def test_unregistered_units_are_not_implemented():
    with pytest.raises(NotImplementedUnit) as info:
        sq.run_saved_query("zz_nobody_registered_this", {})
    assert str(info.value) == "not implemented: zz_nobody_registered_this"
    assert isinstance(info.value, NotImplementedError)
    with pytest.raises(NotImplementedUnit):
        sq.render_document("zz_nobody_registered_this", "1", {})


def test_a_unit_registered_twice_by_different_functions_is_refused(clean_registries):
    @sq.saved_query(UNIT)
    def first(params):
        return []

    with pytest.raises(ImproperlyConfigured):

        @sq.saved_query(UNIT)
        def second(params):
            return []


def test_the_same_function_may_register_again(clean_registries):
    def rows(params):
        return []

    sq.saved_query(UNIT)(rows)
    sq.saved_query(UNIT)(rows)
    assert sq.SAVED_QUERIES[UNIT] is rows


def test_a_unit_id_is_required():
    with pytest.raises(ImproperlyConfigured):
        sq.saved_query("  ")


def test_ready_imported_every_installed_saved_queries_module():
    """CoreConfig.ready() ran autodiscover_modules('saved_queries'): every installed
    app's saved_queries module is imported (installed apps only)."""
    for config in apps.get_app_configs():
        name = f"{config.name}.saved_queries"
        try:
            spec = importlib.util.find_spec(name)
        except ModuleNotFoundError:
            spec = None
        if spec is not None:
            assert name in sys.modules, name
