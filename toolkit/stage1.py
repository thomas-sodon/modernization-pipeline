"""Stage 1: the Access tables on PostgreSQL and SQL Server (docs/contracts.md#cli-registry, stage1).

`toolkit stage1 <eng> --target-db local:<name>|secret:<ref>|URL [--dataset engagement|DIR|fresh-copy]
[--schema-from auto|model|data-model] [--schema-only] [--replace] [--verify] [--require-rows]
[--dialects from-engagement] [--report FILE]`

- The schema comes from the design zone's `out/model/schema.json`, or for workbooks from
  `analysis/data-model.json` (`--schema-from data-model`; `auto` picks it when `source.kind` is
  xlsm), as SQLAlchemy Core MetaData with every column through `toolkit.typemap`: original
  table and column names, primary keys, unique indexes, literal defaults (and Now() as
  CURRENT_TIMESTAMP), and foreign keys for relationships with enforced integrity, keeping their
  cascade rules. On SQL Server a second cascade path to a table becomes NO ACTION. Everything
  not created is listed in the report. A data model builds the schema only (`--schema-only`).
- Rows come from a dataset directory (`toolkit.dataset`, contracts section 7) or, for the cutover,
  from the back-end file at `database.stage1.fresh_copy` (`toolkit.dataset.open_backend`). They
  are inserted in foreign-key order in batches of 1000, keeping autonumber values; foreign keys
  are added after the load and identities are moved past the maximum.
- `--verify` checks tables, columns, mapped types, keys, unique indexes, foreign keys, row counts,
  a sha256 per table over the sorted canonical rows (`format_value`) on both sides, foreign-key
  orphans and identities.

Output holds names, counts and sha256s of input files, never values. A `secret:` target's URL is
never printed, logged or written; it is named by its dialect only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import schema as sa_schema
from sqlalchemy.dialects import mssql as sa_mssql
from sqlalchemy.dialects import postgresql as sa_pg

from harness.dataset import csv_record, sha256_file
from toolkit import dataset as tds
from toolkit import typemap
from toolkit.secrets import SecretError
from toolkit.secrets import resolve as resolve_secret

COMMAND = "stage1"
BATCH = 1000
DIALECTS = typemap.DIALECTS
TITLES = typemap.DIALECT_TITLES
DRIVERS = {"postgresql": "postgresql+psycopg", "mssql": "mssql+mssqlpython"}
URL_SCHEMES = {"postgresql": "postgresql", "postgres": "postgresql", "mssql": "mssql"}
SERVER_ENV = {
    "postgresql": ("MP_DATA_PG_URL", "PG_URL"),
    "mssql": ("MP_DATA_MSSQL_URL", "MSSQL_URL"),
}
MAINTENANCE_DB = {"postgresql": "postgres", "mssql": "master"}
ALLOWED_OWNERS = ("postgres", "sa", "mp_runner")
LOCAL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
SECRET_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
TRUSTED_HOST = "127.0.0.1"
MAX_NAME_BYTES = typemap.PG_MAX_IDENTIFIER_BYTES

ENGAGEMENT_ONLY = (
    "engagement data loads only into mpdata_* databases or a secret: target"
)
DATA_MODEL_FILE = Path("analysis") / "data-model.json"
DATA_MODEL_MISSING = "analysis/data-model.json missing: run S1 (S1.synthesize) first"
DATA_MODEL_ROWS = (
    "--schema-from data-model builds the schema only (add --schema-only); workbook rows load "
    "through the app's import_dataset and legacy/import_map.py"
)
TARGET_DIFFERS = "target differs; use --replace"
SECRET_NOT_EMPTY = "target not empty; ask the DBA to drop and recreate it"

OWNER_SQL = {
    "postgresql": "SELECT pg_get_userbyid(datdba) AS owner FROM pg_database WHERE datname = :name",
    "mssql": "SELECT SUSER_SNAME(owner_sid) AS owner FROM sys.databases WHERE name = :name",
}
_PREPARERS = {
    "postgresql": sa_pg.dialect().identifier_preparer,
    "mssql": sa_mssql.dialect().identifier_preparer,
}

NUMERIC_TYPES = (
    "byte",
    "integer",
    "longinteger",
    "single",
    "double",
    "currency",
    "decimal",
)
INT_TYPES = ("byte", "integer", "longinteger")
_YES = {"yes", "true", "-1", "on"}
_NO = {"no", "false", "0", "off"}
_NUMBER = re.compile(r"[+-]?\d+(\.\d+)?")
_SCRUB_TOKEN = re.compile(r"<[A-Z]+-\d+>")
_NOW = {"now()", "now"}


class Stage1Error(Exception):
    """Exit 2 (usage, missing input, refused target) or 1 (a check failed). str() is safe to
    print: names and counts, never values or a secret URL."""

    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


# ------------------------------------------------------------------ target URLs


@dataclass(frozen=True)
class TargetSpec:
    kind: str  # local | secret | url
    value: str  # database name, secret field name or URL text


def parse_target(text: str) -> TargetSpec:
    """--target-db as local:<name>, secret:<ref> or a postgresql:// or mssql:// URL that names one
    database. Messages never repeat a URL."""
    if text.startswith("local:"):
        name = text[len("local:") :]
        if not LOCAL_NAME_RE.fullmatch(name):
            raise Stage1Error(
                "--target-db local:<name>: the name must match ^[a-z][a-z0-9_]{0,62}$"
            )
        return TargetSpec("local", name)
    if text.startswith("secret:"):
        ref = text[len("secret:") :]
        if not SECRET_NAME_RE.fullmatch(ref):
            raise Stage1Error("--target-db secret:<ref>: bad reference name")
        return TargetSpec("secret", ref)
    scheme = text.split("://", 1)[0] if "://" in text else None
    if scheme in URL_SCHEMES:
        url_database(text)
        return TargetSpec("url", text)
    raise Stage1Error(
        "--target-db must be local:<name>, secret:<ref> or a postgresql:// or mssql:// URL"
    )


def url_dialect(text: str) -> str:
    scheme = text.split("://", 1)[0] if "://" in text else None
    if scheme not in URL_SCHEMES:
        raise Stage1Error("database URL: not a postgresql:// or mssql:// URL")
    return URL_SCHEMES[scheme]


def url_database(text: str) -> str:
    """The one database a URL names (its path); exit 2 without repeating the URL otherwise."""
    try:
        path = urllib.parse.urlsplit(text).path
    except ValueError:
        raise Stage1Error("database URL: cannot be parsed") from None
    name = urllib.parse.unquote(path.lstrip("/"))
    if not name or "/" in name:
        raise Stage1Error("database URL: must name exactly one database")
    return name


def to_sa_url(text: str, database: str | None = None) -> sa.URL:
    """postgresql:// becomes postgresql+psycopg and mssql:// mssql+mssqlpython (Encrypt=yes,
    TrustServerCertificate=yes only for 127.0.0.1). `database` replaces the URL's path."""
    dialect = url_dialect(text)
    rest = text.split("://", 1)[1]
    try:
        url = sa.engine.make_url(f"{DRIVERS[dialect]}://{rest}")
    except Exception:  # noqa: BLE001 - the parser's message would repeat the URL
        raise Stage1Error("database URL: cannot be parsed") from None
    if database is not None:
        url = url.set(database=database)
    if dialect == "mssql":
        query = {"Encrypt": "yes"}
        if url.host == TRUSTED_HOST:
            query["TrustServerCertificate"] = "yes"
        url = url.update_query_dict(query)
    return url


def make_engine(url: sa.URL):
    """The one place that opens a database (tests replace it)."""
    return sa.create_engine(url, poolclass=sa.pool.NullPool)


def resolve_dialects(spec: str, eng: dict) -> list[str]:
    if spec == "from-engagement":
        db = eng.get("database") or {}
        wanted = [db.get("primary"), *(db.get("verify_also") or [])]
    else:
        wanted = [w.strip() for w in spec.split(",") if w.strip()]
    for d in wanted:
        if d not in DIALECTS:
            raise Stage1Error(f"--dialects: unknown dialect {d!r}")
    out = [d for d in DIALECTS if d in wanted]
    if not out:
        raise Stage1Error("--dialects: no dialect selected")
    return out


@dataclass
class Endpoint:
    dialect: str
    kind: str  # local | secret | url
    database: str | None  # None for secret: targets (never shown)
    url: sa.URL = field(repr=False)
    server: sa.URL | None = field(default=None, repr=False)
    exists: bool | None = None
    owner: str | None = None

    @property
    def title(self) -> str:
        return TITLES[self.dialect]

    def label(self) -> str:
        if self.kind == "secret":
            return f"{self.title} (secret target)"
        return f"{self.title} {self.database}"


def server_url_text(dialect: str, env) -> str:
    first, second = SERVER_ENV[dialect]
    value = env.get(first) or env.get(second)
    if not value:
        raise Stage1Error(f"{dialect}: neither {first} nor {second} is set")
    return value


def endpoints(
    spec: TargetSpec, dialects_arg: str | None, eng: dict, env
) -> list[Endpoint]:
    if spec.kind == "local":
        if not dialects_arg:
            raise Stage1Error(
                "--target-db local:<name> needs --dialects from-engagement"
            )
        out = []
        for d in resolve_dialects(dialects_arg, eng):
            text = server_url_text(d, env)
            out.append(
                Endpoint(
                    d,
                    "local",
                    spec.value,
                    to_sa_url(text, spec.value),
                    to_sa_url(text, MAINTENANCE_DB[d]),
                )
            )
        return out
    if dialects_arg:
        raise Stage1Error(
            f"--dialects is refused with a {spec.kind} target: the URL scheme names the dialect"
        )
    if spec.kind == "url":
        d = url_dialect(spec.value)
        name = url_database(spec.value)
        return [
            Endpoint(
                d,
                "url",
                name,
                to_sa_url(spec.value),
                to_sa_url(spec.value, MAINTENANCE_DB[d]),
            )
        ]
    field_name = f"secrets.{spec.value}"
    secrets = eng.get("secrets") or {}
    ref = secrets.get(spec.value)
    if not isinstance(ref, str) or not ref:
        raise Stage1Error(f"--target-db secret:{spec.value}: {field_name} is not set")
    try:
        text = resolve_secret(ref, field_name)
    except SecretError as exc:
        raise Stage1Error(str(exc)) from None
    scheme = text.split("://", 1)[0] if "://" in text else None
    if scheme not in URL_SCHEMES:
        raise Stage1Error(f"{field_name}: not a postgresql:// or mssql:// URL")
    d = URL_SCHEMES[scheme]
    url_database(text)
    return [Endpoint(d, "secret", None, to_sa_url(text))]


# ------------------------------------------------------------------ design


@dataclass
class Design:
    path: Path
    sha256: str
    schema: dict
    tables: list[dict]
    label: str = "schema.json"  # the input's name in summaries and reports

    @property
    def by_name(self) -> dict:
        return {t["name"]: t for t in self.tables}


def schema_path(eng: dict) -> Path:
    return Path(eng["paths"]["private"]) / tds.SCHEMA_FILE


def data_model_path(eng: dict) -> Path:
    return Path(eng["paths"]["target"]) / DATA_MODEL_FILE


def uses_data_model(eng: dict, schema_from: str) -> bool:
    """True for --schema-from data-model, and for auto when source.kind is xlsm."""
    kind = (eng.get("source") or {}).get("kind")
    return schema_from == "data-model" or (schema_from == "auto" and kind == "xlsm")


def _read_json(path: Path, label: str) -> tuple[bytes, dict]:
    raw = path.read_bytes()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Stage1Error(f"{label} is not valid JSON") from None
    if not isinstance(doc, dict) or doc.get("format_version") != 1:
        raise Stage1Error(f"{label}: unknown format_version")
    return raw, doc


def load_data_model(eng: dict) -> Design:
    """The workbook data model (contracts section 8) as a Design: its tables and relationships
    have the schema.json shapes, so build_plan maps them the same way."""
    from toolkit.rules import lint_data_model

    path = data_model_path(eng)
    if not path.is_file():
        raise Stage1Error(DATA_MODEL_MISSING)
    raw, doc = _read_json(path, "analysis/data-model.json")
    problems = lint_data_model(doc)
    if problems:
        shown = "; ".join(f"{ptr or '/'}: {text}" for ptr, text in problems[:5])
        raise Stage1Error(
            f"analysis/data-model.json fails schema-lint ({len(problems)} problem(s)): {shown}"
        )
    tables = [t for t in doc["tables"] if t.get("role", "data") != "documentation"]
    schema = {"tables": tables, "relationships": doc.get("relationships") or []}
    return Design(
        path, hashlib.sha256(raw).hexdigest(), schema, tables, "data-model.json"
    )


def load_design(eng: dict, schema_from: str) -> Design:
    if uses_data_model(eng, schema_from):
        return load_data_model(eng)
    path = schema_path(eng)
    if not path.is_file():
        raise Stage1Error("model schema.json missing: run parse first")
    raw, schema = _read_json(path, "model schema.json")
    tables = [
        t
        for t in schema.get("tables") or []
        if t.get("role", "data") != "documentation"
    ]
    return Design(path, hashlib.sha256(raw).hexdigest(), schema, tables)


def kept_columns(table: dict) -> list[dict]:
    columns, _omitted = tds.split_columns(table)
    return columns


# ------------------------------------------------------------------ plan (MetaData per dialect)


def short_name(name: str) -> str:
    """A constraint or index name of at most 63 bytes (PostgreSQL's limit)."""
    raw = name.encode("utf-8")
    if len(raw) <= MAX_NAME_BYTES:
        return name
    digest = hashlib.sha256(raw).hexdigest()[:8]
    return raw[: MAX_NAME_BYTES - 9].decode("utf-8", "ignore") + "_" + digest


def default_clause(column: dict):
    """(server default or None, reason it is not created or None) for a model column."""
    props = column.get("properties") or {}
    raw = props.get("DefaultValue")
    if raw is None or str(raw).strip() == "":
        return None, None
    text = str(raw).strip()
    ctype = column["type"]
    if ctype == "autonumber":
        return None, "default on an autonumber column"
    if _SCRUB_TOKEN.search(text):
        return None, "default holds a scrubbed token"
    low = text.lower()
    if ctype == "yesno":
        if low in _YES:
            return sa.true(), None
        if low in _NO:
            return sa.false(), None
        return None, "expression default"
    if ctype in NUMERIC_TYPES:
        if _NUMBER.fullmatch(text) and (ctype not in INT_TYPES or "." not in text):
            return sa.text(text.lstrip("+")), None
        return None, "expression default"
    if ctype in tds.TEXT_TYPES:
        if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
            quote, inner = text[0], text[1:-1]
            if quote not in inner.replace(quote * 2, ""):
                return inner.replace(quote * 2, quote), None
        return None, "expression default"
    if ctype == "datetime" and low in _NOW:
        return sa.func.current_timestamp(), None
    return None, "expression default"


def _with_default(col: sa.Column, clause) -> sa.Column:
    return sa.Column(
        col.name,
        col.type,
        primary_key=col.primary_key,
        nullable=col.nullable,
        autoincrement=col.autoincrement,
        server_default=clause,
    )


@dataclass
class Relation:
    name: str
    child: str
    columns: tuple
    parent: str
    parent_columns: tuple


@dataclass
class Plan:
    dialect: str  # the engine's dialect name (postgresql, mssql, sqlite in tests)
    metadata: sa.MetaData
    tables: dict  # name -> sa.Table, in name order
    load_columns: dict  # name -> [{name, type}] created columns that rows fill
    identity: dict  # table -> autonumber column
    fks: list  # sa.ForeignKeyConstraint, added after the load (inline on sqlite)
    inline_fks: bool
    expected_pk: dict
    expected_unique: dict  # table -> {(name, (cols))}
    expected_fks: dict  # table -> {(name, (cols), parent, (cols), ondelete, onupdate)}
    orphan_checks: list  # Relation: every enforced relationship whose columns exist
    omitted: list  # (table, column, type, reason)
    not_created: list  # (kind, object, reason)
    cascade_no_action: list  # relationship names (SQL Server)
    added_columns: list  # (table, column)
    design_label: str = "schema.json"  # schema.json or data-model.json, for messages


def _cascade_conflict(graph: dict, parent: str, child: str) -> bool:
    """True when the edge parent->child gives some table a second cascade path or a cycle."""
    if parent == child:
        return True
    edges = {k: set(v) for k, v in graph.items()}
    edges.setdefault(parent, set()).add(child)
    nodes = set(edges) | {c for v in edges.values() for c in v}
    # cycle check (DFS colouring)
    state: dict = {}

    def cyclic(n) -> bool:
        state[n] = 1
        for m in edges.get(n, ()):
            s = state.get(m)
            if s == 1 or (s is None and cyclic(m)):
                return True
        state[n] = 2
        return False

    for n in sorted(nodes):
        if state.get(n) is None and cyclic(n):
            return True
    # number of paths from each node to every node it reaches
    memo: dict = {}

    def paths(n) -> dict:
        if n in memo:
            return memo[n]
        out: dict = {}
        for m in edges.get(n, ()):
            out[m] = out.get(m, 0) + 1
            for k, v in paths(m).items():
                out[k] = out.get(k, 0) + v
        memo[n] = out
        return out

    return any(v > 1 for n in nodes for v in paths(n).values())


def build_plan(design: Design, dialect: str) -> Plan:
    md = sa.MetaData()
    plan = Plan(
        dialect=dialect,
        metadata=md,
        tables={},
        load_columns={},
        identity={},
        fks=[],
        inline_fks=dialect not in DIALECTS,
        expected_pk={},
        expected_unique={},
        expected_fks={},
        orphan_checks=[],
        omitted=[],
        not_created=[],
        cascade_no_action=[],
        added_columns=[],
        design_label=design.label,
    )
    unique_cols: dict = {}
    for t in sorted(design.tables, key=lambda x: x["name"]):
        tname = t["name"]
        pk = list(t.get("primary_key") or [])
        cols: list[sa.Column] = []
        load: list[dict] = []
        for c in t.get("columns") or []:
            if typemap.is_omitted(c) or c.get("computed"):
                reason = "computed" if c.get("computed") else c["type"]
                plan.omitted.append((tname, c["name"], c["type"], reason))
                continue
            col = typemap.sa_column(c, primary_key=c["name"] in pk)
            if c["type"] != "autonumber":
                # Only an AutoNumber gets an identity: SQLAlchemy would otherwise make a
                # single integer key that Access numbers by hand (a Byte or Long Integer
                # key) SERIAL on PostgreSQL and IDENTITY on SQL Server.
                col.autoincrement = False
            clause, why = default_clause(c)
            if why:
                plan.not_created.append(("default", f"{tname}.{c['name']}", why))
            if clause is not None:
                col = _with_default(col, clause)
            cols.append(col)
            load.append({"name": c["name"], "type": c["type"]})
            if c["type"] == "autonumber":
                plan.identity[tname] = c["name"]
            if (c.get("properties") or {}).get("ValidationRule"):
                plan.not_created.append(
                    ("validation rule", f"{tname}.{c['name']}", "field validation rule")
                )
        if dialect in DIALECTS:
            for extra in typemap.extra_columns(t, dialect):
                cols.append(extra)
                plan.added_columns.append((tname, extra.name))
        names = {c.name for c in cols}
        args = []
        if pk and all(p in names for p in pk):
            args.append(sa.PrimaryKeyConstraint(*pk, name=short_name(f"{tname}_pkey")))
            plan.expected_pk[tname] = pk
        elif pk:
            plan.not_created.append(("primary key", tname, "a key column is omitted"))
            for col in cols:
                col.primary_key = False
        else:
            plan.not_created.append(("primary key", tname, "the table has none"))
        if (t.get("properties") or {}).get("ValidationRule"):
            plan.not_created.append(("validation rule", tname, "table validation rule"))
        table = sa.Table(tname, md, *cols, *args)
        plan.tables[tname] = table
        plan.load_columns[tname] = load
        plan.expected_unique[tname] = set()
        plan.expected_fks[tname] = set()
        unique_cols[tname] = [frozenset(pk)] if tname in plan.expected_pk else []
        for idx in sorted(t.get("indexes") or [], key=lambda i: i["name"]):
            if idx.get("primary"):
                continue
            iname = f"{tname}.{idx['name']}"
            icols = list(idx.get("columns") or [])
            if not idx.get("unique"):
                plan.not_created.append(("index", iname, "not unique"))
                continue
            if not icols or any(c not in names for c in icols):
                plan.not_created.append(("index", iname, "a column is omitted"))
                continue
            if icols == pk:
                plan.not_created.append(
                    ("index", iname, "same columns as the primary key")
                )
                continue
            nullable = [c for c in icols if table.c[c].nullable]
            kw = {}
            if dialect == "mssql" and nullable:
                # Access and PostgreSQL let a unique index hold several Nulls; SQL Server needs a
                # filtered index for that.
                kw["mssql_where"] = sa.and_(*[table.c[c].isnot(None) for c in nullable])
            name = short_name(f"{tname}_{idx['name']}")
            sa.Index(name, *[table.c[c] for c in icols], unique=True, **kw)
            plan.expected_unique[tname].add((name, tuple(icols)))
            if not (dialect == "mssql" and nullable):
                unique_cols[tname].append(frozenset(icols))
    graph: dict = {}
    for r in sorted(design.schema.get("relationships") or [], key=lambda x: x["name"]):
        name = r["name"]
        child, parent = r.get("from_table"), r.get("to_table")
        fcols, pcols = (
            tuple(r.get("from_columns") or []),
            tuple(r.get("to_columns") or []),
        )
        if not r.get("enforce"):
            plan.not_created.append(
                ("relationship", name, "integrity not enforced in Access")
            )
            continue
        if child not in plan.tables or parent not in plan.tables:
            plan.not_created.append(
                ("relationship", name, "a table is not in the schema")
            )
            continue
        ct, pt = plan.tables[child], plan.tables[parent]
        if (
            not fcols
            or len(fcols) != len(pcols)
            or any(c not in ct.c for c in fcols)
            or any(c not in pt.c for c in pcols)
        ):
            plan.not_created.append(("relationship", name, "a column is omitted"))
            continue
        plan.orphan_checks.append(Relation(name, child, fcols, parent, pcols))
        if frozenset(pcols) not in unique_cols[parent]:
            plan.not_created.append(
                (
                    "relationship",
                    name,
                    "no primary key or unique index on the referenced columns",
                )
            )
            continue
        ondelete = "CASCADE" if r.get("cascade_delete") else None
        onupdate = "CASCADE" if r.get("cascade_update") else None
        if dialect == "mssql" and (ondelete or onupdate):
            if _cascade_conflict(graph, parent, child):
                ondelete = onupdate = None
                plan.cascade_no_action.append(name)
            else:
                graph.setdefault(parent, set()).add(child)
        fname = short_name(name)
        fk = sa.ForeignKeyConstraint(
            list(fcols),
            [pt.c[c] for c in pcols],
            name=fname,
            ondelete=ondelete,
            onupdate=onupdate,
        )
        ct.append_constraint(fk)
        plan.fks.append(fk)
        plan.expected_fks[child].add((fname, fcols, parent, pcols, ondelete, onupdate))
    return plan


def table_elements(plan: Plan) -> list:
    out = []
    for table in plan.tables.values():
        fks = None if plan.inline_fks else []
        out.append(sa_schema.CreateTable(table, include_foreign_key_constraints=fks))
        for idx in sorted(table.indexes, key=lambda i: i.name):
            out.append(sa_schema.CreateIndex(idx))
    return out


def fk_elements(plan: Plan) -> list:
    if plan.inline_fks:
        return []
    return [sa_schema.AddConstraint(fk) for fk in plan.fks]


def ddl_text(plan: Plan, dialect) -> str:
    """Every DDL statement stage1 runs for this plan, compiled for `dialect`, one per paragraph."""
    parts = [
        str(el.compile(dialect=dialect)).strip()
        for el in table_elements(plan) + fk_elements(plan)
    ]
    return "\n\n".join(parts) + "\n"


# ------------------------------------------------------------------ dataset sources


class DirSource:
    """A dataset directory (contracts section 7)."""

    kind = "dir"
    sha_name = "dataset.json"

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.meta = tds.read_meta(self.path)
        except tds.DatasetError as exc:
            raise Stage1Error(f"dataset: {exc}", exc.exit_code) from None
        self.entries = {t["name"]: t for t in self.meta.get("tables") or []}
        self.sha256 = hashlib.sha256(
            (self.path / "dataset.json").read_bytes()
        ).hexdigest()

    def table_names(self) -> list[str]:
        order = [n for n in self.meta.get("load_order") or [] if n in self.entries]
        return order + sorted(n for n in self.entries if n not in order)

    def columns(self, table: str) -> list[dict]:
        return [
            {"name": c["name"], "type": c["type"]}
            for c in self.entries[table]["columns"]
        ]

    def row_count(self, table: str) -> int:
        return int(self.entries[table].get("rows") or 0)

    def rows(self, table: str):
        try:
            yield from tds.iter_table(self.path, self.entries[table])
        except tds.DatasetError as exc:
            raise Stage1Error(f"dataset: {exc}", 1) from None


class BackendSource:
    """The fresh-copy back-end file, read through toolkit.dataset.open_backend after its tables,
    columns and types are checked against schema.json."""

    kind = "fresh-copy"
    sha_name = "fresh-copy"

    def __init__(self, path: Path, design: Design):
        from toolkit.sources import SourceError
        from toolkit.sources.accdb import compare_data_copy

        self.path = Path(path)
        if not self.path.is_file():
            raise Stage1Error("database.stage1.fresh_copy: file missing")
        try:
            compared = compare_data_copy(self.path, {"tables": design.tables})
        except SourceError as exc:
            raise Stage1Error(f"fresh copy: {exc}") from None
        if compared["differences"]:
            names = ", ".join(compared["differences"][:20])
            raise Stage1Error(
                f"fresh copy differs from schema.json in {len(compared['differences'])} "
                f"table(s): {names}",
                1,
            )
        try:
            self.reader = tds.open_backend(self.path, design.schema)
        except SourceError as exc:
            raise Stage1Error(f"fresh copy: {exc}") from None
        self.design = design
        self._columns = {t["name"]: kept_columns(t) for t in design.tables}
        self._counts: dict = {}
        self.sha256 = sha256_file(self.path)

    def table_names(self) -> list[str]:
        order, _broken = tds.load_order(self.design.schema, list(self._columns))
        return order

    def columns(self, table: str) -> list[dict]:
        return self._columns[table]

    def row_count(self, table: str) -> int:
        if table not in self._counts:
            self._counts[table] = sum(1 for _ in self.reader.iter_rows(table))
        return self._counts[table]

    def rows(self, table: str):
        cols = self._columns[table]
        for number, row in enumerate(self.reader.iter_rows(table), start=1):
            out = {}
            for c in cols:
                try:
                    out[c["name"]] = tds.parse_value(
                        c["type"], tds.format_value(c["type"], row.get(c["name"]))
                    )
                except ValueError as exc:
                    raise Stage1Error(
                        f"fresh copy: {table}: row {number} column {c['name']}: {exc}",
                        1,
                    ) from None
            yield out


def source_problems(design: Design, source) -> list[str]:
    """Tables and columns of the dataset against schema.json (names only)."""
    problems = []
    names = set(source.table_names())
    for t in design.tables:
        if t["name"] not in names:
            problems.append(f"dataset lacks table {t['name']}")
            continue
        want = [(c["name"], c["type"]) for c in kept_columns(t)]
        got = [(c["name"], c["type"]) for c in source.columns(t["name"])]
        if want != got:
            problems.append(f"{t['name']}: dataset columns differ from schema.json")
    known = {t["name"] for t in design.tables}
    for n in sorted(names - known):
        problems.append(f"dataset table {n} is not in schema.json")
    return problems


def is_engagement_data(dataset_arg: str, eng: dict) -> bool:
    if dataset_arg in ("engagement", "fresh-copy"):
        return True
    private = Path(os.path.realpath(eng["paths"]["private"]))
    real = Path(os.path.realpath(dataset_arg))
    return real == private or private in real.parents


def open_source(dataset_arg: str, eng: dict, design: Design):
    if dataset_arg == "fresh-copy":
        stage1 = (eng.get("database") or {}).get("stage1") or {}
        path = stage1.get("fresh_copy") if isinstance(stage1, dict) else None
        if not path:
            raise Stage1Error(
                "--dataset fresh-copy: database.stage1.fresh_copy is not set"
            )
        return BackendSource(Path(path), design)
    if dataset_arg == "engagement":
        path = Path(eng["paths"]["private"]) / "datasets" / "engagement"
    else:
        path = Path(dataset_arg)
    if not (path / "dataset.json").is_file():
        raise Stage1Error("dataset: dataset.json missing")
    return DirSource(path)


# ------------------------------------------------------------------ server side


def quote_db(dialect: str, name: str) -> str:
    return _PREPARERS[dialect].quote_identifier(name)


def create_database_sql(dialect: str, name: str) -> str:
    return f"CREATE DATABASE {quote_db(dialect, name)}"


def database_state(ep: Endpoint) -> None:
    """Fill ep.exists and ep.owner from the server's catalog (maintenance database)."""
    engine = make_engine(ep.server)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                sa.text(OWNER_SQL[ep.dialect]), {"name": ep.database}
            ).first()
    finally:
        engine.dispose()
    ep.exists = row is not None
    ep.owner = None if row is None else row[0]


def create_database(ep: Endpoint) -> None:
    engine = make_engine(ep.server)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(create_database_sql(ep.dialect, ep.database))
    finally:
        engine.dispose()
    ep.exists = True


# ------------------------------------------------------------------ load


def _bind(ctype: str, value):
    if value is not None and ctype == "guid" and not isinstance(value, uuid.UUID):
        return uuid.UUID(str(value))
    return value


def drop_tables(conn) -> int:
    """Drop every table of the default schema, foreign keys first. Returns the count."""
    md = sa.MetaData()
    md.reflect(conn)
    if conn.dialect.name != "sqlite":
        for table in md.tables.values():
            for fk in table.foreign_key_constraints:
                if fk.name:
                    conn.execute(sa_schema.DropConstraint(fk))
        for table in md.tables.values():
            conn.execute(sa_schema.DropTable(table))
    else:
        md.drop_all(conn)
    return len(md.tables)


def _quote_table(conn, name: str) -> str:
    return conn.dialect.identifier_preparer.quote_identifier(name)


def _insert(conn, table: sa.Table, rows: list, identity_insert: bool) -> None:
    if identity_insert:
        conn.exec_driver_sql(f"SET IDENTITY_INSERT {_quote_table(conn, table.name)} ON")
    conn.execute(table.insert(), rows)


def load_rows(conn, plan: Plan, source) -> dict:
    """Insert every source table in foreign-key order, in batches of 1000, keeping autonumber
    values (SET IDENTITY_INSERT on SQL Server; GENERATED BY DEFAULT takes them on PostgreSQL).
    Returns {table: rows inserted}."""
    counts = {}
    for name in source.table_names():
        table = plan.tables[name]
        cols = plan.load_columns[name]
        identity_insert = conn.dialect.name == "mssql" and name in plan.identity
        n = 0
        batch: list = []
        for row in source.rows(name):
            batch.append(
                {c["name"]: _bind(c["type"], row.get(c["name"])) for c in cols}
            )
            n += 1
            if len(batch) >= BATCH:
                _insert(conn, table, batch, identity_insert)
                batch = []
        if batch:
            _insert(conn, table, batch, identity_insert)
        if identity_insert:
            conn.exec_driver_sql(f"SET IDENTITY_INSERT {_quote_table(conn, name)} OFF")
        counts[name] = n
    return counts


def reset_identities(conn, plan: Plan) -> None:
    """Move each identity (SQL Server) or identity sequence (PostgreSQL) past the maximum."""
    d = conn.dialect.name
    if d not in DIALECTS:
        return
    for name, col in plan.identity.items():
        table = plan.tables[name]
        top = conn.execute(sa.select(sa.func.max(table.c[col]))).scalar()
        if top is None:
            continue
        if d == "postgresql":
            seq = sa.func.pg_get_serial_sequence(_quote_table(conn, name), col)
            conn.execute(sa.select(sa.func.setval(seq, int(top), True)))
        else:
            literal = "'" + _quote_table(conn, name).replace("'", "''") + "'"
            conn.exec_driver_sql(
                f"DBCC CHECKIDENT ({literal}, RESEED, {int(top)}) WITH NO_INFOMSGS"
            )


# ------------------------------------------------------------------ checks


def type_text(typ, dialect) -> str:
    text = typ.compile(dialect=dialect)
    text = re.sub(r"\s+COLLATE\s+\S+", "", text)
    return re.sub(r"\s+", " ", text).strip().upper()


def _datetime2_precision(conn, table: str) -> dict:
    rows = conn.execute(
        sa.text(
            "SELECT COLUMN_NAME, DATETIME_PRECISION FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = SCHEMA_NAME() AND TABLE_NAME = :t AND DATA_TYPE = 'datetime2'"
        ),
        {"t": table},
    ).all()
    return {r[0]: r[1] for r in rows}


def _norm_action(value):
    if value is None:
        return None
    value = str(value).upper()
    return None if value in ("NO ACTION", "RESTRICT") else value


def canonical_digest(rows, columns: list[dict]) -> tuple[int, str]:
    """(row count, sha256 over the sorted canonical CSV records of the rows)."""
    records = []
    for row in rows:
        fields = [tds.format_value(c["type"], row[c["name"]]) for c in columns]
        records.append(csv_record(fields).encode("utf-8"))
    records.sort()
    h = hashlib.sha256()
    for r in records:
        h.update(r)
    return len(records), h.hexdigest()


def structure_problems(conn, plan: Plan, label: str) -> tuple[list[str], set]:
    """Tables, columns, mapped types, primary keys, unique indexes and foreign keys."""
    problems: list[str] = []
    insp = sa.inspect(conn)
    present = set(insp.get_table_names())
    expected = set(plan.tables)
    for t in sorted(expected - present):
        problems.append(f"{label}: table {t} missing")
    for t in sorted(present - expected):
        problems.append(f"{label}: table {t} is not in {plan.design_label}")
    dialect = conn.dialect
    for name in sorted(expected & present):
        table = plan.tables[name]
        reflected = {c["name"]: c for c in insp.get_columns(name)}
        precision = _datetime2_precision(conn, name) if dialect.name == "mssql" else {}
        want = [c for c in table.columns if c.name != typemap.ROWVERSION_COLUMN]
        for c in want:
            got = reflected.get(c.name)
            if got is None:
                problems.append(f"{label}: {name}.{c.name} missing")
                continue
            exp = type_text(c.type, dialect)
            have = type_text(got["type"], dialect)
            if have == "DATETIME2" and c.name in precision:
                have = f"DATETIME2({precision[c.name]})"
            if exp != have:
                problems.append(f"{label}: {name}.{c.name} type differs")
        for cname in sorted(set(reflected) - {c.name for c in table.columns}):
            if cname != typemap.ROWVERSION_COLUMN:
                problems.append(
                    f"{label}: {name}.{cname} is not in {plan.design_label}"
                )
        pk = insp.get_pk_constraint(name).get("constrained_columns") or []
        if list(pk) != list(plan.expected_pk.get(name, [])):
            problems.append(f"{label}: {name} primary key differs")
        uniq = {
            (i["name"], tuple(i["column_names"]))
            for i in insp.get_indexes(name)
            if i.get("unique")
        }
        if uniq != plan.expected_unique.get(name, set()):
            problems.append(f"{label}: {name} unique indexes differ")
        fks = set()
        for fk in insp.get_foreign_keys(name):
            opts = fk.get("options") or {}
            fks.add(
                (
                    fk.get("name"),
                    tuple(fk["constrained_columns"]),
                    fk["referred_table"],
                    tuple(fk["referred_columns"]),
                    _norm_action(opts.get("ondelete")),
                    _norm_action(opts.get("onupdate")),
                )
            )
        expected_fks = plan.expected_fks.get(name, set())
        if any(f[0] is None for f in fks):  # sqlite may not report constraint names
            fks = {f[1:] for f in fks}
            expected_fks = {f[1:] for f in expected_fks}
        if fks != expected_fks:
            problems.append(f"{label}: {name} foreign keys differ")
    return problems, present


def identity_problems(conn, plan: Plan, label: str, present: set) -> list[str]:
    d = conn.dialect.name
    if d not in DIALECTS:
        return []
    problems = []
    for name, col in sorted(plan.identity.items()):
        if name not in present:
            continue
        table = plan.tables[name]
        top = conn.execute(sa.select(sa.func.max(table.c[col]))).scalar()
        if top is None:
            continue
        if d == "postgresql":
            seq = conn.execute(
                sa.select(sa.func.pg_get_serial_sequence(_quote_table(conn, name), col))
            ).scalar()
            if seq is None:
                problems.append(f"{label}: {name}.{col} has no identity")
                continue
            last = conn.execute(
                sa.select(sa.func.pg_sequence_last_value(sa.cast(seq, sa_pg.REGCLASS)))
            ).scalar()
            nxt = 1 if last is None else int(last) + 1
        else:
            cur = conn.execute(
                sa.select(sa.func.IDENT_CURRENT(_quote_table(conn, name)))
            ).scalar()
            if cur is None:
                problems.append(f"{label}: {name}.{col} has no identity")
                continue
            nxt = int(cur) + 1
        if nxt <= int(top):
            problems.append(f"{label}: {name}.{col} identity is not past the maximum")
    return problems


def orphan_count(conn, plan: Plan, rel: Relation) -> int:
    child = plan.tables[rel.child]
    parent = plan.tables[rel.parent].alias("mp_parent")
    match = sa.and_(
        *[child.c[a] == parent.c[b] for a, b in zip(rel.columns, rel.parent_columns)]
    )
    filled = sa.and_(*[child.c[a].isnot(None) for a in rel.columns])
    q = (
        sa.select(sa.func.count())
        .select_from(child)
        .where(
            filled, ~sa.select(parent.c[rel.parent_columns[0]]).where(match).exists()
        )
    )
    return int(conn.execute(q).scalar() or 0)


def count_rows(conn, table: sa.Table) -> int:
    return int(
        conn.execute(sa.select(sa.func.count()).select_from(table)).scalar() or 0
    )


def target_digest(conn, plan: Plan, name: str) -> tuple[int, str]:
    table = plan.tables[name]
    cols = plan.load_columns[name]
    result = conn.execute(sa.select(*[table.c[c["name"]] for c in cols]))
    rows = ({c["name"]: v for c, v in zip(cols, rec)} for rec in result)
    return canonical_digest(rows, cols)


def data_checks(conn, plan: Plan, source, label: str, dialect: str, present: set):
    """Row counts, digests and orphans per table: (entries, problems)."""
    entries, problems = [], []
    orphans_by_child: dict = {}
    for rel in plan.orphan_checks:
        if rel.child in present and rel.parent in present:
            n = orphan_count(conn, plan, rel)
            orphans_by_child[rel.child] = orphans_by_child.get(rel.child, 0) + n
            if n:
                problems.append(
                    f"{label}: {rel.child}: {n} foreign-key orphans for {rel.name}"
                )
    for name in source.table_names():
        ds_rows, ds_digest = canonical_digest(source.rows(name), source.columns(name))
        entry = {
            "table": name,
            "dialect": dialect,
            "dataset_rows": ds_rows,
            "target_rows": None,
            "digest_match": False,
            "fk_orphans": None,
        }
        if name in present:
            t_rows, t_digest = target_digest(conn, plan, name)
            entry["target_rows"] = t_rows
            entry["digest_match"] = t_digest == ds_digest
            entry["fk_orphans"] = orphans_by_child.get(name, 0)
            if t_rows != ds_rows:
                problems.append(
                    f"{label}: {name}: {t_rows} rows on the target, {ds_rows} in the dataset"
                )
            elif t_digest != ds_digest:
                problems.append(f"{label}: {name}: digest differs")
        entries.append(entry)
    return entries, problems


def require_rows_problems(entries: list[dict], label: str) -> list[str]:
    problems = []
    data = [e for e in entries if e["dataset_rows"] is not None]
    for e in data:
        if e["target_rows"] is None or e["target_rows"] < e["dataset_rows"]:
            problems.append(
                f"{label}: {e['table']}: fewer rows on the target than in the dataset"
            )
    if data and all(e["dataset_rows"] == 0 for e in data):
        problems.append(f"{label}: every dataset table is empty")
    return problems


# ------------------------------------------------------------------ run


@dataclass
class Options:
    schema_only: bool
    replace: bool
    verify: bool
    require_rows: bool


@dataclass
class Outcome:
    endpoint: Endpoint
    code: int = 0
    entries: list = field(default_factory=list)
    problems: list = field(default_factory=list)  # verified state (also in the report)
    messages: list = field(default_factory=list)  # what this run did (summary only)
    plan: Plan | None = None


def _fail(out: Outcome, code: int, message: str) -> Outcome:
    out.code = max(out.code, code)
    out.messages.append(message)
    return out


def _db_error(ep: Endpoint, what: str, exc: BaseException) -> str:
    orig = getattr(exc, "orig", None)
    kind = type(orig).__name__ if orig is not None else type(exc).__name__
    if ep.kind != "secret":
        # stderr only: under mp-data it goes to the data-zone log
        print(f"{COMMAND}: {ep.label()}: {what}: {exc}", file=sys.stderr)
    return f"{ep.label()}: {what} ({kind})"


def _schema_entries(plan: Plan, dialect: str) -> list[dict]:
    return [
        {
            "table": n,
            "dialect": dialect,
            "dataset_rows": None,
            "target_rows": None,
            "digest_match": None,
            "fk_orphans": None,
        }
        for n in plan.tables
    ]


def run_endpoint(ep: Endpoint, design: Design, source, opts: Options) -> Outcome:
    out = Outcome(ep)
    label = ep.label()
    read_only = opts.verify and not opts.replace
    if ep.kind != "secret" and ep.exists is False:
        if read_only:
            out.problems.append(f"{label}: database missing")
            out.code = 1
            return out
        try:
            create_database(ep)
        except sa.exc.SQLAlchemyError as exc:
            return _fail(out, 2, _db_error(ep, "cannot create the database", exc))
        out.messages.append(f"{label}: database created")
    try:
        engine = make_engine(ep.url)
    except sa.exc.SQLAlchemyError as exc:
        return _fail(out, 2, _db_error(ep, "cannot open the database", exc))
    try:
        plan = build_plan(design, engine.dialect.name)
        out.plan = plan
        if not read_only:
            try:
                with engine.begin() as conn:
                    present = set(sa.inspect(conn).get_table_names())
                    if present and opts.replace:
                        n = drop_tables(conn)
                        out.messages.append(f"{label}: dropped {n} tables")
                        present = set()
                    if present:
                        same = present == set(plan.tables)
                        if same and not opts.schema_only:
                            same = all(
                                count_rows(conn, plan.tables[t]) == source.row_count(t)
                                for t in source.table_names()
                            )
                        if not same:
                            msg = (
                                SECRET_NOT_EMPTY
                                if ep.kind == "secret"
                                else TARGET_DIFFERS
                            )
                            return _fail(out, 1, f"{label}: {msg}")
                        out.messages.append(
                            f"{label}: tables already match; nothing to do"
                        )
                    else:
                        for el in table_elements(plan):
                            conn.execute(el)
                        if not opts.schema_only:
                            counts = load_rows(conn, plan, source)
                            out.messages.append(
                                f"{label}: loaded {sum(counts.values())} rows into "
                                f"{len(counts)} tables"
                            )
                        for el in fk_elements(plan):
                            conn.execute(el)
                        if not opts.schema_only:
                            reset_identities(conn, plan)
                        out.messages.append(
                            f"{label}: created {len(plan.tables)} tables"
                        )
            except Stage1Error:
                raise
            except sa.exc.OperationalError as exc:
                return _fail(out, 2, _db_error(ep, "cannot reach the database", exc))
            except sa.exc.SQLAlchemyError as exc:
                return _fail(out, 1, _db_error(ep, "load failed", exc))
        try:
            with engine.connect() as conn:
                if opts.verify:
                    problems, present = structure_problems(conn, plan, label)
                    if not opts.schema_only:
                        entries, data_problems = data_checks(
                            conn, plan, source, label, ep.dialect, present
                        )
                        problems += data_problems
                        problems += identity_problems(conn, plan, label, present)
                        out.entries = entries
                    else:
                        out.entries = _schema_entries(plan, ep.dialect)
                    out.problems += problems
                elif opts.schema_only:
                    out.entries = _schema_entries(plan, ep.dialect)
                else:
                    for name in source.table_names():
                        out.entries.append(
                            {
                                "table": name,
                                "dialect": ep.dialect,
                                "dataset_rows": source.row_count(name),
                                "target_rows": count_rows(conn, plan.tables[name]),
                                "digest_match": None,
                                "fk_orphans": None,
                            }
                        )
                if opts.require_rows and not opts.schema_only:
                    out.problems += require_rows_problems(out.entries, label)
        except Stage1Error:
            raise
        except sa.exc.OperationalError as exc:
            return _fail(out, 2, _db_error(ep, "cannot reach the database", exc))
        except sa.exc.SQLAlchemyError as exc:
            return _fail(out, 1, _db_error(ep, "check failed", exc))
    finally:
        engine.dispose()
    if out.problems:
        out.code = max(out.code, 1)
    return out


def preflight(eps: list[Endpoint]) -> None:
    """Read each server's catalog before anything changes: database present, and the owner of an
    existing mpdata_ database (exit 2 for any owner but postgres, sa or mp_runner)."""
    for ep in eps:
        if ep.kind == "secret":
            continue
        try:
            database_state(ep)
        except sa.exc.SQLAlchemyError as exc:
            raise Stage1Error(_db_error(ep, "server did not answer", exc)) from None
    for ep in eps:
        if (
            ep.exists
            and ep.database.startswith("mpdata_")
            and ep.owner not in ALLOWED_OWNERS
        ):
            raise Stage1Error(
                f"mpdata_ database {ep.database} is owned by {ep.owner}, not postgres, sa or "
                "mp_runner: drop it as the superuser"
            )


# ------------------------------------------------------------------ report


def _dialect_list(dialects) -> str:
    return ", ".join(TITLES[d] for d in DIALECTS if d in dialects)


def render_report(
    eng: dict, design: Design, source, outcomes: list[Outcome], verified: bool = True
) -> str:
    """Markdown describing the verified state: no values, no per-table digests, no times."""
    lines = [
        "# Stage 1 load report",
        "",
        (
            f"Written by `toolkit stage1` for engagement `{eng.get('id')}`. It describes the "
            "verified state of each target database: row counts, whether the per-table digest "
            "of the canonical rows matches the dataset (the digests themselves are not "
            "written), foreign-key orphans, and everything that was not created. It holds "
            "names, counts and file hashes only, and no timestamps, so the same data gives the "
            "same bytes."
        ),
        "",
        "## Inputs",
        "",
        "| Input | sha256 |",
        "|---|---|",
        f"| {design.label} | `{design.sha256}` |",
    ]
    if source is None:
        lines.append("| dataset | not read (`--schema-only`) |")
    else:
        what = (
            "fresh copy (back-end file)"
            if source.kind == "fresh-copy"
            else "dataset.json"
        )
        lines.append(f"| {what} | `{source.sha256}` |")
    lines += ["", "## Result", ""]
    problems = [p for o in outcomes for p in o.problems]
    if problems:
        lines.append(f"{len(problems)} check(s) failed:")
        lines.append("")
        lines += [f"- {p}" for p in problems[:200]]
    elif not verified:
        lines.append("The checks were not run (no `--verify`): row counts only.")
    else:
        lines.append("Every check passed.")
    for o in outcomes:
        ep = o.endpoint
        lines += ["", f"## {ep.title}", ""]
        if ep.kind == "secret":
            lines.append("Target: the client's database (secret target).")
        elif ep.kind == "local":
            lines.append(f"Target: local database `{ep.database}`.")
        else:
            lines.append(f"Target: database `{ep.database}`.")
        lines.append("")
        if not o.entries:
            lines.append("No table was checked.")
            continue
        lines += [
            "| Table | Dataset rows | Target rows | Digest match | FK orphans |",
            "|---|---|---|---|---|",
        ]
        for e in o.entries:

            def cell(v):
                if v is None:
                    return "-"
                if isinstance(v, bool):
                    return "yes" if v else "no"
                return str(v)

            lines.append(
                f"| `{e['table']}` | {cell(e['dataset_rows'])} | {cell(e['target_rows'])} | "
                f"{cell(e['digest_match'])} | {cell(e['fk_orphans'])} |"
            )
    plans = [(o.endpoint.dialect, o.plan) for o in outcomes if o.plan is not None]
    if not plans:
        plans = [(d, build_plan(design, d)) for d in DIALECTS[:1]]

    def merged(attr) -> list:
        seen: dict = {}
        for d, p in plans:
            for item in getattr(p, attr):
                seen.setdefault(item, set()).add(d)
        return sorted(seen.items())

    lines += ["", "## Omitted columns", ""]
    omitted = merged("omitted")
    if omitted:
        lines += ["| Column | Type | Reason | Dialects |", "|---|---|---|---|"]
        for (t, c, ctype, reason), ds in omitted:
            lines.append(
                f"| `{t}.{c}` | {ctype} | {reason}: not created, not loaded | {_dialect_list(ds)} |"
            )
    else:
        lines.append("None.")
    not_created = merged("not_created")
    rels = [x for x in not_created if x[0][0] == "relationship"]
    lines += ["", "## Relationships not created", ""]
    if rels:
        lines += ["| Relationship | Reason | Dialects |", "|---|---|---|"]
        for (_k, name, reason), ds in rels:
            lines.append(f"| `{name}` | {reason} | {_dialect_list(ds)} |")
    else:
        lines.append(
            "None: every relationship with enforced integrity is a foreign key."
        )
    cascades = merged("cascade_no_action")
    lines += ["", "### Cascade rules changed to NO ACTION", ""]
    if cascades:
        lines += ["| Relationship | Dialects | Why |", "|---|---|---|"]
        for name, ds in cascades:
            lines.append(
                f"| `{name}` | {_dialect_list(ds)} | a second cascade path to the same table |"
            )
    else:
        lines.append("None.")
    lines += ["", "## Other objects not created", ""]
    others = [
        x
        for x in not_created
        if x[0][0] != "relationship"
        and not (x[0][0] == "index" and x[0][2] == "not unique")
    ]
    plain: dict = {}
    for (_k, name, _r), ds in not_created:
        if _k == "index" and _r == "not unique":
            table, _, index = name.partition(".")
            plain.setdefault((table, tuple(sorted(ds))), []).append(index)
    if plain:
        lines += [
            (
                "Indexes that are not unique (Access keeps one per relationship and for "
                "sorting) are left to the database administrator:"
            ),
            "",
            "| Table | Indexes not created | Dialects |",
            "|---|---|---|",
        ]
        for (table, ds), names in sorted(plain.items()):
            listed = ", ".join(f"`{n}`" for n in sorted(names))
            lines.append(f"| `{table}` | {listed} | {_dialect_list(ds)} |")
        lines.append("")
    if others:
        lines += ["| Kind | Object | Reason | Dialects |", "|---|---|---|---|"]
        for (kind, name, reason), ds in others:
            lines.append(f"| {kind} | `{name}` | {reason} | {_dialect_list(ds)} |")
    elif not plain:
        lines.append("None.")
    added = merged("added_columns")
    lines += ["", "## Columns added", ""]
    if added:
        lines += ["| Column | Dialects | Why |", "|---|---|---|"]
        for (t, c), ds in added:
            lines.append(
                f"| `{t}.{c}` | {_dialect_list(ds)} | row version, so Access stops reporting "
                "write conflicts on floating-point columns |"
            )
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def summary_paths(eng: dict, report: Path | None) -> list[str] | None:
    if report is None:
        return None
    real = Path(os.path.realpath(report))
    paths = eng["paths"]
    roots = [
        Path(os.path.realpath(paths[k])) for k in ("pipeline", "target_repo", "records")
    ]
    private = Path(os.path.realpath(paths["private"]))
    roots += [private / d for d in ("out", "review", "synthetic")]
    if any(real == r or r in real.parents for r in roots):
        return [str(real)]
    return None


def run(eng: dict, args, env=None) -> tuple[int, dict]:
    """Run stage1; returns (exit code, summary fields). Raises Stage1Error for exit-2 and early
    exit-1 conditions."""
    env = os.environ if env is None else env
    opts = Options(
        schema_only=bool(args.schema_only),
        replace=bool(args.replace),
        verify=bool(args.verify),
        require_rows=bool(args.require_rows),
    )
    spec = parse_target(args.target_db)
    if spec.kind == "secret" and opts.replace:
        raise Stage1Error(
            "--replace is refused with a secret: target: the client provides an empty database"
        )
    if opts.schema_only and opts.require_rows:
        raise Stage1Error(
            "--require-rows needs rows; it does not combine with --schema-only"
        )
    engagement_data = not opts.schema_only and is_engagement_data(args.dataset, eng)
    outside = spec.kind == "url" or (
        spec.kind == "local" and not spec.value.startswith("mpdata_")
    )
    if engagement_data and outside:
        raise Stage1Error(ENGAGEMENT_ONLY)
    if uses_data_model(eng, args.schema_from) and not opts.schema_only:
        raise Stage1Error(DATA_MODEL_ROWS)
    design = load_design(eng, args.schema_from)
    eps = endpoints(spec, args.dialects, eng, env)
    source = None
    messages: list[str] = []
    if not opts.schema_only:
        source = open_source(args.dataset, eng, design)
        problems = source_problems(design, source)
        if problems:
            raise Stage1Error("; ".join(problems[:10]), 1)
        if source.kind == "dir" and source.meta.get("schema_sha256") != design.sha256:
            messages.append("dataset.json was exported from another schema.json")
    preflight(eps)
    outcomes = [run_endpoint(ep, design, source, opts) for ep in eps]
    code = max(o.code for o in outcomes) if outcomes else 0
    for o in outcomes:
        messages += o.messages
        messages += o.problems
    report = Path(args.report) if args.report else None
    if report is not None:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_bytes(
            render_report(eng, design, source, outcomes, opts.verify).encode("utf-8")
        )
        messages.append(f"wrote {report.name}")
    entries = [e for o in outcomes for e in o.entries]
    sha256s = {design.label: design.sha256}
    if source is not None:
        sha256s[source.sha_name] = source.sha256
    counts = {
        "dialects": len(eps),
        "tables": len(design.tables),
        "problems": sum(len(o.problems) for o in outcomes),
    }
    if source is not None:
        counts["rows"] = sum(source.row_count(t) for t in source.table_names())
    return code, {
        "counts": counts,
        "tables": entries,
        "sha256s": sha256s,
        "paths": summary_paths(eng, report),
        "messages": messages,
    }
