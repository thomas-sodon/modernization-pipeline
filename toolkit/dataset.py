"""Engagement data as a dataset directory (docs/contracts.md#dataset-directory).

The format itself (reader, writer, validator, `format_value`) lives in the stdlib-only
`harness/dataset.py`, which S2.harness vendors into the target; this module re-exports it and adds
the adapters over the toolkit.sources readers:

- accdt sampleData parts and vcs-export table data files (`tables()`, `iter_rows(table)`);
- xlsm ListObjects: business data only (tables with role documentation are left out), calculated
  columns omitted with reason computed;
- CSV dataset directories (a `source.data_copy` that is a dataset directory);
- `source.data_copy`, accdb and mdb files through the Jackcess bridge of S2.reference
  (`toolkit.sources.accdb`, which imports exactly `toolkit.reference.bridge`).

Rows and values are reachable only through `iter_rows`; this module never prints, logs or
returns a value outside the dataset files it writes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from harness.dataset import (  # noqa: F401 - re-exported
    FORMAT_VERSION,
    NULL,
    OMITTED_TYPES,
    TEXT_TYPES,
    TYPES,
    DatasetError,
    file_safe,
    format_value,
    iter_table,
    load_order,
    parse_value,
    read_dataset,
    read_meta,
    same_tree,
    self_references,
    validate,
    write_dataset,
)
from toolkit.sources import BridgeMissing, SourceError

BRIDGE_MESSAGE = "data_copy needs the Jackcess bridge from S2.reference"
MULTI_BACKEND = "more than one data back end: custom quote"
NO_DATA = "no data in the source"
SCHEMA_FILE = Path("out") / "model" / "schema.json"
OBJECTS_FILE = Path("out") / "model" / "objects.json"


class BridgeNeeded(SourceError):
    """An Access file (data copy, back end, accdb or mdb source) without the Jackcess bridge."""

    def __init__(self):
        super().__init__(BRIDGE_MESSAGE)


class MultiBackend(SourceError):
    def __init__(self):
        super().__init__(MULTI_BACKEND)


# ------------------------------------------------------------------ readers


class DatasetDirReader:
    """A CSV dataset directory as a source: tables() and iter_rows(table)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.meta = read_meta(self.path)
        self.by_name = {t["name"]: t for t in self.meta.get("tables") or []}

    def tables(self) -> list[str]:
        return sorted(self.by_name)

    def iter_rows(self, table: str):
        entry = self.by_name.get(table)
        if entry is None:
            return
        yield from iter_table(self.path, entry)


class _BridgeReader:
    """An Access back-end file through the Jackcess bridge (columns from the model schema)."""

    def __init__(self, path: Path, schema: dict | None):
        from toolkit.sources.accdb import AccdbReader

        self.schema = schema
        try:
            self.reader = AccdbReader(Path(path), Path(path).suffix.lower().lstrip("."))
        except BridgeMissing:
            raise BridgeNeeded() from None

    def tables(self) -> list[str]:
        return self.reader.tables()

    def iter_rows(self, table: str):
        yield from self.reader.iter_rows(table)


def open_backend(path, schema: dict | None = None):
    """A reader with tables() and iter_rows(table) for an Access back-end file at any path,
    through the S2 Jackcess bridge. A dataset directory is read as such. Raises BridgeNeeded
    (exit 2, 'data_copy needs the Jackcess bridge from S2.reference') until S2.reference builds
    `toolkit.reference.bridge`, and SourceError when the file is missing."""
    path = Path(path)
    if path.is_dir() and (path / "dataset.json").is_file():
        return DatasetDirReader(path)
    if not path.is_file():
        raise SourceError("back-end file missing")
    return _BridgeReader(path, schema)


def source_reader(eng: dict):
    """The reader that holds the engagement's own (not linked) table data."""
    src = eng["source"]
    if src.get("data_copy"):
        return open_backend(src["data_copy"])
    if src["kind"] in ("accdb", "mdb"):
        try:
            from toolkit.sources import open_reader

            return open_reader(eng)
        except BridgeMissing:
            raise BridgeNeeded() from None
    from toolkit.sources import open_reader

    return open_reader(eng)


def has_no_data(eng: dict) -> bool:
    src = eng["source"]
    return (
        src["kind"] in ("accdt", "vcs-export")
        and src.get("saved_with_data") is not True
        and not src.get("data_copy")
    )


# ------------------------------------------------------------------ back ends


def received_backends(eng: dict) -> list[Path]:
    from toolkit.commands.parse import received_backends as received

    return [p for p in received(eng) if p.exists()]


@dataclass
class Plan:
    """Which tables come from where."""

    local: list[str] = field(default_factory=list)  # tables of the source or data copy
    linked: list[str] = field(
        default_factory=list
    )  # tables of the one received back end
    backend: Path | None = None
    skipped: list[str] = field(default_factory=list)  # back end not received
    messages: list[str] = field(default_factory=list)


def plan_tables(eng: dict, schema: dict, objects: dict | None) -> Plan:
    """Split the schema's data tables into local, linked (one received back end) and skipped
    (back end not received). Raises MultiBackend when tables link to more than one back end."""
    plan = Plan()
    linked = (objects or {}).get("linked_tables") or []
    by_table = {lt["name"]: lt for lt in linked}
    backends = sorted(
        {
            (lt.get("backend_kind"), lt.get("backend"))
            for lt in linked
            if lt.get("backend") is not None
        },
        key=str,
    )
    if len(backends) > 1:
        raise MultiBackend()
    received = received_backends(eng)
    backend_file = None
    if backends:
        kind = backends[0][0]
        names = {
            lt.get("backend_file") for lt in linked if lt.get("backend") is not None
        }
        for p in received:
            if p.name.lower() in names or len(received) == 1:
                backend_file = p
                break
        if kind != "access" and backend_file is not None:
            backend_file = None
            plan.messages.append(f"{kind} back end is not read by dataset export")
    plan.backend = backend_file
    for t in schema.get("tables") or []:
        if t.get("role") == "documentation":
            continue
        name = t["name"]
        lt = by_table.get(name)
        if lt is None:
            plan.local.append(name)
        elif lt.get("backend") is not None and backend_file is not None:
            plan.linked.append(name)
        else:
            plan.skipped.append(name)
    return plan


# ------------------------------------------------------------------ export


def split_columns(st: dict) -> tuple[list[dict], list[dict]]:
    """(columns kept, omitted columns) of a schema table."""
    columns, omitted = [], []
    for c in st.get("columns") or []:
        if c["type"] in OMITTED_TYPES:
            omitted.append({"name": c["name"], "type": c["type"], "reason": c["type"]})
        elif c.get("computed"):
            omitted.append({"name": c["name"], "type": c["type"], "reason": "computed"})
        else:
            columns.append({"name": c["name"], "type": c["type"]})
    return columns, omitted


class RowError(Exception):
    """A source value that does not fit its column type. str() names the table, the column and
    the row number, never the value."""


def _rows(reader, table, columns, omitted, source_kind, stats):
    """Source rows as {column: value} for the dataset writer. A column missing from a row is
    NULL. accdt and vcs-export rows hold text: an empty field of a non-text column is NULL, and
    an empty element of a text column is the empty string. Fields that are not schema columns
    are counted in stats['unknown_columns'] (by name, per table)."""
    names = {c["name"] for c in columns} | {o["name"] for o in omitted}
    text_rows = source_kind in ("accdt", "vcs-export")
    unknown: set[str] = set()
    for number, row in enumerate(reader.iter_rows(table), start=1):
        out = {}
        for key in row:
            if key not in names:
                unknown.add(key)
        if row and names and not any(key in names for key in row):
            raise RowError(
                f"{table}: row {number}: no field matches a column of the table "
                "(the reader returned something other than rows)"
            )
        for c in columns:
            value = row.get(c["name"])
            if (
                text_rows
                and value is not None
                and c["type"] not in TEXT_TYPES
                and isinstance(value, str)
                and value.strip() == ""
            ):
                value = None
            if text_rows and value is None and c["type"] in TEXT_TYPES:
                # an element that is present but empty is the empty string
                value = "" if c["name"] in row else None
            try:
                format_value(c["type"], value)
            except ValueError as exc:
                raise RowError(
                    f"{table}: row {number} column {c['name']}: {exc}"
                ) from None
            out[c["name"]] = value
        yield out
    stats["unknown_columns"] = stats.get("unknown_columns", 0) + len(unknown)


def schema_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export(eng: dict, out_dir: Path) -> dict:
    """Write the engagement dataset into `out_dir` (which must not exist yet or be empty).

    Returns {"status": "ok"|"no-data", "doc": dataset.json, "messages": [...],
    "skipped": [...], "broken": [...], "self_references": [...], "unknown_columns": n}.
    Raises SourceError subclasses for exit-2 conditions and RowError for a value that does not
    fit its type."""
    private = Path(eng["paths"]["private"])
    schema_path = private / SCHEMA_FILE
    if not schema_path.is_file():
        raise SourceError("model schema.json missing: run parse first")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    objects = None
    if (private / OBJECTS_FILE).is_file():
        objects = json.loads((private / OBJECTS_FILE).read_text(encoding="utf-8"))
    meta = {
        "name": "engagement",
        "kind": "engagement",
        "seed": None,
        "schema_sha256": schema_sha256(schema_path),
    }
    result = {
        "status": "ok",
        "messages": [],
        "skipped": [],
        "broken": [],
        "self_references": [],
        "unknown_columns": 0,
        "schema": schema,
    }
    if has_no_data(eng):
        meta["load_order"] = []
        result["doc"] = write_dataset(out_dir, meta, [])
        result["messages"].append(NO_DATA)
        return result
    plan = plan_tables(eng, schema, objects)
    result["messages"].extend(plan.messages)
    source_kind = eng["source"]["kind"]
    reader = source_reader(eng) if plan.local else None
    backend = open_backend(plan.backend, schema) if plan.linked else None
    if source_kind == "xlsm" and not eng["source"].get("data_copy"):
        row_kind = "xlsm"
    elif eng["source"].get("data_copy") or source_kind in ("accdb", "mdb"):
        row_kind = "bridge"
    else:
        row_kind = source_kind
    schema_tables = {t["name"]: t for t in schema.get("tables") or []}
    names = plan.local + plan.linked
    order, broken = load_order(schema, names)
    meta["load_order"] = order
    stats: dict = {}
    tables = []
    for name in order:
        columns, omitted = split_columns(schema_tables[name])
        src = backend if name in plan.linked else reader
        kind = "bridge" if name in plan.linked else row_kind
        tables.append(
            {
                "name": name,
                "columns": columns,
                "omitted_columns": omitted,
                "rows": _rows(src, name, columns, omitted, kind, stats),
            }
        )
    result["doc"] = write_dataset(out_dir, meta, tables)
    result["broken"] = broken
    result["self_references"] = self_references(schema, names)
    result["unknown_columns"] = stats.get("unknown_columns", 0)
    if plan.skipped:
        result["status"] = "no-data"
        result["skipped"] = plan.skipped
        result["messages"].append(
            f"{len(plan.skipped)} tables skipped: their back end was not received"
        )
    for name in broken:
        result["messages"].append(f"load order: cycle broken at {name}")
    return result
