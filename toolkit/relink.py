"""Relink scan: what breaks or changes when an Access front end is relinked over ODBC.

Reads only the scrubbed object model (docs/contracts.md#object-model: schema.json, objects.json,
procedures.json, datamacros.json and code/<module>.txt), the engagement (through
toolkit.engagement.load, by the caller) and, when it exists, {{p.target}}/analysis/rules.json
for the rule ids of data macros. Never the source.

`scan()` returns the findings; `render()` turns them into the Markdown of
docs/stage1-frontend-changes.md: 12 fixed sections, names, line numbers and counts only (never
code text, query text, expressions or data), no timestamps, so the same model and engagement give
the same bytes.

VBA is read statement by statement: physical lines ending in ` _` are joined, comments (`'`
outside a string, `Rem`) are dropped, and string literals are kept apart so SQL assigned to a
variable earlier in the same procedure resolves the tables a call touches.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from toolkit import typemap
from toolkit.typemap import DIALECT_TITLES, DIALECTS

SECTIONS = (
    "Summary",
    "Type mappings",
    "Tables without a primary key",
    "IDENTITY tables written from code",
    "Yes/No fields",
    "Omitted columns",
    "Calculated and hyperlink fields",
    "Data macros that stop firing",
    "Validation rules and defaults",
    "Seek and table-type recordsets",
    "Pass-through queries and links",
    "ODBC driver and Office bitness",
)
NONE_FOUND = "None found."
NOT_ASSESSED = "Front-end code not available for this source kind: not assessed"
NO_CODE_KINDS = ("accdb", "mdb")
NOT_SET = "not set (demo)"
DYNAMIC = "table name built at run time"
MODEL_FILES = ("schema.json", "objects.json", "procedures.json", "datamacros.json")

ALL = ("postgresql", "mssql")
PG = ("postgresql",)
MS = ("mssql",)

GUIDANCE = (
    "Migrate an Access database to SQL Server (Microsoft Support)",
    "SQL Server Migration Assistant for Access (AccessToSQL) (Microsoft Learn)",
    "psqlODBC configuration options (PostgreSQL ODBC driver documentation)",
)


class ModelError(Exception):
    """Missing or unreadable model: exit 2. str() names files only."""


# --------------------------------------------------------------------------- model


def load_model(model_dir) -> dict:
    root = Path(model_dir)
    if not root.is_dir():
        raise ModelError(f"model directory missing: {root.name}")
    model: dict = {"dir": root}
    for name in MODEL_FILES:
        path = root / name
        if not path.is_file():
            if name in ("schema.json", "objects.json"):
                raise ModelError(f"model file missing: {name}")
            model[name] = {}
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ModelError(f"model file unreadable: {name} ({type(exc).__name__})") from None
        if doc.get("format_version") != 1:
            raise ModelError(f"model file {name}: format_version must be 1")
        model[name] = doc
    return model


def load_rules(path) -> dict | None:
    path = Path(path)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ModelError(f"rules file unreadable: {path.name} ({type(exc).__name__})") from None


def resolve_dialects(spec: str, eng: dict) -> list[str]:
    if spec == "from-engagement":
        db = eng.get("database") or {}
        wanted = [db.get("primary"), *(db.get("verify_also") or [])]
    else:
        wanted = [w.strip() for w in spec.split(",") if w.strip()]
    out: list[str] = []
    for d in wanted:
        if d not in DIALECTS:
            raise ModelError(f"unknown dialect {d!r}: expected postgresql or mssql")
        if d not in out:
            out.append(d)
    if not out:
        raise ModelError("no dialect selected")
    return [d for d in DIALECTS if d in out]


def source_kind(model: dict, eng: dict) -> str:
    return (
        model["schema.json"].get("source_kind")
        or model["objects.json"].get("source_kind")
        or (eng.get("source") or {}).get("kind")
        or ""
    )


# --------------------------------------------------------------------------- VBA statements

_LITERAL = re.compile(r'"((?:[^"]|"")*)"')
_REM = re.compile(r"^\s*(?:\d+\s+)?Rem\b", re.IGNORECASE)
_ASSIGN = re.compile(r"^\s*(?:\d+\s+)?(?:Let\s+)?([A-Za-z_]\w*)\s*=\s*(.+)$", re.IGNORECASE)
_OPEN_RS = re.compile(r"\.OpenRecordset\b\s*\(?", re.IGNORECASE)
_EXECUTE = re.compile(r"\.Execute\b\s*\(?", re.IGNORECASE)
_RUNSQL = re.compile(r"\bDoCmd\s*\.\s*RunSQL\b\s*\(?", re.IGNORECASE)
_SEE_CHANGES = re.compile(r"\bdbSeeChanges\b|\b512\b", re.IGNORECASE)
_SEEK = re.compile(r"\.Seek\b", re.IGNORECASE)
_INDEX = re.compile(r"\.Index\s*=", re.IGNORECASE)
_OPEN_TABLE = re.compile(r"\bdbOpenTable\b", re.IGNORECASE)
_CONNECT = re.compile(r"\.Connect\b", re.IGNORECASE)
_REFRESH = re.compile(r"\bRefreshLink\b", re.IGNORECASE)
_TRANSFER = re.compile(r"\bTransferDatabase\b", re.IGNORECASE)
_MSYS = re.compile(r"\bMSys\w*", re.IGNORECASE)
_EDITS = re.compile(r"\.(AddNew|Edit|Delete)\b(?!\s*\()", re.IGNORECASE)
_SQL_NAMES = re.compile(
    r"\b(?:FROM|INTO|UPDATE|JOIN)\s+(\[[^\]]+\]|[A-Za-z_][\w]*)", re.IGNORECASE
)
_TARGETS = (
    re.compile(r"\bINSERT\s+INTO\s+(\[[^\]]+\]|[A-Za-z_]\w*)", re.IGNORECASE),
    re.compile(r"^\s*UPDATE\s+(\[[^\]]+\]|[A-Za-z_]\w*)", re.IGNORECASE),
    re.compile(r"\bDELETE\b.*?\bFROM\s+(\[[^\]]+\]|[A-Za-z_]\w*)", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bSELECT\b.*?\bINTO\s+(\[[^\]]+\]|[A-Za-z_]\w*)", re.IGNORECASE | re.DOTALL),
)
_VERB = re.compile(r"^\s*(INSERT|UPDATE|DELETE|SELECT|TRANSFORM|PARAMETERS)\b", re.IGNORECASE)
_RS_TYPES = {
    "dbopentable": "table",
    "dbopendynaset": "dynaset",
    "dbopensnapshot": "snapshot",
    "dbopenforwardonly": "forward-only",
    "dbopendynamic": "dynamic",
    "1": "table",
    "2": "dynaset",
    "4": "snapshot",
    "8": "forward-only",
    "16": "dynamic",
}


@dataclass
class Statement:
    line: int  # 1-based line of the first physical line in the code file
    text: str  # joined, comment-free statement (literals kept)
    code: str  # the same with every literal emptied to ""
    literals: list[str] = field(default_factory=list)


def strip_comment(text: str) -> str:
    in_str = False
    for i, ch in enumerate(text):
        if ch == '"':
            in_str = not in_str
        elif ch == "'" and not in_str:
            return text[:i]
    return text


def statements(code_text: str) -> list[Statement]:
    lines = code_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[Statement] = []
    i = 0
    while i < len(lines):
        start = i
        buf = lines[i]
        while (buf.rstrip().endswith(" _") or buf.strip() == "_") and i + 1 < len(lines):
            buf = buf.rstrip()[:-1].rstrip() + " " + lines[i + 1].strip()
            i += 1
        i += 1
        text = strip_comment(buf)
        if _REM.match(text):
            text = ""
        if not text.strip():
            continue
        literals = [m.group(1).replace('""', '"') for m in _LITERAL.finditer(text)]
        code = _LITERAL.sub('""', text)
        out.append(Statement(start + 1, text, code, literals))
    return out


def _split_args(text: str) -> list[str]:
    """Top-level comma split of an argument list (strings and parentheses respected)."""
    args, depth, in_str, cur = [], 0, False, []
    for ch in text:
        if ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
            elif ch == "," and depth == 0:
                args.append("".join(cur).strip())
                cur = []
                continue
        cur.append(ch)
    args.append("".join(cur).strip())
    return args


# --------------------------------------------------------------------------- findings


@dataclass
class Findings:
    kind: str
    dialects: list[str]
    code_assessed: bool
    type_counts: dict = field(default_factory=dict)  # neutral type -> column count
    unsupported: list = field(default_factory=list)  # (table, column, type)
    rowversion: list = field(default_factory=list)  # (table, float column count)
    long_names: list = field(default_factory=list)  # (kind, name, bytes)
    no_pk: list = field(default_factory=list)  # (table, column count)
    identity: list = field(default_factory=list)  # dict rows
    dynamic: list = field(default_factory=list)  # (object, line, call): table named at run time
    yesno: list = field(default_factory=list)  # (table, column)
    omitted: list = field(default_factory=list)  # (table, column, what)
    calculated: list = field(default_factory=list)  # (table, column, kind)
    datamacros: list = field(default_factory=list)  # (table, event, name, actions, rule ids)
    validation: list = field(default_factory=list)  # (object, kind, fix, dialects)
    cascades: list = field(default_factory=list)  # (relationship, from, to, action)
    seek: list = field(default_factory=list)  # (object, line, pattern)
    links: list = field(default_factory=list)  # (kind, name, detail)
    code_links: list = field(default_factory=list)  # (object, line, pattern)
    odbc: dict = field(default_factory=dict)


def _bare(name: str) -> str:
    name = name.strip()
    return name[1:-1] if name.startswith("[") and name.endswith("]") else name


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("-1", "1", "true", "yes")
    return False


_LITERAL_DEFAULT = re.compile(
    r'^\s*(?:[-+]?\d+(?:\.\d+)?|"(?:[^"]|"")*"|\'[^\']*\'|#[^#]*#|yes|no|true|false|on|off|null)\s*$',
    re.IGNORECASE,
)
_FUNC = re.compile(r"([A-Za-z_]\w*)\s*\(")
_SERVER_DEFAULTS = {
    "now": "CURRENT_TIMESTAMP on both servers",
    "date": "CURRENT_DATE on PostgreSQL, CAST(GETDATE() AS date) on SQL Server",
    "time": "CURRENT_TIME on PostgreSQL, CAST(GETDATE() AS time) on SQL Server",
}


class _Names:
    def __init__(self, model: dict):
        schema = model["schema.json"]
        objects = model["objects.json"]
        self.tables = {t["name"]: t for t in schema.get("tables", [])}
        self.lower_tables = {n.lower(): n for n in self.tables}
        self.queries = {q["name"]: q for q in objects.get("queries", [])}
        self.lower_queries = {n.lower(): n for n in self.queries}
        self.autonumber = {
            n for n, t in self.tables.items()
            if any(c.get("type") == "autonumber" for c in t.get("columns", []))
        }
        self._base: dict[str, set[str]] = {}

    def base_tables(self, name: str, seen=None) -> set[str]:
        """Tables behind a table or query name (queries resolved recursively)."""
        key = _bare(name).lower()
        if key in self.lower_tables:
            return {self.lower_tables[key]}
        if key not in self.lower_queries:
            return set()
        qname = self.lower_queries[key]
        if qname in self._base:
            return self._base[qname]
        seen = set() if seen is None else seen
        if qname in seen:
            return set()
        seen.add(qname)
        q = self.queries[qname]
        names = [i.get("name", "") for i in (q.get("design") or {}).get("input_tables", [])]
        if q.get("sql"):
            names += [m.group(1) for m in _SQL_NAMES.finditer(q["sql"])]
        out: set[str] = set()
        for n in names:
            out |= self.base_tables(n, seen)
        self._base[qname] = out
        return out

    def from_sql(self, texts: list[str], action: bool) -> tuple[set[str], str | None]:
        """Tables a call touches, from its literal texts; and the SQL verb when there is one."""
        joined = " ".join(texts)
        verb = None
        m = _VERB.match(joined)
        if m:
            verb = m.group(1).upper()
            if verb == "SELECT" and action and re.search(r"\bINTO\b", joined, re.IGNORECASE):
                verb = "SELECT INTO"
        stripped = [t.strip() for t in texts if t.strip()]
        if len(stripped) == 1 and not verb:
            direct = self.base_tables(stripped[0])
            if direct:
                return direct, None
        names: list[str] = []
        if action:
            for rx in _TARGETS:
                hit = rx.search(joined)
                if hit:
                    names = [hit.group(1)]
                    break
        if not names:
            names = [m.group(1) for m in _SQL_NAMES.finditer(joined)]
        out: set[str] = set()
        for n in names:
            out |= self.base_tables(n)
        return out, verb


def _proc_for(module: dict, line: int) -> str:
    for p in module.get("procedures", []):
        if p.get("start", 0) <= line <= p.get("end", 0):
            return f"{module['name']}.{p['name']}"
    return f"{module['name']} (declarations)"


def _proc_refs(module: dict, line: int) -> list[str]:
    for p in module.get("procedures", []):
        if p.get("start", 0) <= line <= p.get("end", 0):
            return list((p.get("refs") or {}).get("tables", []))
    return []


def _scan_code(model: dict, names: _Names, f: Findings) -> None:
    modules = (model["procedures.json"] or {}).get("modules", [])
    for module in sorted(modules, key=lambda m: m["name"]):
        code_file = module.get("code_file")
        if not code_file:
            continue
        path = model["dir"] / code_file
        if not path.is_file():
            continue
        stmts = statements(path.read_text(encoding="utf-8", errors="replace"))
        # Statements grouped by procedure, so variables and edits stay procedure-local.
        by_proc: dict[str, list[Statement]] = {}
        for st in stmts:
            by_proc.setdefault(_proc_for(module, st.line), []).append(st)
        for proc, body in by_proc.items():
            edits = any(_EDITS.search(st.code) for st in body)
            variables: dict[str, list[str]] = {}
            for st in body:
                _scan_statement(module, proc, st, edits, variables, names, f)


def _arg_texts(arg: str, variables: dict[str, list[str]]) -> list[str]:
    texts = [m.group(1).replace('""', '"') for m in _LITERAL.finditer(arg)]
    code = _LITERAL.sub('""', arg)
    for var, lits in variables.items():
        if re.search(rf"\b{re.escape(var)}\b", code, re.IGNORECASE):
            texts.extend(lits)
    return texts


def _scan_statement(module, proc, st, edits, variables, names, f) -> None:
    code = st.code
    # Track string variables: x = "..." resets, x = x & "..." appends.
    m = _ASSIGN.match(st.text)
    if m and not re.match(r"^\s*(?:\d+\s+)?(Set|If|ElseIf|For|Do|While|Case)\b", st.text, re.IGNORECASE):
        var, rhs = m.group(1).lower(), m.group(2)
        lits = [x.group(1).replace('""', '"') for x in _LITERAL.finditer(rhs)]
        rhs_code = _LITERAL.sub('""', rhs)
        if re.search(rf"\b{re.escape(var)}\b", rhs_code, re.IGNORECASE):
            variables[var] = variables.get(var, []) + lits
        else:
            variables[var] = lits
            # also pick up literals of other variables the expression concatenates
            for other, olits in list(variables.items()):
                if other != var and re.search(rf"\b{re.escape(other)}\b", rhs_code, re.IGNORECASE):
                    variables[var] = variables[var] + olits

    spans = [(m.start(), m.end()) for m in _LITERAL.finditer(st.text)]
    calls = []
    for rx, call in ((_OPEN_RS, "OpenRecordset"), (_EXECUTE, "Execute"), (_RUNSQL, "DoCmd.RunSQL")):
        for hit in rx.finditer(st.text):
            if not any(a <= hit.start() < b for a, b in spans):
                calls.append((hit.end(), call))
    for pos, call in sorted(calls):
        args = _split_args(st.text[pos:])
        first = args[0] if args else ""
        options = _LITERAL.sub('""', ",".join(args[1:]))
        see_changes = bool(_SEE_CHANGES.search(options))
        if call == "OpenRecordset":
            first_code = _LITERAL.sub('""', first).strip().lower()
            if first_code in _RS_TYPES:
                rs_type, texts = _RS_TYPES[first_code], []
            else:
                kind = _LITERAL.sub('""', args[1]).strip().lower() if len(args) > 1 else ""
                rs_type = _RS_TYPES.get(kind)
                texts = _arg_texts(first, variables) if first else []
            if (
                rs_type is None
                and len(texts) == 1
                and _LITERAL.fullmatch(first.strip())
                and _bare(texts[0]).lower() in names.lower_tables
            ):
                f.seek.append((proc, st.line, "OpenRecordset on a table without a type"))
            if rs_type in ("snapshot", "forward-only", "table"):
                continue
            tables, _verb = names.from_sql(texts, action=False) if texts else (set(), None)
            how = f"OpenRecordset ({rs_type or 'default type'}"
            how += ", edits rows)" if edits else ")"
            _identity_row(f, names, module, proc, st.line, how, tables, bool(texts), see_changes)
            if texts and not tables:
                f.dynamic.append((proc, st.line, how))
        else:
            texts = _arg_texts(first, variables)
            tables, verb = names.from_sql(texts, action=True) if texts else (set(), None)
            if verb in ("SELECT", "TRANSFORM"):
                continue
            label = "Database.Execute" if call == "Execute" else call
            how = f"{label} ({verb})" if verb else f"{label} (action)"
            if call == "DoCmd.RunSQL":
                see_changes = False
            _identity_row(f, names, module, proc, st.line, how, tables, bool(texts), see_changes)
            if texts and not tables:
                f.dynamic.append((proc, st.line, how))

    for rx, label, bucket in (
        (_SEEK, ".Seek", f.seek),
        (_INDEX, ".Index", f.seek),
        (_CONNECT, "TableDef .Connect", f.code_links),
        (_REFRESH, "RefreshLink", f.code_links),
        (_TRANSFER, "DoCmd.TransferDatabase", f.code_links),
    ):
        if rx.search(code):
            bucket.append((proc, st.line, label))
    if _OPEN_TABLE.search(code):
        f.seek.append((proc, st.line, "dbOpenTable recordset"))
    msys = sorted({m.group(0) for m in _MSYS.finditer(st.text)})
    if msys:
        f.code_links.append((proc, st.line, "system table " + ", ".join(msys)))


def _identity_row(f, names, module, proc, line, how, tables, resolved, see_changes) -> None:
    if see_changes:
        return
    if resolved and tables:
        hit = sorted(t for t in tables if t in names.autonumber)
        if not hit:
            return
        source = "code"
    elif resolved:
        # SQL text found, but no model table in it: the table name is built at run time
        # (a format placeholder or a concatenated variable), so it can be any IDENTITY table.
        hit = []
        source = DYNAMIC
    else:
        hit = sorted(t for t in _proc_refs(module, line) if t in names.autonumber)
        source = "procedure references" if hit else "not resolved"
    f.identity.append(
        {"object": proc, "line": line, "call": how, "tables": hit, "from": source}
    )


def _cascade_conflicts(relationships: list) -> list:
    out = set()
    for action in ("delete", "update"):
        edges = [
            r for r in relationships
            if r.get("enforce", True) and r.get(f"cascade_{action}")
        ]
        children: dict[str, list] = {}
        for r in edges:
            children.setdefault(r["to_table"], []).append(r)
        for r in edges:
            if r["to_table"] == r["from_table"]:
                out.add((r["name"], r["from_table"], r["to_table"], action))
        for start in sorted(children):
            counts: dict[str, int] = {}
            via: dict[str, list] = {}
            stack = [(start, (start,))]
            while stack:
                node, path = stack.pop()
                for r in children.get(node, []):
                    nxt = r["from_table"]
                    if nxt in path:
                        out.add((r["name"], r["from_table"], r["to_table"], action))
                        continue
                    counts[nxt] = counts.get(nxt, 0) + 1
                    via.setdefault(nxt, []).append(r)
                    stack.append((nxt, path + (nxt,)))
            for node, n in counts.items():
                if n > 1:
                    for r in via[node]:
                        out.add((r["name"], r["from_table"], r["to_table"], action))
    return sorted(out)


def _rule_ids(rules: dict | None, table: str, event: str, name: str | None) -> list[str]:
    if not rules:
        return []
    keys = {f"{table}.{event}"}
    if name:
        keys.add(f"{table}.{name}")
    ids = set()
    for r in rules.get("rules", []):
        trig = r.get("trigger") or ""
        if trig.startswith("datamacro:") and trig[len("datamacro:"):] in keys:
            ids.add(r["id"])
        for s in r.get("sources", []):
            if s.get("type") in ("data_macro", "datamacro") and s.get("ref") in keys:
                ids.add(r["id"])
    return sorted(ids)


def scan(eng: dict, model: dict, dialects: list[str], rules: dict | None) -> Findings:
    kind = source_kind(model, eng)
    f = Findings(kind=kind, dialects=list(dialects), code_assessed=kind not in NO_CODE_KINDS)
    names = _Names(model)
    schema = model["schema.json"]
    objects = model["objects.json"]

    for t in sorted(schema.get("tables", []), key=lambda t: t["name"]):
        tname = t["name"]
        cols = t.get("columns", [])
        if typemap.too_long_for_postgresql(tname):
            f.long_names.append(("table", tname, len(tname.encode("utf-8"))))
        if not t.get("primary_key"):
            f.no_pk.append((tname, len(cols)))
        floats = sum(1 for c in cols if c.get("type") in typemap.FLOAT_TYPES)
        if floats:
            f.rowversion.append((tname, floats))
        for c in cols:
            cname, ctype = c["name"], c.get("type")
            col = f"{tname}.{cname}"
            if typemap.too_long_for_postgresql(cname):
                f.long_names.append(("column", col, len(cname.encode("utf-8"))))
            if typemap.is_supported(ctype):
                f.type_counts[ctype] = f.type_counts.get(ctype, 0) + 1
            else:
                f.unsupported.append((tname, cname, str(ctype)))
                continue
            props = c.get("properties") or {}
            if ctype == "yesno":
                f.yesno.append((tname, cname))
            if ctype == "complex":
                f.omitted.append((tname, cname, "complex (attachment or multi-valued)"))
            elif ctype == "oleobject":
                f.omitted.append((tname, cname, "oleobject"))
            elif _truthy(props.get("AllowMultipleValues")):
                f.omitted.append((tname, cname, "multi-valued lookup (values flattened)"))
            if c.get("computed"):
                f.calculated.append((tname, cname, "calculated"))
            if ctype == "hyperlink":
                f.calculated.append((tname, cname, "hyperlink"))
            if props.get("ValidationRule"):
                f.validation.append(
                    (col, "field validation rule",
                     ("Not carried over: Access does not enforce a linked table's field rules. "
                     "Add a CHECK constraint on the server or validate in the form."), ALL)
                )
            default = props.get("DefaultValue")
            if isinstance(default, str) and default.strip() and not _LITERAL_DEFAULT.match(default):
                funcs = sorted({m.group(1) for m in _FUNC.finditer(default)})
                server = [_SERVER_DEFAULTS[x.lower()] for x in funcs if x.lower() in _SERVER_DEFAULTS]
                what = "expression default" + (f" ({', '.join(x + '()' for x in funcs)})" if funcs else "")
                fix = "Not carried over. " + (
                    f"Set a server default: {'; '.join(server)}."
                    if server and len(server) == len(funcs)
                    else "Set it in the form's control or in a server default."
                )
                f.validation.append((col, what, fix, ALL))
        tprops = t.get("properties") or {}
        if tprops.get("ValidationRule"):
            f.validation.append(
                (tname, "table validation rule",
                 "Not carried over. Add a table CHECK constraint on the server or validate in the form.",
                 ALL)
            )
        for idx in t.get("indexes", []):
            iname = idx.get("name") or ""
            if typemap.too_long_for_postgresql(iname):
                f.long_names.append(("index", f"{tname}.{iname}", len(iname.encode("utf-8"))))
    for r in schema.get("relationships", []):
        if typemap.too_long_for_postgresql(r.get("name", "")):
            f.long_names.append(("relationship", r["name"], len(r["name"].encode("utf-8"))))
    f.long_names.sort()
    f.cascades = _cascade_conflicts(schema.get("relationships", []))
    f.yesno.sort()
    f.omitted.sort()
    f.calculated.sort()
    f.validation.sort(key=lambda v: (v[0], v[1]))

    for dm in sorted(
        (model["datamacros.json"] or {}).get("datamacros", []),
        key=lambda d: (d.get("table", ""), d.get("event", ""), d.get("name") or ""),
    ):
        f.datamacros.append(
            (dm.get("table", ""), dm.get("event", ""), dm.get("name"),
             len(dm.get("actions", [])), _rule_ids(rules, dm.get("table", ""), dm.get("event", ""), dm.get("name")))
        )

    # Links: pass-through queries, linked tables, external links, MSys references in the design.
    for q in sorted(objects.get("queries", []), key=lambda q: q["name"]):
        if q.get("query_type") == "pass-through":
            f.links.append(("pass-through query", q["name"], "sent to the server unchanged"))
        hits = set()
        if q.get("sql"):
            hits |= {m.group(0) for m in _MSYS.finditer(q["sql"])}
        for it in (q.get("design") or {}).get("input_tables", []):
            if _MSYS.match(it.get("name") or ""):
                hits.add(it["name"])
        if hits:
            f.links.append(("query reads a system table", q["name"], ", ".join(sorted(hits))))
    for lt in sorted(objects.get("linked_tables", []), key=lambda x: x["name"]):
        f.links.append(("linked table", lt["name"], f"back end: {lt.get('backend_kind') or 'unresolved'}"))
    for link in sorted(
        (eng.get("source") or {}).get("external_links") or [],
        key=lambda x: (x.get("name", ""), x.get("type", "")),
    ):
        f.links.append(("external link", link.get("name", ""), f"type: {link.get('type')}"))
    for kind_key, label in (("forms", "form"), ("reports", "report")):
        for obj in sorted(objects.get(kind_key, []), key=lambda x: x["name"]):
            srcs = [obj.get("record_source") or ""]
            srcs += [c.get("row_source") or "" for c in obj.get("controls", [])]
            srcs += [c.get("control_source") or "" for c in obj.get("controls", [])]
            hits = sorted({m.group(0) for s in srcs for m in _MSYS.finditer(s)})
            if hits:
                f.links.append((f"{label} reads a system table", obj["name"], ", ".join(hits)))

    if f.code_assessed:
        _scan_code(model, names, f)
    f.identity.sort(key=lambda r: (r["object"], r["line"], r["call"]))
    f.dynamic = sorted(set(f.dynamic))
    f.seek = sorted(set(f.seek))
    f.code_links = sorted(set(f.code_links))

    stage1 = (eng.get("database") or {}).get("stage1")
    f.odbc = {
        "odbc_driver": (stage1 or {}).get("odbc_driver") if stage1 else None,
        "office_bitness": (stage1 or {}).get("office_bitness") if stage1 else None,
        "stage1_set": bool(stage1),
    }
    return f


def unsupported_messages(f: Findings) -> list[str]:
    return [
        f"{t}.{c}: type {ty} has no mapping for {', '.join(DIALECT_TITLES[d] for d in f.dialects)}"
        for t, c, ty in f.unsupported
    ]


# --------------------------------------------------------------------------- render


def _identity_fix(call: str) -> str:
    if call.startswith("DoCmd.RunSQL"):
        return "Replace with a database Execute call with dbFailOnError + dbSeeChanges"
    if call.startswith("OpenRecordset"):
        return "Add dbSeeChanges to the OpenRecordset options (run-time error 3622 otherwise)"
    return "Add dbSeeChanges to the Execute options"


def _cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _name(value) -> str:
    return f"`{_cell(value)}`"


def _table(header: list[str], rows: list[list]) -> list[str]:
    if not rows:
        return [NONE_FOUND]
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(_cell(c) if not str(c).startswith("`") else str(c) for c in r) + " |" for r in rows]
    return out


def _dl(f: Findings, affects) -> str:
    hit = [d for d in f.dialects if d in affects]
    return ", ".join(DIALECT_TITLES[d] for d in hit)


def _applies(f: Findings, affects) -> bool:
    return any(d in f.dialects for d in affects)


def counts(f: Findings) -> dict:
    ms = _applies(f, MS)
    pg = _applies(f, PG)
    return {
        "mapped_columns": sum(v for k, v in f.type_counts.items() if k not in typemap.OMITTED_TYPES),
        "unsupported_types": len(f.unsupported),
        "rowversion_tables": len(f.rowversion) if ms else 0,
        "long_names": len(f.long_names) if pg else 0,
        "tables_without_key": len(f.no_pk),
        "identity_writes": len(f.identity) if ms else 0,
        "dynamic_writes": len(f.dynamic),
        "yesno_fields": len(f.yesno),
        "omitted_columns": len(f.omitted),
        "calculated_hyperlink": len(f.calculated),
        "data_macros": len(f.datamacros),
        "validation_defaults": len(f.validation),
        "cascade_paths": len(f.cascades) if ms else 0,
        "seek_table_type": len(f.seek),
        "passthrough_links": len(f.links) + len(f.code_links),
    }


def render(eng: dict, f: Findings) -> str:
    dialect_names = " and ".join(DIALECT_TITLES[d] for d in f.dialects)
    ms = _applies(f, MS)
    pg = _applies(f, PG)
    c = counts(f)
    out: list[str] = [
        "# Stage 1 front-end changes",
        "",
        (f"Generated by `toolkit relink-scan` from the scrubbed object model of engagement "
        f"`{eng.get('id')}` (source kind `{f.kind}`) for {dialect_names}. Do not edit by hand: "
        "re-run the command, and `--check` compares this file with a fresh render."),
        "",
        ("It lists every table design and front-end pattern that breaks or behaves differently "
        "once the Access front end is relinked to the server over ODBC, with the mapping or fix "
        "for each. It holds names, line numbers and counts only: no code, query text or data. "
        "Line numbers are lines of the module text as the model keeps it (`code/<module>.txt`)."),
        "",
        "Background reading (cited by title, not copied): " + "; ".join(GUIDANCE) + ".",
        "",
    ]

    def section(title: str, body: list[str]) -> None:
        out.extend([f"## {title}", "", *body, ""])

    na = "not assessed"
    rows = [
        ["Columns with a type mapping", c["mapped_columns"], _dl(f, ALL)],
        ["Columns with an unsupported type", c["unsupported_types"], _dl(f, ALL)],
        ["Tables that gain mp_rowversion", c["rowversion_tables"] if ms else "n/a", _dl(f, MS) or "n/a"],
        ["Names longer than 63 bytes", c["long_names"] if pg else "n/a", _dl(f, PG) or "n/a"],
        ["Tables without a primary key", c["tables_without_key"], _dl(f, ALL)],
        ["IDENTITY tables written from code", (c["identity_writes"] if ms else "n/a") if f.code_assessed else na, _dl(f, MS) or "n/a"],
        ["Writes to tables named at run time", c["dynamic_writes"] if f.code_assessed else na, _dl(f, ALL)],
        ["Yes/No fields", c["yesno_fields"], _dl(f, ALL)],
        ["Omitted columns", c["omitted_columns"], _dl(f, ALL)],
        ["Calculated and hyperlink fields", c["calculated_hyperlink"], _dl(f, ALL)],
        ["Data macros that stop firing", c["data_macros"], _dl(f, ALL)],
        ["Validation rules and defaults not carried over", c["validation_defaults"], _dl(f, ALL)],
        ["Relationships with multiple cascade paths", c["cascade_paths"] if ms else "n/a", _dl(f, MS) or "n/a"],
        ["Seek and table-type recordsets", c["seek_table_type"] if f.code_assessed else na, _dl(f, ALL)],
        ["Pass-through queries and links", c["passthrough_links"], _dl(f, ALL)],
        ["ODBC driver and Office bitness", "set" if f.odbc.get("stage1_set") else NOT_SET, _dl(f, ALL)],
    ]
    section("Summary", _table(["Check", "Findings", "Dialects"], rows))

    # Type mappings
    trows = []
    for t in typemap.NEUTRAL_TYPES:
        if t not in f.type_counts:
            continue
        cells = [t, f.type_counts[t]]
        for d in f.dialects:
            cells.append("omitted" if t in typemap.OMITTED_TYPES else typemap.type_template(t, d))
        trows.append(cells)
    body = _table(["Neutral type", "Columns", *(DIALECT_TITLES[d] for d in f.dialects)], trows)
    body += ["", "### Unsupported types", ""]
    body += _table(
        ["Column", "Type", "Dialects", "Fix"],
        [[_name(f"{t}.{col}"), ty, _dl(f, ALL), "No mapping: extend toolkit/typemap.py before the load"]
         for t, col, ty in f.unsupported],
    )
    body += ["", "### Tables that gain mp_rowversion on SQL Server", ""]
    body += _table(
        ["Table", "Single or double columns", "Dialects", "Fix"],
        [[_name(t), n, _dl(f, MS),
          "stage1 adds mp_rowversion rowversion, so Access compares it instead of the floats and stops reporting write conflicts"]
         for t, n in f.rowversion] if ms else [],
    )
    body += ["", "### Names longer than 63 bytes", ""]
    body += _table(
        ["Kind", "Name", "Bytes", "Dialects", "Fix"],
        [[k, _name(n), b, _dl(f, PG), "PostgreSQL truncates it: shorten the name and keep the Access name as the linked table's local name"]
         for k, n, b in f.long_names] if pg else [],
    )
    section("Type mappings", body)

    section(
        "Tables without a primary key",
        _table(
            ["Table", "Columns", "Dialects", "Fix"],
            [[_name(t), n, _dl(f, ALL),
              "Read-only once linked: add a primary key (a surrogate IDENTITY key when there is no natural one) before relinking"]
             for t, n in f.no_pk],
        ),
    )

    if not f.code_assessed:
        body = [NOT_ASSESSED]
    else:
        body = _table(
            ["Procedure", "Line", "Call", "IDENTITY tables", "Resolved from", "Dialects", "Fix"],
            [[_name(r["object"]), r["line"], r["call"],
              ", ".join(_name(t) for t in r["tables"])
              or ("any IDENTITY table" if r["from"] == DYNAMIC else "not resolved"),
              r["from"], _dl(f, MS),
              _identity_fix(r["call"])]
             for r in f.identity] if ms else [],
        )
        body += ["", "### Writes to tables named at run time", ""]
        body += _table(
            ["Procedure", "Line", "Call", "Dialects", "Fix"],
            [[_name(o), ln, call, _dl(f, ALL),
              ("Writes whichever server table the name resolves to once relinked: confirm it should "
               "still run after cutover (startup and admin routines included) and list it in the cutover notes")]
             for o, ln, call in f.dynamic],
        )
    section("IDENTITY tables written from code", body)

    yn_fix = []
    if pg:
        yn_fix.append("PostgreSQL boolean NOT NULL DEFAULT false; set 'True is -1' in the psqlODBC data source so Access reads Yes as -1")
    if ms:
        yn_fix.append("SQL Server bit NOT NULL DEFAULT 0, so Access never meets a Null bit (write conflicts)")
    section(
        "Yes/No fields",
        _table(
            ["Column", "Dialects", "Fix"],
            [[_name(f"{t}.{col}"), _dl(f, ALL), "; ".join(yn_fix)] for t, col in f.yesno],
        ),
    )

    omit_fix = {
        "oleobject": "Not created or loaded: store the objects as files or a binary column (decided at the gate) and remove the bound control",
    }
    section(
        "Omitted columns",
        _table(
            ["Column", "Kind", "Dialects", "Fix"],
            [[_name(f"{t}.{col}"), what, _dl(f, ALL),
              omit_fix.get(what, "Not created or loaded: move the values to a child table or a file store (decided at the gate) and remove the bound control")]
             for t, col, what in f.omitted],
        ),
    )

    calc_fix = {
        "calculated": "Loaded as a plain value that no longer updates itself: compute it in a view, a computed column or front-end code",
        "hyperlink": "Loaded as plain text that no longer opens as a link: set Is Hyperlink on the bound controls",
    }
    section(
        "Calculated and hyperlink fields",
        _table(
            ["Column", "Kind", "Dialects", "Fix"],
            [[_name(f"{t}.{col}"), k, _dl(f, ALL), calc_fix[k]] for t, col, k in f.calculated],
        ),
    )

    section(
        "Data macros that stop firing",
        _table(
            ["Table", "Event", "Name", "Actions", "Rule ids", "Dialects", "Fix"],
            [[_name(t), e, _name(n) if n else "-", a, ", ".join(ids) or "-", _dl(f, ALL),
              "Stops firing on the linked table: replace with a database trigger or front-end code (decided at the gate)"]
             for t, e, n, a, ids in f.datamacros],
        ),
    )

    body = _table(
        ["Object", "Kind", "Dialects", "Fix"],
        [[_name(o), k, _dl(f, aff), fix] for o, k, fix, aff in f.validation if _applies(f, aff)],
    )
    body += ["", "### Relationships with multiple cascade paths", ""]
    body += _table(
        ["Relationship", "From", "To", "Cascade", "Dialects", "Fix"],
        [[_name(n), _name(fr), _name(to), action, _dl(f, MS),
          "SQL Server rejects the foreign key: keep it without cascade and cascade in a trigger or front-end code"]
         for n, fr, to, action in f.cascades] if ms else [],
    )
    section("Validation rules and defaults", body)

    seek_fix = {
        ".Seek": "Seek needs a table-type recordset, which linked tables do not have: use FindFirst on a dynaset or a WHERE clause",
        ".Index": "Index applies only to table-type recordsets: open a dynaset with ORDER BY instead",
        "dbOpenTable recordset": "Linked tables cannot open as dbOpenTable: use dbOpenDynaset (with dbSeeChanges on SQL Server)",
    }
    if not f.code_assessed:
        body = [NOT_ASSESSED]
    else:
        body = _table(
            ["Procedure", "Line", "Pattern", "Dialects", "Fix"],
            [[_name(o), ln, p, _dl(f, ALL),
              seek_fix.get(p, "Becomes a dynaset once linked: check RecordCount, Seek and Index use, and open it explicitly as a dynaset")]
             for o, ln, p in f.seek],
        )
    section("Seek and table-type recordsets", body)

    link_fix = {
        "pass-through query": "Rewrite its SQL for the new server and point its connect string at the new data source",
        "linked table": "Relink it to the new data source or keep its current back end (decided at the gate)",
        "external link": "Keep the link and check it still opens from the relinked front end",
        "query reads a system table": "System tables describe the local file, not the server: replace the lookup",
        "form reads a system table": "System tables describe the local file, not the server: replace the lookup",
        "report reads a system table": "System tables describe the local file, not the server: replace the lookup",
    }
    code_fix = {
        "TableDef .Connect": "Point the connect string at the new ODBC data source (no credentials in code)",
        "RefreshLink": "Relink to the new ODBC data source; refresh only the server tables",
        "DoCmd.TransferDatabase": "Update the transfer to the ODBC data source or drop it (decided at the gate)",
    }
    body = _table(
        ["Kind", "Name", "Detail", "Dialects", "Fix"],
        [[k, _name(n), d, _dl(f, ALL), link_fix[k]] for k, n, d in f.links],
    )
    body += ["", "### Code that reads or changes links", ""]
    if not f.code_assessed:
        body += [NOT_ASSESSED]
    else:
        body += _table(
            ["Procedure", "Line", "Pattern", "Dialects", "Fix"],
            [[_name(o), ln, p, _dl(f, ALL),
              code_fix.get(p, "System tables describe the local file, not the server: replace the lookup")]
             for o, ln, p in f.code_links],
        )
    section("Pass-through queries and links", body)

    def setting(v) -> str:
        if not f.odbc.get("stage1_set"):
            return NOT_SET
        return _cell(v) if v not in (None, "") else "not set"

    rows = [
        ["ODBC driver", setting(f.odbc.get("odbc_driver")), _dl(f, ALL),
         "Install the driver whose bitness matches Office on every machine that runs the front end"],
        ["Office bitness", setting(f.odbc.get("office_bitness")), _dl(f, ALL),
         "32-bit Office needs the 32-bit driver and a 32-bit data source; 64-bit needs 64-bit"],
    ]
    if pg:
        rows.append(["PostgreSQL driver", "psqlODBC (Unicode)", _dl(f, PG),
                     "Set 'True is -1' and keep 'Bools as Char' off in the data source"])
    if ms:
        rows.append(["SQL Server driver", "Microsoft ODBC Driver 18 for SQL Server", _dl(f, MS),
                     "Encrypt the connection; trust the server certificate only on a private network"])
    section("ODBC driver and Office bitness", _table(["Setting", "Value", "Dialects", "Note"], rows))

    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def split_sections(text: str) -> dict[str, str]:
    """Section title -> text; the part before the first `## ` is '(header)'."""
    parts: dict[str, list[str]] = {"(header)": []}
    current = "(header)"
    for line in text.split("\n"):
        if line.startswith("## "):
            current = line[3:].strip()
            parts.setdefault(current, [])
            continue
        parts[current].append(line)
    return {k: "\n".join(v) for k, v in parts.items()}


def differing_sections(expected: str, actual: str) -> list[str]:
    a, b = split_sections(expected), split_sections(actual)
    order = list(a) + [k for k in b if k not in a]
    return [k for k in order if a.get(k) != b.get(k)]
