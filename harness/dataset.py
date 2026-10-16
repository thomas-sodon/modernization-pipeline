"""Dataset directory: reader, writer, validator and value formatter (docs/contracts.md#dataset-directory).

Standard library only, and no import of toolkit or of other harness modules, so S2.harness can
vendor this file into the target (import_dataset, load_dataset) unchanged.

A dataset directory is `<dir>/dataset.json` plus `<dir>/tables/<file-safe table>.csv`:

- CSV: UTF-8 without BOM, RFC 4180 (comma separator, CRLF after every record), header row = the
  column names in schema order. A field is quoted when it holds a comma, a double quote, CR or LF,
  and the empty string is always written quoted (`""`), so a one-column row is never a blank line.
- NULL is the unquoted field `\\N`. A text that is exactly `\\N`, or more backslashes then `N`,
  gets one extra leading backslash, which readers strip.
- `format_value(column type, value)` is the one implementation of the text form of a value.

Public API: `format_value`, `parse_value`, `file_safe`, `load_order`, `write_dataset`,
`read_dataset`, `read_meta`, `iter_table`, `validate`, `DatasetError`.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import heapq
import io
import json
import math
import os
import re
import struct
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

FORMAT_VERSION = 1
NULL = "\\N"
META_FILE = "dataset.json"
TABLES_DIR = "tables"

TYPES = (
    "text",
    "memo",
    "byte",
    "integer",
    "longinteger",
    "autonumber",
    "single",
    "double",
    "currency",
    "decimal",
    "datetime",
    "yesno",
    "hyperlink",
    "oleobject",
    "complex",
    "guid",
)
TEXT_TYPES = ("text", "memo", "hyperlink")
INT_TYPES = ("byte", "integer", "longinteger", "autonumber")
DECIMAL_TYPES = ("currency", "decimal")
OMITTED_TYPES = ("complex", "oleobject")
OMIT_REASONS = ("complex", "oleobject", "computed")
INT_RANGES = {
    "byte": (0, 255),
    "integer": (-32768, 32767),
    "longinteger": (-(2**31), 2**31 - 1),
    "autonumber": (-(2**31), 2**31 - 1),
}

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_ESCAPED_NULL = re.compile(r"\\+N")
_INT_TEXT = re.compile(r"[+-]?\d+")
_DATETIME_TEXT = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(\.\d+)?)?)?"
)
_CANON_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_YES = {"1", "-1", "true", "yes", "on"}
_NO = {"0", "false", "no", "off"}


class ValueTypeError(TypeError, ValueError):
    """A value of a Python type that its column type cannot take (also a ValueError, so callers
    catch every unfit value as ValueError)."""


class DatasetError(Exception):
    """A dataset that cannot be read or written. exit_code 2 for a missing or unreadable
    dataset.json or an unknown format_version, 1 for a check that failed. str() never holds a
    data value."""

    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


# ------------------------------------------------------------------ names


def file_safe(name: str) -> str:
    """File-safe id (docs/contracts.md#conventions): characters outside [A-Za-z0-9._-] become _,
    and when anything was replaced, '-' plus the first 8 hex digits of sha256(name) is appended."""
    safe = _UNSAFE.sub("_", name)
    if safe != name:
        safe += "-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return safe


def table_file(name: str) -> str:
    return f"{TABLES_DIR}/{file_safe(name)}.csv"


# ------------------------------------------------------------------ values


def _single_text(x: float) -> str:
    """The shortest decimal that round-trips through IEEE binary32, in Python repr style."""
    try:
        packed = struct.pack("<f", x)
    except OverflowError:
        raise ValueError("single out of range") from None
    x32 = struct.unpack("<f", packed)[0]
    if math.isinf(x32):
        raise ValueError("single out of range")
    for digits in range(1, 10):
        text = format(x32, f".{digits}g")
        if struct.pack("<f", float(text)) == packed:
            break
    as_repr = repr(float(text))
    if struct.pack("<f", float(as_repr)) == packed:
        return as_repr
    return text


def _to_float(value) -> float:
    if isinstance(value, bool):
        raise ValueTypeError("boolean given for a number")
    if isinstance(value, (int, float, Decimal)):
        x = float(value)
    elif isinstance(value, str):
        x = float(value.strip())
    else:
        raise ValueTypeError(f"{type(value).__name__} given for a number")
    if math.isnan(x) or math.isinf(x):
        raise ValueError("NaN or infinity")
    return x


def _to_int(value) -> int:
    if isinstance(value, bool):
        raise ValueTypeError("boolean given for an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError("fraction given for an integer")
        return int(value)
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise ValueError("fraction given for an integer")
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if _INT_TEXT.fullmatch(s):
            return int(s)
        d = _to_decimal(s)
        if d != d.to_integral_value():
            raise ValueError("fraction given for an integer")
        return int(d)
    raise ValueError(f"{type(value).__name__} given for an integer")


def _to_decimal(value) -> Decimal:
    if isinstance(value, bool):
        raise ValueTypeError("boolean given for a decimal")
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("NaN or infinity")
        d = Decimal(repr(value))
    elif isinstance(value, str):
        try:
            d = Decimal(value.strip())
        except InvalidOperation:
            raise ValueError("not a decimal") from None
    else:
        raise ValueTypeError(f"{type(value).__name__} given for a decimal")
    if not d.is_finite():
        raise ValueError("NaN or infinity")
    return d


def _decimal_text(d: Decimal) -> str:
    if d == 0:
        return "0"
    text = format(d.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _round_second(value: dt.datetime) -> dt.datetime:
    if value.microsecond >= 500000:
        value = value + dt.timedelta(seconds=1)
    return value.replace(microsecond=0)


def _to_datetime(value) -> dt.datetime:
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            raise ValueError("datetime with a time zone")
        return _round_second(value)
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        m = _DATETIME_TEXT.fullmatch(value.strip())
        if not m:
            raise ValueError("not a datetime")
        y, mo, d, h, mi, s, frac = m.groups()
        base = dt.datetime(
            int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0)
        )
        if frac:
            micro = round(float("0" + frac) * 1_000_000)
            base = base + dt.timedelta(microseconds=micro)
        return _round_second(base)
    raise ValueError(f"{type(value).__name__} given for a datetime")


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, Decimal)):
        return value != 0
    if isinstance(value, float):
        if math.isnan(value):
            raise ValueError("NaN given for yesno")
        return value != 0
    if isinstance(value, str):
        s = value.strip().lower()
        if s in _YES:
            return True
        if s in _NO:
            return False
        raise ValueError("not a yes/no value")
    raise ValueError(f"{type(value).__name__} given for yesno")


def _text_of(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("NaN or infinity")
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, Decimal):
        return _decimal_text(_to_decimal(value))
    if isinstance(value, (dt.datetime, dt.date)):
        return _to_datetime(value).strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(value, uuid.UUID):
        return str(value)
    raise ValueError(f"{type(value).__name__} given for text")


def format_value(ctype: str, value) -> str:
    """The text form of one value of neutral type `ctype` (before CSV quoting).

    None is NULL (`\\N`). Raises ValueError (never naming the value) when the value does not fit
    the type, for NaN and infinity, and for the omitted types complex and oleobject."""
    if ctype not in TYPES:
        raise ValueError(f"unknown column type {ctype!r}")
    if value is None:
        return NULL
    if ctype in TEXT_TYPES:
        text = _text_of(value)
        if _ESCAPED_NULL.fullmatch(text):
            return "\\" + text
        return text
    if ctype in INT_TYPES:
        n = _to_int(value)
        lo, hi = INT_RANGES[ctype]
        if not lo <= n <= hi:
            raise ValueError(f"{ctype} out of range")
        return str(n)
    if ctype in DECIMAL_TYPES:
        return _decimal_text(_to_decimal(value))
    if ctype == "single":
        return _single_text(_to_float(value))
    if ctype == "double":
        return repr(_to_float(value))
    if ctype == "datetime":
        return _to_datetime(value).strftime("%Y-%m-%dT%H:%M:%S")
    if ctype == "yesno":
        return "1" if _to_bool(value) else "0"
    if ctype == "guid":
        if isinstance(value, uuid.UUID):
            return str(value)
        if isinstance(value, str):
            try:
                return str(uuid.UUID(value.strip()))
            except ValueError:
                raise ValueError("not a guid") from None
        raise ValueError(f"{type(value).__name__} given for a guid")
    raise ValueError(f"{ctype} columns are omitted from datasets")


def parse_value(ctype: str, text: str):
    """The typed value of one CSV field (the inverse of format_value): None for NULL; str for
    text, memo, hyperlink and guid; int; Decimal for currency and decimal; float for single and
    double; datetime.datetime; bool for yesno. Raises ValueError when the field is not in the
    canonical text form of its type."""
    if ctype not in TYPES:
        raise ValueError(f"unknown column type {ctype!r}")
    if text == NULL:
        return None
    if ctype in TEXT_TYPES:
        if _ESCAPED_NULL.fullmatch(text):
            if not text.startswith("\\\\"):
                raise ValueError("unescaped backslash-N text")
            return text[1:]
        return text
    if ctype in OMITTED_TYPES:
        raise ValueError(f"{ctype} columns are omitted from datasets")
    if ctype in INT_TYPES:
        if not re.fullmatch(r"-?(0|[1-9]\d*)", text):
            raise ValueError(f"not a canonical {ctype}")
        n = int(text)
        lo, hi = INT_RANGES[ctype]
        if not lo <= n <= hi:
            raise ValueError(f"{ctype} out of range")
        return n
    if ctype in DECIMAL_TYPES:
        if not re.fullmatch(r"-?(0|[1-9]\d*)(\.\d*[1-9])?", text) or text == "-0":
            raise ValueError(f"not a canonical {ctype}")
        return Decimal(text)
    if ctype in ("single", "double"):
        try:
            x = float(text)
        except ValueError:
            raise ValueError(f"not a {ctype}") from None
        if math.isnan(x) or math.isinf(x):
            raise ValueError("NaN or infinity")
        if format_value(ctype, x) != text:
            raise ValueError(f"not a canonical {ctype}")
        return x
    if ctype == "datetime":
        if not _CANON_DATETIME.fullmatch(text):
            raise ValueError("not a canonical datetime")
        try:
            return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            raise ValueError("not a valid datetime") from None
    if ctype == "yesno":
        if text not in ("0", "1"):
            raise ValueError("not a canonical yesno")
        return text == "1"
    if ctype == "guid":
        if not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text
        ):
            raise ValueError("not a canonical guid")
        return text
    raise ValueError(f"unknown column type {ctype!r}")


# ------------------------------------------------------------------ CSV


def _quote(field: str) -> str:
    if field == "" or any(ch in field for ch in ',"\r\n'):
        return '"' + field.replace('"', '""') + '"'
    return field


def csv_record(fields) -> str:
    """One CSV record (already formatted fields) with its CRLF end."""
    return ",".join(_quote(f) for f in fields) + "\r\n"


def format_row(columns: list[dict], row) -> list[str]:
    """The text fields of one row. `row` is a dict keyed by column name (a missing key is NULL)
    or a sequence in column order."""
    if isinstance(row, dict):
        values = [row.get(c["name"]) for c in columns]
    else:
        values = list(row)
        if len(values) != len(columns):
            raise ValueError("row length differs from the column count")
    return [format_value(c["type"], v) for c, v in zip(columns, values)]


# ------------------------------------------------------------------ load order


def load_order(schema: dict, names) -> tuple[list[str], list[str]]:
    """Parents before children over schema.json relationships, restricted to `names`.

    Ties and cycles are settled by name order: when no table is free, the remaining table with
    the smallest name is placed next, and its name is reported in the second list. A table's
    reference to itself is not an ordering constraint."""
    nodes = set(names)
    parents: dict[str, set[str]] = {n: set() for n in nodes}
    children: dict[str, set[str]] = {n: set() for n in nodes}
    for rel in schema.get("relationships") or []:
        child, parent = rel.get("from_table"), rel.get("to_table")
        if child in nodes and parent in nodes and child != parent:
            parents[child].add(parent)
            children[parent].add(child)
    waiting = {n: len(parents[n]) for n in nodes}
    ready = [n for n in nodes if waiting[n] == 0]
    heapq.heapify(ready)
    order: list[str] = []
    placed: set[str] = set()
    broken: list[str] = []
    while len(order) < len(nodes):
        if not ready:
            nxt = min(n for n in nodes if n not in placed)
            broken.append(nxt)
        else:
            nxt = heapq.heappop(ready)
            if nxt in placed:
                continue
        placed.add(nxt)
        order.append(nxt)
        for c in sorted(children[nxt]):
            if c in placed:
                continue
            waiting[c] -= 1
            if waiting[c] == 0:
                heapq.heappush(ready, c)
    return order, broken


def self_references(schema: dict, names) -> list[str]:
    nodes = set(names)
    return sorted(
        {
            rel["from_table"]
            for rel in schema.get("relationships") or []
            if rel.get("from_table") == rel.get("to_table")
            and rel.get("from_table") in nodes
        }
    )


# ------------------------------------------------------------------ write


def dumps_json(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def write_table(path: Path, columns: list[dict], rows) -> tuple[int, str]:
    """Write one CSV file; returns (row count, sha256 of its bytes)."""
    h = hashlib.sha256()
    count = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        head = csv_record([c["name"] for c in columns]).encode("utf-8")
        fh.write(head)
        h.update(head)
        for row in rows:
            data = csv_record(format_row(columns, row)).encode("utf-8")
            fh.write(data)
            h.update(data)
            count += 1
    return count, h.hexdigest()


def write_dataset(directory, meta: dict, tables: list[dict]) -> dict:
    """Write dataset.json and tables/*.csv into `directory` (created when missing) and return the
    dataset.json document.

    `meta`: name, kind (engagement or synthetic), seed (int or None), schema_sha256 and optionally
    load_order (default: the order of `tables`). `tables`: dicts with name, columns
    [{name, type}] in schema order, omitted_columns [{name, type, reason}] (optional) and rows (an
    iterable of dicts keyed by column name, or of sequences in column order)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    out_tables = []
    for t in tables:
        columns = [{"name": c["name"], "type": c["type"]} for c in t["columns"]]
        for c in columns:
            if c["type"] in OMITTED_TYPES:
                raise ValueError(
                    f"{t['name']}.{c['name']}: {c['type']} columns are omitted"
                )
        rel = table_file(t["name"])
        rows, sha = write_table(directory / rel, columns, t.get("rows") or [])
        out_tables.append(
            {
                "name": t["name"],
                "file": rel,
                "rows": rows,
                "sha256": sha,
                "columns": columns,
                "omitted_columns": [
                    {"name": o["name"], "type": o["type"], "reason": o["reason"]}
                    for o in t.get("omitted_columns") or []
                ],
            }
        )
    order = meta.get("load_order")
    if order is None:
        order = [t["name"] for t in tables]
    doc = {
        "format_version": FORMAT_VERSION,
        "name": meta["name"],
        "kind": meta["kind"],
        "seed": meta.get("seed"),
        "schema_sha256": meta["schema_sha256"],
        "load_order": list(order),
        "tables": out_tables,
    }
    (directory / META_FILE).write_text(dumps_json(doc), encoding="utf-8")
    return doc


# ------------------------------------------------------------------ read


def read_meta(directory) -> dict:
    """dataset.json (DatasetError exit 2 when missing, not JSON or of an unknown version)."""
    path = Path(directory) / META_FILE
    if not path.is_file():
        raise DatasetError(f"{META_FILE} missing", 2)
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise DatasetError(f"{META_FILE} is not valid JSON", 2) from None
    if not isinstance(meta, dict):
        raise DatasetError(f"{META_FILE} is not an object", 2)
    if meta.get("format_version") != FORMAT_VERSION:
        raise DatasetError(f"{META_FILE}: unknown format_version", 2)
    return meta


def iter_records(path: Path):
    """The raw CSV records of a table file (header first) as lists of strings."""
    with open(path, encoding="utf-8", newline="") as fh:
        yield from csv.reader(fh, strict=True)


def iter_table(directory, table: dict):
    """Typed rows (dicts keyed by column name) of one dataset.json table entry."""
    columns = table["columns"]
    names = [c["name"] for c in columns]
    records = iter_records(Path(directory) / table["file"])
    header = next(records, None)
    if header != names:
        raise DatasetError(f"{table['name']}: header differs from columns", 1)
    for number, rec in enumerate(records, start=1):
        if len(rec) != len(columns):
            raise DatasetError(f"{table['name']}: row {number}: field count", 1)
        try:
            yield {c["name"]: parse_value(c["type"], f) for c, f in zip(columns, rec)}
        except ValueError as exc:
            raise DatasetError(f"{table['name']}: row {number}: {exc}", 1) from None


def read_dataset(directory) -> tuple[dict, dict]:
    """(dataset.json, {table name: iterator of typed rows}), tables in load order."""
    meta = read_meta(directory)
    by_name = {t["name"]: t for t in meta.get("tables") or []}
    order = [n for n in meta.get("load_order") or [] if n in by_name]
    order += [n for n in by_name if n not in order]
    return meta, {n: iter_table(directory, by_name[n]) for n in order}


# ------------------------------------------------------------------ validate


_META_KEYS = {
    "format_version",
    "name",
    "kind",
    "seed",
    "schema_sha256",
    "load_order",
    "tables",
}
_TABLE_KEYS = {"name", "file", "rows", "sha256", "columns", "omitted_columns"}


def _check_meta(meta: dict, problems: list[str]) -> None:
    extra = sorted(set(meta) - _META_KEYS)
    missing = sorted(_META_KEYS - set(meta))
    if extra:
        problems.append(f"{META_FILE}: unknown keys {', '.join(extra)}")
    if missing:
        problems.append(f"{META_FILE}: missing keys {', '.join(missing)}")
    kind, name, seed = meta.get("kind"), meta.get("name"), meta.get("seed")
    if kind == "engagement":
        if name != "engagement" or seed is not None:
            problems.append(
                f"{META_FILE}: engagement datasets are named engagement, seed null"
            )
    elif kind == "synthetic":
        if (
            not isinstance(seed, int)
            or isinstance(seed, bool)
            or name != f"seed-{seed}"
        ):
            problems.append(
                f"{META_FILE}: synthetic datasets are named seed-N with seed N"
            )
    else:
        problems.append(f"{META_FILE}: kind must be engagement or synthetic")
    if not re.fullmatch(r"[0-9a-f]{64}", str(meta.get("schema_sha256"))):
        problems.append(f"{META_FILE}: schema_sha256 is not a sha256")
    if not isinstance(meta.get("tables"), list):
        problems.append(f"{META_FILE}: tables must be a list")
    if not isinstance(meta.get("load_order"), list):
        problems.append(f"{META_FILE}: load_order must be a list")


def _check_columns(t: dict, st: dict, problems: list[str]) -> None:
    name = t["name"]
    listed = t.get("columns") or []
    omitted = t.get("omitted_columns") or []
    schema_cols = st.get("columns") or []
    by_name = {c["name"]: c for c in schema_cols}
    omitted_names = set()
    for o in omitted:
        sc = by_name.get(o.get("name"))
        if sc is None:
            problems.append(
                f"{name}: omitted column {o.get('name')} is not in the schema"
            )
            continue
        omitted_names.add(sc["name"])
        reason = o.get("reason")
        if o.get("type") != sc["type"]:
            problems.append(
                f"{name}.{sc['name']}: omitted column type differs from the schema"
            )
        if reason in ("complex", "oleobject"):
            if sc["type"] != reason:
                problems.append(
                    f"{name}.{sc['name']}: omitted as {reason} but the type is {sc['type']}"
                )
        elif reason == "computed":
            if not sc.get("computed"):
                problems.append(
                    f"{name}.{sc['name']}: omitted as computed but not computed in the schema"
                )
        else:
            problems.append(f"{name}.{sc['name']}: unknown omit reason")
    for c in schema_cols:
        if c["name"] not in omitted_names and c["type"] in OMITTED_TYPES:
            problems.append(
                f"{name}.{c['name']}: {c['type']} column must be omitted and listed"
            )
    want = [
        (c["name"], c["type"])
        for c in schema_cols
        if c["name"] not in omitted_names and c["type"] not in OMITTED_TYPES
    ]
    got = [(c.get("name"), c.get("type")) for c in listed]
    if got == want:
        return
    want_names = [w[0] for w in want]
    got_names = [g[0] for g in got]
    for n in want_names:
        if n not in got_names:
            problems.append(f"{name}.{n}: column missing")
    for n, ctype in got:
        if n not in by_name:
            problems.append(f"{name}.{n}: column not in the schema")
        elif n in omitted_names:
            problems.append(f"{name}.{n}: column both listed and omitted")
        elif ctype != by_name[n]["type"]:
            problems.append(f"{name}.{n}: type differs from the schema")
    if sorted(got_names) == sorted(want_names) and got_names != want_names:
        problems.append(f"{name}: columns are not in schema order")


def _check_csv(directory: Path, t: dict, problems: list[str], keys: dict) -> int:
    """Check one CSV against its entry; returns the number of data rows read. Records the
    tuples of the columns that relationships use in `keys`."""
    name = t["name"]
    path = directory / t["file"]
    if t["file"] != table_file(name):
        problems.append(f"{name}: file is not {table_file(name)}")
    if not path.is_file():
        problems.append(f"{name}: {t['file']} missing")
        return 0
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != t.get("sha256"):
        problems.append(f"{name}: sha256 differs")
    if data.startswith(b"\xef\xbb\xbf"):
        problems.append(f"{name}: CSV starts with a BOM")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        problems.append(f"{name}: CSV is not UTF-8")
        return 0
    columns = t.get("columns") or []
    position = {c["name"]: i for i, c in enumerate(columns)}
    rows = 0
    bad = 0
    canonical = io.StringIO()
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader, None)
        if header != [c["name"] for c in columns]:
            problems.append(f"{name}: header differs from columns")
        canonical.write(csv_record(header or []))
        for number, rec in enumerate(reader, start=1):
            rows += 1
            if len(rec) != len(columns):
                problems.append(f"{name}: row {number}: field count")
                bad += 1
                if bad >= 5:
                    break
                continue
            out = []
            for c, f in zip(columns, rec):
                try:
                    out.append(format_value(c["type"], parse_value(c["type"], f)))
                except ValueError as exc:
                    problems.append(f"{name}: row {number} column {c['name']}: {exc}")
                    bad += 1
                    out.append(f)
            canonical.write(csv_record(out))
            for cols, store in keys.get(name, []):
                if all(k in position for k in cols):
                    store.append(tuple(rec[position[k]] for k in cols))
            if bad >= 5:
                problems.append(f"{name}: further rows not checked")
                break
    except csv.Error:
        problems.append(f"{name}: CSV is malformed")
        return rows
    if bad == 0 and text != canonical.getvalue():
        problems.append(
            f"{name}: quoting or record ends differ from the format (RFC 4180, CRLF)"
        )
    if rows != t.get("rows"):
        problems.append(f"{name}: row count differs")
    return rows


def validate(directory, schema, allow_absent=()) -> dict:
    """Check a dataset directory against a model schema (a dict, or a path to schema.json).

    Returns {"problems": [...], "counts": {...}, "schema_sha256_matches": bool}. Raises
    DatasetError (exit 2) when dataset.json is missing or unreadable. Problems name tables,
    columns and row numbers, never values. Tables whose schema role is documentation, and tables
    named in `allow_absent`, may be absent."""
    directory = Path(directory)
    schema_sha = None
    if not isinstance(schema, dict):
        raw = Path(schema).read_bytes()
        schema_sha = hashlib.sha256(raw).hexdigest()
        schema = json.loads(raw.decode("utf-8"))
    meta = read_meta(directory)
    problems: list[str] = []
    _check_meta(meta, problems)
    tables = [t for t in meta.get("tables") or [] if isinstance(t, dict)]
    schema_tables = {t["name"]: t for t in schema.get("tables") or []}
    names = []
    for t in tables:
        extra = sorted(set(t) - _TABLE_KEYS)
        missing = sorted(_TABLE_KEYS - set(t))
        if extra or missing:
            problems.append(f"{t.get('name')}: table entry keys differ from the format")
            continue
        names.append(t["name"])
    if len(set(names)) != len(names):
        problems.append(f"{META_FILE}: a table is listed twice")
    allowed_absent = set(allow_absent)
    for sname, st in sorted(schema_tables.items()):
        if sname in names:
            if st.get("role") == "documentation":
                problems.append(f"{sname}: documentation tables are not in datasets")
            continue
        if st.get("role") == "documentation" or sname in allowed_absent:
            continue
        problems.append(f"{sname}: table missing")
    present = [t for t in tables if t.get("name") in names]
    # relationship keys to check for orphans
    rels = [
        r
        for r in schema.get("relationships") or []
        if r.get("enforce", True)
        and r.get("from_table") in names
        and r.get("to_table") in names
    ]
    keys: dict[str, list] = {}
    rel_stores = []
    for r in rels:
        child: list = []
        parent: list = []
        keys.setdefault(r["from_table"], []).append((r["from_columns"], child))
        keys.setdefault(r["to_table"], []).append((r["to_columns"], parent))
        rel_stores.append((r, child, parent))
    total_rows = 0
    for t in present:
        st = schema_tables.get(t["name"])
        if st is None:
            problems.append(f"{t['name']}: table not in the schema")
            continue
        _check_columns(t, st, problems)
        total_rows += _check_csv(directory, t, problems, keys)
    orphans = 0
    for r, child, parent in rel_stores:
        have = set(parent)
        n = sum(1 for k in child if NULL not in k and k not in have)
        if n:
            orphans += n
            problems.append(
                f"{r['from_table']}: {n} foreign-key orphans for {r.get('name')}"
            )
    order = meta.get("load_order") if isinstance(meta.get("load_order"), list) else []
    expected_order, _broken = load_order(schema, names)
    if sorted(order) != sorted(names):
        problems.append(f"{META_FILE}: load_order does not list exactly the tables")
    elif order != expected_order:
        problems.append(f"{META_FILE}: load_order is not parents before children")
    return {
        "problems": problems,
        "counts": {
            "tables": len(present),
            "rows": total_rows,
            "problems": len(problems),
            "fk_orphans": orphans,
        },
        "schema_sha256_matches": schema_sha is None
        or schema_sha == meta.get("schema_sha256"),
    }


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def same_tree(a, b) -> list[str]:
    """Relative paths that differ between two directories (missing on either side counts)."""
    a, b = Path(a), Path(b)

    def files(root: Path) -> set[str]:
        if not root.is_dir():
            return set()
        return {
            os.path.relpath(os.path.join(d, f), root)
            for d, _dirs, fs in os.walk(root)
            for f in fs
        }

    fa, fb = files(a), files(b)
    out = sorted(fa ^ fb)
    for rel in sorted(fa & fb):
        if (a / rel).read_bytes() != (b / rel).read_bytes():
            out.append(rel)
    return sorted(out)
