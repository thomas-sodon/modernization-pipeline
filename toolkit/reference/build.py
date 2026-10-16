"""The `reference build` step: reference databases of an Access engagement.

- Seeds (`uv run toolkit reference build <eng> --dataset seeds|seed-N`): a new database
  (UCanAccess newDatabaseVersion=V2010) with Access DDL from model/schema.json and the rows of
  `<target>/goldens/synthetic/seed-N/input` (read with toolkit.dataset), written as
  `<private>/synthetic/reference/seed-N.accdb`. A seed never takes anything from engagement files.
- Engagement (`mp-data reference build <eng> --dataset engagement`): `<private>/reference/
  engagement.accdb`, a private copy of `source.data_copy` (or of the source file for kinds accdb
  and mdb with layout single-file), else built like a seed from `<private>/datasets/engagement`.

Each database is built in a temp file in its target directory and renamed over the target (a
rename from $TMPDIR would cross mounts inside mp-data's jail). Rows go in through Jackcess with
auto-number insert allowed, so keys keep their values. After the rename the catalog's saved
queries are created on a read-only open (toolkit.reference.engine), which proves that queries on
queries resolve. A database newer than its inputs and the catalog is skipped.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from toolkit.reference import bridge

DDL_TYPES = {
    "text": "TEXT({size})",
    "memo": "MEMO",
    "byte": "BYTE",
    "integer": "SMALLINT",
    "longinteger": "LONG",
    "autonumber": "COUNTER",
    "single": "SINGLE",
    "double": "DOUBLE",
    "currency": "CURRENCY",
    "decimal": "NUMERIC({precision},{scale})",
    "datetime": "DATETIME",
    "yesno": "YESNO",
    "hyperlink": "HYPERLINK",
    "guid": "GUID",
    "oleobject": "OLE",
}
BATCH = 500


class BuildError(Exception):
    """exit_code 1 (a check failed) or 2 (missing input)."""

    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class Result:
    built: int = 0
    skipped: int = 0
    tables: int = 0
    rows: int = 0
    queries: int = 0
    query_failures: dict = field(default_factory=dict)
    messages: list = field(default_factory=list)


# ------------------------------------------------------------------ DDL


def quote(name: str) -> str:
    if "]" in name:
        raise BuildError(f"identifier with ']' cannot be quoted: {name[:40]}")
    return f"[{name}]"


def column_ddl(column: dict) -> str:
    """Access DDL of one column. A complex column (attachments, multi-valued fields) cannot be
    created through DDL and holds no dataset values, so it becomes an all-NULL MEMO placeholder
    that queries naming it can still select."""
    ctype = column["type"]
    if ctype == "complex":
        return f"{quote(column['name'])} MEMO"
    template = DDL_TYPES.get(ctype)
    if template is None:
        raise BuildError(f"column type {ctype} has no Access DDL")
    size = column.get("size") or 255
    size = max(1, min(int(size), 255))
    precision = column.get("precision") or 18
    scale = column.get("scale") or 0
    return f"{quote(column['name'])} {template.format(size=size, precision=precision, scale=scale)}"


def table_ddl(table: dict) -> str:
    parts = [column_ddl(c) for c in table.get("columns") or []]
    keep = {c["name"] for c in table.get("columns") or [] if c["type"] != "complex"}
    pk = [c for c in table.get("primary_key") or [] if c in keep]
    if pk:
        parts.append("PRIMARY KEY (" + ", ".join(quote(c) for c in pk) + ")")
    return f"CREATE TABLE {quote(table['name'])} (" + ", ".join(parts) + ")"


def ddl_tables(schema: dict, meta: dict) -> list[dict]:
    """Tables to create: every data table of the schema, plus any dataset table it lacks."""
    tables = [
        t
        for t in schema.get("tables") or []
        if t.get("role", "data") != "documentation"
    ]
    names = {t["name"] for t in tables}
    for t in meta.get("tables") or []:
        if t["name"] not in names:
            tables.append(
                {"name": t["name"], "columns": t["columns"], "primary_key": []}
            )
    return tables


# ------------------------------------------------------------------ build steps


def create_tables(conn, tables: list[dict]) -> None:
    st = conn.createStatement()
    try:
        for t in tables:
            st.execute(table_ddl(t))
    finally:
        st.close()


def load_rows(path: Path, dataset_dir: Path) -> tuple[int, int]:
    """Insert every dataset table's rows through Jackcess. Returns (tables, rows)."""
    import jpype

    from toolkit.dataset import read_dataset

    meta, tables = read_dataset(dataset_dir)
    types = {
        t["name"]: {c["name"]: c["type"] for c in t["columns"]}
        for t in meta.get("tables") or []
    }
    db = bridge.open_database(path, read_only=False)
    n_tables = n_rows = 0
    try:
        db.setAllowAutoNumberInsert(True)
        ObjArray = jpype.JArray(jpype.JObject)
        ArrayList = bridge.jclass("java.util.ArrayList")
        for name, rows in tables.items():
            t = db.getTable(name)
            if t is None:
                raise BuildError(f"table {name} missing from the reference database", 1)
            cols = [str(c.getName()) for c in t.getColumns()]
            ctypes = types.get(name, {})
            unknown = set(ctypes) - set(cols)
            if unknown:
                raise BuildError(
                    f"{name}: {len(unknown)} dataset column(s) not in the schema", 1
                )
            batch = ArrayList()
            for row in rows:
                values = [
                    bridge.java_value(ctypes[c], row.get(c)) if c in ctypes else None
                    for c in cols
                ]
                batch.add(ObjArray(values))
                n_rows += 1
                if batch.size() >= BATCH:
                    t.addRows(batch)
                    batch = ArrayList()
            if batch.size():
                t.addRows(batch)
            n_tables += 1
    finally:
        db.close()
    return n_tables, n_rows


def count_rows(path: Path) -> tuple[int, int]:
    db = bridge.open_database(path, read_only=True)
    try:
        names = [n for n in bridge.tables(db) if not bridge.link_connect(db, n)]
        return len(names), sum(int(db.getTable(n).getRowCount()) for n in names)
    finally:
        db.close()


def temp_path(target: Path) -> Path:
    return target.with_name(f".{target.stem}.{os.getpid()}.tmp{target.suffix}")


def build_from_dataset(
    target: Path, schema: dict, dataset_dir: Path
) -> tuple[Path, int, int]:
    """Build into a temp file next to `target`; returns (temp path, tables, rows)."""
    from toolkit.dataset import read_meta

    meta = read_meta(dataset_dir)
    tmp = temp_path(target)
    if tmp.exists():
        tmp.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = bridge.connect(
            str(tmp), read_only=False, memory=True, new_version="V2010"
        )
        try:
            create_tables(conn, ddl_tables(schema, meta))
        finally:
            conn.close()
        n_tables, n_rows = load_rows(tmp, dataset_dir)
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise
    return tmp, n_tables, n_rows


def copy_source(target: Path, source: Path) -> tuple[Path, int, int]:
    tmp = temp_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(source, tmp)
        n_tables, n_rows = count_rows(tmp)
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise
    return tmp, n_tables, n_rows


def newest_mtime(paths) -> int:
    out = 0
    for p in paths:
        p = Path(p)
        if p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    out = max(out, f.stat().st_mtime_ns)
        elif p.exists():
            out = max(out, p.stat().st_mtime_ns)
    return out


def is_current(target: Path, inputs) -> bool:
    return target.is_file() and target.stat().st_mtime_ns > newest_mtime(inputs)


def finish(tmp: Path, target: Path) -> None:
    os.replace(tmp, target)


def create_queries(private: Path, target: Path, catalog: dict, result: Result) -> None:
    from toolkit.reference.engine import AccessReference

    ref = AccessReference(private, target, catalog)
    try:
        ref.open()
        failures = ref.view_errors
    finally:
        ref.close()
    ok = [u for u in catalog["units"] if u["status"] == "ok"]
    result.queries += len(ok) - len(failures)
    for uid, cls in sorted(failures.items()):
        result.query_failures[uid] = cls


# ------------------------------------------------------------------ datasets


def seed_dirs(target_root: Path) -> list[tuple[str, Path]]:
    root = Path(target_root) / "goldens" / "synthetic"
    out = []
    if root.is_dir():
        for d in root.iterdir():
            if (
                re.fullmatch(r"seed-\d+", d.name)
                and (d / "input" / "dataset.json").is_file()
            ):
                out.append((d.name, d / "input"))
    return sorted(out, key=lambda x: int(x[0].split("-")[1]))


def build_one(
    name: str,
    target: Path,
    schema: dict,
    dataset_dir: Path | None,
    copy_from: Path | None,
    catalog_file: Path,
    private: Path,
    catalog: dict,
    result: Result,
    require_rows: bool,
) -> None:
    inputs = [catalog_file] + ([copy_from] if copy_from else [dataset_dir])
    if is_current(target, inputs):
        result.skipped += 1
        result.messages.append(f"{name}: up to date")
        return
    if copy_from is not None:
        tmp, n_tables, n_rows = copy_source(target, copy_from)
    else:
        if require_rows:
            from toolkit.dataset import read_meta

            meta = read_meta(dataset_dir)
            if sum(int(t.get("rows") or 0) for t in meta.get("tables") or []) == 0:
                raise BuildError("empty reference", 1)
        tmp, n_tables, n_rows = build_from_dataset(target, schema, dataset_dir)
    if require_rows and n_rows == 0:
        tmp.unlink()
        raise BuildError("empty reference", 1)
    finish(tmp, target)
    result.built += 1
    result.tables += n_tables
    result.rows += n_rows
    create_queries(private, target, catalog, result)


def load_schema(private: Path) -> dict:
    path = private / "out" / "model" / "schema.json"
    if not path.is_file():
        raise BuildError("out/model/schema.json missing: run parse first")
    return json.loads(path.read_text(encoding="utf-8"))
