"""Saved-query designs of the model to Access SQL, through Jackcess's query formatter.

`design_rows(query)` turns one parsed query design of `model/objects.json` into the rows Access
keeps in MSysQueries, as plain tuples `(attribute, expression, flag, name1, name2)`:

    0 START, 1 TYPE (flag = the design's operation: 1 select, 9 union), 2 PARAMETER (one per
    declared parameter: name1 = name, flag = Jackcess DataType code), 3 FLAG (flag = the select
    options: 1 select *, 2 distinct, 4 owner access, 8 distinctrow, 16 top, 32 percent; name1 =
    the TOP value), 5 InputTables (name1 = table or query, name2 = alias; for a union, expression
    = the part's SQL and name2 = X7YZ_____1 or X7YZ_____2), 6 OutputColumns (expression, name1 =
    alias), 7 Joins (expression, flag 1 inner / 2 left / 3 right, name1 = left, name2 = right),
    8 Where, 9 Groups, 10 Having, 11 OrderBy (name1 = "D" when descending) and 255 END.

`format_sql(name, rows, object_flag)` builds `QueryImpl.Row(id=null, attribute, expression, flag,
extra, name1, name2, objectId, order)` objects and returns
`QueryImpl.create(objectFlag, name, rows, objectId).toSQLString()`; objectFlag is 0 for SELECT and
128 for UNION. `query_sql(query)` gives (status, reason, sql) for a model query: SQL-text queries
pass through unchanged; action queries are unsupported with reason action-query, crosstab and
pass-through queries with their own reasons.

`python -m toolkit.reference.qbe <eng>` loads the engagement with toolkit.engagement.load and
prints, for every saved query in model/objects.json, its id and the SQL the formatter makes from
its design (or its status and reason). It reads only the design zone and writes nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

START, TYPE, PARAMETER, FLAG, TABLE, COLUMN, JOIN, WHERE, GROUPBY, HAVING, ORDERBY = (
    0,
    1,
    2,
    3,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
)
END = 255
SELECT_OBJECT_FLAG = 0
UNION_OBJECT_FLAG = 128
UNION_PART1 = "X7YZ_____1"
UNION_PART2 = "X7YZ_____2"
UNION_DISTINCT_FLAG = 2
SELECT_OPERATION = 1
UNION_OPERATION = 9
# neutral type -> Jackcess DataType code (the PARAMETER row's flag)
PARAM_TYPE_CODES = {
    "yesno": 1,
    "byte": 2,
    "integer": 3,
    "longinteger": 4,
    "autonumber": 4,
    "currency": 5,
    "single": 6,
    "double": 7,
    "datetime": 8,
    "text": 10,
    "memo": 12,
    "hyperlink": 12,
    "guid": 15,
    "decimal": 16,
}
REASONS = {
    "action": "action-query",
    "crosstab": "crosstab",
    "pass-through": "pass-through",
}

Row = tuple  # (attribute, expression, flag, name1, name2)


def param_name_type(p) -> tuple[str, str]:
    """(name without brackets, neutral type) of a declared parameter (a name or {name, type})."""
    if isinstance(p, dict):
        name, ptype = p.get("name") or "", p.get("type") or "text"
    else:
        name, ptype = str(p), "text"
    name = name.strip()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    return name, ptype


def _param_rows(query: dict) -> list[Row]:
    rows = []
    for p in query.get("params") or []:
        name, ptype = param_name_type(p)
        rows.append((PARAMETER, None, PARAM_TYPE_CODES.get(ptype, 10), name, None))
    return rows


def _order_rows(design: dict) -> list[Row]:
    rows = []
    for o in design.get("order_by") or []:
        desc = "D" if (o.get("flag") or 0) == 1 or o.get("desc") else None
        rows.append((ORDERBY, o.get("expression"), None, desc, None))
    return rows


def select_rows(query: dict) -> list[Row]:
    design = query.get("design") or {}
    rows: list[Row] = [(START, None, None, None, None)]
    rows.append((TYPE, None, design.get("operation") or SELECT_OPERATION, None, None))
    rows += _param_rows(query)
    top = design.get("top")
    rows.append(
        (
            FLAG,
            None,
            int(design.get("option") or 0),
            None if top is None else str(top),
            None,
        )
    )
    for t in design.get("input_tables") or []:
        rows.append((TABLE, None, None, t.get("name"), t.get("alias")))
    for c in design.get("output_columns") or []:
        rows.append((COLUMN, c.get("expression"), None, c.get("alias"), None))
    for j in design.get("joins") or []:
        rows.append(
            (
                JOIN,
                j.get("expression"),
                int(j.get("flag") or 1),
                j.get("left"),
                j.get("right"),
            )
        )
    if design.get("where"):
        rows.append((WHERE, design["where"], None, None, None))
    for g in design.get("group_by") or []:
        rows.append((GROUPBY, g.get("expression"), 0, None, None))
    if design.get("having"):
        rows.append((HAVING, design["having"], None, None, None))
    rows += _order_rows(design)
    rows.append((END, None, None, None, None))
    return rows


def union_rows(
    part1: str, part2: str, union_all: bool = False, order_by=None, params=()
) -> list[Row]:
    """MSysQueries rows of a two-part UNION (a longer union nests in part 2)."""
    rows = [(START, None, None, None, None), (TYPE, None, UNION_OPERATION, None, None)]
    rows += _param_rows({"params": list(params)})
    rows.append((FLAG, None, 0 if union_all else UNION_DISTINCT_FLAG, None, None))
    rows.append((TABLE, part1, None, None, UNION_PART1))
    rows.append((TABLE, part2, None, None, UNION_PART2))
    rows += _order_rows({"order_by": order_by or []})
    rows.append((END, None, None, None, None))
    return rows


def design_rows(query: dict) -> tuple[int, list[Row]]:
    """(objectFlag, rows) of a grid design: SELECT, or a UNION given as design.union_parts."""
    design = query.get("design") or {}
    parts = design.get("union_parts")
    if parts:
        if len(parts) < 2:
            raise ValueError("a union needs two parts")
        part2 = parts[1]
        sep = "\nUNION ALL " if design.get("union_all") else "\nUNION "
        for extra in parts[2:]:
            part2 = part2 + sep + extra
        return UNION_OBJECT_FLAG, union_rows(
            parts[0],
            part2,
            bool(design.get("union_all")),
            design.get("order_by"),
            query.get("params") or (),
        )
    return SELECT_OBJECT_FLAG, select_rows(query)


def _signed_byte(n: int) -> int:
    return n - 256 if n > 127 else n


def java_rows(rows: list[Row], object_id: int):
    """A java.util.ArrayList of QueryImpl.Row for `rows`."""
    from toolkit.reference import bridge

    bridge.start_jvm()
    RowCls = bridge.jclass("io.github.spannm.jackcess.impl.query.QueryImpl$Row")
    JByte = bridge.jclass("java.lang.Byte")
    JShort = bridge.jclass("java.lang.Short")
    JInt = bridge.jclass("java.lang.Integer")
    out = bridge.jclass("java.util.ArrayList")()
    for attribute, expression, flag, name1, name2 in rows:
        out.add(
            RowCls(
                None,
                JByte(_signed_byte(attribute)),
                expression,
                None if flag is None else JShort(int(flag)),
                None,
                name1,
                name2,
                JInt(object_id),
                None,
            )
        )
    return out


TABLES_CONTAINER = "Tables"
QUERY_OBJECT_TYPE = 5
CONTAINER_IDS = 0x0F000000


def write_saved_query(
    db, name: str, rows: list[Row], object_flag: int = SELECT_OBJECT_FLAG
) -> int:
    """Add one saved query to a Jackcess Database opened read-write: an MSysObjects row (Type 5,
    Flags = objectFlag, a new Id, under the Tables container) and its MSysQueries rows. Jackcess
    has no query writer and UCanAccess keeps views only in its HSQLDB mirror, so tests and
    fixtures that need saved queries in an .accdb or .mdb file write them this way. Returns the
    new object id."""
    from toolkit.reference import bridge

    objects = db.getSystemTable("MSysObjects")
    parent, top = None, 0
    for r in objects:
        oid = int(r.get("Id"))
        if 0 < oid < CONTAINER_IDS:  # ordinary objects; containers and MSysDb sit above
            top = max(top, oid)
        if str(r.get("Name")) == TABLES_CONTAINER and int(r.get("Type")) == 3:
            parent = oid
    if parent is None:
        raise ValueError("MSysObjects has no Tables container")
    new_id = top + 1
    HashMap = bridge.jclass("java.util.HashMap")
    now = bridge.jclass("java.time.LocalDateTime").now()
    obj = HashMap()
    obj.put("Id", bridge.jclass("java.lang.Integer")(new_id))
    obj.put("ParentId", bridge.jclass("java.lang.Integer")(parent))
    obj.put("Name", name)
    obj.put("Type", bridge.jclass("java.lang.Short")(QUERY_OBJECT_TYPE))
    obj.put("Flags", bridge.jclass("java.lang.Integer")(int(object_flag)))
    obj.put("DateCreate", now)
    obj.put("DateUpdate", now)
    objects.addRowFromMap(obj)
    import jpype

    queries = db.getSystemTable("MSysQueries")
    JByte = bridge.jclass("java.lang.Byte")
    JShort = bridge.jclass("java.lang.Short")
    for n, (attribute, expression, flag, name1, name2) in enumerate(rows):
        row = HashMap()
        row.put("ObjectId", bridge.jclass("java.lang.Integer")(new_id))
        row.put("Attribute", JByte(_signed_byte(attribute)))
        # Order: part of the (ObjectId, Attribute, Order) key; rows keep their sequence
        row.put("Order", jpype.JArray(jpype.JByte)(bytes([0, 0, n // 256, n % 256])))
        if expression is not None:
            row.put("Expression", expression)
        if flag is not None:
            row.put("Flag", JShort(int(flag)))
        if name1 is not None:
            row.put("Name1", name1)
        if name2 is not None:
            row.put("Name2", name2)
        queries.addRowFromMap(row)
    return new_id


class FormatError(Exception):
    """The formatter could not turn the rows into SQL (str(): the Java exception class)."""


def format_sql(
    name: str,
    rows: list[Row],
    object_flag: int = SELECT_OBJECT_FLAG,
    object_id: int = 1,
) -> str:
    from toolkit.reference import bridge

    jrows = java_rows(rows, object_id)
    QueryImpl = bridge.jclass("io.github.spannm.jackcess.impl.query.QueryImpl")
    q = QueryImpl.create(int(object_flag), name, jrows, int(object_id))
    try:
        return str(q.toSQLString())
    except Exception as exc:  # noqa: BLE001 - Java exceptions surface through JPype
        raise FormatError(bridge.java_exception_class(exc)) from None


def query_sql(query: dict, object_id: int = 1) -> tuple[str, str | None, str | None]:
    """(status, reason, sql) for one model query: status ok or unsupported (before app functions
    and parameters are looked at)."""
    qtype = query.get("query_type") or "select"
    text = query.get("sql")
    if qtype in REASONS:
        return "unsupported", REASONS[qtype], text
    if query.get("storage") == "sql" or not (query.get("design") or {}):
        sql = text
    else:
        try:
            flag, rows = design_rows(query)
            sql = format_sql(query["name"], rows, flag, object_id)
        except (FormatError, ValueError):
            return "unsupported", "other", text
    if not sql:
        return "unsupported", "other", None
    return "ok", None, sql


def model_queries(private: Path) -> list[dict]:
    path = Path(private) / "out" / "model" / "objects.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    return sorted(doc.get("queries") or [], key=lambda q: q["name"])


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print(
            "usage: python -m toolkit.reference.qbe <records>/engagement.json",
            file=sys.stderr,
        )
        return 2
    from toolkit.engagement import EngagementError, load
    from toolkit.sources import SourceError

    try:
        eng = load(argv[0])
        queries = model_queries(Path(eng["paths"]["private"]))
    except (EngagementError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"qbe: {type(exc).__name__}: cannot read the model", file=sys.stderr)
        return 2
    try:
        for n, q in enumerate(queries, start=1):
            status, reason, sql = query_sql(q, n)
            head = f"-- {q['name']}"
            if status != "ok":
                head += f": {status} ({reason})"
            print(head)
            print((sql or "").rstrip() or "-- (no SQL)")
            print()
    except SourceError as exc:
        print(f"qbe: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
