"""The one-page health check a prospect receives (docs/contracts.md#cli-registry, healthcheck).

`render(inventory, pipeline, template)` fills templates/docs/health-check.md (string.Template)
from inventory.json alone: the counts table, the risk flags with their triggers, the complexity
level with the ten largest objects by lines, the effort and price band (estimate.compute in
memory) and the next step from pricing_tiers. Object names and counts only: no data values, no
private paths and no generation date, so `--check` can compare bytes.
"""

from __future__ import annotations

from pathlib import Path
from string import Template

from toolkit import estimate as est_mod
from toolkit import inventory as inv_mod

TEMPLATE = (
    Path(__file__).resolve().parent.parent / "templates" / "docs" / "health-check.md"
)
GB = 1024**3
ACCESS_KINDS = est_mod.ACCESS_KINDS
LINK_LABEL = {
    "odbc": "ODBC",
    "sharepoint": "SharePoint",
    "excel": "Excel",
    "text": "text",
    "access": "Access",
    "unresolved": "unresolved",
}
SIZE_LABELS = {
    "tables": "Tables",
    "queries": "Saved queries",
    "queries_design_grid": "Queries kept as a design grid",
    "queries_sql_text": "Queries kept as SQL text",
    "forms": "Forms",
    "reports": "Reports",
    "modules": "VBA modules (standard and class)",
    "class_modules": "Class modules",
    "macros": "Macros (standalone)",
    "embedded_macros": "Macros embedded in form and report events",
    "data_macro_files": "Data macro files",
    "data_macros": "Data macros",
    "objects_with_code_behind": "Forms and reports with code behind",
    "vba_nonblank_lines": "VBA lines (non-blank)",
    "worksheets": "Worksheets",
    "hidden_worksheets": "Hidden worksheets",
    "chartsheets": "Chart sheets",
    "table_columns": "Columns per table",
    "calculated_columns": "Calculated table columns",
    "defined_names": "Defined names",
    "defined_names_broken": "Broken defined names",
    "data_validations": "Data validations",
    "formula_cells": "Formula cells",
    "formula_cells_by_sheet": "Formula cells per sheet",
    "formula_cells_without_cached_value": "Formula cells without a cached value",
    "array_formula_cells": "Array formula cells",
    "charts": "Charts",
    "form_buttons": "Form buttons",
    "macro_assignments": "Shapes with an assigned macro",
    "activex_controls": "ActiveX controls",
    "cell_comments": "Cell comments",
    "vba_components": "VBA components",
    "vba_standard_modules": "Standard modules",
    "vba_class_modules": "Class modules",
    "vba_document_modules": "Document modules",
    "vba_userforms": "UserForms",
    "vba_procedures": "VBA procedures",
    "userform_controls": "UserForm controls",
    "vba_references": "VBA references",
    "sample_outputs": "Sample outputs by kind",
}
LARGEST_TYPES = ("module", "form", "report", "userform")


def _n(value) -> str:
    return f"{value:,}" if isinstance(value, int) else str(value)


def _gb(value: int) -> str:
    return f"{value / GB:.1f} GB"


def size_section(inv: dict) -> str:
    rows = ["| What | Count |", "|---|---|"]
    counts = inv["counts"]
    known = (
        inv_mod.XLSM_KEYS if inv["source"]["kind"] == "xlsm" else inv_mod.ACCESS_KEYS
    )
    order = [k for k in known if k in counts] + sorted(set(counts) - set(known))
    for key in order:
        value = counts[key]
        label = SIZE_LABELS.get(key, key.replace("_", " ").capitalize())
        if isinstance(value, dict):
            shown = ", ".join(f"{k} {_n(v)}" for k, v in value.items()) or "none"
        else:
            shown = _n(value)
        rows.append(f"| {label} | {shown} |")
    return "\n".join(rows)


def risk_flags(inv: dict) -> list[tuple[str, str]]:
    """(flag, trigger) pairs."""
    facts = inv.get("facts") or {}
    counts = inv["counts"]
    kind = inv["source"]["kind"]
    flags = []
    for label, key in (
        ("source file", "source_bytes"),
        ("data file", "data_copy_bytes"),
    ):
        size = facts.get(key)
        if isinstance(size, int) and size > GB:
            flags.append(
                (
                    f"Large {label}",
                    f"the {label} is {_gb(size)}, near the 2 GB Access file limit",
                )
            )
    linked = facts.get("linked_tables") or {}
    if facts.get("layout") == "split" or linked.get("access"):
        flags.append(
            (
                "Split front end and back end",
                (
                    f"{_n(linked.get('access', 0))} linked Access tables: multi-user use over a "
                    "file share, where corruption and locking problems are common"
                ),
            )
        )
    if facts.get("vba_present"):
        flags.append(
            (
                "VBA present",
                (
                    f"{_n(counts.get('vba_nonblank_lines', 0))} lines of VBA: Office blocks macros "
                    "in files from the internet, so every copy must be trusted by hand"
                ),
            )
        )
    external = facts.get("external_links") or {}
    if external:
        shown = ", ".join(
            f"{LINK_LABEL.get(k, k)} {_n(v)}" for k, v in sorted(external.items())
        )
        flags.append(("External links", f"links by kind: {shown}"))
    if facts.get("compiled_only"):
        flags.append(
            (
                "Compiled-only source",
                "no VBA source text (.accde or .mde): the code cannot be read; custom quote",
            )
        )
    backends = int(facts.get("data_backends") or 0)
    if backends > 1:
        flags.append(
            (
                "More than one data back end",
                f"{backends} data back ends: custom quote (an engagement has one data back end)",
            )
        )
    if linked.get("access") and facts.get("data_copy_bytes") is None:
        flags.append(
            (
                "Back end not received",
                "back end not received: the counts cover the front end only",
            )
        )
    if kind == "xlsm":
        refs = facts.get("vba_office_references") or []
        if refs:
            flags.append(
                (
                    "Outlook or Word automation",
                    "the VBA references " + ", ".join(refs) + ": the app drives other "
                    "Office programs, which a web app replaces with its own mail and documents",
                )
            )
        broken = int(facts.get("defined_names_broken") or 0)
        if broken:
            flags.append(
                (
                    "Broken defined names",
                    f"{broken} defined names refer to #REF!: formulas that use them fail",
                )
            )
    return flags


def risks_section(inv: dict) -> str:
    flags = risk_flags(inv)
    if not flags:
        return "None of the checked risks applies."
    return "\n".join(f"- **{flag}**: {trigger}." for flag, trigger in flags)


def complexity_level(inv: dict) -> tuple[str, str]:
    counts = inv["counts"]
    lines = int(counts.get("vba_nonblank_lines", 0))
    if inv["source"]["kind"] == "xlsm":
        blocks = sum(1 for o in inv["objects"] if o["type"] == "formula_block")
        objects = int(counts.get("vba_userforms", 0)) + blocks
        what = "userforms plus formula blocks"
    else:
        objects = int(counts.get("forms", 0)) + int(counts.get("reports", 0))
        what = "forms plus reports"
    if lines > 5000 or objects > 40:
        level = "High"
    elif lines > 1000 or objects > 20:
        level = "Medium"
    else:
        level = "Low"
    return level, f"{_n(lines)} VBA lines; {_n(objects)} {what}"


def largest(inv: dict, limit: int = 10) -> list[tuple[str, str, int]]:
    items = [
        (o["name"], o["type"], int((o.get("metrics") or {}).get("lines", 0)))
        for o in inv["objects"]
        if o["type"] in LARGEST_TYPES
    ]
    items = [i for i in items if i[2] > 0]
    items.sort(key=lambda i: (-i[2], i[0], i[1]))
    return items[:limit]


def complexity_section(inv: dict) -> str:
    level, why = complexity_level(inv)
    out = [
        (
            f"**{level}** ({why}). High above 5,000 VBA lines or 40 screens and reports; "
            "medium above 1,000 lines or 20."
        ),
    ]
    top = largest(inv)
    if top:
        out += [
            "",
            "Largest objects by lines of code:",
            "",
            "| Object | Type | Lines |",
            "|---|---|---|",
        ]
        out += [f"| {name} | {typ} | {_n(lines)} |" for name, typ, lines in top]
    return "\n".join(out)


def _range(lo, hi, unit="") -> str:
    return f"{_n(lo)} to {_n(hi)}{unit}"


def _hours(h) -> str:
    return est_mod._h(h)


def effort_section(est: dict, pipeline: dict) -> str:
    tiers = {row["tier"]: row for row in pipeline["pricing_tiers"]}
    h, p = est["hours"], est["price_usd"]
    out = [
        "| Offer | Hours | Price (USD) |",
        "|---|---|---|",
        (
            f"| Stage 2: the web app | {_hours(h['low'])} to {_hours(h['high'])} "
            f"(point {_hours(h['point'])}) | {_range(p['low'], p['high'])} (point {_n(p['point'])}) |"
        ),
    ]
    if est.get("stage1"):
        sh, sp = est["stage1"]["hours"], est["stage1"]["price_usd"]
        out.append(
            f"| Stage 1 alone: data off the Access file | {_hours(sh['low'])} to "
            f"{_hours(sh['high'])} (point {_hours(sh['point'])}) | "
            f"{_range(sp['low'], sp['high'])} (point {_n(sp['point'])}) |"
        )
    notes = []
    tier = est["tier"]
    row = tiers.get(tier)
    if row:
        notes.append(
            f"Price band: **{tier}** ({row['scope']}), {_range(row['price_usd']['min'], row['price_usd']['max'])} USD "
            f"for {_range(row['hours']['min'], row['hours']['max'])} hours."
        )
    elif tier.startswith("custom quote"):
        notes.append(f"Price band: **custom quote**: {tier.split(': ', 1)[1]}.")
    else:
        notes.append(f"Price band: **{tier}**.")
    if est.get("stage1") and "stage1" in tiers:
        s1 = tiers["stage1"]
        notes.append(
            f"Stage 1 band: {_range(s1['price_usd']['min'], s1['price_usd']['max'])} USD "
            f"for {_range(s1['hours']['min'], s1['hours']['max'])} hours."
        )
    notes.append(
        f"Rate {_n(est['rate_usd_per_hour'])} USD per hour. Starting coefficients, not "
        "measured; the assessment fixes the quote."
    )
    if est.get("price_note"):
        notes.append(f"Note: {est['price_note']}.")
    return "\n".join(out) + "\n\n" + "\n".join(f"- {n}" for n in notes)


def next_step_section(inv: dict, est: dict, pipeline: dict) -> str:
    tiers = {row["tier"]: row for row in pipeline["pricing_tiers"]}
    a = tiers["assessment"]
    scope = a["scope"]
    if "credited" not in scope:
        scope += "; credited toward the migration"
    out = [
        (
            f"Book the paid assessment ({_range(a['price_usd']['min'], a['price_usd']['max'])} USD: "
            f"{scope})."
        )
    ]
    if inv["source"]["kind"] in ACCESS_KINDS and est.get("stage1"):
        sp = est["stage1"]["price_usd"]
        out.append(
            f"Or start with Stage 1 alone ({tiers['stage1']['scope']}): "
            f"{_range(sp['low'], sp['high'])} USD for this app."
        )
    return "\n\n".join(out)


def render(inv: dict, pipeline: dict, coef: dict, coef_sha: str, template: str) -> str:
    est = est_mod.compute(inv, "", pipeline, coef, coef_sha)
    src = inv["source"]
    size = src.get("bytes")
    shown = (
        f"{size / (1024 * 1024):.1f} MB" if isinstance(size, int) else "size not pinned"
    )
    intro = (
        f"An automated scan of the {src['kind']} source ({shown}). It counts the design "
        "(object names and counts only); it reads no data values."
    )
    return Template(template).substitute(
        project_name=inv.get("project_name") or inv["engagement"],
        intro=intro,
        size=size_section(inv),
        risks=risks_section(inv),
        complexity=complexity_section(inv),
        effort=effort_section(est, pipeline),
        next_step=next_step_section(inv, est, pipeline),
    )
