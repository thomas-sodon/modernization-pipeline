"""Inventory of an engagement's design, counted from the object model only (docs/contracts.md#inventory).

`build(eng, model_dir)` reads the four model files of `out/model/` (S0.parse) and returns the
inventory document: counts under the source kind's `expected_inventory` keys, every object with
its integer metrics, dependency edges, the rule-bearing kinds and the non-data facts the health
check and the estimate use. It never opens the source file: sizes and hashes come from the
engagement's pins (or source.lock.json through the loader).

`expect(eng, counts)` implements `inventory --expect`: compare with the numeric
`expected_inventory`, else with the records' inventory.lock.json, else record that lock.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path

FORMAT_VERSION = 1
ACCESS_KINDS = ("accdt", "vcs-export", "accdb", "mdb")
FULL_ACCESS_KINDS = ("accdt", "vcs-export")
TABLES_QUERIES_ONLY = ("accdb", "mdb")

RULE_BEARING = {
    "access": ["procedure", "data_macro", "macro", "query", "form", "report"],
    "xlsm": [
        "procedure",
        "userform",
        "calculated_column",
        "formula_block",
        "data_validation",
        "chart",
    ],
}

ACCESS_KEYS = (
    "tables",
    "queries",
    "queries_design_grid",
    "queries_sql_text",
    "forms",
    "reports",
    "modules",
    "class_modules",
    "macros",
    "embedded_macros",
    "data_macro_files",
    "data_macros",
    "objects_with_code_behind",
    "vba_nonblank_lines",
)

XLSM_KEYS = (
    "worksheets",
    "hidden_worksheets",
    "chartsheets",
    "tables",
    "table_columns",
    "calculated_columns",
    "defined_names",
    "defined_names_broken",
    "data_validations",
    "formula_cells",
    "formula_cells_by_sheet",
    "formula_cells_without_cached_value",
    "array_formula_cells",
    "charts",
    "form_buttons",
    "macro_assignments",
    "activex_controls",
    "cell_comments",
    "vba_components",
    "vba_standard_modules",
    "vba_class_modules",
    "vba_document_modules",
    "vba_userforms",
    "vba_nonblank_lines",
    "vba_procedures",
    "userform_controls",
    "vba_references",
    "sample_outputs",
)

# engagement source.external_links[].type -> inventory kind
LINK_KIND = {
    "odbc-sqlserver": "odbc",
    "odbc": "odbc",
    "sharepoint": "sharepoint",
    "excel": "excel",
    "text": "text",
}
EXTERNAL_BACKENDS = ("odbc", "sharepoint", "excel", "text")
OFFICE_REFERENCE = re.compile(r"outlook|\bword\b", re.IGNORECASE)
LOCK_MESSAGE = "recorded, not compared; approve at the gate"


class ModelMissing(Exception):
    """The model directory or one of its files is missing: exit 2."""


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def family(kind: str) -> str:
    return "xlsm" if kind == "xlsm" else "access"


def load_model(model_dir) -> dict:
    model_dir = Path(model_dir)
    if not model_dir.is_dir():
        raise ModelMissing("model directory missing")
    docs = {}
    for stem in ("schema", "objects", "procedures", "datamacros"):
        path = model_dir / f"{stem}.json"
        if not path.is_file():
            raise ModelMissing(f"model file {stem}.json missing")
        try:
            docs[stem] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ModelMissing(f"model file {stem}.json is not JSON") from exc
        if docs[stem].get("format_version") != 1:
            raise ModelMissing(f"model file {stem}.json: unknown format_version")
    return docs


# ----------------------------------------------------------------------------- counts


def _access_counts(kind: str, m: dict) -> dict:
    schema, objects = m["schema"], m["objects"]
    procs, dms = m["procedures"], m["datamacros"]
    linked = {lt["name"] for lt in objects.get("linked_tables") or []}
    local_tables = [t for t in schema.get("tables") or [] if t["name"] not in linked]
    queries = objects.get("queries") or []
    counts = {"tables": len(local_tables), "queries": len(queries)}
    if kind in TABLES_QUERIES_ONLY:
        return counts
    modules = procs.get("modules") or []
    macros = objects.get("macros") or []
    forms = objects.get("forms") or []
    reports = objects.get("reports") or []
    code_behind = objects.get("code_behind")
    if code_behind is None:
        code_behind = [f for f in forms + reports if f.get("has_code_behind")]
    counts.update(
        {
            "queries_design_grid": sum(1 for q in queries if q["storage"] == "design"),
            "queries_sql_text": sum(1 for q in queries if q["storage"] == "sql"),
            "forms": len(forms),
            "reports": len(reports),
            "modules": sum(1 for x in modules if x["kind"] in ("standard", "class")),
            "class_modules": sum(1 for x in modules if x["kind"] == "class"),
            "macros": sum(1 for x in macros if x["kind"] == "standalone"),
            "embedded_macros": sum(1 for x in macros if x["kind"] == "embedded"),
            "data_macro_files": len(dms.get("files") or []),
            "data_macros": len(dms.get("datamacros") or []),
            "objects_with_code_behind": len(code_behind),
            "vba_nonblank_lines": sum(x["nonblank_lines"] for x in modules),
        }
    )
    return counts


def _chartsheets(objects: dict) -> set[str]:
    sheet_names = {s["name"] for s in objects.get("sheets") or []}
    return {
        c["sheet"] for c in objects.get("charts") or [] if c["sheet"] not in sheet_names
    }


def _xlsm_counts(m: dict) -> tuple[dict, list[str]]:
    """Counts and the keys the model cannot provide (left out, never 0)."""
    schema, objects, procs = m["schema"], m["objects"], m["procedures"]
    facts = objects.get("facts") or {}
    sheets = objects.get("sheets") or []
    tables = schema.get("tables") or []
    modules = procs.get("modules") or []
    names = objects.get("defined_names") or []
    by_sheet = dict(facts.get("formula_cells_by_sheet") or {})
    blocks = objects.get("formula_blocks") or []
    kinds: dict[str, int] = {}
    for x in modules:
        kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
    counts = {
        "worksheets": len(sheets),
        "hidden_worksheets": sum(
            1 for s in sheets if (s.get("state") or "visible") != "visible"
        ),
        "chartsheets": facts.get("chartsheets", len(_chartsheets(objects))),
        "tables": len(tables),
        "table_columns": {t["name"]: len(t["columns"]) for t in tables},
        "calculated_columns": len(objects.get("calculated_columns") or []),
        "defined_names": len(names),
        "defined_names_broken": sum(1 for d in names if d.get("broken")),
        "data_validations": len(objects.get("data_validations") or []),
        "formula_cells": sum(by_sheet.values()),
        "formula_cells_by_sheet": by_sheet,
        "formula_cells_without_cached_value": facts.get(
            "formula_cells_without_cached_value",
            sum(1 for b in blocks for c in b["cells"] if not c["has_cached_value"]),
        ),
        "charts": len(objects.get("charts") or []),
        "form_buttons": len(objects.get("buttons") or []),
        "macro_assignments": len(objects.get("macro_assignments") or []),
        "activex_controls": len(objects.get("activex") or []),
        "cell_comments": sum((objects.get("comments") or {}).values()),
        "vba_components": len(modules),
        "vba_standard_modules": kinds.get("standard", 0),
        "vba_class_modules": kinds.get("class", 0),
        "vba_document_modules": kinds.get("document", 0),
        "vba_userforms": kinds.get("userform", 0),
        "vba_nonblank_lines": sum(x["nonblank_lines"] for x in modules),
        "vba_procedures": sum(len(x["procedures"]) for x in modules),
        "userform_controls": sum(
            len(u["controls"]) for u in objects.get("userforms") or []
        ),
        "vba_references": len(objects.get("vba_references") or []),
        "sample_outputs": dict(facts.get("sample_outputs") or {}),
    }
    missing = []
    if "array_formula_cells" in facts:
        counts["array_formula_cells"] = facts["array_formula_cells"]
    else:
        missing.append("array_formula_cells")
    return counts, missing


def count(kind: str, m: dict) -> tuple[dict, list[str]]:
    if kind == "xlsm":
        return _xlsm_counts(m)
    if kind in ACCESS_KINDS:
        return _access_counts(kind, m), []
    raise ValueError(f"unknown source kind {kind}")


# ----------------------------------------------------------------------------- objects


def _code_lines(m: dict) -> dict[str, dict]:
    return {x["name"]: x for x in m["procedures"].get("modules") or []}


def _proc_lines(p: dict) -> int:
    if isinstance(p.get("lines"), int):
        return p["lines"]
    return max(0, int(p["end"]) - int(p["start"]) + 1)


def _names_in(text: str | None, candidates: dict[str, str]) -> list[str]:
    """`<type>:<name>` for every known object name found in a SQL string or expression."""
    if not text:
        return []
    found = []
    for name, typ in candidates.items():
        pattern = r"(?<![A-Za-z0-9_])\[?" + re.escape(name) + r"\]?(?![A-Za-z0-9_])"
        if re.search(pattern, text):
            found.append(f"{typ}:{name}")
    return found


def _access_objects(kind: str, m: dict) -> tuple[list, list]:
    schema, objects = m["schema"], m["objects"]
    modules = _code_lines(m)
    objs, edges = [], set()
    linked = {lt["name"]: lt for lt in objects.get("linked_tables") or []}
    tables = {t["name"]: t for t in schema.get("tables") or []}
    for name in sorted(set(tables) | set(linked)):
        metrics = {}
        if name in tables:
            metrics["columns"] = len(tables[name]["columns"])
        objs.append({"type": "table", "name": name, "metrics": metrics})
    queries = objects.get("queries") or []
    qnames = {q["name"] for q in queries}
    sources = {n: "table" for n in set(tables) | set(linked)}
    sources.update({n: "query" for n in qnames})
    for q in queries:
        design = q.get("design") or {}
        joins = design.get("joins") or []
        objs.append(
            {
                "type": "query",
                "name": q["name"],
                "metrics": {
                    "joins": len(joins),
                    "outer_joins": sum(1 for j in joins if j.get("flag") in (2, 3)),
                    "groups": len(design.get("group_by") or []),
                    "parameters": len(q.get("params") or []),
                },
            }
        )
        src = f"query:{q['name']}"
        for it in design.get("input_tables") or []:
            n = it.get("name")
            if n in sources:
                edges.add((src, f"{sources[n]}:{n}"))
        if q.get("sql"):
            for target in _names_in(q["sql"], sources):
                if target != src:
                    edges.add((src, target))
        for fn in q.get("functions") or []:
            edges.add((src, f"function:{fn}"))
    if kind in TABLES_QUERIES_ONLY:
        return objs, edges
    for typ, prefix in (("form", "Form_"), ("report", "Report_")):
        for f in objects.get(f"{typ}s") or []:
            module = modules.get(prefix + f["name"])
            control_events = sum(len(c.get("events") or []) for c in f["controls"])
            metrics = {
                "controls": len(f["controls"]),
                "events": len(f.get("events") or []) + control_events,
                "lines": module["nonblank_lines"] if module else 0,
                "procedures": len(module["procedures"]) if module else 0,
            }
            objs.append({"type": typ, "name": f["name"], "metrics": metrics})
            src = f"{typ}:{f['name']}"
            rs = f.get("record_source")
            if rs in sources:
                edges.add((src, f"{sources[rs]}:{rs}"))
            elif rs:
                edges.update((src, t) for t in _names_in(rs, sources))
            if module:
                for p in module["procedures"]:
                    edges.add((src, f"procedure:{module['name']}.{p['name']}"))
    for name, module in sorted(modules.items()):
        if module["kind"] in ("standard", "class"):
            objs.append(
                {
                    "type": "module",
                    "name": name,
                    "metrics": {
                        "lines": module["nonblank_lines"],
                        "procedures": len(module["procedures"]),
                    },
                }
            )
        for p in module["procedures"]:
            pname = f"{name}.{p['name']}"
            objs.append(
                {
                    "type": "procedure",
                    "name": pname,
                    "metrics": {"lines": _proc_lines(p)},
                }
            )
            refs = p.get("refs") or {}
            src = f"procedure:{pname}"
            for t in refs.get("tables") or []:
                edges.add((src, f"table:{t}"))
            for t in refs.get("queries") or []:
                edges.add((src, f"query:{t}"))
            for t in refs.get("forms") or []:
                edges.add((src, f"form:{t}"))
            for t in refs.get("reports") or []:
                edges.add((src, f"report:{t}"))
    for mac in objects.get("macros") or []:
        objs.append(
            {
                "type": "macro",
                "name": mac["name"],
                "metrics": {"actions": len(mac.get("actions") or [])},
            }
        )
        if mac.get("owner"):
            otype, rest = mac["owner"].split(":", 1)
            edges.add((f"{otype}:{rest.split('.', 1)[0]}", f"macro:{mac['name']}"))
    for dm in m["datamacros"].get("datamacros") or []:
        label = (
            dm["name"] if dm.get("event") == "Named" and dm.get("name") else dm["event"]
        )
        name = f"{dm['table']}.{label}"
        objs.append(
            {
                "type": "data_macro",
                "name": name,
                "metrics": {"actions": len(dm.get("actions") or [])},
            }
        )
        edges.add((f"data_macro:{name}", f"table:{dm['table']}"))
    return objs, edges


_STRUCT_REF = re.compile(r"([A-Za-z_][A-Za-z0-9_.]*)\[")


def _resolve_macro(macro: str, procs: dict[str, list[str]]) -> str:
    """A button's macro text to `procedure:<component>.<proc>` when it can be resolved."""
    text = macro.split("!", 1)[1] if "!" in macro else macro
    text = text.strip("'\"")
    if "." in text:
        comp, proc = text.rsplit(".", 1)
        if proc in procs.get(comp, []):
            return f"procedure:{comp}.{proc}"
    owners = [c for c, ps in procs.items() if text in ps]
    if len(owners) == 1:
        return f"procedure:{owners[0]}.{text}"
    return f"macro:{text}"


def _xlsm_objects(m: dict) -> tuple[list, list]:
    schema, objects = m["schema"], m["objects"]
    modules = _code_lines(m)
    objs, edges = [], set()
    by_sheet = (objects.get("facts") or {}).get("formula_cells_by_sheet") or {}
    for s in objects.get("sheets") or []:
        objs.append(
            {
                "type": "worksheet",
                "name": s["name"],
                "metrics": {"formula_cells": by_sheet.get(s["name"], 0)},
            }
        )
    tables = {t["name"] for t in schema.get("tables") or []}
    for t in schema.get("tables") or []:
        objs.append(
            {
                "type": "table",
                "name": t["name"],
                "metrics": {"columns": len(t["columns"])},
            }
        )
    dnames = {d["name"] for d in objects.get("defined_names") or []}
    for d in objects.get("defined_names") or []:
        objs.append({"type": "defined_name", "name": d["name"], "metrics": {}})

    def formula_edges(src: str, formulas: list[str]) -> None:
        for f in formulas:
            for t in _STRUCT_REF.findall(f):
                if t in tables:
                    edges.add((src, f"table:{t}"))
            for n in dnames:
                if not n.startswith("_xlnm") and re.search(
                    r"(?<![A-Za-z0-9_.])" + re.escape(n) + r"(?![A-Za-z0-9_(\[])", f
                ):
                    edges.add((src, f"defined_name:{n}"))

    for c in objects.get("calculated_columns") or []:
        objs.append({"type": "calculated_column", "name": c["name"], "metrics": {}})
        edges.add((f"calculated_column:{c['name']}", f"table:{c['table']}"))
        formula_edges(f"calculated_column:{c['name']}", [c.get("formula") or ""])
    for b in objects.get("formula_blocks") or []:
        objs.append(
            {
                "type": "formula_block",
                "name": b["name"],
                "metrics": {"cells": len(b["cells"])},
            }
        )
        formula_edges(f"formula_block:{b['name']}", [c["formula"] for c in b["cells"]])
    for v in objects.get("data_validations") or []:
        objs.append({"type": "data_validation", "name": v["name"], "metrics": {}})
    for c in objects.get("charts") or []:
        objs.append(
            {
                "type": "chart",
                "name": c["name"],
                "metrics": {"series": len(c.get("series") or [])},
            }
        )
    forms = {u["name"]: u for u in objects.get("userforms") or []}
    procs = {n: [p["name"] for p in x["procedures"]] for n, x in modules.items()}
    for name, module in sorted(modules.items()):
        typ = "userform" if module["kind"] == "userform" else "module"
        metrics = {
            "lines": module["nonblank_lines"],
            "procedures": len(module["procedures"]),
        }
        if typ == "userform":
            metrics["controls"] = len((forms.get(name) or {}).get("controls") or [])
        objs.append({"type": typ, "name": name, "metrics": metrics})
        for p in module["procedures"]:
            pname = f"{name}.{p['name']}"
            objs.append(
                {
                    "type": "procedure",
                    "name": pname,
                    "metrics": {"lines": _proc_lines(p)},
                }
            )
            if typ == "userform":
                edges.add((f"userform:{name}", f"procedure:{pname}"))
            refs = p.get("refs") or {}
            for t in refs.get("tables") or []:
                edges.add((f"procedure:{pname}", f"table:{t}"))
            for t in refs.get("forms") or []:
                edges.add((f"procedure:{pname}", f"userform:{t}"))
    for b in objects.get("buttons") or []:
        if b.get("macro"):
            edges.add((f"button:{b['name']}", _resolve_macro(b["macro"], procs)))
    for a in objects.get("macro_assignments") or []:
        if a.get("macro"):
            edges.add((f"button:{a['name']}", _resolve_macro(a["macro"], procs)))
    return objs, edges


# ----------------------------------------------------------------------------- facts


def _facts(eng: dict, kind: str, m: dict) -> dict:
    src = eng["source"]
    objects, schema = m["objects"], m["schema"]
    mfacts = objects.get("facts") or {}
    lock = eng.get("source_lock") or {}
    linked: dict[str, int] = {}
    for lt in objects.get("linked_tables") or []:
        linked[lt["backend_kind"]] = linked.get(lt["backend_kind"], 0) + 1
    if not linked and mfacts.get("linked_tables"):
        linked = dict(mfacts["linked_tables"])
    external = {k: v for k, v in linked.items() if k in EXTERNAL_BACKENDS}
    for link in src.get("external_links") or []:
        k = LINK_KIND.get(link.get("type"), link.get("type"))
        external[k] = external.get(k, 0) + 1
    backends = mfacts.get("data_backends")
    if backends is None:
        backends = len(
            {
                (lt["backend_kind"], lt.get("backend"))
                for lt in objects.get("linked_tables") or []
            }
        )
    local = [
        t
        for t in schema.get("tables") or []
        if t["name"] not in {lt["name"] for lt in objects.get("linked_tables") or []}
        and t.get("role", "data") == "data"
    ]
    if backends == 0 and local:
        backends = 1  # the app's own tables are its one data back end
    copy_bytes = None
    dcopy = lock.get("data_copy") or {}
    if dcopy.get("bytes") is not None:
        copy_bytes = dcopy["bytes"]
    locked_backends = [
        b.get("bytes") for b in lock.get("backends") or [] if b.get("bytes")
    ]
    if locked_backends:
        copy_bytes = (copy_bytes or 0) + sum(locked_backends)
    facts = {
        "source_bytes": src.get("bytes"),
        "data_copy_bytes": copy_bytes,
        "data_backends": backends,
        "linked_tables": dict(sorted(linked.items())),
        "external_links": dict(sorted(external.items())),
        "compiled_only": bool(mfacts.get("compiled_only") or src.get("compiled_only")),
        "vba_present": bool(mfacts.get("vba_present")),
        "layout": src.get("layout"),
    }
    if kind in ACCESS_KINDS:
        facts["relationships"] = len(schema.get("relationships") or [])
    if kind == "xlsm":
        refs = []
        for r in objects.get("vba_references") or []:
            name = r.get("name") or ""
            if OFFICE_REFERENCE.search(name):
                refs.append(name)
        facts["vba_office_references"] = sorted(refs)
        facts["defined_names_broken"] = sum(
            1 for d in objects.get("defined_names") or [] if d.get("broken")
        )
        facts["business_tables"] = sum(
            1 for t in schema.get("tables") or [] if t.get("role", "data") == "data"
        )
    return facts


# ----------------------------------------------------------------------------- build


def build(eng: dict, model_dir) -> tuple[dict, list[str]]:
    """(inventory document, keys the model cannot provide)."""
    m = load_model(model_dir)
    kind = eng["source"]["kind"]
    counts, missing = count(kind, m)
    if kind == "xlsm":
        objs, edges = _xlsm_objects(m)
    else:
        objs, edges = _access_objects(kind, m)
    objs.sort(key=lambda o: (o["type"], o["name"]))
    commercial = eng.get("commercial") or {}
    inv = {
        "format_version": FORMAT_VERSION,
        "engagement": eng["id"],
        "project_name": (eng.get("app") or {}).get("project_name") or eng["id"],
        "source": {
            "kind": kind,
            "bytes": eng["source"].get("bytes"),
            "sha256": eng["source"].get("sha256"),
        },
        "expectation": None,
        "rate_usd_per_hour": commercial.get("rate_usd_per_hour"),
        "rule_bearing_kinds": RULE_BEARING[family(kind)],
        "counts": counts,
        "objects": objs,
        "edges": [{"from": a, "to": b} for a, b in sorted(edges)],
        "facts": _facts(eng, kind, m),
    }
    return inv, missing


# ----------------------------------------------------------------------------- expect


def numeric_expected(expected) -> dict:
    """The comparable part of expected_inventory: keys without a leading _, numbers or
    objects of numbers."""
    out = {}
    for key, value in (expected or {}).items():
        if key.startswith("_"):
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            out[key] = value
        elif isinstance(value, dict):
            nested = numeric_expected(value)
            if nested:
                out[key] = nested
    return out


def compare(expected: dict, counts: dict, prefix: str = "") -> tuple[list, list]:
    """(differences as (key, expected, counted), extra counted keys)."""
    diffs, extra = [], []
    for key in sorted(expected):
        want = expected[key]
        label = f"{prefix}{key}"
        if key not in counts:
            diffs.append((label, want, None))
            continue
        got = counts[key]
        if isinstance(want, dict):
            if not isinstance(got, dict):
                diffs.append((label, want, got))
                continue
            d, e = compare(want, got, label + ".")
            diffs.extend(d)
            extra.extend(e)
        elif got != want:
            diffs.append((label, want, got))
    for key in sorted(set(counts) - set(expected)):
        extra.append(f"{prefix}{key}")
    return diffs, extra


def lock_path(eng: dict) -> Path:
    return Path(eng["paths"]["records"]) / "inventory.lock.json"


def expect(eng: dict, counts: dict, today: str | None = None) -> dict:
    """{status: compared|recorded|failed, against, diffs, extra, messages, lock_written}."""
    expected = numeric_expected(eng.get("expected_inventory"))
    result = {"diffs": [], "extra": [], "messages": [], "lock_written": None}
    if expected:
        result["against"] = "expected_inventory"
        diffs, extra = compare(expected, counts)
    elif eng.get("inventory_lock"):
        lock = eng["inventory_lock"]
        result["against"] = "inventory.lock.json"
        diffs, extra = compare(numeric_expected(lock.get("counts")), counts)
        if lock.get("source_sha256") != eng["source"].get("sha256"):
            result["messages"].append(
                "inventory.lock.json: source_sha256 differs from the engagement's source sha256"
            )
            diffs = [("source_sha256", "lock", "engagement"), *diffs]
    else:
        lock = {
            "format_version": 1,
            "recorded": today or _dt.date.today().isoformat(),
            "source_sha256": eng["source"].get("sha256"),
            "counts": counts,
        }
        path = lock_path(eng)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dumps(lock), encoding="utf-8")
        result.update(
            status="recorded",
            against=None,
            lock_written=str(path),
            messages=[LOCK_MESSAGE],
        )
        return result
    result["diffs"] = diffs
    result["extra"] = extra
    for key, want, got in diffs:
        if key == "source_sha256":
            continue
        shown = "not counted" if got is None else got
        result["messages"].append(f"{key}: expected {want}, counted {shown}")
    if extra:
        result["messages"].append("extra counted keys (pass): " + ", ".join(extra))
    result["status"] = "failed" if diffs else "compared"
    return result
