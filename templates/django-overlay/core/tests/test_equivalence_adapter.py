"""DjangoAdapter on seed-1: load_dataset flushes and loads, run_saved_query and
render_document dispatch to the registries, unregistered units are
not-implemented, and adapter.toml names this adapter."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from django.conf import settings
from equivalence.adapter import NotImplementedUnit
from equivalence.adapter import load_entrypoint
from equivalence.adapter import read_config

from .. import loader  # noqa: TID252
from .. import saved_queries as sq  # noqa: TID252
from ..equivalence import DjangoAdapter  # noqa: TID252
from ..equivalence import pytest_db_blocker  # noqa: TID252
from ..testing import seed_directory  # noqa: TID252

ADAPTER_TOML = Path(settings.BASE_DIR).parent / "equivalence" / "adapter.toml"
UNIT = "zz_core_adapter_unit"


@pytest.fixture
def temporary_units():
    before = dict(sq.SAVED_QUERIES), dict(sq.DOCUMENTS)
    yield
    sq.SAVED_QUERIES.clear()
    sq.SAVED_QUERIES.update(before[0])
    sq.DOCUMENTS.clear()
    sq.DOCUMENTS.update(before[1])


def test_adapter_toml_names_this_adapter():
    config = read_config(ADAPTER_TOML)
    assert config["adapter"] == "django"
    assert load_entrypoint(config["entrypoint"]) is DjangoAdapter
    with ADAPTER_TOML.open("rb") as fh:
        raw = tomllib.load(fh)
    assert raw["settings"] == "config.settings.test"
    assert raw["databases"] == {"postgresql": "PG_URL", "mssql": "MSSQL_URL"}


def test_database_access_is_blocked_outside_db_tests():
    assert pytest_db_blocker() is not None


@pytest.mark.django_db
def test_adapter_loads_seed_1_and_reloads_with_flush():
    adapter = DjangoAdapter()
    first = adapter.load_dataset(seed_directory(1))
    second = adapter.load_dataset(seed_directory(1))
    assert first.counts == second.counts
    for table, rows in second.counts.items():
        assert loader.model_for(table)._base_manager.count() == rows  # noqa: SLF001
    assert second.flushed


@pytest.mark.django_db
def test_adapter_dispatches_to_the_registries(temporary_units):
    adapter = DjangoAdapter()
    adapter.load_dataset(seed_directory(1))
    table = next(iter(adapter.load_dataset(seed_directory(1)).counts))
    model = loader.model_for(table)

    @sq.saved_query(UNIT)
    def count_rows(params):
        return [(model._base_manager.count(), params.get("P"))]  # noqa: SLF001

    @sq.document(UNIT)
    def one_document(key, params):
        return {"key": key, "rows": str(model._base_manager.count())}  # noqa: SLF001

    rows = adapter.run_saved_query(UNIT, {"P": "1"})
    assert rows == [(model._base_manager.count(), "1")]  # noqa: SLF001
    assert adapter.render_document(UNIT, "7", {})["key"] == "7"


def test_unregistered_units_raise_not_implemented():
    adapter = DjangoAdapter()
    with pytest.raises(NotImplementedUnit, match="not implemented: zz_nobody"):
        adapter.run_saved_query("zz_nobody", {})
    with pytest.raises(NotImplementedError):
        adapter.render_document("zz_nobody", "1", {})


def test_close_without_a_test_database_is_a_no_op():
    DjangoAdapter().close()
