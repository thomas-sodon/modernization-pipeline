"""Seeded synthetic datasets built from the design only (S2.synth).

`generate(design, seed)` returns the tables of one seed for `toolkit.dataset.write_dataset`
(docs/contracts.md#dataset-directory). Inputs are the scrubbed model (`schema.json`,
`objects.json`, `procedures.json`, `datamacros.json` and the `code/` files that procedures.json
lists) and the `lookups` of analysis/rules.json. Nothing else is read: never the engagement
dataset, the source or any other data-zone path.

How values are chosen:

- Tables come in foreign-key order (`toolkit.dataset.load_order`); documentation tables are left
  out, and complex, oleobject and computed columns are omitted (`toolkit.dataset.split_columns`).
- About ROWS rows per table. A lookup table (the parent of a column whose rules.json lookups[]
  entry has copy_values true, or a table whose own key has such an entry) gets one row per code.
- Codes of a copy_values lookup come from the model: value lists (column RowSource, form value
  lists, xlsm list validations) and literals that query criteria, macros, data macros and VBA
  compare the column with (VBA enum members resolve to their values; an enum is the code list of
  the columns it is compared with and of the table its name names). When the model has none, the
  entry's `values` are used; when those are null too, codes are generated and the column is
  reported in `generated`.
- Every other column gets generated values plus the model literals it is compared with, so
  filters select rows on every seed. Each literal and each lookup code appears at least once.
- Each column has its own Faker stream, seeded from sha256(seed, table, column); Python's hash()
  is never used, so the bytes do not depend on PYTHONHASHSEED. Dates fall in WINDOW_START to
  WINDOW_END (with weekends and month ends); the current date is never used. The seeds depend on
  the pinned Faker version: an upgrade regenerates the seeds and goldens through the record path.
- Seed 1 is plain. Seed 2 adds NULL in every nullable column, empty strings (where the column
  allows a zero-length string), boundary values (0, negative numbers where the type and the
  validation rule allow, maximum text length, the window's first and last second) and non-ASCII
  text. Seed 3 adds duplicates in columns outside every unique constraint and many children for
  the first parents.
"""

from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import json
import random
import re
import string
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from faker import Faker

from harness.dataset import INT_RANGES
from toolkit import dataset as ds

ROWS = 50
GENERATED_CODES = 5
WINDOW_START = dt.date(2023, 1, 1)
WINDOW_END = dt.date(2025, 12, 31)
MAX_ATTEMPTS = 200
PLAIN_ATTEMPTS = (
    3  # attempts after which a row is generated without specials, skew or duplicates
)
MEMO_MAX = 2000
NULL_SHARE = 0.25  # seed 2: share of NULLs in a nullable column
DUP_SHARE = 0.35  # seed 3: share of repeated values in a non-unique column
SKEW_SHARE = 0.6  # seed 3: share of children that go to the first two parents
EXTRA_SHARE = 0.25  # share of model literals in a generated column
# Hand-written non-ASCII words for seed 2 (never data).
CODE_MAX_LEN = (
    40  # a text literal longer than this, or of more words, is prose, not a code
)
CODE_MAX_WORDS = 4
# VBA and Access runtime objects whose properties share names with columns (Err.Description).
RUNTIME_OBJECTS = {
    "err",
    "debug",
    "application",
    "docmd",
    "screen",
    "currentproject",
    "currentdb",
    "dbengine",
}
NON_ASCII = (
    "Müller",
    "Ångström",
    "Zürich",
    "Señora",
    "Œuvre",
    "Ελλάδα",
    "東京",
    "Łódź",
    "Café",
)

INT_TYPES = ("byte", "integer", "longinteger", "autonumber")
DECIMAL_TYPES = ("currency", "decimal")
FLOAT_TYPES = ("single", "double")
NUMERIC_TYPES = INT_TYPES + DECIMAL_TYPES + FLOAT_TYPES
TEXT_TYPES = ds.TEXT_TYPES


class SynthError(Exception):
    """A design that cannot be turned into seeds (exit 2)."""


# ------------------------------------------------------------------ design


@dataclass
class Design:
    schema: dict
    schema_sha256: str
    objects: dict = field(default_factory=dict)
    procedures: dict = field(default_factory=dict)
    datamacros: dict = field(default_factory=dict)
    code: dict = field(default_factory=dict)  # module name -> VBA text
    lookups: list = field(default_factory=list)


def _json(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_design(model_dir, rules_path=None) -> Design:
    """Read the model directory and the lookups of rules.json (nothing else)."""
    model_dir = Path(model_dir)
    schema_path = model_dir / "schema.json"
    if not schema_path.is_file():
        raise SynthError("model schema.json missing: run parse first")
    raw = schema_path.read_bytes()
    procedures = _json(model_dir / "procedures.json")
    code = {}
    root = model_dir.resolve()
    for module in procedures.get("modules") or []:
        rel = module.get("code_file")
        if not rel:
            continue
        path = (model_dir / rel).resolve()
        if root not in path.parents or not path.is_file():
            continue
        code[module["name"]] = path.read_text(encoding="utf-8")
    lookups = []
    if rules_path is not None:
        lookups = _json(Path(rules_path)).get("lookups") or []
    return Design(
        schema=json.loads(raw.decode("utf-8")),
        schema_sha256=hashlib.sha256(raw).hexdigest(),
        objects=_json(model_dir / "objects.json"),
        procedures=procedures,
        datamacros=_json(model_dir / "datamacros.json"),
        code=code,
        lookups=lookups,
    )


# ------------------------------------------------------------------ columns


@dataclass
class Col:
    table: str
    name: str
    type: str
    size: int | None
    required: bool
    scale: int | None
    zero_length: bool
    validation: list
    mask: tuple | None


_RULE_PART = re.compile(r"(>=|<=|<>|>|<|=)\s*(-?\d+(?:\.\d+)?)")


def parse_validation(rule) -> list:
    """Simple numeric validation rules ('>0', '>=0', 'Between 1 And 9', '>=0 And <=1') as
    [(op, Decimal)]; anything else gives no constraint."""
    if not rule or not isinstance(rule, str):
        return []
    text = rule.strip()
    m = re.fullmatch(
        r"between\s+(-?\d+(?:\.\d+)?)\s+and\s+(-?\d+(?:\.\d+)?)", text, re.IGNORECASE
    )
    if m:
        return [(">=", Decimal(m.group(1))), ("<=", Decimal(m.group(2)))]
    out = []
    for part in re.split(r"\s+and\s+", text, flags=re.IGNORECASE):
        m = _RULE_PART.fullmatch(part.strip())
        if not m:
            return []
        out.append((m.group(1), Decimal(m.group(2))))
    return out


def _passes(col: Col, value) -> bool:
    if value is None or not col.validation or col.type not in NUMERIC_TYPES:
        return True
    v = Decimal(str(value))
    for op, bound in col.validation:
        ok = {
            ">": v > bound,
            ">=": v >= bound,
            "<": v < bound,
            "<=": v <= bound,
            "=": v == bound,
            "<>": v != bound,
        }[op]
        if not ok:
            return False
    return True


def parse_mask(mask) -> tuple | None:
    """An Access input mask as (pattern, store_literals)."""
    if not mask or not isinstance(mask, str):
        return None
    parts = mask.split(";")
    store = len(parts) > 1 and parts[1].strip() == "0"
    return parts[0], store


def make_col(table: str, c: dict) -> Col:
    props = c.get("properties") or {}
    azl = props.get("AllowZeroLength")
    if c["type"] in TEXT_TYPES:
        zero = azl is None or str(azl).strip().lower() in ("1", "true", "yes", "-1")
    else:
        zero = False
    size = c.get("size")
    return Col(
        table=table,
        name=c["name"],
        type=c["type"],
        size=size if isinstance(size, int) and size > 0 else None,
        required=bool(c.get("required")),
        scale=c.get("scale"),
        zero_length=zero,
        validation=parse_validation(props.get("ValidationRule")),
        mask=parse_mask(props.get("InputMask")),
    )


# ------------------------------------------------------------------ model literals

_IDENT = r"(?:\[[^\[\]\r\n]+\]|[A-Za-z_][A-Za-z0-9_]*)"
_CMP = re.compile(
    r"(?<![\w\]])(?:(?P<q>" + _IDENT + r")\s*[.!]\s*)?(?P<c>" + _IDENT + r")\s*\)*\s*"
    r"(?P<op><>|<=|>=|=|<|>|\bLike\b|\bIn\b)\s*"
    r"(?P<rhs>\((?:[^()\"\r\n]|\"[^\"\r\n]*\")*\)"
    r"|\"(?:[^\"\r\n]|\"\")*\""
    r"|'[^'\r\n]*'"
    r"|#[^#\r\n]+#"
    r"|-?\d+(?:\.\d+)?(?![\w.])"
    r"|[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)",
    re.IGNORECASE,
)
_ENUM_START = re.compile(
    r"^\s*(?:(?:Public|Private|Friend)\s+)?Enum\s+([A-Za-z_]\w*)", re.IGNORECASE
)
_ENUM_END = re.compile(r"^\s*End\s+Enum\b", re.IGNORECASE)
_ENUM_MEMBER = re.compile(
    r"^\s*([A-Za-z_]\w*)\s*(?:=\s*(-?(?:&H[0-9A-Fa-f]+|\d+)))?\s*$", re.IGNORECASE
)
_FROM = re.compile(r"\b(?:FROM|JOIN)\s+(\[[^\]]+\]|[A-Za-z_]\w*)", re.IGNORECASE)
_SELECTISH = ("SELECT", "TRANSFORM", "PARAMETERS", "TABLE ")


def strip_comment(line: str) -> str:
    """A VBA line without its comment (a ' outside a string, or a leading Rem)."""
    if re.match(r"^\s*(?:\d+\s+)?Rem\b", line, re.IGNORECASE):
        return ""
    in_str = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_str = not in_str
        elif ch == "'" and not in_str:
            return line[:i]
    return line


def parse_enums(code: dict) -> dict:
    """{enum name: {member: int}} from every VBA module."""
    enums: dict[str, dict[str, int]] = {}
    for module in sorted(code):
        current = None
        nxt = 0
        for raw in code[module].splitlines():
            line = strip_comment(raw)
            if current is None:
                m = _ENUM_START.match(line)
                if m:
                    current = enums.setdefault(m.group(1), {})
                    nxt = 0
                continue
            if _ENUM_END.match(line):
                current = None
                continue
            m = _ENUM_MEMBER.match(line)
            if not m:
                continue
            if m.group(2) is not None:
                text = m.group(2)
                if text.lower().lstrip("-").startswith("&h"):
                    value = int(text.lower().replace("&h", ""), 16)
                else:
                    value = int(text)
                nxt = value
            current[m.group(1)] = nxt
            nxt += 1
    return enums


def _unbracket(name: str) -> str:
    return name[1:-1] if name.startswith("[") and name.endswith("]") else name


def _split_list(inner: str) -> list[str]:
    items, cur, in_str = [], [], None
    for ch in inner:
        if in_str:
            cur.append(ch)
            if ch == in_str:
                in_str = None
        elif ch in "\"'":
            in_str = ch
            cur.append(ch)
        elif ch == ",":
            items.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    items.append("".join(cur))
    return [i.strip() for i in items if i.strip()]


class Literals:
    """Tokens that the design compares each schema column with, and the enums linked to it."""

    def __init__(self, design: Design):
        self.design = design
        self.tables = {
            t["name"]: t for t in design.schema.get("tables") or [] if t.get("name")
        }
        self.by_lower_table = {n.lower(): n for n in self.tables}
        self.columns_by_name: dict[str, list[tuple[str, str]]] = {}
        for t in self.tables.values():
            for c in t.get("columns") or []:
                self.columns_by_name.setdefault(c["name"].lower(), []).append(
                    (t["name"], c["name"])
                )
        self.enums = parse_enums(design.code)
        members: dict[str, list[tuple[str, int]]] = {}
        for ename in sorted(self.enums):
            for member, value in self.enums[ename].items():
                members.setdefault(member.lower(), []).append((ename, value))
        self.members = {k: v[0] for k, v in members.items() if len(v) == 1}
        self.enum_lower = {n.lower(): n for n in self.enums}
        self.tokens: dict[tuple[str, str], list[tuple]] = {}
        self.enum_links: dict[tuple[str, str], set[str]] = {}
        self._scan_all()

    # -- sinks
    def _add(self, cols, token) -> None:
        for key in cols:
            if token[0] == "enum":
                self.enum_links.setdefault(key, set()).add(token[1])
            else:
                self.tokens.setdefault(key, []).append(token)

    def resolve(self, qualifier: str | None, column: str) -> list[tuple[str, str]]:
        column = _unbracket(column)
        found = self.columns_by_name.get(column.lower(), [])
        if qualifier and _unbracket(qualifier).lower() in RUNTIME_OBJECTS:
            return []
        if qualifier:
            table = self.by_lower_table.get(_unbracket(qualifier).lower())
            if table:
                own = [k for k in found if k[0] == table]
                if own:
                    return own
        return list(found)

    def token(self, text: str, code: bool):
        s = text.strip()
        if len(s) >= 2 and s[0] == s[-1] == '"':
            inner = s[1:-1]
            if code and (inner.strip().startswith("&") or inner.strip().endswith("&")):
                return None
            return ("str", inner.replace('""', '"'))
        if len(s) >= 2 and s[0] == s[-1] == "'":
            return ("str", s[1:-1])
        if len(s) >= 2 and s[0] == s[-1] == "#":
            return ("date", s[1:-1])
        if re.fullmatch(r"-?\d+(?:\.\d+)?", s):
            return ("num", s)
        low = s.lower()
        if low in ("true", "yes"):
            return ("bool", True)
        if low in ("false", "no"):
            return ("bool", False)
        parts = s.split(".")
        if len(parts) == 2:
            ename = self.enum_lower.get(parts[0].lower())
            if ename:
                for member, value in self.enums[ename].items():
                    if member.lower() == parts[1].lower():
                        return ("enum", ename, value)
            return None
        if len(parts) == 1 and low in self.members:
            ename, value = self.members[low]
            return ("enum", ename, value)
        return None

    def scan(self, text, code: bool = False) -> None:
        if not isinstance(text, str) or not text:
            return
        for m in _CMP.finditer(text):
            op = m.group("op").lower()
            rhs = m.group("rhs")
            if rhs.startswith('"') and len(rhs) > 2:
                # SQL inside a string literal (VBA filters, DLookup criteria, row sources)
                self.scan(rhs[1:-1].replace('""', '"'), code)
            cols = self.resolve(m.group("q"), m.group("c"))
            if not cols:
                continue
            if op == "in" or rhs.startswith("("):
                if not rhs.startswith("("):
                    continue
                for item in _split_list(rhs[1:-1]):
                    tok = self.token(item, code)
                    if tok:
                        self._add(cols, tok)
                continue
            tok = self.token(rhs, code)
            if tok is None:
                continue
            if op == "like":
                if tok[0] != "str":
                    continue
                tok = ("like", tok[1])
            self._add(cols, tok)

    # -- sources
    def source_tables(self, name, depth: int = 0) -> set[str]:
        if not name or depth > 8:
            return set()
        name = _unbracket(name.strip())
        if name in self.tables:
            return {name}
        if name.upper().startswith(_SELECTISH):
            return {
                _unbracket(t)
                for t in _FROM.findall(name)
                if _unbracket(t) in self.tables
            }
        for q in self.design.objects.get("queries") or []:
            if q.get("name") == name:
                out: set[str] = set()
                design = q.get("design") or {}
                for it in design.get("input_tables") or []:
                    out |= self.source_tables(it.get("name"), depth + 1)
                for t in _FROM.findall(q.get("sql") or ""):
                    out |= self.source_tables(t, depth + 1)
                return out
        return set()

    def _value_list(self, cols, items: list[str]) -> None:
        for item in items:
            s = item.strip()
            if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
                s = s[1:-1]
            if s:
                self._add(cols, ("str", s))

    def _scan_all(self) -> None:
        objects = self.design.objects
        # value lists and list validations on schema columns
        for t in self.tables.values():
            for c in t.get("columns") or []:
                key = [(t["name"], c["name"])]
                props = c.get("properties") or {}
                if str(props.get("RowSourceType") or "").lower() == "value list":
                    items = _split_list_semicolon(props.get("RowSource") or "")
                    try:
                        count = max(1, int(props.get("ColumnCount") or 1))
                        bound = max(1, int(props.get("BoundColumn") or 1))
                    except ValueError:
                        count, bound = 1, 1
                    self._value_list(key, items[bound - 1 :: count])
                choices = c.get("choices_from")
                if isinstance(choices, str) and choices.strip():
                    s = choices.strip().lstrip("=")
                    if not (
                        "!" in s
                        or "$" in s
                        or re.fullmatch(r"[A-Za-z]{1,3}\d+(:[A-Za-z]{1,3}\d+)?", s)
                    ):
                        self._value_list(key, s.strip('"').split(","))
        # queries: criteria, joins, expressions, SQL text
        for q in objects.get("queries") or []:
            design = q.get("design") or {}
            for key in ("where", "having"):
                self.scan(design.get(key))
            for j in design.get("joins") or []:
                self.scan(j.get("expression"))
            for oc in design.get("output_columns") or []:
                self.scan(oc.get("expression"))
            self.scan(q.get("sql"))
        # forms and reports: value lists bound to columns, SQL row sources
        for obj in (objects.get("forms") or []) + (objects.get("reports") or []):
            rs_tables = self.source_tables(obj.get("record_source"))
            if (obj.get("record_source") or "").upper().startswith(_SELECTISH):
                self.scan(obj.get("record_source"))
            for ctl in obj.get("controls") or []:
                row_source = (ctl.get("row_source") or "").strip()
                if not row_source:
                    continue
                if row_source.upper().startswith(_SELECTISH):
                    self.scan(row_source)
                    continue
                if row_source in self.tables or ";" not in row_source:
                    continue
                cs = ctl.get("control_source")
                if not cs or cs.startswith("="):
                    continue
                cols = [
                    k for k in self.resolve(None, cs) if k[0] in rs_tables
                ] or self.resolve(None, cs)
                self._value_list(cols, _split_list_semicolon(row_source))
        # macros and data macros: conditions and where clauses
        for macro in objects.get("macros") or []:
            for action in macro.get("actions") or []:
                for value in (action.get("args") or {}).values():
                    self.scan(value)
        for dm in self.design.datamacros.get("datamacros") or []:
            for action in dm.get("actions") or []:
                for value in (action.get("args") or {}).values():
                    self.scan(value)
        # VBA: comparisons and assignments (comments removed)
        for module in sorted(self.design.code):
            for raw in self.design.code[module].splitlines():
                self.scan(strip_comment(raw), code=True)
        # enums named after a table (its key) or after table + column
        for ename in sorted(self.enums):
            for key in self.enum_targets(ename):
                self.enum_links.setdefault(key, set()).add(ename)

    def enum_targets(self, ename: str) -> list[tuple[str, str]]:
        s = ename[4:] if ename.lower().startswith("enum") else ename
        s = s.lstrip("_").lower()
        if not s:
            return []
        out = []
        for tname, t in sorted(self.tables.items()):
            tl = tname.lower()
            pk = t.get("primary_key") or []
            if len(pk) == 1 and (
                tl in (s, s + "s", s + "es") or (s.endswith("s") and tl == s[:-1])
            ):
                out.append((tname, pk[0]))
            for c in t.get("columns") or []:
                if tl + c["name"].lower() == s:
                    out.append((tname, c["name"]))
        return out

    def raw(self, key) -> list[tuple]:
        """Every token for a column, enum members included, in a stable order."""
        toks = list(self.tokens.get(key, []))
        for ename in sorted(self.enum_links.get(key, set())):
            for value in self.enums.get(ename, {}).values():
                toks.append(("enum", ename, value))
        return toks


def _split_list_semicolon(text: str) -> list[str]:
    items, cur, in_str = [], [], False
    for ch in text:
        if ch == '"':
            in_str = not in_str
            cur.append(ch)
        elif ch == ";" and not in_str:
            items.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    items.append("".join(cur))
    return [i.strip() for i in items if i.strip()]


def _parse_date(text: str):
    text = text.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def coerce(col: Col, token) -> object | None:
    """A token as a value of the column, or None when it does not fit the column (type, size,
    range, date window, validation rule)."""
    kind = token[0]
    value = None
    if kind == "enum":
        raw = token[2]
    elif kind == "like":
        raw = (
            token[1]
            .replace("*", "")
            .replace("%", "")
            .replace("?", "x")
            .replace("#", "0")
        )
    else:
        raw = token[1]
    t = col.type
    try:
        if t in INT_TYPES:
            if kind in ("enum", "num") or (
                kind == "str" and re.fullmatch(r"-?\d+", raw)
            ):
                d = Decimal(str(raw))
                if d == d.to_integral_value():
                    value = int(d)
        elif t in DECIMAL_TYPES:
            if kind in ("enum", "num") or (
                kind == "str" and re.fullmatch(r"-?\d+(\.\d+)?", raw)
            ):
                value = Decimal(str(raw))
        elif t in FLOAT_TYPES:
            if kind in ("enum", "num") or (
                kind == "str" and re.fullmatch(r"-?\d+(\.\d+)?", raw)
            ):
                value = float(Decimal(str(raw)))
        elif t in TEXT_TYPES:
            # codes, not prose: a message text compared with a column is no code
            if (
                kind in ("str", "like")
                and raw.strip()
                and len(raw) <= CODE_MAX_LEN
                and len(raw.split()) <= CODE_MAX_WORDS
            ):
                value = raw
        elif t == "datetime":
            if kind in ("date", "str"):
                value = _parse_date(raw)
                if value is not None and not (
                    WINDOW_START <= value.date() <= WINDOW_END
                ):
                    value = None
        elif t == "yesno":
            if kind == "bool":
                value = bool(raw)
            elif kind in ("num", "enum") and str(raw) in ("0", "-1", "1"):
                value = str(raw) != "0"
    except (InvalidOperation, ValueError):
        return None
    if value is None:
        return None
    if t in TEXT_TYPES and col.size is not None and len(value) > col.size:
        return None
    if t == "autonumber" and value < 1:
        return None
    if not _passes(col, value):
        return None
    try:
        ds.format_value(t, value)
    except ValueError:
        return None
    return value


def _sort_values(col: Col, values) -> list:
    uniq = {}
    for v in values:
        uniq[ds.format_value(col.type, v)] = v
    if col.type in NUMERIC_TYPES:
        return [uniq[k] for k in sorted(uniq, key=lambda k: (Decimal(k), k))]
    return [uniq[k] for k in sorted(uniq)]


# ------------------------------------------------------------------ plan


@dataclass
class TablePlan:
    name: str
    cols: list  # Col, kept columns in schema order
    omitted: list
    nrows: int
    pk: list
    uniques: list  # tuples of column names (primary key first)
    fks: list  # (from columns, parent table, to columns)
    codes: list | None = None  # lookup table: key values, one row each
    pools: dict = field(default_factory=dict)  # column -> fixed code list (copy_values)
    extras: dict = field(default_factory=dict)  # column -> model literals

    def col(self, name: str) -> Col:
        for c in self.cols:
            if c.name == name:
                return c
        raise KeyError(name)


@dataclass
class Plan:
    order: list
    tables: dict
    generated: list  # "Table.Column" whose codes were generated
    from_rules: list  # "Table.Column" whose codes came from rules.json lookups[].values
    schema_sha256: str


def _sha_int(*parts) -> int:
    text = "\x1f".join(str(p) for p in parts)
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def _rng(*parts) -> random.Random:
    return random.Random(_sha_int(*parts))


def _generated_codes(col: Col, count: int) -> list:
    """Codes for a copy_values lookup that neither the model nor rules.json gives."""
    rng = _rng("codes", col.table, col.name)
    if col.type in INT_TYPES:
        lo = 1
        return list(range(lo, lo + count))
    if col.type in DECIMAL_TYPES:
        return [Decimal(i) for i in range(1, count + 1)]
    if col.type in FLOAT_TYPES:
        return [float(i) for i in range(1, count + 1)]
    if col.type in TEXT_TYPES:
        fake = _faker()
        fake.random = rng
        size = col.size or 255
        out: list[str] = []
        while len(out) < count:
            if size <= 6:
                word = "".join(
                    rng.choice(string.ascii_uppercase) for _ in range(min(size, 3))
                )
            else:
                word = fake.word().title()[:size]
            if word not in out:
                out.append(word)
        return out
    if col.type == "yesno":
        return [False, True]
    if col.type == "datetime":
        return [dt.datetime(2024, m, 1) for m in range(1, count + 1)]
    return []


def make_plan(design: Design, rows: int = ROWS) -> Plan:
    schema = design.schema
    lits = Literals(design)
    stables = {
        t["name"]: t
        for t in schema.get("tables") or []
        if t.get("role") != "documentation"
    }
    names = sorted(stables)
    order, _broken = ds.load_order(schema, names)
    cols = {}
    for name in names:
        kept, _om = ds.split_columns(stables[name])
        by = {c["name"]: c for c in stables[name].get("columns") or []}
        cols[name] = {k["name"]: make_col(name, by[k["name"]]) for k in kept}
    rels = [
        r
        for r in schema.get("relationships") or []
        if r.get("from_table") in stables and r.get("to_table") in stables
    ]

    def tokens_for(keys, target: Col) -> list:
        vals = []
        for key in keys:
            for tok in lits.raw(key):
                v = coerce(target, tok)
                if v is not None:
                    vals.append(v)
        return _sort_values(target, vals)

    # copy_values lookups
    targets: dict[tuple, dict] = {}
    for entry in sorted(
        design.lookups, key=lambda e: (str(e.get("table")), str(e.get("column")))
    ):
        if not entry.get("copy_values"):
            continue
        t, c = entry.get("table"), entry.get("column")
        if t not in cols or c not in cols[t]:
            continue
        rel = next(
            (
                r
                for r in rels
                if r["from_table"] == t
                and r["from_columns"] == [c]
                and r["to_table"] != t
                and (stables[r["to_table"]].get("primary_key") or []) == r["to_columns"]
                and r["to_columns"][0] in cols[r["to_table"]]
            ),
            None,
        )
        if rel is not None:
            key = ("table", rel["to_table"], rel["to_columns"][0])
            keys = [(t, c), (rel["to_table"], rel["to_columns"][0])]
        elif (stables[t].get("primary_key") or []) == [c]:
            key = ("table", t, c)
            keys = [(t, c)]
        else:
            key = ("pool", t, c)
            keys = [(t, c)]
        slot = targets.setdefault(key, {"keys": set(), "values": []})
        slot["keys"].update(keys)
        if isinstance(entry.get("values"), list):
            slot["values"].extend(str(v) for v in entry["values"])

    lookup_codes: dict[str, list] = {}
    pools: dict[tuple[str, str], list] = {}
    generated, from_rules = [], []
    # lookup tables first, so a unique pool column knows how many rows its table gets
    for key in sorted(targets, key=lambda k: (k[0] != "table", k)):
        kind, t, c = key
        target = cols[t][c]
        slot = targets[key]
        codes = tokens_for(sorted(slot["keys"]), target)
        if not codes and slot["values"]:
            codes = _sort_values(
                target,
                [
                    v
                    for v in (coerce(target, ("str", s)) for s in slot["values"])
                    if v is not None
                ],
            )
            if codes:
                from_rules.append(f"{t}.{c}")
        need = 1
        if kind == "pool" and _single_unique(stables[t], c):
            need = len(lookup_codes[t]) if t in lookup_codes else rows
        if not codes:
            codes = _generated_codes(target, max(GENERATED_CODES, need))
            generated.append(f"{t}.{c}")
        elif len(codes) < need:
            # a unique column needs one code per row: the design's codes plus generated ones
            have = {ds.format_value(target.type, v) for v in codes}
            extra = [
                v
                for v in _generated_codes(target, need + len(codes))
                if ds.format_value(target.type, v) not in have
            ]
            codes = _sort_values(target, codes + extra[: need - len(codes)])
            generated.append(f"{t}.{c} (partly)")
        if kind == "table":
            lookup_codes[t] = codes
        else:
            pools[(t, c)] = codes

    tables = {}
    for name in order:
        st = stables[name]
        kept, omitted = ds.split_columns(st)
        pk = [p for p in st.get("primary_key") or []]
        uniques = []
        if pk and all(p in cols[name] for p in pk):
            uniques.append(tuple(pk))
        for idx in st.get("indexes") or []:
            u = tuple(idx.get("columns") or [])
            if (
                idx.get("unique")
                and u
                and all(x in cols[name] for x in u)
                and u not in uniques
            ):
                uniques.append(u)
        fks = [
            (tuple(r["from_columns"]), r["to_table"], tuple(r["to_columns"]))
            for r in rels
            if r["from_table"] == name
            and all(x in cols[name] for x in r["from_columns"])
            and all(x in cols[r["to_table"]] for x in r["to_columns"])
        ]
        fks = sorted(set(fks), key=lambda f: (f[0], f[1], f[2]))
        codes = lookup_codes.get(name)
        tp = TablePlan(
            name=name,
            cols=[cols[name][k["name"]] for k in kept],
            omitted=omitted,
            nrows=len(codes) if codes is not None else rows,
            pk=pk,
            uniques=uniques,
            fks=fks,
            codes=codes,
        )
        for col in tp.cols:
            if (name, col.name) in pools:
                tp.pools[col.name] = pools[(name, col.name)]
                continue
            if col.type == "autonumber" or (codes is not None and pk == [col.name]):
                continue
            extra = tokens_for([(name, col.name)], col)
            if extra:
                tp.extras[col.name] = extra
        tables[name] = tp
    return Plan(
        order=order,
        tables=tables,
        generated=generated,
        from_rules=from_rules,
        schema_sha256=design.schema_sha256,
    )


def _single_unique(st: dict, column: str) -> bool:
    if (st.get("primary_key") or []) == [column]:
        return True
    return any(
        idx.get("unique") and (idx.get("columns") or []) == [column]
        for idx in st.get("indexes") or []
    )


# ------------------------------------------------------------------ values

_FAKER: list = []


def _faker() -> Faker:
    if not _FAKER:
        _FAKER.append(Faker("en_US"))
    return _FAKER[0]


@dataclass
class ColState:
    col: Col
    rng: random.Random  # structure: specials, NULLs, duplicates, pool picks
    frng: random.Random  # Faker stream of the column
    forced: dict = field(default_factory=dict)  # row index -> special or ("value", v)
    history: list = field(default_factory=list)
    used: set = field(default_factory=set)  # formatted values (single-column unique)
    unique: bool = False
    dup_ok: bool = False
    is_key: bool = False
    pool: list | None = None
    extras: list = field(default_factory=list)


def _clip(text: str, size: int | None) -> str:
    return text if size is None else text[:size]


def mask_value(mask: tuple, rng: random.Random) -> str:
    pattern, store = mask
    out = []
    i = 0
    first_digit = True
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            if store:
                out.append(pattern[i + 1])
            i += 2
            continue
        if ch == '"':
            j = pattern.find('"', i + 1)
            j = len(pattern) if j < 0 else j
            if store:
                out.append(pattern[i + 1 : j])
            i = j + 1
            continue
        if ch in "09#":
            digit = rng.randint(2, 9) if first_digit else rng.randint(0, 9)
            first_digit = False
            out.append(str(digit))
        elif ch in "LA&":
            out.append(rng.choice(string.ascii_uppercase))
        elif ch in "?aC<>!":
            pass  # optional characters stay empty; case markers are not characters
        elif store:
            out.append(ch)
        i += 1
    return "".join(out).rstrip()


def _text_base(col: Col, fake: Faker, rng: random.Random) -> str:
    n = col.name.lower()
    size = col.size
    if col.type == "hyperlink":
        word = fake.domain_word()
        return f"{word.title()}#https://www.{word}.example.com/#"
    if col.mask is not None:
        return _clip(mask_value(col.mask, rng), size)
    if "email" in n:
        v = fake.safe_email()
    elif "firstname" in n or n == "first":
        v = fake.first_name()
    elif "lastname" in n or n in ("last", "surname"):
        v = fake.last_name()
    elif "phone" in n or "fax" in n:
        v = fake.numerify("555-###-####")
    elif "address" in n:
        v = fake.street_address()
    elif "city" in n:
        v = fake.city()
    elif "zip" in n or "postal" in n:
        v = fake.zipcode()
    elif n.endswith("by") or "user" in n or "login" in n:
        v = fake.user_name()
    elif "jobtitle" in n or n == "job":
        v = fake.job()
    elif "company" in n and "name" in n:
        v = fake.company()
    elif "url" in n or "website" in n:
        v = fake.url()
    elif (size is not None and size <= 5) or "abbrev" in n:
        length = size if size is not None and size <= 3 else min(size or 3, 3)
        v = "".join(rng.choice(string.ascii_uppercase) for _ in range(max(1, length)))
    elif n.endswith(("code", "sku")):
        v = (
            fake.bothify("??-####").upper()
            if (size or 7) >= 7
            else fake.bothify("??##").upper()
        )
    elif col.type == "memo" or "note" in n or "desc" in n or "text" in n:
        v = fake.sentence(nb_words=rng.randint(5, 14))
    elif "name" in n or "title" in n:
        v = " ".join(fake.words(nb=rng.randint(1, 3))).title()
    else:
        v = fake.sentence(nb_words=rng.randint(2, 5)).rstrip(".")
    v = _clip(v, size)
    return v if v.strip() else "x"


def _num_base(col: Col, rng: random.Random):
    n = col.name.lower()
    t = col.type
    if t in INT_TYPES:
        lo, hi = INT_RANGES[t]
        if "quantity" in n or "qty" in n:
            a, b = 1, 100
        elif "level" in n or "reorder" in n or "target" in n or "minimum" in n:
            a, b = 0, 200
        elif n.endswith("id") or "pkvalue" in n:
            a, b = 1, ROWS
        elif t == "byte":
            a, b = 0, 255
        elif t == "integer":
            a, b = 0, 1000
        else:
            a, b = 0, 100000
        return rng.randint(max(a, lo), min(b, hi))
    if t in DECIMAL_TYPES:
        if "price" in n or "cost" in n:
            cents = rng.randint(100, 25000)
        elif "fee" in n:
            cents = rng.randint(0, 6000)
        elif "tax" in n:
            cents = rng.randint(0, 8000)
        elif "amount" in n or "balance" in n or "total" in n:
            cents = rng.randint(1000, 500000)
        else:
            cents = rng.randint(0, 100000)
        scale = col.scale if isinstance(col.scale, int) and 0 <= col.scale <= 2 else 2
        return (Decimal(cents) / 100).quantize(Decimal(1).scaleb(-scale))
    if t == "single":
        if "discount" in n:
            return rng.choice([0.0, 0.05, 0.1, 0.15, 0.2, 0.25])
        if "rate" in n:
            return rng.choice([0.0, 0.05, 0.0625, 0.0825, 0.1])
        return round(rng.uniform(0, 100), 2)
    return round(rng.uniform(0, 10000), 4)


def _clamp(col: Col, value, rng: random.Random):
    """A value that satisfies the column's validation rule."""
    for _ in range(20):
        if _passes(col, value):
            return value
        value = _num_base(col, rng)
        if col.type in NUMERIC_TYPES and not _passes(col, value):
            for op, bound in col.validation:
                if op in (">", ">="):
                    base = bound + (1 if op == ">" else 0)
                    if col.type in INT_TYPES:
                        value = int(base) + rng.randint(0, 50)
                    elif col.type in DECIMAL_TYPES:
                        value = base + Decimal(rng.randint(0, 5000)) / 100
                    else:
                        value = float(base) + round(rng.uniform(0.01, 50), 2)
    return value if _passes(col, value) else None


def _date_base(seed: int, rng: random.Random) -> dt.datetime:
    days = (WINDOW_END - WINDOW_START).days
    d = WINDOW_START + dt.timedelta(days=rng.randint(0, days))
    if seed != 1 and rng.random() < 0.5:
        return dt.datetime(
            d.year,
            d.month,
            d.day,
            rng.randint(0, 23),
            rng.randint(0, 59),
            rng.randint(0, 59),
        )
    return dt.datetime(d.year, d.month, d.day)


def _special(col: Col, special: str, base, fake: Faker, rng: random.Random):
    if special == "null":
        return None
    if special == "empty":
        return ""
    if special == "zero":
        return {"currency": Decimal(0), "decimal": Decimal(0)}.get(
            col.type, 0.0 if col.type in FLOAT_TYPES else 0
        )
    if special == "neg":
        if col.type in INT_TYPES:
            return -rng.randint(1, 100)
        if col.type in DECIMAL_TYPES:
            return -(Decimal(rng.randint(1, 100000)) / 100)
        return -round(rng.uniform(0.01, 100), 2)
    if special == "maxlen":
        size = col.size if col.size is not None and col.size <= MEMO_MAX else MEMO_MAX
        text = str(base or "x")
        while len(text) < size:
            text += " " + fake.word()
        text = text[:size]
        return text[:-1] + "x" if text.endswith(" ") else text
    if special == "nonascii":
        return _clip(f"{rng.choice(NON_ASCII)} {base}", col.size)
    if special == "punct":
        extra = "\r\n" + fake.word() if col.type == "memo" else ""
        return _clip(f'{base}, "{fake.word()}"{extra}', col.size)
    if special == "wstart":
        return dt.datetime(WINDOW_START.year, WINDOW_START.month, WINDOW_START.day)
    if special == "wend":
        return dt.datetime(
            WINDOW_END.year, WINDOW_END.month, WINDOW_END.day, 23, 59, 59
        )
    if special == "monthend":
        year = rng.randint(WINDOW_START.year, WINDOW_END.year)
        month = rng.randint(1, 12)
        return dt.datetime(year, month, calendar.monthrange(year, month)[1])
    if special == "weekend":
        days = (WINDOW_END - WINDOW_START).days
        d = WINDOW_START + dt.timedelta(days=rng.randint(0, days))
        d += dt.timedelta(days=(5 - d.weekday()) % 7)
        if d > WINDOW_END:
            d -= dt.timedelta(days=7)
        return dt.datetime(d.year, d.month, d.day)
    return base


def _specials(col: Col, seed: int, st: ColState) -> list[str]:
    out = []
    if col.type == "datetime" and not st.is_key:
        out += ["monthend", "weekend"]
    if seed != 2 or st.is_key or st.pool is not None:
        if seed == 2 and not col.required and st.pool is not None:
            out.append("null")
        return out
    if not col.required:
        out.append("null")
    if col.type in TEXT_TYPES:
        if col.zero_length:
            out.append("empty")
        if col.type != "hyperlink" and col.mask is None:
            size = col.size
            if size is None or size >= 8:
                out += ["maxlen", "nonascii"]
            if size is None or size >= 16:
                out.append("punct")
    elif col.type in NUMERIC_TYPES:
        if _passes(col, 0):
            out.append("zero")
        if col.type != "byte" and _passes(col, -1):
            out.append("neg")
    elif col.type == "datetime":
        out += ["wstart", "wend"]
    return out


# ------------------------------------------------------------------ tables


def _keyval(col: Col, value) -> str:
    return ds.format_value(col.type, value)


class TableGen:
    def __init__(self, tp: TablePlan, seed: int, done: dict):
        self.tp = tp
        self.seed = seed
        self.done = done
        self.fake = _faker()
        self.rows: list[dict] = []
        self.skipped = 0
        fk_cols = {x for f in tp.fks for x in f[0]}
        unique_cols = {x for u in tp.uniques for x in u}
        single = {u[0] for u in tp.uniques if len(u) == 1}
        self.states: dict[str, ColState] = {}
        for col in tp.cols:
            st = ColState(
                col=col,
                rng=_rng(seed, tp.name, col.name, "plan"),
                frng=_rng(seed, tp.name, col.name),
                unique=col.name in single,
                dup_ok=col.name not in unique_cols and col.name not in tp.pk,
                is_key=col.name in tp.pk or col.name in fk_cols,
                pool=tp.pools.get(col.name),
                extras=list(tp.extras.get(col.name, [])),
            )
            self.states[col.name] = st
        # foreign keys: parent key tuples
        self.fk_parents = []
        for from_cols, parent, to_cols in tp.fks:
            if parent == tp.name:
                self.fk_parents.append((from_cols, parent, to_cols, None))
                continue
            prow = done.get(parent)
            keys = None
            if prow is not None:
                keys = []
                seen = set()
                for r in prow:
                    k = tuple(r.get(c) for c in to_cols)
                    if None in k or k in seen:
                        continue
                    seen.add(k)
                    keys.append(k)
            self.fk_parents.append((from_cols, parent, to_cols, keys))
        self._plan_forced()

    def _plan_forced(self) -> None:
        n = self.tp.nrows
        fk_cols = {x for f in self.tp.fks for x in f[0]}
        for name, st in self.states.items():
            if (
                name in fk_cols
                or name in self.tp.pk
                and (self.tp.codes is not None or st.col.type == "autonumber")
            ):
                continue
            items: list = []
            if st.pool is not None:
                items += [("value", v) for v in st.pool]
            else:
                items += [("value", v) for v in st.extras]
            items += _specials(st.col, self.seed, st)
            if not items or n == 0:
                continue
            positions = st.rng.sample(range(n), min(n, len(items)))
            for pos, item in zip(positions, items):
                st.forced[pos] = item
        # foreign keys: every lookup code (or literal) appears at least once
        for gi, (from_cols, parent, to_cols, keys) in enumerate(self.fk_parents):
            if len(from_cols) != 1 or keys is None:
                continue
            st = self.states[from_cols[0]]
            col = st.col
            ptp_codes = self.done_codes(parent)
            want = []
            if ptp_codes:
                want = [k[0] for k in keys]
            else:
                have = {_keyval(col, k[0]) for k in keys}
                want = [v for v in st.extras if _keyval(col, v) in have]
            items: list = [("value", v) for v in want]
            if self.seed == 2 and not col.required:
                items.append("null")
            st.forced = {}
            if items and n:
                positions = st.rng.sample(range(n), min(n, len(items)))
                for pos, item in zip(positions, items):
                    st.forced[pos] = item

    def done_codes(self, parent: str) -> bool:
        return self.done.get(("codes", parent)) is True

    # -- values
    def value(self, st: ColState, i: int, attempt: int):
        col = st.col
        fake = self.fake
        fake.random = st.frng
        rng = st.rng
        plain = attempt >= PLAIN_ATTEMPTS
        special = None if plain else st.forced.get(i)
        if isinstance(special, tuple):
            return special[1]
        if st.pool is not None:
            if special == "null":
                return None
            if (
                self.seed == 2
                and not col.required
                and rng.random() < NULL_SHARE
                and not plain
            ):
                return None
            return rng.choice(st.pool)
        free = not plain and special is None  # a forced special takes the row
        if free and self.seed == 2 and not col.required and rng.random() < NULL_SHARE:
            return None
        if (
            free
            and self.seed == 3
            and st.dup_ok
            and st.history
            and rng.random() < DUP_SHARE
        ):
            return rng.choice(st.history)
        if free and st.extras and rng.random() < EXTRA_SHARE:
            return rng.choice(st.extras)
        for _ in range(30):
            value = self._base(col, rng, fake)
            if special is not None:
                value = _special(col, special, value, fake, rng)
                if value is not None and not _passes(col, value):
                    value = self._base(col, rng, fake)
                special = None
            if value is None or not st.unique or _keyval(col, value) not in st.used:
                return value
        return self._unique_fallback(st, value)

    def _base(self, col: Col, rng, fake):
        t = col.type
        if t in TEXT_TYPES:
            return _text_base(col, fake, rng)
        if t in NUMERIC_TYPES:
            return _clamp(col, _num_base(col, rng), rng)
        if t == "datetime":
            return _date_base(self.seed, rng)
        if t == "yesno":
            return rng.random() < 0.5
        if t == "guid":
            return str(uuid.UUID(int=rng.getrandbits(128), version=4))
        return None

    def _unique_fallback(self, st: ColState, value):
        col = st.col
        if col.type in TEXT_TYPES:
            base = str(value or "x")
            for k in range(2, 10000):
                sfx = f" {k}"
                cand = _clip(base, (col.size - len(sfx)) if col.size else None) + sfx
                if col.size is not None and len(cand) > col.size:
                    cand = cand[-col.size :]
                if _keyval(col, cand) not in st.used:
                    return cand
        if col.type in INT_TYPES:
            lo, hi = INT_RANGES[col.type]
            for k in range(max(lo, 0), hi + 1):
                if _passes(col, k) and _keyval(col, k) not in st.used:
                    return k
        return value

    def fk_value(self, gi: int, i: int, attempt: int, row: dict):
        from_cols, _parent, to_cols, keys = self.fk_parents[gi]
        cols = [self.states[c] for c in from_cols]
        nullable = all(not s.col.required for s in cols)
        plain = attempt >= PLAIN_ATTEMPTS
        st = cols[0]
        rng = st.rng
        if keys is None:  # self reference: rows accepted so far
            keys = [tuple(r.get(c) for c in to_cols) for r in self.rows]
            keys = [k for k in keys if None not in k]
            if not keys:
                own = tuple(row.get(c) for c in to_cols)
                if nullable or None in own:
                    return (None,) * len(from_cols)
                return own
            if self.seed == 1 and nullable and rng.random() < 0.2:
                return (None,) * len(from_cols)
        if not keys:
            return (None,) * len(from_cols)
        special = None if plain or len(from_cols) != 1 else st.forced.get(i)
        if isinstance(special, tuple) and (special[1],) in keys:
            return (special[1],)
        if special == "null":
            return (None,) * len(from_cols)
        if self.seed == 2 and nullable and not plain and rng.random() < NULL_SHARE:
            return (None,) * len(from_cols)
        if self.seed == 3 and not plain and rng.random() < SKEW_SHARE:
            return keys[rng.randrange(min(2, len(keys)))]
        return rng.choice(keys)

    def make_row(self, i: int, attempt: int) -> dict:
        tp = self.tp
        row: dict = {}
        fk_cols = {x for f in tp.fks for x in f[0]}
        for col in tp.cols:
            if col.name in fk_cols:
                continue
            if tp.codes is not None and tp.pk == [col.name]:
                row[col.name] = tp.codes[i]
            elif col.type == "autonumber":
                row[col.name] = len(self.rows) + 1
            else:
                row[col.name] = self.value(self.states[col.name], i, attempt)
        for gi, (from_cols, _p, _t, _k) in enumerate(self.fk_parents):
            if all(c in row for c in from_cols):
                continue
            values = self.fk_value(gi, i, attempt, row)
            for c, v in zip(from_cols, values):
                row.setdefault(c, v)
        return {c.name: row.get(c.name) for c in tp.cols}

    def run(self) -> list[dict]:
        tp = self.tp
        seen = {u: set() for u in tp.uniques}
        cols = {c.name: c for c in tp.cols}
        for i in range(tp.nrows):
            for attempt in range(MAX_ATTEMPTS):
                row = self.make_row(i, attempt)
                if any(row[c] is None for c in tp.pk):
                    continue
                keys = {
                    u: tuple(
                        ds.NULL if row[c] is None else _keyval(cols[c], row[c])
                        for c in u
                    )
                    for u in tp.uniques
                }
                if any(keys[u] in seen[u] for u in tp.uniques):
                    continue
                for u in tp.uniques:
                    seen[u].add(keys[u])
                for name, st in self.states.items():
                    v = row[name]
                    if v is not None:
                        st.history.append(v)
                        if st.unique:
                            st.used.add(_keyval(st.col, v))
                self.rows.append(row)
                break
            else:
                self.skipped += 1
        return self.rows


# ------------------------------------------------------------------ seeds


@dataclass
class SeedResult:
    meta: dict
    tables: list  # for ds.write_dataset
    skipped: int


def generate(plan: Plan, seed: int) -> SeedResult:
    """The tables of one seed, in load order."""
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 1:
        raise SynthError("seeds are positive integers")
    done: dict = {}
    tables = []
    skipped = 0
    for name in plan.order:
        tp = plan.tables[name]
        gen = TableGen(tp, seed, done)
        rows = gen.run()
        skipped += gen.skipped
        done[name] = rows
        if tp.codes is not None:
            done[("codes", name)] = True
        tables.append(
            {
                "name": name,
                "columns": [{"name": c.name, "type": c.type} for c in tp.cols],
                "omitted_columns": tp.omitted,
                "rows": rows,
            }
        )
    meta = {
        "name": f"seed-{seed}",
        "kind": "synthetic",
        "seed": seed,
        "schema_sha256": plan.schema_sha256,
        "load_order": list(plan.order),
    }
    return SeedResult(meta=meta, tables=tables, skipped=skipped)


def write_seed(plan: Plan, seed: int, directory) -> dict:
    """Write one seed as a dataset directory; returns its dataset.json and the skipped rows."""
    result = generate(plan, seed)
    doc = ds.write_dataset(Path(directory), result.meta, result.tables)
    return {"doc": doc, "skipped": result.skipped}
