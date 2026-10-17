"""Running catalog units on a reference database (UCanAccess 5.1.8).

A reference database is opened through `bridge.connect(path, read_only=True, memory=True)`, so
parallel readers never conflict and nothing is written. Jackcess has no query writer and
UCanAccess refuses CREATE VIEW on the Access file, so the catalog's saved queries are created at
every open as views of UCanAccess's in-memory HSQLDB mirror: each ok unit, in dependency order,
converted with UCanAccess's own SQLConverter (the path UCanAccess takes for the saved queries of
a file) and executed on `UcanaccessConnection.getHSQLDBConnection()`. Queries on queries
therefore resolve the same way in a database built from a dataset (which has no saved queries)
and in a copy of a back end that lacks them; a view of the same name that UCanAccess loaded from
the file itself is replaced by the catalog's version. Every `source.*` of a view is written as
the source's own qualified columns before HSQLDB stores it (`expand_stars`), so `Orders.*` over a
join of sources that share column names is Orders' columns, as in Access.

UCanAccess's Nz rounds currency and decimal arguments (its NUMERIC overload has scale 0), so every
view and statement first goes through `catalog.nz_for_engine`, which writes such calls as
COALESCE; column kinds (text or not) come from the HSQLDB mirror's INFORMATION_SCHEMA.

Units with parameters run with values bound as literals (PARAMETERS clause removed, `[name]` or
`Forms!frm!ctl` replaced); their parameterised dependencies are created as views with the same
values for that run. Errors carry the Java exception class only, never values.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from toolkit.reference import bridge
from toolkit.reference import catalog as cat

_OURS: dict[str, set[str]] = {}  # resolved db path -> view names this process created


class UnitError(Exception):
    """A unit failed at run time. str(): the Java exception class."""


class ParamError(Exception):
    """A parameter value is missing or does not fit its type (never names the value)."""


# ------------------------------------------------------------------ parameters


def literal(ptype: str, text: str) -> str:
    """An Access SQL literal for a canonical parameter string of neutral type `ptype`."""
    if text is None:
        return "Null"
    if ptype in ("byte", "integer", "longinteger", "autonumber"):
        if not re.fullmatch(r"-?\d+", text):
            raise ParamError(f"not an integer for a {ptype} parameter")
        return str(int(text))
    if ptype in ("currency", "decimal", "single", "double"):
        try:
            return format(Decimal(text), "f")
        except InvalidOperation:
            raise ParamError(f"not a number for a {ptype} parameter") from None
    if ptype == "yesno":
        if text not in ("0", "1", "-1", "true", "false"):
            raise ParamError("not a yes/no value")
        return "True" if text in ("1", "-1", "true") else "False"
    if ptype == "datetime":
        try:
            value = dt.datetime.fromisoformat(text)
        except ValueError:
            raise ParamError("not a datetime for a datetime parameter") from None
        return value.strftime("#%Y-%m-%d %H:%M:%S#")
    return "'" + str(text).replace("'", "''") + "'"


def bind(sql: str, params: list[dict], values: dict) -> str:
    """`sql` with the PARAMETERS clause removed and every parameter replaced by its literal."""
    out = cat.strip_parameters(sql)
    lowered = {str(k).lower(): v for k, v in (values or {}).items()}
    for p in params:
        if p["name"].lower() not in lowered:
            raise ParamError(f"no value for parameter {p['name']}")
        lit = literal(p["type"], lowered[p["name"].lower()])
        if p["source"] == "form_control":
            for name, _ctl, start, end in sorted(
                cat.form_refs(out), key=lambda r: -r[2]
            ):
                if name.lower() == p["name"].lower():
                    out = out[:start] + lit + out[end:]
        else:
            masked = cat.mask(out, brackets=False)
            pattern = re.compile(r"\[" + re.escape(p["name"]) + r"\]", re.IGNORECASE)
            if "." in p["name"] and not pattern.search(out):  # an unknown Table.Column
                table, column = p["name"].split(".", 1)
                pattern = re.compile(
                    r"(?<![\w\].!])\[?"
                    + re.escape(table)
                    + r"\]?\s*\.\s*\[?"
                    + re.escape(column)
                    + r"\]?(?![\w\[])",
                    re.IGNORECASE,
                )
            for m in sorted(pattern.finditer(out), key=lambda m: -m.start()):
                if masked[m.start()] != "\0":
                    out = out[: m.start()] + lit + out[m.end() :]
    return out


def _clean(sql: str) -> str:
    return sql.strip().rstrip(";").strip()


_IDENT = r'(?:"(?:[^"]|"")+"|[A-Za-z_][\w$]*)'
# `source.*` in converted (HSQLDB) SQL; never a schema-qualified `PUBLIC.T.*`
_STAR = re.compile(rf'(?<![\w$."])({_IDENT})\s*\.\s*\*')
# `source [AS] alias` right after FROM, JOIN, a comma or an opening parenthesis
_ALIAS = re.compile(
    rf'(?:\bFROM|\bJOIN|,|\()\s*({_IDENT})\s+(?:AS\s+)?({_IDENT})(?![\w$."])',
    re.IGNORECASE,
)
_NOT_ALIAS = {
    "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "CROSS", "JOIN", "ON", "WHERE", "GROUP",
    "ORDER", "HAVING", "UNION", "AS", "SELECT", "FROM", "LIMIT", "OFFSET", "NATURAL",
}  # fmt: skip


def _hsql_name(ident: str) -> str:
    """The stored HSQLDB name of an identifier: "quoted" as written, otherwise upper case."""
    ident = ident.strip()
    if ident.startswith('"'):
        return ident[1:-1].replace('""', '"')
    return ident.upper()


def _key(name: str) -> str:
    """A table or column name compared across Access and UCanAccess's escaped HSQLDB names."""
    return re.sub(r"[^A-Z0-9]", "", name.upper())


# ------------------------------------------------------------------ types

_JDBC_TYPES = {
    "INTEGER": "longinteger",
    "SMALLINT": "integer",
    "TINYINT": "byte",
    "BIGINT": "longinteger",
    "DOUBLE": "double",
    "FLOAT": "double",
    "REAL": "single",
    "BOOLEAN": "yesno",
    "BIT": "yesno",
    "TIMESTAMP": "datetime",
    "DATE": "datetime",
    "TIME": "datetime",
    "VARCHAR": "text",
    "CHAR": "text",
    "CHARACTER": "text",
    "LONGVARCHAR": "memo",
    "CLOB": "memo",
    "BLOB": "oleobject",
    "VARBINARY": "oleobject",
    "BINARY": "oleobject",
}


def neutral_of(type_name: str, scale: int) -> str:
    name = type_name.upper()
    if name in ("DECIMAL", "NUMERIC"):
        return "currency" if scale == 4 else "decimal"
    return _JDBC_TYPES.get(name, "text")


# ------------------------------------------------------------------ engine


class AccessReference:
    """One reference database opened read-only with the catalog's queries as views."""

    def __init__(self, private, db_path, catalog: dict | None = None):
        self.private = Path(private)
        self.db_path = Path(db_path)
        self.catalog = (
            catalog if catalog is not None else cat.load_catalog(self.private)
        )
        self.units = {u["id"]: u for u in self.catalog["units"]}
        self.conn = None
        self.view_errors: dict[str, str] = {}
        self._names: dict[str, str] | None = None
        self._schema_types: dict[str, str] | None = None
        self._kinds: dict[tuple[str, str], bool] | None = None

    # ---- lifecycle
    def open(self) -> AccessReference:
        if not self.db_path.is_file():
            raise FileNotFoundError(str(self.db_path))
        self.conn = bridge.connect(str(self.db_path), read_only=True, memory=True)
        self.create_views()
        return self

    def close(self) -> None:
        if self.conn is not None:
            with contextlib.suppress(Exception):
                self.conn.close()
            self.conn = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    # ---- SQL
    def sql(self, unit_id: str) -> str:
        return cat.unit_sql(self.private, self.units[unit_id])

    def order(self) -> list[str]:
        """Unit ids, each after its dependencies."""
        out: list[str] = []
        for uid in sorted(self.units):
            for d in self.units[uid]["depends_on"] + [uid]:
                if d not in out and d in self.units:
                    out.append(d)
        return out

    def _hsql(self):
        return self.conn.getHSQLDBConnection()

    def _views(self) -> set[str]:
        rs = (
            self._hsql()
            .createStatement()
            .executeQuery(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.VIEWS WHERE TABLE_SCHEMA = 'PUBLIC'"
            )
        )
        out = set()
        while rs.next():
            out.add(str(rs.getString(1)).upper())
        rs.close()
        return out

    def _view_name(self, name: str) -> str:
        conv = bridge.jclass("net.ucanaccess.converters.SQLConverter")
        return str(conv.completeEscaping(name, False))

    def _column_kinds(self) -> dict[tuple[str, str], bool]:
        """{(table key, column key): is text} of every table and view of the HSQLDB mirror."""
        if self._kinds is None:
            rs = (
                self._hsql()
                .createStatement()
                .executeQuery(
                    "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS"
                    " WHERE TABLE_SCHEMA = 'PUBLIC'"
                )
            )
            kinds: dict[tuple[str, str], bool] = {}
            while rs.next():
                dtype = str(rs.getString(3)).upper()
                kinds[(_key(str(rs.getString(1))), _key(str(rs.getString(2))))] = (
                    "CHAR" in dtype or "CLOB" in dtype
                )
            rs.close()
            self._kinds = kinds
        return self._kinds

    def _is_text(self, sql: str, expr: str) -> bool | None:
        """Whether `expr` (an Nz argument in `sql`) is a text column; None when unknown."""
        ref = cat.column_ref(expr)
        if ref is None:
            return None
        table, column = ref
        kinds = self._column_kinds()
        if table is not None:
            for m in re.finditer(
                rf"({cat._SEG})\s+AS\s+({cat._SEG})",
                cat.mask(sql, brackets=False),
                re.IGNORECASE,
            ):
                if cat._unbracket(sql[m.start(2) : m.end(2)]).lower() == table.lower():
                    table = cat._unbracket(sql[m.start(1) : m.end(1)])
                    break
            found = kinds.get((_key(table), _key(column)))
            if found is not None:
                return found
        seen = {v for (_t, c), v in kinds.items() if c == _key(column)}
        return seen.pop() if len(seen) == 1 else None

    def engine_sql(self, sql: str) -> str:
        """Access SQL as the engine runs it: Nz written so UCanAccess does not round it."""
        return cat.nz_for_engine(sql, lambda expr: self._is_text(sql, expr))[0]

    def _columns_of(self, table: str) -> list[str]:
        """The column names of an HSQLDB table or view (exact stored name), in order."""
        ps = self._hsql().prepareStatement(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS"
            " WHERE TABLE_SCHEMA = 'PUBLIC' AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION"
        )
        try:
            ps.setString(1, table)
            rs = ps.executeQuery()
            out = []
            while rs.next():
                out.append(str(rs.getString(1)))
            rs.close()
            return out
        finally:
            ps.close()

    def expand_stars(self, converted: str) -> str:
        """`converted` (HSQLDB SQL from SQLConverter) with every `source.*` written as the
        source's columns, each qualified: `source."COL1",source."COL2",...`.

        Access's `Orders.*` is exactly the columns of Orders. HSQLDB stores a view with its
        asterisks expanded, and when the source sits inside a parenthesised join it writes the
        names unqualified; the view is then compiled from that text, where a name that several
        joined sources share (Notes, AddedBy, ...) resolves to the first of them in FROM order,
        not to Orders. Explicit qualified columns are stored as written. A qualifier that names
        no table or view, nor an alias of one (a derived table), is left as it is when the
        query has no join, and raises UnresolvedStarError when it has one."""
        masked = cat.mask(converted, brackets=False)
        aliases: dict[str, str] = {}
        for m in _ALIAS.finditer(masked):
            src = _hsql_name(converted[m.start(1) : m.end(1)])
            alias = _hsql_name(converted[m.start(2) : m.end(2)])
            if alias.upper() not in _NOT_ALIAS:
                aliases.setdefault(alias, src)
        out = converted
        for m in sorted(_STAR.finditer(masked), key=lambda m: -m.start()):
            qualifier = converted[m.start(1) : m.end(1)]
            name = _hsql_name(qualifier)
            columns = self._columns_of(aliases.get(name, name))
            if not columns and name in aliases:
                columns = self._columns_of(name)
            if not columns:
                if _JOIN.search(masked):
                    # a derived table's `.*` over a join could come back from the wrong
                    # source: refuse the view (the unit lands in view_errors) rather than
                    # record it silently wrong
                    raise UnresolvedStarError(name)
                continue
            listed = ",".join(
                qualifier + '."' + c.replace('"', '""') + '"' for c in columns
            )
            out = out[: m.start()] + listed + out[m.end() :]
        return out

    def _create_view(self, name: str, sql: str, replace: bool) -> None:
        conv = bridge.jclass("net.ucanaccess.converters.SQLConverter")
        escaped = self._view_name(name)
        if replace:
            st = self._hsql().createStatement()
            st.execute(f"DROP VIEW {escaped} IF EXISTS CASCADE")
            st.close()
            self._kinds = None
        body = self.engine_sql(_clean(cat.strip_parameters(sql)))
        text = f"CREATE VIEW {escaped} AS {body}"
        converted = self.expand_stars(_clean(str(conv.convertSQL(text, True).getSql())))
        st = self._hsql().createStatement()
        st.execute(converted)
        st.close()
        self._kinds = None

    def create_views(self) -> dict[str, str]:
        """Create every ok unit as a view, in dependency order. Returns {unit id: Java exception
        class} of the units whose view failed (their dependants are skipped)."""
        key = str(self.db_path.resolve())
        ours = _OURS.setdefault(key, set())
        existing = self._views()
        failed: dict[str, str] = {}
        for uid in self.order():
            unit = self.units[uid]
            if unit["status"] != "ok":
                continue
            if any(d in failed for d in unit["depends_on"]):
                failed[uid] = "dependency"
                continue
            stored = self._view_name(uid).strip('"').upper()
            if stored in existing and uid in ours:
                continue
            try:
                self._create_view(uid, self.sql(uid), replace=stored in existing)
                ours.add(uid)
            except Exception as exc:  # noqa: BLE001 - Java exceptions surface through JPype
                failed[uid] = bridge.java_exception_class(exc)
        self.view_errors = failed
        return failed

    def final_sql(self, unit_id: str, values: dict | None = None) -> str:
        unit = self.units[unit_id]
        sql = self.sql(unit_id)
        if unit["params"]:
            sql = bind(sql, unit["params"], values or {})
            for d in unit["depends_on"]:
                dep = self.units[d]
                if dep["status"] == "needs-params":
                    self._create_view(
                        d, bind(self.sql(d), dep["params"], values or {}), replace=True
                    )
        return self.engine_sql(_clean(sql))

    def runnable(self, unit_id: str, values: dict | None) -> bool:
        unit = self.units[unit_id]
        if unit["status"] == "ok":
            return True
        if unit["status"] != "needs-params" or not values:
            return False
        have = {str(k).lower() for k in values}
        return all(p["name"].lower() in have for p in unit["params"])

    def _execute(self, unit_id: str, values: dict | None):
        if self.conn is None:
            self.open()
        try:
            sql = self.final_sql(unit_id, values)
            st = self.conn.createStatement()
            return st, st.executeQuery(sql)
        except ParamError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise UnitError(bridge.java_exception_class(exc)) from None

    def count(self, unit_id: str, values: dict | None = None) -> int:
        st, rs = self._execute(unit_id, values)
        n = 0
        try:
            while rs.next():
                n += 1
        except Exception as exc:  # noqa: BLE001
            raise UnitError(bridge.java_exception_class(exc)) from None
        finally:
            st.close()
        return n

    def rows(self, unit_id: str, values: dict | None = None) -> list[tuple]:
        st, rs = self._execute(unit_id, values)
        out = []
        try:
            md = rs.getMetaData()
            n = int(md.getColumnCount())
            types = [str(md.getColumnTypeName(i)).upper() for i in range(1, n + 1)]
            while rs.next():
                row = []
                for i in range(1, n + 1):
                    v = bridge.py_value(rs.getObject(i))
                    if types[i - 1] in ("BOOLEAN", "BIT") and isinstance(v, int):
                        v = bool(v)
                    row.append(v)
                out.append(tuple(row))
        except Exception as exc:  # noqa: BLE001
            raise UnitError(bridge.java_exception_class(exc)) from None
        finally:
            st.close()
        return out

    # ---- describe
    def _name_map(self, unit_id: str) -> dict[str, str]:
        names: dict[str, str] = {}
        schema_path = self.private / "out" / "model" / "schema.json"
        if schema_path.is_file():
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            for t in schema.get("tables") or []:
                for c in t.get("columns") or []:
                    names.setdefault(c["name"].upper(), c["name"])
        unit = self.units[unit_id]
        for uid in unit["depends_on"] + [unit_id]:
            text = self.sql(uid)
            for m in re.finditer(
                r"\bAS\s+(\[[^\]]+\]|[A-Za-z_]\w*)", text, re.IGNORECASE
            ):
                alias = m.group(1).strip("[]")
                names[alias.upper()] = alias
        return names

    def _schema_type(self, column: str) -> str | None:
        if self._schema_types is None:
            self._schema_types = {}
            schema_path = self.private / "out" / "model" / "schema.json"
            if schema_path.is_file():
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                for t in schema.get("tables") or []:
                    for c in t.get("columns") or []:
                        self._schema_types.setdefault(c["name"].upper(), c["type"])
        return self._schema_types.get(column.upper())

    def describe(
        self, unit_id: str, values: dict | None = None
    ) -> list[tuple[str, str]]:
        """[(output column, neutral type)] in output order."""
        unit = self.units[unit_id]
        if values is None and unit["params"]:
            values = {p["name"]: _placeholder(p["type"]) for p in unit["params"]}
        if self.conn is None:
            self.open()
        try:
            ps = self.conn.prepareStatement(self.final_sql(unit_id, values))
            md = ps.getMetaData()
            names = self._name_map(unit_id)
            out = []
            for i in range(1, int(md.getColumnCount()) + 1):
                label = str(md.getColumnLabel(i))
                label = names.get(label.upper(), label)
                ntype = neutral_of(str(md.getColumnTypeName(i)), int(md.getScale(i)))
                st = self._schema_type(label)
                if st in ("single", "memo", "hyperlink") and (
                    (st == "single" and ntype in ("double", "decimal"))
                    or (st != "single" and ntype in ("text", "memo"))
                ):
                    ntype = st
                out.append((label, ntype))
            ps.close()
            return out
        except ParamError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise UnitError(bridge.java_exception_class(exc)) from None


_JOIN = re.compile(r"\bJOIN\b", re.IGNORECASE)


class UnresolvedStarError(ValueError):
    """A view's `source.*` names no table or view in a query with a join."""


def _placeholder(ptype: str) -> str:
    if ptype in (
        "byte",
        "integer",
        "longinteger",
        "autonumber",
        "currency",
        "decimal",
        "single",
        "double",
    ):
        return "0"
    if ptype == "yesno":
        return "0"
    if ptype == "datetime":
        return "2000-01-01T00:00:00"
    return "x"


# ------------------------------------------------------------------ datasets


def database_for(private, dataset: str) -> Path:
    """The reference database of a dataset name (engagement or seed-N)."""
    private = Path(private)
    if dataset == "engagement":
        return private / "reference" / "engagement.accdb"
    if re.fullmatch(r"seed-\d+", dataset):
        return private / "synthetic" / "reference" / f"{dataset}.accdb"
    raise ValueError(f"unknown dataset {dataset!r}")


def build_command(dataset: str) -> str:
    if dataset == "engagement":
        return "mp-data reference build <eng> --dataset engagement"
    return f"uv run toolkit reference build <eng> --dataset {dataset}"


class AccessAdapter:
    """The Access side of ReferenceAdapter (docs/contracts.md#harness)."""

    def __init__(self, private):
        self.private = Path(private)
        self.catalog = cat.load_catalog(self.private)
        self.ref: AccessReference | None = None
        self.dataset: str | None = None

    def load_dataset(self, directory) -> None:
        from toolkit.dataset import read_meta

        meta = read_meta(Path(directory))
        name = meta.get("name")
        path = database_for(self.private, name)
        if not path.is_file():
            raise FileNotFoundError(
                f"reference database for {name} missing: run {build_command(name)}"
            )
        if self.ref is not None:
            self.ref.close()
        self.ref = AccessReference(self.private, path, self.catalog).open()
        self.dataset = name

    def _ref(self) -> AccessReference:
        if self.ref is None:
            raise RuntimeError("load_dataset was not called")
        return self.ref

    def run_saved_query(self, unit_id: str, params: dict | None = None) -> list[tuple]:
        ref = self._ref()
        unit = ref.units.get(unit_id)
        if unit is None or unit["status"] == "unsupported":
            raise NotImplementedError(f"not implemented: {unit_id}")
        return ref.rows(unit_id, params or {})

    def render_document(self, unit_id: str, key=None, params=None):
        raise NotImplementedError(f"not implemented: {unit_id}")

    def describe(self, unit_id: str) -> list[tuple[str, str]]:
        return self._ref().describe(unit_id)

    def close(self) -> None:
        if self.ref is not None:
            self.ref.close()
            self.ref = None
