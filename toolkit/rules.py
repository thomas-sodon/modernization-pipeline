"""Rule files, the workbook data model and the signed spec (docs/contracts.md#rules-json, #records).

Pure functions behind the S1.tooling commands (rules-lint, rules-coverage, rules-render,
spec-check, schema-lint). Every problem is a (JSON pointer, text) pair; texts hold names, ids and
hashes only, never data values. Missing or unreadable inputs raise InputError (exit 2).
"""

from __future__ import annotations

import hashlib
import json
import keyword
import re
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema
from referencing import Registry, Resource

SCHEMAS = Path(__file__).resolve().parent / "schemas"
RULES_SCHEMA = SCHEMAS / "rules.schema.json"
DATA_MODEL_SCHEMA = SCHEMAS / "data-model.schema.json"
MODEL_SCHEMA = SCHEMAS / "model.schema.json"

CALC_COLUMN_RE = re.compile(r"^[^\[\]!]+\[[^\[\]]+\]$")
_CELL = r"\$?[A-Z]{1,3}\$?[0-9]+"
_RANGE = rf"{_CELL}(:{_CELL})?"
SHEET_RANGE_RE = re.compile(rf"^[^!]+!{_RANGE}( {_RANGE})*$")
SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
RULE_ID_RE = re.compile(r"^R-([a-z][a-z0-9_]{1,29})-[0-9]{3}$")
QUESTION_ID_RE = re.compile(r"^Q-([a-z][a-z0-9_]{1,29})-[0-9]{3}$")
CO_RE = re.compile(r"^CO-([0-9]{3})$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")

KEEP_DROP_HEADER = (
    "_Generated from analysis/rules.json by `toolkit rules-render`. "
    "Do not edit by hand._"
)


class InputError(Exception):
    """A missing or unreadable input: exit 2."""

    exit_code = 2


Problem = tuple[str, str]


# --------------------------------------------------------------------------- helpers


def pointer(*parts) -> str:
    """JSON pointer (RFC 6901) from path parts; the document root is ''."""
    out = []
    for p in parts:
        out.append("/" + str(p).replace("~", "~0").replace("/", "~1"))
    return "".join(out)


def show(problem: Problem) -> str:
    ptr, text = problem
    return f"{ptr or '(root)'}: {text}"


def load_json(path, what: str = "file") -> object:
    p = Path(path)
    if not p.is_file():
        raise InputError(f"{what} missing: {p.name}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise InputError(
            f"{what} not readable JSON: {p.name} ({type(exc).__name__})"
        ) from None


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validator(schema_path: Path) -> jsonschema.Draft202012Validator:
    model = _schema(MODEL_SCHEMA)
    registry = Registry().with_resource(model["$id"], Resource.from_contents(model))
    return jsonschema.Draft202012Validator(_schema(schema_path), registry=registry)


def schema_problems(doc, schema_path: Path) -> list[Problem]:
    validator = _validator(schema_path)
    out: list[Problem] = []
    errors = sorted(
        validator.iter_errors(doc), key=lambda e: [str(p) for p in e.absolute_path]
    )
    for err in errors:
        ptr = pointer(*err.absolute_path)
        if err.validator == "additionalProperties":
            allowed = set(err.schema.get("properties", {}))
            extra = sorted(str(k) for k in set(err.instance) - allowed)
            out.append((ptr, f"unknown key(s) {', '.join(extra)}"))
        elif err.validator == "required":
            missing = [
                k
                for k in err.validator_value
                if isinstance(err.instance, dict) and k not in err.instance
            ]
            out.append((ptr, f"missing key(s) {', '.join(missing)}"))
        elif isinstance(err.schema, dict) and err.schema.get("description"):
            out.append((ptr, err.schema["description"]))
        else:
            out.append((ptr, f"schema: fails {err.validator}"))
    # de-duplicate (oneOf and if/then can report one spot twice), keep order
    seen, unique = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


# --------------------------------------------------------------------------- rules-lint


def reserved_ids() -> list[str]:
    from toolkit.engagement import pipeline_json

    return list(pipeline_json().get("item_ids", {}).get("reserved", []))


def _name_problem(otype: str, name: str) -> str | None:
    if otype == "calculated_column" and not CALC_COLUMN_RE.match(name):
        return "calculated_column name must be <Table>[<Column>]"
    if otype in ("formula_block", "data_validation") and not SHEET_RANGE_RE.match(name):
        return f"{otype} name must be <Sheet>!<A1 range>"
    return None


def lint_rules(doc, reserved: list[str] | None = None) -> list[Problem]:
    """Schema, then the semantic checks of docs/contracts.md#rules-json (Failure)."""
    problems = schema_problems(doc, RULES_SCHEMA)
    if problems:
        return problems
    reserved = reserved_ids() if reserved is None else reserved
    areas = doc["areas"]
    rules = doc["rules"]
    objects = doc["objects"]
    questions = doc["open_questions"]

    def unique(items, key, base):
        seen: dict[str, int] = {}
        for i, item in enumerate(items):
            value = item[key]
            if value in seen:
                problems.append(
                    (
                        pointer(base, i, key),
                        f"duplicate id {value} (first at {pointer(base, seen[value], key)})",
                    )
                )
            else:
                seen[value] = i

    unique(areas, "id", "areas")
    unique(rules, "id", "rules")
    unique(questions, "id", "open_questions")
    unique(doc["workflows"], "id", "workflows")
    unique(doc["services"], "name", "services")

    area_ids = {a["id"] for a in areas}
    rule_ids = {r["id"] for r in rules}
    question_ids = {q["id"] for q in questions}

    for i, area in enumerate(areas):
        aid = area["id"]
        if keyword.iskeyword(aid):
            problems.append(
                (pointer("areas", i, "id"), f"area id {aid} is a Python keyword")
            )
        if aid in reserved:
            problems.append(
                (
                    pointer("areas", i, "id"),
                    f"area id {aid} is reserved (pipeline.json item_ids.reserved)",
                )
            )

    for i, rule in enumerate(rules):
        rid_area = RULE_ID_RE.match(rule["id"]).group(1)
        if rid_area != rule["area"]:
            problems.append(
                (
                    pointer("rules", i, "id"),
                    f"rule id {rule['id']} does not match its area {rule['area']}",
                )
            )
        if rule["area"] not in area_ids:
            problems.append(
                (pointer("rules", i, "area"), f"area {rule['area']} is not in areas")
            )
        for j, qid in enumerate(rule["questions"]):
            if qid not in question_ids:
                problems.append(
                    (
                        pointer("rules", i, "questions", j),
                        f"question {qid} is not in open_questions",
                    )
                )
        for j, src in enumerate(rule["sources"]):
            msg = _name_problem(src["type"], src["ref"])
            if msg:
                problems.append((pointer("rules", i, "sources", j, "ref"), msg))

    for i, q in enumerate(questions):
        qarea = QUESTION_ID_RE.match(q["id"]).group(1)
        if qarea not in area_ids:
            problems.append(
                (pointer("open_questions", i, "id"), f"area {qarea} is not in areas")
            )

    for base in ("lookups", "relations"):
        for i, item in enumerate(doc[base]):
            if item["rule"] not in rule_ids:
                problems.append(
                    (pointer(base, i, "rule"), f"rule {item['rule']} does not exist")
                )
    in_workflow: set[str] = set()
    for i, wf in enumerate(doc["workflows"]):
        for j, rid in enumerate(wf["rules"]):
            in_workflow.add(rid)
            if rid not in rule_ids:
                problems.append(
                    (pointer("workflows", i, "rules", j), f"rule {rid} does not exist")
                )
    for i, svc in enumerate(doc["services"]):
        if svc["area"] not in area_ids:
            problems.append(
                (pointer("services", i, "area"), f"area {svc['area']} is not in areas")
            )

    seen_objects: dict[tuple[str, str], int] = {}
    kept_in_area: dict[str, int] = {}
    status = {a["id"]: a["status"] for a in areas}
    for i, obj in enumerate(objects):
        key = (obj["type"], obj["name"])
        if key in seen_objects:
            problems.append(
                (
                    pointer("objects", i),
                    f"object {obj['type']} {obj['name']} listed twice (first at {pointer('objects', seen_objects[key])})",
                )
            )
        else:
            seen_objects[key] = i
        msg = _name_problem(obj["type"], obj["name"])
        if msg:
            problems.append((pointer("objects", i, "name"), msg))
        if obj["area"] not in area_ids:
            problems.append(
                (pointer("objects", i, "area"), f"area {obj['area']} is not in areas")
            )
        elif obj["decision"] == "keep":
            kept_in_area[obj["area"]] = kept_in_area.get(obj["area"], 0) + 1
            if status[obj["area"]] == "dropped":
                problems.append(
                    (
                        pointer("objects", i, "decision"),
                        f"keep in dropped area {obj['area']}",
                    )
                )
    for i, area in enumerate(areas):
        if area["status"] == "kept" and not kept_in_area.get(area["id"]):
            problems.append(
                (pointer("areas", i), f"kept area {area['id']} has no kept object")
            )

    for i, rule in enumerate(rules):
        if rule["test_kind"] == "e2e" and rule["id"] not in in_workflow:
            problems.append(
                (
                    pointer("rules", i, "test_kind"),
                    f"e2e rule {rule['id']} is in no workflows[].rules",
                )
            )

    for i, lk in enumerate(doc["lookups"]):
        if lk["values"] is not None and lk["copy_values"] is not True:
            problems.append(
                (pointer("lookups", i, "values"), "values set without copy_values true")
            )

    if doc["kind"] == "area":
        if len(areas) != 1:
            problems.append(
                (
                    pointer("areas"),
                    f"an area file holds exactly one area, not {len(areas)}",
                )
            )
        if areas:
            own = areas[0]["id"]
            for base, key in (
                ("rules", "area"),
                ("objects", "area"),
                ("services", "area"),
            ):
                for i, item in enumerate(doc[base]):
                    if item[key] != own:
                        problems.append(
                            (
                                pointer(base, i, key),
                                f"area file {own} holds an item of area {item[key]}",
                            )
                        )
            for i, q in enumerate(questions):
                if QUESTION_ID_RE.match(q["id"]).group(1) != own:
                    problems.append(
                        (
                            pointer("open_questions", i, "id"),
                            f"area file {own} holds question {q['id']}",
                        )
                    )

    if doc["signed"] is not None:
        problems.append(
            (
                pointer("signed"),
                "signed must be null in committed files (the signature lives in gates.json)",
            )
        )
    return problems


# --------------------------------------------------------------------------- rules-coverage


@dataclass
class Coverage:
    covered: int = 0
    total: int = 0
    uncovered: list[str] = field(default_factory=list)
    hard: list[Problem] = field(default_factory=list)

    @property
    def percent(self) -> float:
        return 100.0 if self.total == 0 else self.covered * 100.0 / self.total

    def meets(self, require: float) -> bool:
        return not self.hard and self.covered * 100.0 >= require * self.total - 1e-9


def coverage(doc: dict, inventory: dict) -> Coverage:
    if inventory.get("format_version") != 1:
        raise InputError("inventory: unknown format_version")
    inv_objects = inventory.get("objects")
    kinds = inventory.get("rule_bearing_kinds")
    if not isinstance(inv_objects, list) or not isinstance(kinds, list):
        raise InputError("inventory: objects or rule_bearing_kinds missing")
    known = {(o.get("type"), o.get("name")) for o in inv_objects if isinstance(o, dict)}
    rule_bearing = set(kinds)
    cov = Coverage()

    entries: dict[tuple[str, str], list[int]] = {}
    for i, obj in enumerate(doc["objects"]):
        key = (obj["type"], obj["name"])
        entries.setdefault(key, []).append(i)
        if key not in known:
            cov.hard.append(
                (
                    pointer("objects", i),
                    f"{obj['type']} {obj['name']} is not in the inventory",
                )
            )
    for key, idx in entries.items():
        for i in idx[1:]:
            cov.hard.append((pointer("objects", i), f"{key[0]} {key[1]} listed twice"))
    cited: set[tuple[str, str]] = set()
    for i, rule in enumerate(doc["rules"]):
        for j, src in enumerate(rule["sources"]):
            key = (src["type"], src["ref"])
            cited.add(key)
            if key not in known:
                cov.hard.append(
                    (
                        pointer("rules", i, "sources", j, "ref"),
                        f"{src['type']} {src['ref']} is not in the inventory",
                    )
                )

    denominator = sorted(
        {
            (o["type"], o["name"])
            for o in inv_objects
            if isinstance(o, dict)
            and (o.get("type") in rule_bearing or o.get("type") == "table")
        }
    )
    cov.total = len(denominator)
    for key in denominator:
        idx = entries.get(key, [])
        ok = len(idx) == 1
        if ok:
            obj = doc["objects"][idx[0]]
            if obj["decision"] == "keep" and key[0] in rule_bearing:
                reason = obj.get("reason")
                ok = key in cited or (isinstance(reason, str) and reason.strip() != "")
        if ok:
            cov.covered += 1
        else:
            cov.uncovered.append(key[1])
    return cov


# --------------------------------------------------------------------------- rules-render


def _cell(text) -> str:
    s = "" if text is None else str(text)
    return s.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def render_keep_drop(doc: dict) -> str:
    lines = [
        KEEP_DROP_HEADER,
        "",
        "# Keep and drop list",
        "",
        f"Engagement `{doc['engagement']}`, spec version {doc['spec_version']}.",
    ]
    sections = (
        ("Keep", "keep", "target component"),
        ("Drop", "drop", "reason"),
        ("Out of scope", "out_of_scope", "reason"),
    )
    for title, decision, last in sections:
        rows = sorted(
            (o for o in doc["objects"] if o["decision"] == decision),
            key=lambda o: (o["area"], o["type"], o["name"]),
        )
        lines += ["", f"## {title}", ""]
        if not rows:
            lines.append("None.")
            continue
        lines += [f"| type | name | area | {last} |", "|---|---|---|---|"]
        for o in rows:
            value = o["target_component"] if decision == "keep" else o["reason"]
            lines.append(
                f"| {_cell(o['type'])} | {_cell(o['name'])} | {_cell(o['area'])} | {_cell(value)} |"
            )
    return "\n".join(lines) + "\n"


def differing_lines(expected: str, actual: str) -> list[int]:
    a, b = expected.split("\n"), actual.split("\n")
    out = [
        i + 1
        for i in range(max(len(a), len(b)))
        if (a[i] if i < len(a) else None) != (b[i] if i < len(b) else None)
    ]
    return out


# --------------------------------------------------------------------------- spec-check


@dataclass
class SpecResult:
    code: int
    rules_sha256: str | None = None
    rules_md_sha256: str | None = None
    approved: dict = field(default_factory=dict)
    matched: str | None = None
    messages: list[str] = field(default_factory=list)


def co_header(text: str) -> dict[str, str]:
    """Header lines of a CO-NNN.md: `key: value` at column 0, before the first `## ` heading."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("## "):
            break
        m = re.match(r"^([a-z_0-9]+):[ \t]*(.*?)\s*$", line)
        if m and m.group(1) not in out:
            out[m.group(1)] = m.group(2)
    return out


def _approved(entry) -> bool:
    return isinstance(entry, dict) and bool(entry.get("approved"))


def spec_check(analysis, gates_path, change_orders) -> SpecResult:
    analysis = Path(analysis)
    rules_json, rules_md = analysis / "rules.json", analysis / "rules.md"
    for p, what in (
        (rules_json, "rules.json"),
        (rules_md, "rules.md"),
        (Path(gates_path), "gates.json"),
    ):
        if not p.is_file():
            raise InputError(f"{what} missing")
    gates = load_json(gates_path, "gates.json")
    if not isinstance(gates, dict):
        raise InputError("gates.json is not an object")
    res = SpecResult(
        code=1,
        rules_sha256=sha256_file(rules_json),
        rules_md_sha256=sha256_file(rules_md),
    )

    s1 = gates.get("S1")
    if not (
        isinstance(s1, dict)
        and isinstance(s1.get("rules_sha256"), str)
        and SHA_RE.match(s1["rules_sha256"])
        and isinstance(s1.get("rules_md_sha256"), str)
        and SHA_RE.match(s1["rules_md_sha256"])
    ):
        res.messages.append(
            "spec not signed: gates.json has no S1 rules_sha256 and rules_md_sha256"
        )
        return res
    state_name = "S1"
    state = {
        "rules_sha256": s1["rules_sha256"],
        "rules_md_sha256": s1["rules_md_sha256"],
    }
    approved_cos = sorted(
        (int(m.group(1)), key)
        for key in gates
        if (m := CO_RE.match(key)) and _approved(gates[key])
    )
    if approved_cos:
        state_name = approved_cos[-1][1]
        entry = gates[state_name]
        state = {
            "rules_sha256": entry.get("rules_sha256"),
            "rules_md_sha256": entry.get("rules_md_sha256"),
        }
    res.approved = {"state": state_name, **state}

    if (
        res.rules_sha256 == state["rules_sha256"]
        and res.rules_md_sha256 == state["rules_md_sha256"]
    ):
        res.code, res.matched = 0, state_name
        res.messages.append(f"matches the approved state {state_name}")
        return res

    co_dir = Path(change_orders)
    pending = []
    if co_dir.is_dir():
        for path in co_dir.iterdir():
            m = re.match(r"^(CO-([0-9]{3}))\.md$", path.name)
            if not m or not path.is_file() or _approved(gates.get(m.group(1))):
                continue
            header = co_header(path.read_text(encoding="utf-8"))
            if header.get("base_rules_sha256") == state["rules_sha256"]:
                pending.append((int(m.group(2)), m.group(1), header))
    if pending:
        _, co, header = max(pending)
        if (
            header.get("rules_sha256_after") == res.rules_sha256
            and header.get("rules_md_sha256_after") == res.rules_md_sha256
        ):
            res.code, res.matched = 0, co
            res.messages.append(f"pending {co}: approve at the gate")
            return res
        res.messages.append(
            f"{co} is pending but its _after hashes differ from the files"
        )
    res.messages.append(
        f"rules.json or rules.md differ from the approved state {state_name}"
    )
    return res


# --------------------------------------------------------------------------- schema-lint


def lint_data_model(doc) -> list[Problem]:
    problems = schema_problems(doc, DATA_MODEL_SCHEMA)
    if problems:
        return problems
    tables: dict[str, set[str]] = {}
    for i, table in enumerate(doc["tables"]):
        name = table["name"]
        if not SNAKE_RE.match(name):
            problems.append(
                (pointer("tables", i, "name"), f"table name {name} is not snake_case")
            )
        if name in tables:
            problems.append(
                (pointer("tables", i, "name"), f"table {name} listed twice")
            )
            continue
        cols: set[str] = set()
        for j, col in enumerate(table["columns"]):
            cname = col["name"]
            if not SNAKE_RE.match(cname):
                problems.append(
                    (
                        pointer("tables", i, "columns", j, "name"),
                        f"column name {cname} is not snake_case",
                    )
                )
            if cname in cols:
                problems.append(
                    (
                        pointer("tables", i, "columns", j, "name"),
                        f"column {name}.{cname} listed twice",
                    )
                )
            cols.add(cname)
        tables[name] = cols
        for j, key in enumerate(table["primary_key"]):
            if key not in cols:
                problems.append(
                    (
                        pointer("tables", i, "primary_key", j),
                        f"key column {name}.{key} does not exist",
                    )
                )
        for j, index in enumerate(table["indexes"]):
            for k, col in enumerate(index["columns"]):
                if col not in cols:
                    problems.append(
                        (
                            pointer("tables", i, "indexes", j, "columns", k),
                            f"index column {name}.{col} does not exist",
                        )
                    )
    for i, rel in enumerate(doc["relationships"]):
        for side in ("from", "to"):
            tname = rel[f"{side}_table"]
            if tname not in tables:
                problems.append(
                    (
                        pointer("relationships", i, f"{side}_table"),
                        f"table {tname} does not exist",
                    )
                )
                continue
            for j, col in enumerate(rel[f"{side}_columns"]):
                if col not in tables[tname]:
                    problems.append(
                        (
                            pointer("relationships", i, f"{side}_columns", j),
                            f"column {tname}.{col} does not exist",
                        )
                    )
        if len(rel["from_columns"]) != len(rel["to_columns"]):
            problems.append(
                (
                    pointer("relationships", i),
                    "from_columns and to_columns differ in length",
                )
            )
    mapped: set[tuple[str, str]] = set()
    for i, m in enumerate(doc["mappings"]):
        mapped.add((m["list_object"], m["source_column"]))
        if m["table"] not in tables:
            problems.append(
                (pointer("mappings", i, "table"), f"table {m['table']} does not exist")
            )
        elif m["column"] not in tables[m["table"]]:
            problems.append(
                (
                    pointer("mappings", i, "column"),
                    f"column {m['table']}.{m['column']} does not exist",
                )
            )
    for i, c in enumerate(doc["computed"]):
        if (c["list_object"], c["source_column"]) in mapped:
            problems.append(
                (
                    pointer("computed", i),
                    f"{c['list_object']}[{c['source_column']}] is both mapped and computed",
                )
            )
    return problems
