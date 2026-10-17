"""import_dataset and its loader: seed-1 loads with the dataset.json counts, a second
run with --flush gives the same counts, history rows carry the import reason, the
target check refuses engagement rows outside mpdata_ databases, and row errors never
show a value."""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from equivalence import dataset as eqds

from .. import loader  # noqa: TID252
from ..testing import seed_directory  # noqa: TID252

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REFUSED = 2  # import_dataset's exit code for a refused target
SEEDS = sorted(p.parent.name for p in seed_directory("*").parent.parent.glob("*/input"))


def seed_meta(n: int = 1) -> dict:
    return json.loads((seed_directory(n) / "dataset.json").read_text("utf-8"))


def expected_counts(n: int = 1) -> dict[str, int]:
    tables = loader.resources()
    return {t["name"]: t["rows"] for t in seed_meta(n)["tables"] if t["name"] in tables}


def database_counts(tables) -> dict[str, int]:
    return {t: loader.model_for(t)._base_manager.count() for t in tables}  # noqa: SLF001


def run_import(*args) -> str:
    out = io.StringIO()
    call_command("import_dataset", *args, stdout=out, stderr=io.StringIO())
    return out.getvalue()


# ------------------------------------------------------------------ seed-1


@pytest.mark.django_db
def test_import_seed_1_counts_equal_dataset_json():
    expected = expected_counts()
    assert expected, "seed-1 has no table with a legacy model"
    out = run_import(str(seed_directory(1)))
    assert database_counts(expected) == expected
    for table, rows in expected.items():
        assert f"{table}: {rows}" in out.splitlines()


@pytest.mark.django_db
def test_import_twice_with_flush_gives_the_same_counts():
    expected = expected_counts()
    run_import(str(seed_directory(1)))
    out = run_import(str(seed_directory(1)), "--flush")
    assert database_counts(expected) == expected
    assert any(line.startswith("flushed ") for line in out.splitlines())


@pytest.mark.django_db
def test_import_without_flush_updates_rows_in_place():
    """The key column is the import id: a second load updates the same rows."""
    expected = expected_counts()
    run_import(str(seed_directory(1)))
    run_import(str(seed_directory(1)))
    assert database_counts(expected) == expected


@pytest.mark.django_db
@pytest.mark.parametrize("seed", SEEDS)
def test_every_seed_loads_with_its_counts(seed):
    n = seed.removeprefix("seed-")
    expected = expected_counts(n)
    loader.load_dataset(seed_directory(n))
    assert database_counts(expected) == expected


@pytest.mark.django_db
def test_history_rows_carry_the_import_reason():
    result = loader.load_dataset(seed_directory(1))
    reason = f"import {result.dataset}"
    for table, rows in result.counts.items():
        model = loader.model_for(table)
        history = getattr(model, "history", None)
        if history is None:
            continue
        assert history.filter(history_change_reason=reason).count() == rows, table


@pytest.mark.django_db
def test_tables_without_a_model_are_skipped_and_named():
    meta = seed_meta()
    result = loader.load_dataset(seed_directory(1))
    unmapped = [t["name"] for t in meta["tables"] if t["name"] not in result.counts]
    assert sorted(result.skipped) == sorted(unmapped)


@pytest.mark.django_db
def test_stdout_holds_counts_only():
    out = run_import(str(seed_directory(1)))
    for line in out.splitlines():
        assert (
            line.startswith(("skipped (no model): ", "loaded "))
            or line.rsplit(": ", 1)[-1].isdigit()
        ), line


def cells(directory: Path, types: tuple[str, ...] | None = None):
    """(field, key, where, dataset value) of every cell of every table with a model,
    audit stamps and other columns a model's own save() would fill included. `types`
    limits the cells to those Django field types."""
    _meta, tables = eqds.read_dataset(directory)
    for table, rows in tables.items():
        model = loader.model_for(table)
        if model is None:
            continue
        fields = {
            f.column: f
            for f in model._meta.concrete_fields  # noqa: SLF001
            if types is None or f.get_internal_type() in types
        }
        key = model._meta.pk.column  # noqa: SLF001
        for row in rows:
            for column, fld in fields.items():
                if column in row:
                    yield fld, row[key], f"{table}.{column}", row[column]


def stored_value(fld, key):
    return (
        fld.model._base_manager.filter(pk=key)  # noqa: SLF001
        .values_list(fld.attname, flat=True)
        .get()
    )


@pytest.mark.django_db
def test_text_keeps_null_and_empty_strings_apart():
    """Every text cell of every seed loads as it is: NULL stays NULL and an empty
    string stays empty (the stock CharWidget turns NULL into "")."""
    nulls = 0
    for seed in SEEDS:
        directory = seed_directory(seed.removeprefix("seed-"))
        loader.load_dataset(directory, flush=True)
        for fld, key, where, value in cells(directory, ("CharField", "TextField")):
            assert stored_value(fld, key) == value, f"{seed} {where}"
            nulls += value is None
    assert nulls > 0, "no seed has a NULL text to check"


@pytest.mark.django_db
def test_every_cell_loads_exactly_as_the_dataset_carries_it():
    """Every cell of every seed, the audit stamps (AddedBy, AddedOn, ModifiedBy,
    ModifiedOn) included, is stored as the dataset carries it: a NULL stamp stays
    NULL and no load time is written, so equivalence compares the dataset's own
    values. A model's own save() (the stamps) does not run during a load."""
    system_kept_nulls = 0
    for seed in SEEDS:
        directory = seed_directory(seed.removeprefix("seed-"))
        loader.load_dataset(directory, flush=True)
        for fld, key, where, value in cells(directory):
            assert stored_value(fld, key) == value, f"{seed} {where}"
            system_kept_nulls += value is None and not fld.editable
    assert system_kept_nulls > 0, "no seed has a NULL in a system-kept column"


@pytest.mark.django_db
def test_a_second_load_stores_the_same_rows():
    """Two loads of the same seed (with flush) give identical rows: a load writes
    no clock value."""
    tables = expected_counts(2)

    def snapshot():
        return {
            table: sorted(
                repr(row)
                for row in loader.model_for(table)._base_manager.values_list()  # noqa: SLF001
            )
            for table in tables
        }

    loader.load_dataset(seed_directory(2), flush=True)
    first = snapshot()
    loader.load_dataset(seed_directory(2), flush=True)
    assert snapshot() == first


# ------------------------------------------------------------------ row errors


def _broken_copy(tmp_path: Path) -> tuple[Path, str, str, str]:
    """A copy of seed-1 whose first child row points at a parent key that does not
    exist (an invented key). Returns (dir, table, column, invented key)."""
    source = seed_directory(1)
    target = tmp_path / "broken"
    shutil.copytree(source, target)
    meta, tables = eqds.read_dataset(target)
    invented = "987654321"
    for name in meta["load_order"]:
        model = loader.model_for(name)
        fks = [
            f
            for f in (model._meta.concrete_fields if model else [])  # noqa: SLF001
            if f.is_relation and f.related_model is not model
        ]
        if not fks:
            continue
        column = fks[0].column
        entry = next(t for t in meta["tables"] if t["name"] == name)
        rows = list(tables[name])
        rows[0][column] = int(invented)
        eqds.write_dataset(
            target,
            meta,
            [
                {
                    **t,
                    "rows": rows
                    if t["name"] == name
                    else list(eqds.iter_table(target, t)),
                }
                for t in meta["tables"]
            ],
        )
        del entry
        return target, name, column, invented
    pytest.fail("seed-1 has no table with a foreign key")
    raise AssertionError


@pytest.mark.django_db
def test_row_errors_name_table_row_and_column_but_no_value(tmp_path):
    directory, table, column, invented = _broken_copy(tmp_path)
    err = io.StringIO()
    with pytest.raises(CommandError) as info:
        call_command(
            "import_dataset",
            str(directory),
            stdout=io.StringIO(),
            stderr=err,
        )
    assert info.value.returncode == 1
    text = err.getvalue() + str(info.value)
    assert f"{table}: row " in text
    assert column in text
    assert invented not in text
    assert database_counts([table]) == {table: 0}


# ------------------------------------------------------------------ target check


@pytest.mark.django_db
def test_engagement_dataset_is_refused_on_a_local_test_database():
    facts = loader.connection_facts(connection)
    assert facts.host in loader.LOCAL_HOSTS, "the test database is expected locally"
    assert not facts.name.startswith(loader.DATA_PREFIX)
    with pytest.raises(CommandError) as info:
        run_import(str(FIXTURES / "engagement_kind"))
    assert info.value.returncode == REFUSED
    message = str(info.value)
    assert facts.name in message
    assert "Invented" not in message


def facts(**kw) -> loader.TargetFacts:
    base = {
        "vendor": "postgresql",
        "host": "127.0.0.1",
        "name": "mpdata_test_example_slot",
        "owner": "mp_runner",
    }
    base.update(kw)
    return loader.TargetFacts(**base)


@pytest.mark.parametrize(
    ("kind", "target", "refused"),
    [
        ("synthetic", facts(name="example_dev", owner="mp_agent"), False),
        ("engagement", facts(), False),
        ("engagement", facts(owner="postgres"), False),
        ("engagement", facts(vendor="microsoft", owner="sa"), False),
        ("engagement", facts(name="example_dev"), True),
        ("engagement", facts(name="test_example_slot", owner="postgres"), True),
        ("engagement", facts(owner="mp_agent"), True),
        ("engagement", facts(owner=None), True),
        ("engagement", facts(host="localhost", owner="mp_agent"), True),
        ("engagement", facts(host="", name="example"), True),
        ("engagement", facts(host="db.client.example", name="staging"), False),
    ],
)
def test_target_rule(kind, target, refused):
    reason = loader.target_refusal(kind, target)
    assert (reason is not None) is refused
    if reason:
        assert target.name in reason


# ------------------------------------------------------------------ row order


class _Fk:
    def __init__(self, model):
        self.is_relation = True
        self.related_model = model
        self.column = "ParentID"
        self.target_field = type("T", (), {"column": "ID"})()


def test_parents_first_orders_self_references():
    model = type("M", (), {})
    pk = type("P", (), {"column": "ID"})()
    model._meta = type("O", (), {"concrete_fields": [_Fk(model)], "pk": pk})()  # noqa: SLF001
    rows = [
        (1, {"ID": 3, "ParentID": 2}),
        (2, {"ID": 2, "ParentID": 1}),
        (3, {"ID": 1, "ParentID": None}),
        (4, {"ID": 4, "ParentID": 4}),
    ]
    ordered = [n for n, _row in loader.parents_first(model, rows)]
    assert ordered.index(3) < ordered.index(2) < ordered.index(1)
    assert sorted(ordered) == [1, 2, 3, 4]
