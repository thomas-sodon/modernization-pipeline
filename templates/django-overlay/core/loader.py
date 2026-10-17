"""Load a dataset directory into the legacy models.

The dataset directory format is docs/contracts.md#dataset-directory.

One loader for `manage.py import_dataset`, DjangoAdapter.load_dataset and the
load_seed fixture:

1. dataset.json and tables/*.csv are read with the vendored, standard-library reader
   (equivalence/dataset.py), so values arrive typed (int, Decimal, float, datetime,
   bool, str, None) and NULL stays NULL.
2. The target is checked first: a dataset whose kind is engagement is refused on a
   local database (host 127.0.0.1 or localhost) unless its name starts with mpdata_
   and its owner is postgres, sa or mp_runner, the rule stage1 applies. mp_agent may
   create databases, so it could own an mpdata_ one. The refusal names the database
   and its owner, never a value.
3. Tables load in dataset.json load_order. A table goes through
   legacy/import_map.py IMPORT_MAP when it is listed there (xlsm engagements), else
   to the model whose db_table equals the table name; tables with neither are
   skipped and counted. Each table goes through its legacy/resources.py RESOURCES
   resource (django-import-export Resource.import_data, dry_run=False,
   use_transactions=True): widget conversion, foreign-key resolution, full_clean()
   validation and one history row per row, change reason "import <dataset name>".
   Rows are stored exactly as the dataset carries them, NULL included: a NULL cell
   is not replaced by the model field's default, and the save goes through
   django.db.models.Model.save, so a model's own save() (for example audit stamps
   that fill AddedBy/AddedOn) does not run, as with Django's loaddata. Signals
   still fire, so the history rows are written.
4. Primary-key values are kept. Rows of a table that references itself load parents
   first. Afterwards the sequences are reset: PostgreSQL runs
   connection.ops.sequence_reset_sql; on SQL Server, mssql-django wraps every insert
   that carries the key in SET IDENTITY_INSERT ON/OFF, and DBCC CHECKIDENT RESEED
   runs afterwards.
5. With flush, the legacy rows are deleted first, in reverse load order.

Everything runs in one transaction. A row error names the table, the row number in
its CSV file and the column (with Django's error code), never a value.
core/local/load_hooks.py may define load_context() (a context manager the load runs
in). It must not change the values the rows carry.
"""

from __future__ import annotations

import importlib
from contextlib import ExitStack
from contextlib import nullcontext
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from urllib.parse import urlsplit

import tablib
from django.apps import apps
from django.core.exceptions import NON_FIELD_ERRORS
from django.core.exceptions import ObjectDoesNotExist
from django.core.management.color import no_style
from django.db import DEFAULT_DB_ALIAS
from django.db import connections
from django.db import models
from django.db import transaction
from django.db.models.fields import NOT_PROVIDED
from equivalence import dataset as eqds
from import_export.widgets import CharWidget
from import_export.widgets import ForeignKeyWidget

from ..legacy import resources as legacy_resources  # noqa: TID252

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Iterator

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "")
DATA_PREFIX = "mpdata_"
DATA_OWNERS = ("postgres", "sa", "mp_runner")
MAX_REPORTED = 20

_PACKAGE = __package__ or ""
_SLUG_PACKAGE = _PACKAGE.rpartition(".")[0]


class LoadError(Exception):
    """A dataset that cannot be loaded. exit_code 2 for a refused target or an
    unreadable dataset.json, 1 for rows that fail. str() never holds a value."""

    def __init__(self, message: str, exit_code: int = 1, problems=()):
        super().__init__(message)
        self.exit_code = exit_code
        self.problems = list(problems)


class TargetRefusedError(LoadError):
    """Engagement rows refused on this database."""

    def __init__(self, message: str):
        super().__init__(message, exit_code=2)


@dataclass
class LoadResult:
    dataset: str
    kind: str
    counts: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    flushed: dict[str, int] = field(default_factory=dict)


# ------------------------------------------------------------------ target check


@dataclass(frozen=True)
class TargetFacts:
    """What the target check needs to know about a database (no values)."""

    vendor: str
    host: str
    name: str
    owner: str | None


def database_owner(connection) -> str | None:
    """The login that owns the connected database, or None when unknown."""
    if connection.vendor == "postgresql":
        sql = (
            "SELECT pg_catalog.pg_get_userbyid(datdba) FROM pg_catalog.pg_database "
            "WHERE datname = current_database()"
        )
    elif connection.vendor == "microsoft":
        sql = "SELECT SUSER_SNAME(owner_sid) FROM sys.databases WHERE name = DB_NAME()"
    else:
        return None
    with connection.cursor() as cursor:
        cursor.execute(sql)
        row = cursor.fetchone()
    return str(row[0]) if row and row[0] is not None else None


def database_name(connection) -> str:
    if connection.vendor == "postgresql":
        sql = "SELECT current_database()"
    elif connection.vendor == "microsoft":
        sql = "SELECT DB_NAME()"
    else:
        return str(connection.settings_dict.get("NAME") or "")
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return str(cursor.fetchone()[0])


def connection_facts(connection) -> TargetFacts:
    host = str(connection.settings_dict.get("HOST") or "")
    if "://" in host:
        host = urlsplit(host).hostname or ""
    return TargetFacts(
        vendor=connection.vendor,
        host=host.strip().strip("[]").lower(),
        name=database_name(connection),
        owner=database_owner(connection),
    )


def target_refusal(kind: str | None, facts: TargetFacts) -> str | None:
    """Why rows of this dataset kind may not enter this database, or None."""
    if kind != "engagement":
        return None
    if facts.host not in LOCAL_HOSTS:
        return None
    rule = (
        "engagement rows load into a local database only when its name starts "
        f"with {DATA_PREFIX} and its owner is {', '.join(DATA_OWNERS)}"
    )
    if not facts.name.startswith(DATA_PREFIX):
        return f"refused: database {facts.name} is local and {rule}"
    if facts.owner not in DATA_OWNERS:
        owner = facts.owner or "unknown"
        return f"refused: database {facts.name} is owned by {owner}; {rule}"
    return None


def check_target(kind: str | None, using: str = DEFAULT_DB_ALIAS) -> TargetFacts:
    facts = connection_facts(connections[using])
    reason = target_refusal(kind, facts)
    if reason:
        raise TargetRefusedError(reason)
    return facts


# ------------------------------------------------------------------ mapping


def _optional_module(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name and name.startswith(exc.name):
            return None
        raise


def import_map() -> dict[str, Any]:
    module = _optional_module(f"{_SLUG_PACKAGE}.legacy.import_map")
    return dict(getattr(module, "IMPORT_MAP", {}) or {}) if module else {}


def load_context():
    """core/local/load_hooks.py load_context(), else a no-op context."""
    module = _optional_module(f"{_PACKAGE}.local.load_hooks")
    hook = getattr(module, "load_context", None) if module else None
    return hook() if hook else nullcontext()


def resources() -> dict[str, Any]:
    return dict(legacy_resources.RESOURCES)


def model_for(db_table: str):
    resource = resources().get(db_table)
    return resource._meta.model if resource is not None else None  # noqa: SLF001


def legacy_models() -> list:
    return [r._meta.model for r in resources().values()]  # noqa: SLF001


# ------------------------------------------------------------------ resources


class KeyForeignKeyWidget(ForeignKeyWidget):
    """ForeignKeyWidget that resolves a key of 0 (Access code lists start at 0, for
    example Tax Exempt); the stock widget reads every falsy value as NULL."""

    def clean(self, value, row=None, **kwargs):
        if value is None or value == "":
            return None
        if self.use_natural_foreign_keys:
            return super().clean(value, row, **kwargs)
        obj = self.get_instance_by_lookup_fields(value, row, **kwargs)
        return obj.pk if self.key_is_id else obj


class _LoadingMixin:
    """What a dataset load adds to a legacy resource: NULL kept as NULL (no model
    default fills an empty cell, and nullable text columns keep NULL apart from
    ""), full_clean() validation, and the change reason of the history rows."""

    def __init__(self, *, change_reason: str, **kwargs):
        super().__init__(**kwargs)
        self.change_reason = change_reason
        opts = self._meta.model._meta  # noqa: SLF001
        for name, fld in self.fields.items():
            # ModelResource copies the model field's default (0, "now", a code) into
            # the import field, which then replaces a NULL cell. A load keeps the
            # NULL; a column the dataset leaves out still gets the model default.
            fld.default = NOT_PROVIDED
            if type(fld.widget) is ForeignKeyWidget:
                fld.widget.__class__ = KeyForeignKeyWidget
                continue
            if not isinstance(fld.widget, CharWidget):
                continue
            try:
                model_field = opts.get_field(fld.attribute or name)
            except LookupError:
                continue
            if getattr(model_field, "null", False):
                fld.widget.allow_blank = False

    def before_save_instance(self, instance, row, **kwargs):
        super().before_save_instance(instance, row, **kwargs)
        instance._change_reason = self.change_reason  # noqa: SLF001

    def do_instance_save(self, instance, is_create):
        """Store the row as the dataset carries it: the base Model.save, not the
        model's own save() (which may stamp or derive columns)."""
        models.Model.save(instance)


_LOADING_CLASSES: dict[type, type] = {}


def loading_resource(resource_class: type) -> type:
    cls = _LOADING_CLASSES.get(resource_class)
    if cls is None:
        meta = type(
            "Meta",
            (),
            {"clean_model_instances": True, "skip_diff": True, "skip_html_diff": True},
        )
        cls = type(
            f"Loading{resource_class.__name__}",
            (_LoadingMixin, resource_class),
            {"Meta": meta, "__module__": __name__},
        )
        _LOADING_CLASSES[resource_class] = cls
    return cls


# ------------------------------------------------------------------ rows


def _self_references(model) -> list:
    return [
        f
        for f in model._meta.concrete_fields  # noqa: SLF001
        if f.is_relation and f.related_model is model
    ]


def parents_first(
    model,
    rows: list[tuple[int, dict[str, Any]]],
) -> list[tuple[int, dict[str, Any]]]:
    """Rows of a table that references itself, each after the row it references
    (rows in a cycle keep their order at the end)."""
    refs = _self_references(model)
    if not refs or not rows:
        return rows
    pk_column = model._meta.pk.column  # noqa: SLF001
    columns = [(f.column, f.target_field.column) for f in refs]
    present = {row.get(pk_column) for _n, row in rows}
    placed: set[Any] = set()
    out: list[tuple[int, dict[str, Any]]] = []
    waiting = list(rows)
    while waiting:
        rest = []
        for number, row in waiting:
            needs = {
                row.get(col)
                for col, target in columns
                if target == pk_column and row.get(col) is not None
            }
            needs = {k for k in needs if k in present and k != row.get(pk_column)}
            if needs <= placed:
                out.append((number, row))
                placed.add(row.get(pk_column))
            else:
                rest.append((number, row))
        if len(rest) == len(waiting):
            out.extend(rest)
            break
        waiting = rest
    return out


def _column_of(model, name: str) -> str:
    if name == NON_FIELD_ERRORS:
        return "(row)"
    try:
        return model._meta.get_field(name).column  # noqa: SLF001
    except LookupError:
        return name


def _codes(errors) -> str:
    codes = sorted({getattr(e, "code", None) or "invalid" for e in errors})
    return ",".join(codes)


def _row_problems(table: str, model, result, numbers: list[int]) -> list[str]:
    """Row errors as 'table: row n: column c (code)', never a value."""
    problems: list[str] = []

    def original(number: int | None) -> str:
        if number is not None and 1 <= number <= len(numbers):
            return str(numbers[number - 1])
        return "?"

    for invalid in result.invalid_rows:
        err = invalid.error
        items = (
            err.error_dict.items()
            if hasattr(err, "error_dict")
            else [(NON_FIELD_ERRORS, err.error_list)]
        )
        for name, errors in items:
            problems.append(
                f"{table}: row {original(invalid.number)}: column "
                f"{_column_of(model, name)} ({_codes(errors)})",
            )
    for number, errors in result.row_errors():
        for error in errors:
            exc = error.error
            column = "(row)"
            if isinstance(exc, ObjectDoesNotExist):
                related = getattr(exc, "model", None) or _model_of_exception(exc)
                fks = [
                    f.column
                    for f in model._meta.concrete_fields  # noqa: SLF001
                    if f.is_relation and f.related_model is related
                ]
                column = "/".join(fks) or column
            problems.append(
                f"{table}: row {original(number)}: column {column} "
                f"({type(exc).__name__})",
            )
    return problems


def _model_of_exception(exc: Exception):
    for model in apps.get_models():
        if type(exc) is getattr(model, "DoesNotExist", None):
            return model
    return None


# ------------------------------------------------------------------ load


def _flush(models_in_load_order: list, using: str) -> dict[str, int]:
    """Delete every legacy row, children first (reverse load order)."""
    rest = [m for m in legacy_models() if m not in models_in_load_order]
    order = list(reversed(models_in_load_order)) + _children_first(rest)
    deleted: dict[str, int] = {}
    for model in order:
        manager = model._base_manager.using(using)  # noqa: SLF001
        for fk in _self_references(model):
            if fk.null:
                manager.exclude(**{f"{fk.attname}__isnull": True}).update(
                    **{fk.attname: None},
                )
        count = manager.count()
        manager.all().delete()
        deleted[model._meta.db_table] = count  # noqa: SLF001
    return deleted


def _children_first(models: list) -> list:
    """Models ordered so that a model comes before the models it references."""
    pending = list(models)
    out: list = []
    while pending:
        progress = False
        for model in list(pending):
            referenced_by = [
                m
                for m in pending
                if m is not model
                and any(
                    f.is_relation and f.related_model is model
                    for f in m._meta.concrete_fields  # noqa: SLF001
                )
            ]
            if not referenced_by:
                out.append(model)
                pending.remove(model)
                progress = True
        if not progress:
            out.extend(pending)
            break
    return out


def _reset_sequences(models: list, using: str) -> None:
    connection = connections[using]
    if connection.vendor == "microsoft":
        statements = [
            f"DBCC CHECKIDENT ('{connection.ops.quote_name(m._meta.db_table)}', RESEED)"  # noqa: SLF001
            for m in models
            if m._meta.auto_field is not None  # noqa: SLF001
        ]
    else:
        statements = connection.ops.sequence_reset_sql(no_style(), models)
    if not statements:
        return
    with connection.cursor() as cursor:
        for sql in statements:
            cursor.execute(sql)


def _plan(tables: dict[str, Iterator]) -> tuple[list, list[str]]:
    """[(dataset table, "map" or "table", target)] in load order, and the tables
    that have no model."""
    mapped = import_map()
    plan: list[tuple[str, str, Any]] = []
    skipped: list[str] = []
    for name in tables:
        if name in mapped:
            plan.append((name, "map", mapped[name]))
        elif name in resources():
            plan.append((name, "table", name))
        else:
            skipped.append(name)
    return plan, skipped


def _import_rows(
    db_table: str,
    columns: list[str],
    rows: list[tuple[int, dict[str, Any]]],
    reason: str,
) -> tuple[int, list[str]]:
    resource_class = resources()[db_table]
    model = resource_class._meta.model  # noqa: SLF001
    rows = parents_first(model, rows)
    data = tablib.Dataset(headers=columns)
    for _number, row in rows:
        data.append([row.get(c) for c in columns])
    resource = loading_resource(resource_class)(change_reason=reason)
    result = resource.import_data(
        data,
        dry_run=False,
        raise_errors=False,
        use_transactions=True,
    )
    numbers = [n for n, _row in rows]
    problems = _row_problems(db_table, model, result, numbers)
    if result.base_errors:
        problems.extend(
            f"{db_table}: {type(e.error).__name__}" for e in result.base_errors
        )
    return len(rows), problems


def _mapped_rows(
    table: str,
    function,
    rows: Iterable[dict[str, Any]],
) -> dict[str, tuple[list[str], list[tuple[int, dict[str, Any]]]]]:
    """Rows that IMPORT_MAP turns into (db_table, field dict) pairs, grouped by
    db_table in the order the tables first appear."""
    out: dict[str, tuple[list[str], list[tuple[int, dict[str, Any]]]]] = {}
    for number, row in enumerate(rows, start=1):
        for db_table, values in function(row):
            if db_table not in resources():
                msg = f"{table}: row {number}: IMPORT_MAP names unknown table"
                raise LoadError(msg)
            columns, bucket = out.setdefault(db_table, ([], []))
            for column in values:
                if column not in columns:
                    columns.append(column)
            bucket.append((number, dict(values)))
    return out


def load_dataset(
    directory: str | Path,
    *,
    flush: bool = False,
    using: str = DEFAULT_DB_ALIAS,
) -> LoadResult:
    """Load one dataset directory into the legacy tables of `using`."""
    directory = Path(directory)
    try:
        meta = eqds.read_meta(directory)
    except eqds.DatasetError as exc:
        msg = f"{directory}: {exc}"
        raise LoadError(msg, exit_code=2) from None
    kind = str(meta.get("kind"))
    name = str(meta.get("name"))
    check_target(kind, using)
    meta, tables = eqds.read_dataset(directory)
    plan, skipped = _plan(tables)
    by_name = {t["name"]: t for t in meta.get("tables") or []}
    reason = f"import {name}"
    result = LoadResult(dataset=name, kind=kind, skipped=skipped)
    loaded_models: list = []
    with ExitStack() as stack:
        stack.enter_context(transaction.atomic(using=using))
        stack.enter_context(load_context())
        if flush:
            result.flushed = _flush(_load_order_models(plan), using)
        for table, how, target in plan:
            try:
                rows = list(tables[table])
            except eqds.DatasetError as exc:
                raise LoadError(str(exc)) from None
            if how == "table":
                columns = [c["name"] for c in by_name[table]["columns"]]
                groups = {target: (columns, list(enumerate(rows, start=1)))}
            else:
                groups = _mapped_rows(table, target, rows)
            problems: list[str] = []
            for db_table, (columns, numbered) in groups.items():
                count, errors = _import_rows(db_table, columns, numbered, reason)
                problems.extend(errors)
                result.counts[db_table] = result.counts.get(db_table, 0) + count
                model = model_for(db_table)
                if model not in loaded_models:
                    loaded_models.append(model)
            if problems:
                msg = f"{table}: {len(problems)} row errors; nothing was loaded"
                raise LoadError(msg, problems=problems[:MAX_REPORTED])
        _reset_sequences(loaded_models, using)
    return result


def _load_order_models(plan: list) -> list:
    """The legacy models of the plan's tables, in load order."""
    out: list = []
    for _table, how, target in plan:
        model = model_for(target) if how == "table" else None
        if model is not None and model not in out:
            out.append(model)
    return out


def summary_lines(result: LoadResult) -> list[str]:
    """Per-table counts, for stdout (no values)."""
    lines = [f"{table}: {count}" for table, count in result.counts.items()]
    lines.extend(f"skipped (no model): {table}" for table in result.skipped)
    return lines
