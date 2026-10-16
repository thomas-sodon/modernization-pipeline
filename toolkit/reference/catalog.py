"""The `reference sql` step: the reference catalog of an Access engagement.

Reads only the design zone (`<private>/out/model/`) and the committed app-function rewrites
(`<target>/goldens/reference/functions/<Function>.json`), and writes
`<private>/out/reference/catalog.json` and `out/reference/sql/<file-safe id>.sql`
(docs/contracts.md#reference-catalog). One unit per saved query:

- SQL: the formatter's SQL for a grid design (toolkit.reference.qbe), the stored text for an
  SQL-only query; then every call of an app function (a VBA Function of procedures.json) is
  replaced by its correlated-subquery rewrite, or kept when java/ registers it, or the unit is
  unsupported with reason unsupported-function;
- `depends_on`: the saved queries the SQL reads, transitively, each after its own dependencies;
- `params`: declared parameters (source prompt) and Forms!/Reports! references (source
  form_control); a unit with params is needs-params (reason form-reference-unresolved for a form
  reference, other for a prompt);
- a unit reading an unsupported unit is unsupported with that reason, one reading a needs-params
  unit is needs-params with its params;
- `needs_access_confirmation`: the SQL calls Format, FormatDateTime, DateValue, CDate, DatePart,
  DateDiff, DateAdd, Weekday, MonthName or an app function.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from toolkit.reference import qbe

ENGINE = "ucanaccess-5.1.8"
FORMAT_VERSION = 1
MODEL_FILES = ("schema.json", "objects.json", "procedures.json")
CONFIRM_FUNCTIONS = (
    "Format",
    "FormatDateTime",
    "DateValue",
    "CDate",
    "DatePart",
    "DateDiff",
    "DateAdd",
    "Weekday",
    "MonthName",
)
REASONS = (
    "action-query",
    "crosstab",
    "form-reference-unresolved",
    "unsupported-function",
    "pass-through",
    "other",
)
FUNCTION_KEYS = {"function", "args", "rewrite", "rule", "choice"}
MAX_REWRITES = 500
ACCESS_PARAM_TYPES = {
    "text": "text",
    "char": "text",
    "varchar": "text",
    "memo": "memo",
    "longtext": "memo",
    "long": "longinteger",
    "integer": "longinteger",
    "int": "longinteger",
    "short": "integer",
    "smallint": "integer",
    "byte": "byte",
    "currency": "currency",
    "money": "currency",
    "ieeesingle": "single",
    "single": "single",
    "real": "single",
    "ieeedouble": "double",
    "double": "double",
    "float": "double",
    "datetime": "datetime",
    "date": "datetime",
    "bit": "yesno",
    "yesno": "yesno",
    "logical": "yesno",
    "decimal": "decimal",
    "numeric": "decimal",
    "guid": "guid",
    "value": "text",
}


class CatalogError(Exception):
    """A design-zone input is missing or malformed (exit 2)."""


# ------------------------------------------------------------------ paths


def file_safe(name: str) -> str:
    """docs/contracts.md Conventions: characters outside [A-Za-z0-9._-] become _, and when any
    was replaced, - and the first 8 hex digits of sha256(name) are appended."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    if safe != name:
        safe += "-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return safe


def reference_dir(private) -> Path:
    return Path(private) / "out" / "reference"


def catalog_path(private) -> Path:
    return reference_dir(private) / "catalog.json"


def functions_dir(target) -> Path:
    return Path(target) / "goldens" / "reference" / "functions"


def input_files(private, target) -> list[Path]:
    model = Path(private) / "out" / "model"
    files = [model / f for f in MODEL_FILES if (model / f).is_file()]
    fdir = functions_dir(target)
    if fdir.is_dir():
        files += sorted(fdir.glob("*.json"))
    return files


def catalog_state(private, target) -> str | None:
    """None when the catalog is current; 'missing' or 'older than the model' otherwise."""
    path = catalog_path(private)
    if not path.is_file():
        return "missing"
    newest = max(
        (p.stat().st_mtime_ns for p in input_files(private, target)), default=0
    )
    if path.stat().st_mtime_ns < newest:
        return "older than the model"
    return None


def load_catalog(private) -> dict:
    path = catalog_path(private)
    if not path.is_file():
        raise CatalogError("out/reference/catalog.json missing")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("format_version") != FORMAT_VERSION:
        raise CatalogError("catalog.json: unknown format_version")
    return doc


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


# ------------------------------------------------------------------ SQL scanning


def mask(sql: str, brackets: bool = True) -> str:
    """`sql` with the insides of string literals (and of [bracketed] names) replaced by NUL, so
    that regex positions still match the original text."""
    out = list(sql)
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in "\"'":
            j = i + 1
            while j < n:
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:
                        j += 2
                        continue
                    break
                j += 1
            for k in range(i + 1, min(j, n)):
                out[k] = "\0"
            i = j + 1
        elif ch == "[":
            j = sql.find("]", i + 1)
            j = n if j < 0 else j
            if brackets:
                for k in range(i + 1, j):
                    out[k] = "\0"
            i = j + 1
        else:
            i += 1
    return "".join(out)


_CALL = re.compile(r"(?<![\w.!\]\0])([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_WORD = re.compile(r"\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_]*)")


def calls(sql: str) -> list[tuple[str, int, int, list[str]]]:
    """Function calls in `sql` outside strings and brackets: (name, start, end, args)."""
    masked = mask(sql)
    out = []
    for m in _CALL.finditer(masked):
        depth, j = 0, m.end() - 1
        args, last = [], j + 1
        while j < len(masked):
            c = masked[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            elif c == "," and depth == 1:
                args.append(sql[last:j].strip())
                last = j + 1
            j += 1
        if depth != 0:
            continue
        tail = sql[last:j].strip()
        if tail or args:
            args.append(tail)
        out.append((m.group(1), m.start(1), j + 1, args))
    return out


def identifiers(sql: str) -> set[str]:
    """Bare words and [bracketed] names of `sql` outside string literals."""
    masked = mask(sql)
    out = set()
    for m in re.finditer(r"\[\0*\]|[A-Za-z_][A-Za-z0-9_]*", masked):
        if m.group(0).startswith("["):
            out.add(sql[m.start() + 1 : m.end() - 1])
        else:
            out.add(m.group(0))
    return out


_SEG = r"(?:\[[^\]]+\]|[A-Za-z_]\w*)"
# Forms!frm!ctl and Reports!rpt!ctl, and the relative forms of a control's own form: Form!ctl,
# Report!ctl, Parent!ctl (also Parent!Parent!ctl) and Me!ctl
_FORM_REF = re.compile(
    rf"(?<![\w\].!])(\[(?:Forms|Reports|Form|Report|Parent|Me)\]|\b(?:Forms|Reports|Form|Report|Parent|Me)\b)"
    rf"((?:\s*[!.]\s*{_SEG})+)",
    re.IGNORECASE,
)
_DOTTED = re.compile(rf"(?<![\w\].!])({_SEG})\s*\.\s*({_SEG})(?!\s*[.!(])")
_PARAMS_CLAUSE = re.compile(r"^\s*PARAMETERS\s+(.*?);", re.IGNORECASE | re.DOTALL)
_COMPARE_BEFORE = re.compile(
    rf"({_SEG})\s*\)*\s*(?:=|<>|<=|>=|<|>|\bLike\b)\s*\(*\s*$", re.IGNORECASE
)
_COMPARE_AFTER = re.compile(
    rf"^\s*\)*\s*(?:=|<>|<=|>=|<|>)\s*\(*\s*(?:{_SEG}\s*\.\s*)?({_SEG})", re.IGNORECASE
)


def _unbracket(s: str) -> str:
    s = s.strip()
    return s[1:-1] if s.startswith("[") and s.endswith("]") else s


def form_refs(sql: str) -> list[tuple[str, str, int, int]]:
    """(param name such as 'Forms!frm!ctl' or 'Form!ctl', control, start, end) of each form or
    report control reference outside string literals."""
    out = []
    masked = mask(sql, brackets=False)
    for m in _FORM_REF.finditer(sql):
        if masked[m.start()] == "\0":  # inside a string literal
            continue
        head = _unbracket(m.group(1))
        segs = [_unbracket(s) for s in re.findall(_SEG, m.group(2))]
        if head.lower() in ("forms", "reports") and len(segs) < 2:
            continue
        name = "!".join([head[0].upper() + head[1:]] + segs)
        out.append((name, segs[-1], m.start(), m.end()))
    return out


def compared_column(sql: str, start: int, end: int) -> str | None:
    """The column a reference at sql[start:end] is compared with (`col = ref` or `ref = col`)."""
    m = _COMPARE_BEFORE.search(sql[:start])
    if m:
        return _unbracket(m.group(1))
    m = _COMPARE_AFTER.match(sql[end:])
    if m:
        return _unbracket(m.group(1))
    return None


def unknown_fields(sql: str, schema: dict) -> list[tuple[str, int, int]]:
    """`Table.Column` references to a schema table that has no such column: Access asks for
    them as parameters. (name 'Table.Column', start, end)."""
    columns = {
        t["name"].lower(): {c["name"].lower() for c in t.get("columns") or []}
        for t in schema.get("tables") or []
    }
    aliases = {
        _unbracket(m.group(1)).lower()
        for m in re.finditer(
            rf"\bAS\s+({_SEG})", mask(sql, brackets=False), re.IGNORECASE
        )
    }
    masked = mask(sql, brackets=False)
    out = []
    for m in _DOTTED.finditer(sql):
        if masked[m.start()] == "\0":
            continue
        table, column = _unbracket(m.group(1)), _unbracket(m.group(2))
        if table.lower() in aliases or table.lower() not in columns or column == "*":
            continue
        if column.lower() not in columns[table.lower()]:
            out.append((f"{table}.{column}", m.start(), m.end()))
    return out


def declared_params(sql: str) -> dict[str, str]:
    """{name: neutral type} from a PARAMETERS clause."""
    m = _PARAMS_CLAUSE.match(sql or "")
    out: dict[str, str] = {}
    if not m:
        return out
    for part in re.split(r",(?![^\[]*\])", m.group(1)):
        part = part.strip()
        pm = re.match(r"^(\[[^\]]*\]|\S+)\s*(\w+)?", part)
        if pm:
            out[_unbracket(pm.group(1))] = ACCESS_PARAM_TYPES.get(
                (pm.group(2) or "text").lower(), "text"
            )
    return out


def strip_parameters(sql: str) -> str:
    return _PARAMS_CLAUSE.sub("", sql or "", count=1).lstrip()


# ------------------------------------------------------------------ app functions


def vba_functions(procedures: dict) -> set[str]:
    out = set()
    for mod in procedures.get("modules") or []:
        for p in mod.get("procedures") or []:
            if p.get("kind") == "Function" and p.get("name"):
                out.add(p["name"].lower())
    return out


def load_functions(directory: Path) -> tuple[dict[str, dict], list[str]]:
    """({function lower: spec}, problems) of the committed rewrite files."""
    out, problems = {}, []
    if not directory.is_dir():
        return out, problems
    for path in sorted(directory.glob("*.json")):
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            problems.append(f"{path.name}: not JSON")
            continue
        if not isinstance(spec, dict) or set(spec) != FUNCTION_KEYS:
            problems.append(
                f"{path.name}: keys must be {', '.join(sorted(FUNCTION_KEYS))}"
            )
            continue
        if spec["choice"] != "subquery" or not isinstance(spec["args"], list):
            problems.append(f"{path.name}: choice must be subquery and args a list")
            continue
        if path.stem != spec["function"]:
            problems.append(f"{path.name}: file name differs from function")
            continue
        out[spec["function"].lower()] = spec
    return out, problems


def apply_rewrites(sql: str, rewrites: dict[str, dict]) -> tuple[str, list[str]]:
    """Replace every call of a rewritten function, one call at a time (the last one first, so a
    call nested in another's arguments goes before it), re-scanning after each replacement so
    rewrites that call other rewritten functions resolve too. Returns (sql, problems)."""
    for _ in range(MAX_REWRITES):
        found = [c for c in calls(sql) if c[0].lower() in rewrites]
        if not found:
            return sql, []
        name, start, end, args = max(found, key=lambda c: c[1])
        spec = rewrites[name.lower()]
        if len(args) != len(spec["args"]):
            return sql, [
                f"{spec['function']}: {len(args)} arguments, rewrite takes {len(spec['args'])}"
            ]
        text = spec["rewrite"]
        for placeholder, arg in zip(spec["args"], args):
            text = text.replace("{" + placeholder + "}", arg)
        sql = sql[:start] + "(" + text + ")" + sql[end:]
    return sql, ["rewrites nest deeper than allowed"]


# ------------------------------------------------------------------ engine compatibility

_DATE_LETTERS = re.compile(r"[dmyhnswtqDMYHNSWTQ]|AM/PM|am/pm|A/P|a/p")


def _format_parts(pattern: str) -> list[tuple[str, str]] | None:
    """Split a Format pattern at its quarter tokens: [("q", ""), ("fmt", "yyyy"), ("lit", "-")].
    None when the pattern has quoted or escaped sections (left as written)."""
    if '"' in pattern or "\\" in pattern or "q" not in pattern.lower():
        return None
    parts: list[tuple[str, str]] = []
    for i, piece in enumerate(re.split(r"[qQ]", pattern)):
        if i:
            parts.append(("q", ""))
        for run in re.findall(r"[A-Za-z/:]+|[^A-Za-z/:]+", piece):
            parts.append(("fmt" if _DATE_LETTERS.search(run) else "lit", run))
    return parts


def engine_compat(sql: str) -> tuple[str, int]:
    """Access built-ins that UCanAccess 5.1.8 lacks, rewritten to equivalent Access SQL:
    Format(x, "<pattern with q>") (UCanAccess's Format has no quarter token) becomes
    DatePart("q", x) & "<rest>" pieces, e.g. Format(d, "q-yyyy") ->
    (DatePart("q", d) & "-" & Format(d, "yyyy")). Returns (sql, rewrites made)."""
    made = 0
    for _ in range(MAX_REWRITES):
        target = None
        for name, start, end, args in calls(sql):
            if name.lower() != "format" or len(args) != 2:
                continue
            m = re.fullmatch(r'"([^"]*)"|\'([^\']*)\'', args[1].strip())
            if not m:
                continue
            parts = _format_parts(m.group(1) if m.group(1) is not None else m.group(2))
            if parts:
                target = (start, end, args[0], parts)
        if target is None:
            return sql, made
        start, end, expr, parts = target
        pieces = []
        for kind, text in parts:
            if kind == "q":
                pieces.append(f'DatePart("q", {expr})')
            elif kind == "fmt":
                pieces.append(f'Format({expr}, "{text}")')
            else:
                pieces.append('"' + text + '"')
        sql = sql[:start] + "(" + " & ".join(pieces) + ")" + sql[end:]
        made += 1
    return sql, made


# ------------------------------------------------------------------ catalog


def _column_types(schema: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for t in schema.get("tables") or []:
        for c in t.get("columns") or []:
            ctype = "longinteger" if c["type"] == "autonumber" else c["type"]
            out.setdefault(c["name"].lower(), ctype)
    return out


def _confirm(sql: str) -> bool:
    names = {n.lower() for n in CONFIRM_FUNCTIONS}
    return any(m.group(1).lower() in names for m in _CALL.finditer(mask(sql)))


def _order(units: dict[str, dict], direct: dict[str, list[str]]) -> list[str]:
    """Unit ids, each after its dependencies (cycles broken by name)."""
    order: list[str] = []
    state: dict[str, int] = {}

    def visit(u: str):
        if state.get(u) == 2:
            return
        if state.get(u) == 1:  # cycle
            return
        state[u] = 1
        for d in sorted(direct.get(u, [])):
            visit(d)
        state[u] = 2
        order.append(u)

    for u in sorted(units):
        visit(u)
    return order


def build(
    private,
    target,
    engagement: str | None = None,
    java_functions: dict[str, str] | None = None,
) -> tuple[dict, dict[str, str], list[str]]:
    """(catalog, {unit id: final SQL}, messages). Raises CatalogError on missing inputs."""
    model = Path(private) / "out" / "model"
    docs = {}
    for name in MODEL_FILES:
        path = model / name
        if not path.is_file():
            raise CatalogError(f"out/model/{name} missing: run parse first")
        try:
            docs[name] = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CatalogError(f"out/model/{name} is not JSON") from None
    objects, schema, procedures = (
        docs["objects.json"],
        docs["schema.json"],
        docs["procedures.json"],
    )
    rewrites, messages = load_functions(functions_dir(target))
    if java_functions is None:
        from toolkit.reference.bridge import java_function_names

        java_functions = java_function_names()
    vba = vba_functions(procedures)
    col_types = _column_types(schema)
    queries = sorted(objects.get("queries") or [], key=lambda q: q["name"])
    names = {q["name"].lower(): q["name"] for q in queries}

    units: dict[str, dict] = {}
    sqls: dict[str, str] = {}
    direct: dict[str, list[str]] = {}
    for n, q in enumerate(queries, start=1):
        qid = q["name"]
        status, reason, sql = qbe.query_sql(q, n)
        sql = sql or ""
        found = {c[0] for c in calls(sql) if c[0].lower() in vba}
        found |= {f for f in q.get("functions") or [] if f.lower() in vba}
        app_functions = sorted(
            {
                rewrites[f.lower()]["function"] if f.lower() in rewrites else f
                for f in found
            },
            key=str.lower,
        )
        if sql and status == "ok":
            sql, problems = apply_rewrites(sql, rewrites)
            for p in problems:
                messages.append(f"{qid}: {p}")
                status, reason = "unsupported", "other"
            sql, compat = engine_compat(sql)
            if compat:
                messages.append(
                    f"{qid}: {compat} Format quarter pattern(s) written with DatePart"
                )
        left = sorted(
            {
                c[0]
                for c in calls(sql)
                if c[0].lower() in vba and c[0].lower() not in java_functions
            }
            | {
                f
                for f in app_functions
                if f.lower() not in rewrites and f.lower() not in java_functions
            },
            key=str.lower,
        )
        if left and status == "ok":
            status, reason = "unsupported", "unsupported-function"
            messages.append(f"{qid}: no rewrite for {', '.join(left)}")
        params = []
        declared = declared_params(sql)
        for p in q.get("params") or []:
            pname, ptype = qbe.param_name_type(p)
            declared.setdefault(pname, ptype)
        for pname, ptype in declared.items():
            params.append({"name": pname, "type": ptype, "source": "prompt"})
        seen = {p["name"].lower() for p in params}
        for pname, control, start, end in form_refs(sql):
            if pname.lower() in seen:
                continue
            seen.add(pname.lower())
            compared = compared_column(sql, start, end)
            ptype = col_types.get((compared or "").lower()) or col_types.get(
                control.lower(), "text"
            )
            params.append({"name": pname, "type": ptype, "source": "form_control"})
        for pname, _s, _e in unknown_fields(sql, schema):
            if pname.lower() in seen:
                continue
            seen.add(pname.lower())
            params.append({"name": pname, "type": "text", "source": "prompt"})
        if params and status == "ok":
            status = "needs-params"
            reason = (
                "form-reference-unresolved"
                if any(p["source"] == "form_control" for p in params)
                else "other"
            )
        refs = identifiers(sql) if sql else set()
        direct[qid] = sorted(
            {
                names[r.lower()]
                for r in refs
                if r.lower() in names and r.lower() != qid.lower()
            }
        )
        units[qid] = {
            "id": qid,
            "kind": "saved_query",
            "object": {"type": "query", "name": qid},
            "sql_ref": f"sql/{file_safe(qid)}.sql",
            "depends_on": [],
            "params": params,
            "app_functions": app_functions,
            "status": status,
            "reason": reason,
            "needs_access_confirmation": bool(app_functions)
            or (bool(sql) and _confirm(sql)),
        }
        sqls[qid] = sql

    order = _order(units, direct)
    position = {u: i for i, u in enumerate(order)}
    for qid in order:
        unit = units[qid]
        deps: set[str] = set()
        for d in direct[qid]:
            deps.add(d)
            deps |= set(units[d]["depends_on"])
        deps.discard(qid)
        unit["depends_on"] = sorted(deps, key=lambda d: position[d])
        if unit["status"] == "unsupported":
            continue
        for d in unit["depends_on"]:
            dep = units[d]
            if dep["status"] == "unsupported":
                unit["status"], unit["reason"] = "unsupported", dep["reason"]
                break
        if unit["status"] == "unsupported":
            continue
        for d in unit["depends_on"]:
            dep = units[d]
            if dep["status"] == "needs-params":
                have = {p["name"].lower() for p in unit["params"]}
                for p in dep["params"]:
                    if p["name"].lower() not in have:
                        unit["params"].append(dict(p))
                if unit["status"] == "ok":
                    unit["status"], unit["reason"] = "needs-params", dep["reason"]
    catalog = {
        "format_version": FORMAT_VERSION,
        "engine": ENGINE,
        "engagement": engagement,
        "units": [units[k] for k in sorted(units)],
    }
    return catalog, sqls, messages


def write(private, catalog: dict, sqls: dict[str, str]) -> list[Path]:
    """Write catalog.json and sql/*.sql (stale .sql files removed). Returns the paths."""
    out = reference_dir(private)
    sql_dir = out / "sql"
    sql_dir.mkdir(parents=True, exist_ok=True)
    wanted = set()
    for unit in catalog["units"]:
        path = out / unit["sql_ref"]
        wanted.add(path.name)
        text = sqls.get(unit["id"]) or ""
        _write_text(path, text if text.endswith("\n") or not text else text + "\n")
    for old in sql_dir.glob("*.sql"):
        if old.name not in wanted:
            old.unlink()
    _write_text(out / "catalog.json", dumps(catalog))
    return [out / "catalog.json", sql_dir]


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, path)


def unit_sql(private, unit: dict) -> str:
    return (reference_dir(private) / unit["sql_ref"]).read_text(encoding="utf-8")
