"""The equivalence report (docs/contracts.md#harness, #pass-rate, #goldens-manifest, #rules-json).

`render(inputs)` turns the committed results.json, goldens/manifest.json, analysis/rules.json,
docs/known-differences.json, the design-zone inventory.json, the screenshot names in
docs/img/after/ and the rule ids in the rule-test docstrings into the 8 sections of
templates/docs/equivalence-report.md (string.Template). It never reads the data zone or the
reference catalog: unit statuses and reasons come only from the manifest.

`checks` lists the reasons the report fails (exit 1): the lowest cell rate below 0.90, an
equivalence cell whose total differs from |C_d|, rule or e2e tests below 100% or missing, the
characterization rate below 0.90 or empty outputs above 25% on engagement data, a kept rule
without a test, and a not-passed unit and cell without a known-differences entry.

The text holds names, counts and rates only, and no timestamp other than results.json's date, so
two runs on the same inputs give the same bytes.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from string import Template

from toolkit import golden as gl
from toolkit import results as res
from toolkit import rules as rl
from toolkit.engagement import toolkit_root

TEMPLATE = toolkit_root() / "templates" / "docs" / "equivalence-report.md"
FORMAT_VERSION = 1
THRESHOLD = res.THRESHOLD
MIN_CHARACTERIZATION = 0.9
MAX_EMPTY = 0.25
SECTIONS = (
    "Summary",
    "Method",
    "Results by dataset and database",
    "Results by output kind",
    "Coverage",
    "Rule tests and end-to-end tests",
    "Known differences",
    "How to re-run",
)
ACCESS_KINDS = ("accdt", "accdb", "mdb", "vcs-export")
REPORT_COVERAGE_SENTENCE = (
    "Access report layouts and report-level totals are checked by the S5 view tests "
    "(test_views.py), rule tests and end-to-end tests (not compared with Access), not by "
    "goldens (the Access reference adapter's render_document raises NotImplementedError)."
)
DB_TITLES = {"postgresql": "PostgreSQL", "mssql": "SQL Server"}
KINDS = ("saved_query", "formula_output", "document")
KIND_WORDS = {
    "saved_query": ("saved-query output", "saved-query outputs"),
    "formula_output": ("formula output", "formula outputs"),
    "document": ("document", "documents"),
}
CONFIRMATION_WHY = (
    "their queries call a date or formatting function (Format, FormatDateTime, DateValue, CDate, "
    "DatePart, DateDiff, DateAdd, Weekday, MonthName) or an app function, which UCanAccess may "
    "evaluate differently from Microsoft Access"
)
KIND_TITLES = {
    "saved_query": "Saved queries (saved_query)",
    "formula_output": "Formula outputs (formula_output)",
    "document": "Documents (document)",
}
SOURCE_TITLES = {
    "accdt": "an Access database template (.accdt)",
    "accdb": "an Access database (.accdb)",
    "mdb": "an Access database (.mdb)",
    "vcs-export": "an Access source export",
    "xlsm": "an Excel macro workbook (.xlsm)",
}
ENGINES = {
    "access": (
        "UCanAccess 5.1.8 (with Jackcess 5.1.7 and HSQLDB 2.7.4) ran every kept query of the "
        "Access database on a read-only copy built from each dataset"
    ),
    "xlsm": (
        "formualizer 0.11.1 evaluated the workbook's formulas on a copy built from each "
        "dataset; on the engagement data the workbook's own cached values are the oracle"
    ),
}
RULE_ID = r"R-[a-z][a-z0-9_]*-\d{3}"
LEADING_IDS = re.compile(rf"^\s*({RULE_ID}(?:\s*(?:,|/|&|\band\b)\s*{RULE_ID})*)")
OUTCOME_TITLES = {
    "failed": "failed",
    "error": "error",
    "skipped": "skipped",
    "not_implemented": "not implemented",
}


class ReportError(Exception):
    """A missing or unreadable input: exit 2. The message names files, never values."""

    exit_code = 2


# ------------------------------------------------------------------ inputs


@dataclass
class Inputs:
    eng: dict
    results: dict
    manifest: dict
    rules: dict
    known: dict
    inventory: dict
    status: dict
    after: list[str]
    rule_tests: dict[str, set[str]]  # area folder -> rule ids found in its docstrings
    template: str


def _json(path: Path, what: str) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ReportError(f"{what} missing: {path}") from None
    except (OSError, UnicodeDecodeError, ValueError):
        raise ReportError(f"{what} is not readable JSON") from None
    if not isinstance(doc, dict):
        raise ReportError(f"{what} is not a JSON object")
    if doc.get("format_version") != FORMAT_VERSION:
        raise ReportError(f"{what}: unknown format_version")
    return doc


def docstring_rule_ids(text: str) -> set[str]:
    """Rule ids a test_rules.py module names at the start of its test docstrings."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        raise ReportError("a test_rules.py does not parse") from None
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef
        ) and node.name.startswith("test"):
            doc = ast.get_docstring(node) or ""
            m = LEADING_IDS.match(doc)
            if m:
                out.update(re.findall(RULE_ID, m.group(1)))
    return out


def load_inputs(eng: dict, results_path: Path) -> Inputs:
    target = Path(eng["paths"]["target"])
    private = Path(eng["paths"]["private"])
    results = _json(Path(results_path), "results.json")
    try:
        manifest = gl.read_json(
            target / "goldens" / "manifest.json", "goldens/manifest.json"
        )
    except gl.GoldenError as exc:
        raise ReportError(str(exc)) from None
    if manifest.get("format_version") != FORMAT_VERSION:
        raise ReportError("goldens/manifest.json: unknown format_version")
    rules_path = target / "analysis" / "rules.json"
    try:
        rules = rl.load_json(rules_path, "analysis/rules.json")
    except rl.InputError as exc:
        raise ReportError(str(exc)) from None
    if not isinstance(rules, dict) or rules.get("format_version") != FORMAT_VERSION:
        raise ReportError("analysis/rules.json: unknown format_version")
    known = _json(
        target / "docs" / "known-differences.json", "docs/known-differences.json"
    )
    inventory = _json(private / "out" / "inventory.json", "out/inventory.json")
    try:
        status = gl.status_report(target / "goldens", rules_path, "engagement")
    except gl.GoldenError as exc:
        raise ReportError(str(exc)) from None
    after_dir = target / "docs" / "img" / "after"
    after = (
        sorted(
            p.name for p in after_dir.iterdir() if p.is_file() and p.suffix == ".png"
        )
        if after_dir.is_dir()
        else []
    )
    slug = (eng.get("app") or {}).get("slug")
    rule_tests: dict[str, set[str]] = {}
    for path in sorted((target / "app" / str(slug)).glob("*/tests/test_rules.py")):
        rule_tests[path.parent.parent.name] = docstring_rule_ids(
            path.read_text(encoding="utf-8")
        )
    try:
        template = TEMPLATE.read_text(encoding="utf-8")
    except OSError:
        raise ReportError(f"template missing: {TEMPLATE}") from None
    return Inputs(
        eng,
        results,
        manifest,
        rules,
        known,
        inventory,
        status,
        after,
        rule_tests,
        template,
    )


# ------------------------------------------------------------------ analysis


@dataclass
class Analysis:
    dbs: list[str]
    datasets: list[str]
    q: int
    c: dict[str, int]
    eq_cells: list[dict]
    rule_cells: list[dict]
    e2e_cells: list[dict]
    min_cell: float
    min_cells: list[str]
    pooled: float
    coverage_adjusted: float
    passed: int
    tests: int
    rule_cover: dict[str, tuple[int, int]]
    uncovered: list[str]
    not_passed: dict[str, list[tuple[str, str]]]  # unit -> [(cell, outcome)]
    known_index: dict[tuple[str, str], dict]
    missing_known: list[tuple[str, str]]
    stale_known: list[tuple[str, str]]
    reasons: list[str] = field(default_factory=list)


def cell_label(cell: dict) -> str:
    return f"{cell['dataset']}:{cell['db']}"


def cell_rate(passed: int, total: int) -> float:
    return round(passed / total, 4) if total else 0.0


def rule_coverage(inp: Inputs) -> tuple[dict[str, tuple[int, int]], list[str]]:
    kept_areas = {
        a.get("id") for a in inp.rules.get("areas") or [] if a.get("status") == "kept"
    }
    units = set((inp.manifest.get("units") or {}).keys())
    tested = set().union(*inp.rule_tests.values()) if inp.rule_tests else set()
    shot_workflows = {
        w.get("id")
        for w in inp.rules.get("workflows") or []
        if any(n.startswith(f"{w.get('id')}-") for n in inp.after)
    }
    in_shot_workflow = set()
    for w in inp.rules.get("workflows") or []:
        if w.get("id") in shot_workflows:
            in_shot_workflow.update(w.get("rules") or [])
    cover: dict[str, list[int]] = {k: [0, 0] for k in ("rule", "equivalence", "e2e")}
    uncovered = []
    for r in inp.rules.get("rules") or []:
        if r.get("area") not in kept_areas:
            continue
        kind = r.get("test_kind")
        if kind not in cover:
            continue
        if kind == "rule":
            ok = r.get("id") in tested
        elif kind == "equivalence":
            ok = any(s.get("ref") in units for s in r.get("sources") or [])
        else:
            ok = r.get("id") in in_shot_workflow
        cover[kind][1] += 1
        if ok:
            cover[kind][0] += 1
        else:
            uncovered.append(f"{r.get('id')} ({kind})")
    return {k: (v[0], v[1]) for k, v in cover.items()}, sorted(uncovered)


def analyse(inp: Inputs) -> Analysis:
    units = inp.manifest.get("units") or {}
    dbs = res.databases(inp.eng)
    datasets = res.datasets(inp.manifest)
    q = len(units)
    c = {
        d: sum(
            1
            for u in units.values()
            if ((u.get("datasets") or {}).get(d) or {}).get("status") == "ok"
        )
        for d in datasets
    }
    cells = inp.results.get("cells") or []
    eq_cells = [x for x in cells if x.get("suite") == "equivalence"]
    rule_cells = [x for x in cells if x.get("suite") == "rules"]
    e2e_cells = [x for x in cells if x.get("suite") == "e2e"]
    reasons: list[str] = []
    if inp.results.get("engagement") != inp.eng.get("id"):
        reasons.append("results.json belongs to another engagement")

    have = {(x.get("dataset"), x.get("db")) for x in eq_cells}
    for d in datasets:
        for db in dbs:
            if (d, db) not in have:
                reasons.append(f"results.json has no equivalence cell {d}:{db}")
    rates = []
    for x in eq_cells:
        cd = c.get(x.get("dataset"), 0)
        if x.get("total") != cd:
            reasons.append(
                f"cell {cell_label(x)}: total {x.get('total')} differs from |C_d| {cd} "
                "in goldens/manifest.json"
            )
        rates.append((cell_rate(int(x.get("passed") or 0), cd), cell_label(x)))
    min_cell = min((r for r, _ in rates), default=0.0)
    min_cells = sorted(lbl for r, lbl in rates if r == min_cell)
    passed = sum(int(x.get("passed") or 0) for x in eq_cells)
    tests = sum(c.get(x.get("dataset"), 0) for x in eq_cells)
    pooled = cell_rate(passed, tests)
    n_cells = len(eq_cells)
    coverage_adjusted = cell_rate(passed, q * n_cells)
    if not eq_cells:
        reasons.append("results.json has no equivalence cells")
    elif min_cell < THRESHOLD:
        reasons.append(
            f"lowest cell rate {min_cell:.4f} ({', '.join(min_cells)}) is below {THRESHOLD:.2f}"
        )

    for suite, group in (("rules", rule_cells), ("e2e", e2e_cells)):
        if not group:
            reasons.append(f"results.json has no {suite} cell")
        for x in group:
            if int(x.get("total") or 0) == 0 or x.get("passed") != x.get("total"):
                reasons.append(
                    f"{suite} tests on {x.get('db')}: {x.get('passed')} of {x.get('total')} passed, "
                    "100% required"
                )

    st = inp.status
    if st["rate"] < MIN_CHARACTERIZATION:
        reasons.append(
            f"characterization rate {st['rate']:.4f} on engagement data is below {MIN_CHARACTERIZATION:.2f}"
        )
    if st["empty_share"] > MAX_EMPTY:
        reasons.append(
            f"empty outputs {st['empty_share']:.4f} on engagement data are above {MAX_EMPTY:.2f}"
        )
    reasons += [f"goldens: {p}" for p in st["problems"]]

    rule_cover, uncovered = rule_coverage(inp)
    reasons += [f"kept rule without a test: {u}" for u in uncovered]

    known_index: dict[tuple[str, str], dict] = {}
    for e in inp.known.get("entries") or []:
        for cl in e.get("cells") or []:
            known_index[(e.get("unit"), cl)] = e
    not_passed: dict[str, list[tuple[str, str]]] = {}
    for x in eq_cells:
        for np_ in x.get("not_passed") or []:
            not_passed.setdefault(np_["unit"], []).append(
                (cell_label(x), np_["outcome"])
            )
    missing_known = sorted(
        (u, cl)
        for u, lst in not_passed.items()
        for cl, _o in lst
        if (u, cl) not in known_index
    )
    for u, cl in missing_known:
        reasons.append(
            f"not-passed unit {u} in cell {cl} has no known-differences entry"
        )
    current = {(u, cl) for u, lst in not_passed.items() for cl, _o in lst}
    stale_known = sorted(k for k in known_index if k not in current)

    return Analysis(
        dbs=dbs,
        datasets=datasets,
        q=q,
        c=c,
        eq_cells=eq_cells,
        rule_cells=rule_cells,
        e2e_cells=e2e_cells,
        min_cell=min_cell,
        min_cells=min_cells,
        pooled=pooled,
        coverage_adjusted=coverage_adjusted,
        passed=passed,
        tests=tests,
        rule_cover=rule_cover,
        uncovered=uncovered,
        not_passed={u: sorted(v) for u, v in sorted(not_passed.items())},
        known_index=known_index,
        missing_known=missing_known,
        stale_known=stale_known,
        reasons=reasons,
    )


# ------------------------------------------------------------------ text helpers


def r4(x: float) -> str:
    return f"{x:.4f}"


def pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def num(n: int) -> str:
    return f"{n:,}"


def cell_text(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def table(head: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(cell_text(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def db_title(db: str) -> str:
    return DB_TITLES.get(db, db)


def join_words(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def result_dbs(inp: Inputs, a: Analysis) -> list[str]:
    """Databases named in the report: only those with cells in results.json, engagement order."""
    seen = {x.get("db") for x in (inp.results.get("cells") or [])}
    return [db for db in a.dbs if db in seen] + sorted(
        d for d in seen if d not in a.dbs and d
    )


# ------------------------------------------------------------------ sections


def is_access(inp: Inputs) -> bool:
    return (inp.eng.get("source") or {}).get("kind") in ACCESS_KINDS


def kind_counts(inp: Inputs) -> dict[str, int]:
    units = inp.manifest.get("units") or {}
    return {k: sum(1 for u in units.values() if u.get("kind") == k) for k in KINDS}


def compared_words(inp: Inputs) -> tuple[str, str]:
    """What the comparison covers, and the kinds it does not compare, in words."""
    n = kind_counts(inp)
    has = [
        f"{n[k]} {KIND_WORDS[k][0] if n[k] == 1 else KIND_WORDS[k][1]}"
        for k in KINDS
        if n[k]
    ]
    lacks = [KIND_WORDS[k][1] for k in KINDS if not n[k]]
    lacks_text = (
        ", ".join(lacks[:-1]) + " or " + lacks[-1] if len(lacks) > 1 else "".join(lacks)
    )
    return join_words(has) or "no outputs", lacks_text


def unconfirmed_units(inp: Inputs) -> list[str]:
    """Kept units flagged needs_access_confirmation in goldens/manifest.json (Access kinds)."""
    if not is_access(inp):
        return []
    units = inp.manifest.get("units") or {}
    return sorted(u for u, x in units.items() if x.get("needs_access_confirmation"))


def no_access_run_words(inp: Inputs) -> str:
    box = (inp.eng.get("environment") or {}).get("windows_box") or {}
    if not box.get("available"):
        return (
            "no confirmation run on Microsoft Access is recorded (this engagement had no "
            "Windows + Access machine)"
        )
    return "no confirmation run on Microsoft Access is recorded in the inputs of this report"


def intro(inp: Inputs, a: Analysis) -> str:
    kind = (inp.eng.get("source") or {}).get("kind")
    has, lacks = compared_words(inp)
    reference = (
        "UCanAccess, an open-source engine that reads Access files, not Microsoft Access itself"
        if is_access(inp)
        else "formualizer on the synthetic data and the workbook's cached Excel values on the "
        "original data"
    )
    text = (
        f"_Generated by `toolkit report` from `equivalence/results.json` (results of "
        f"{inp.results.get('date') or 'an undated run'}), `goldens/manifest.json`, "
        "`analysis/rules.json` and `docs/known-differences.json`. Do not edit by hand: re-run the "
        "commands under How to re-run._\n\n"
        f"This report compares outputs of the new application with reference outputs recorded "
        f"from the original application, {SOURCE_TITLES.get(kind, kind)}, on the original data "
        f"and on synthetic test data. The comparison covers {has} ({a.q} kept units)"
    )
    text += f"; it compares no {lacks}." if lacks else "."
    text += (
        f" The reference outputs were computed by {reference}. The rates below measure how "
        "many compared outputs match their reference; they say nothing about behaviour no output "
        "covers, which rests on the rule tests and end-to-end tests, and the limits of the "
        "comparison are listed under Coverage and Known differences."
    )
    flagged = unconfirmed_units(inp)
    if flagged:
        text += (
            f" {len(flagged)} of the {a.q} kept units have reference outputs that were not "
            "confirmed on Microsoft Access."
        )
    return text + " The report holds names, counts and rates only, never data values."


def summary(inp: Inputs, a: Analysis) -> str:
    verdict = "PASS" if a.eq_cells and a.min_cell >= THRESHOLD else "FAIL"
    if not a.min_cells:
        where = "no cells"
    elif len(a.min_cells) == len(a.eq_cells) > 1:
        where = f"all {len(a.eq_cells)} cells"
    else:
        where = ", ".join(a.min_cells)
    lines = [
        (
            f"- **Gate: {verdict}.** The lowest cell rate is {r4(a.min_cell)} ({pct(a.min_cell)}; "
            f"{where}) against the threshold {THRESHOLD:.2f}. Every dataset and database pair is "
            "one cell, and the gate needs every cell at or above the threshold."
        ),
        (
            f"- **Pooled rate:** {r4(a.pooled)} ({num(a.passed)} of {num(a.tests)} unit tests "
            f"passed over {len(a.eq_cells)} cells)."
        ),
        (
            f"- **Coverage-adjusted rate:** {r4(a.coverage_adjusted)} ({num(a.passed)} passed over "
            f"{a.q} kept units x {len(a.eq_cells)} cells = {num(a.q * len(a.eq_cells))}). It also "
            "counts kept units that could not be characterized on a dataset as not passed."
        ),
    ]
    has, lacks = compared_words(inp)
    scope = f"- **What the rates cover:** {has} ({a.q} kept units)"
    scope += f"; no {lacks} are compared." if lacks else "."
    lines.append(scope)
    flagged = unconfirmed_units(inp)
    if flagged:
        lines.append(
            f"- **Not confirmed on Microsoft Access:** {len(flagged)} of the {a.q} kept units are "
            "flagged needs_access_confirmation in `goldens/manifest.json`; their reference outputs "
            f"come from UCanAccess alone and {no_access_run_words(inp)}. A pass on these units "
            "shows agreement with UCanAccess, not with Access. They are listed under Known "
            "differences."
        )
    for suite, group, title in (
        ("rules", a.rule_cells, "Rule tests"),
        ("e2e", a.e2e_cells, "End-to-end tests"),
    ):
        if group:
            parts = [
                f"{x.get('passed')} of {x.get('total')} passed on {db_title(x.get('db'))}"
                for x in group
            ]
            lines.append(f"- **{title}:** {join_words(parts)}.")
        else:
            lines.append(f"- **{title}:** no results.")
    lines.append(f"- **Results date:** {inp.results.get('date') or 'unknown'}.")
    if a.reasons:
        lines.append("")
        lines.append("**Open gaps.** The report fails until each of these is closed:")
        lines.append("")
        lines += [f"- {r}" for r in a.reasons]
    else:
        lines.append(
            "- **Checks:** all pass (gate, cell totals, rule and end-to-end tests, "
            "characterization, rule coverage, known differences)."
        )
    return "\n".join(lines)


def method(inp: Inputs, a: Analysis) -> str:
    kind = (inp.eng.get("source") or {}).get("kind")
    engine = ENGINES["xlsm" if kind == "xlsm" else "access"]
    seeds = [d for d in a.datasets if d != "engagement"]
    versions = sorted(
        {
            str(u.get("canonical_version"))
            for u in (inp.manifest.get("units") or {}).values()
            if u.get("canonical_version") is not None
        }
    )
    dbs = result_dbs(inp, a)
    primary = (inp.eng.get("database") or {}).get("primary")
    db_words = [f"{db_title(d)}{' (primary)' if d == primary else ''}" for d in dbs]
    lines = [
        f"- **Reference.** {engine}. Its outputs, recorded once, are the goldens."
        + (
            " UCanAccess is an independent engine, not Microsoft Access: where it evaluates a "
            "function differently from Access, the goldens carry that difference. Units whose "
            "queries call such functions are flagged needs_access_confirmation and listed under "
            "Known differences."
            if is_access(inp)
            else ""
        ),
        (
            f"- **Datasets.** The engagement dataset (the original application's own data, kept on "
            f"our side) and {len(seeds)} synthetic datasets ({join_words(seeds)}), generated from "
            "the data model and committed in `goldens/synthetic/`."
        ),
        f"- **Databases.** The new application ran every unit on {join_words(db_words) or 'no database'}.",
    ]
    if "mssql" in dbs:
        lines.append(
            "- **SQL Server.** SQL Server cells come from local runs in Docker (mp-mssql22). "
            "The target repository's CI runs the harness on the synthetic seeds as a cross-check "
            "only; its results are not part of this report."
        )
    else:
        lines.append(
            "- **CI.** The target repository's CI runs the harness on the synthetic seeds as a "
            "cross-check only; its results are not part of this report."
        )
    lines += [
        (
            f"- **Canonical form.** Every output is put into canonical form version "
            f"{', '.join(versions) or '1'} (typed values, fixed number and date formats, normalised "
            "row order) before it is compared."
        ),
        (
            "- **Comparison.** Synthetic outputs are compared file by file with the committed "
            "expected files. Engagement outputs are compared by HMAC-SHA256 digest of their "
            "canonical bytes with the digest in `goldens/manifest.json`; the key is derived from the "
            "original data file, so the outputs themselves never leave our side."
        ),
        (
            "- **Redaction.** Failure output on the engagement data is redacted: the terminal and the "
            "JUnit files show only the unit, the number of differing rows and the column names; the "
            "detail stays in our private logs."
        ),
        (
            "- **Pass rate.** A cell's rate is passed / |C_d|, where C_d is the kept units "
            "characterized (status ok) on that dataset. Failed, error, skipped and not-implemented "
            "outcomes count as not passed."
        ),
    ]
    return "\n".join(lines)


def results_by_cell(a: Analysis) -> str:
    rows = []
    for x in a.eq_cells:
        cd = a.c.get(x.get("dataset"), 0)
        rate = cell_rate(int(x.get("passed") or 0), cd)
        rows.append(
            [
                x.get("dataset"),
                db_title(x.get("db")),
                x.get("passed"),
                int(x.get("total") or 0) - int(x.get("passed") or 0),
                cd,
                r4(rate),
                "pass" if rate >= THRESHOLD else "FAIL",
            ]
        )
    if not rows:
        return "No equivalence cells."
    return table(
        [
            "Dataset",
            "Database",
            "Passed",
            "Not passed",
            "Total (C_d)",
            "Rate",
            f"Gate (>= {THRESHOLD:.2f})",
        ],
        rows,
    )


def results_by_kind(inp: Inputs, a: Analysis) -> str:
    units = inp.manifest.get("units") or {}
    rows = []
    for kind in KINDS:
        ids = {u for u, x in units.items() if x.get("kind") == kind}
        if not ids:
            rows.append([KIND_TITLES[kind], 0, "-", "-", "-"])
            continue
        tests = 0
        failed = 0
        for x in a.eq_cells:
            d = x.get("dataset")
            tests += sum(
                1
                for u in ids
                if ((units[u].get("datasets") or {}).get(d) or {}).get("status") == "ok"
            )
            failed += sum(
                1 for np_ in x.get("not_passed") or [] if np_.get("unit") in ids
            )
        passed = tests - failed
        rows.append(
            [KIND_TITLES[kind], len(ids), tests, passed, r4(cell_rate(passed, tests))]
        )
    text = table(["Output kind", "Kept units", "Unit tests", "Passed", "Rate"], rows)
    return (
        text
        + "\n\nUnit tests count every kept unit of the kind once per cell where it is characterized."
    )


def coverage(inp: Inputs, a: Analysis) -> str:
    units = inp.manifest.get("units") or {}
    by_area: dict[str, int] = {}
    for u in units.values():
        by_area[str(u.get("area"))] = by_area.get(str(u.get("area")), 0) + 1
    st = inp.status
    out = [
        f"**Kept units.** {a.q} kept units (|Q|): "
        + join_words([f"{area} {n}" for area, n in sorted(by_area.items())])
        + ".",
        "",
    ]
    rows = []
    for d in a.datasets:
        counts: dict[str, int] = {}
        for u in units.values():
            s = ((u.get("datasets") or {}).get(d) or {}).get("status", "missing")
            counts[s] = counts.get(s, 0) + 1
        other = sum(n for s, n in counts.items() if s not in ("ok", "empty"))
        rows.append(
            [
                d,
                a.c.get(d, 0),
                counts.get("empty", 0),
                other,
                r4(cell_rate(a.c.get(d, 0), a.q)),
            ]
        )
    out += [
        table(
            [
                "Dataset",
                "Characterized (C_d)",
                "Empty",
                "Not run (other statuses)",
                "C_d / Q",
            ],
            rows,
        ),
        "",
        (
            f"**Characterization on engagement data.** {r4(st['rate'])} of the kept units are "
            f"characterized (at least {MIN_CHARACTERIZATION:.2f} required) and {pct(st['empty_share'])} "
            f"give an empty output (at most {pct(MAX_EMPTY)} allowed)."
        ),
        "",
        "**Kept rules with a test.**",
        "",
    ]
    how = {
        "rule": "a rule test whose docstring starts with the rule id (`*/tests/test_rules.py`)",
        "equivalence": "a kept unit that implements one of the rule's sources",
        "e2e": "a workflow whose end-to-end test left screenshots in `docs/img/after/`",
    }
    out.append(
        table(
            ["Test kind", "Kept rules", "With a test", "Checked by"],
            [
                [k, a.rule_cover[k][1], a.rule_cover[k][0], how[k]]
                for k in ("rule", "equivalence", "e2e")
            ],
        )
    )
    objects = inp.rules.get("objects") or []
    types = sorted({str(o.get("type")) for o in objects})
    orow = []
    for t in types:
        n = {
            d: sum(1 for o in objects if o.get("type") == t and o.get("decision") == d)
            for d in ("keep", "drop", "out_of_scope")
        }
        orow.append([t, n["keep"], n["drop"], n["out_of_scope"]])
    totals = {
        d: sum(1 for o in objects if o.get("decision") == d)
        for d in ("keep", "drop", "out_of_scope")
    }
    orow.append(["**all**", totals["keep"], totals["drop"], totals["out_of_scope"]])
    out += [
        "",
        (
            "**Objects of the original application** (`analysis/rules.json`; reasons in "
            "`analysis/keep-drop.md`):"
        ),
        "",
        table(["Type", "Kept", "Dropped", "Out of scope"], orow),
        "",
        "**Inventory** (counted from the design, `toolkit inventory`):",
        "",
        table(
            ["Count", "Value"],
            [
                [k, num(v) if isinstance(v, int) else v]
                for k, v in sorted((inp.inventory.get("counts") or {}).items())
            ],
        ),
    ]
    return "\n".join(out)


def _shot_link(name: str, prefix: str) -> str:
    label = name[len(prefix) :].removesuffix(".png").replace("-", " ").strip() or name
    return f"[{label}](img/after/{name})"


def tests_section(inp: Inputs, a: Analysis) -> str:
    rows = []
    for x in a.rule_cells + a.e2e_cells:
        rows.append(
            [
                "rule tests" if x.get("suite") == "rules" else "end-to-end tests",
                x.get("dataset"),
                db_title(x.get("db")),
                x.get("passed"),
                x.get("failed"),
                x.get("errors"),
                x.get("skipped"),
                x.get("total"),
                r4(float(x.get("rate") or 0)),
            ]
        )
    out = [
        table(
            [
                "Suite",
                "Data",
                "Database",
                "Passed",
                "Failed",
                "Errors",
                "Skipped",
                "Total",
                "Rate",
            ],
            rows,
        )
        if rows
        else "No rule or end-to-end test results.",
        "",
        (
            "Rule tests run on hand-written fixtures (`fixtures`); end-to-end tests drive a real "
            "browser through each workflow on synthetic seed 1 (`seed-1`), on the primary database."
        ),
        "",
    ]
    if inp.rule_tests:
        out.append(
            "Rule ids named by the rule tests, per folder: "
            + join_words([f"{k} {len(v)}" for k, v in sorted(inp.rule_tests.items())])
            + "."
        )
        out.append("")
    out.append("**Workflows and their screens after the move** (`docs/img/after/`):")
    out.append("")
    used: set[str] = set()
    for w in inp.rules.get("workflows") or []:
        wid = w.get("id")
        shots = [n for n in inp.after if n.startswith(f"{wid}-")]
        used.update(shots)
        links = ", ".join(_shot_link(n, f"{wid}-") for n in shots) or "no screenshots"
        out.append(f"- {w.get('title')} (`{wid}`): {links}")
    areas = [
        x.get("id") for x in inp.rules.get("areas") or [] if x.get("status") == "kept"
    ]
    mains = [f"{x}-main.png" for x in areas if f"{x}-main.png" in inp.after]
    used.update(mains)
    if mains:
        out.append(
            "- Main screen of each area: "
            + ", ".join(
                f"[{m.removesuffix('-main.png')}](img/after/{m})" for m in mains
            )
        )
    rest = [n for n in inp.after if n not in used]
    if rest:
        out.append(
            "- Other screens: "
            + ", ".join(f"[{n.removesuffix('.png')}](img/after/{n})" for n in rest)
        )
    return "\n".join(out)


def known_differences(inp: Inputs, a: Analysis) -> str:
    units = inp.manifest.get("units") or {}
    out = []
    if a.not_passed:
        rows = []
        for unit, lst in a.not_passed.items():
            by_entry: dict[int, list[tuple[str, str]]] = {}
            entries: dict[int, dict | None] = {}
            for cl, outcome in lst:
                e = a.known_index.get((unit, cl))
                key = id(e) if e is not None else 0
                entries[key] = e
                by_entry.setdefault(key, []).append((cl, outcome))
            for key, cells in sorted(by_entry.items(), key=lambda kv: kv[1]):
                e = entries[key]
                rows.append(
                    [
                        unit,
                        (units.get(unit) or {}).get("area") or "-",
                        ", ".join(cl for cl, _o in cells),
                        ", ".join(
                            sorted({OUTCOME_TITLES.get(o, o) for _c, o in cells})
                        ),
                        e.get("reason")
                        if e
                        else "**no entry in docs/known-differences.json**",
                        ", ".join(e.get("rule_ids") or []) if e else "-",
                    ]
                )
        out += [
            "Units that did not pass in some cell, with the reason:",
            "",
            table(["Unit", "Area", "Cells", "Outcome", "Reason", "Rules"], rows),
        ]
    else:
        out.append(
            "Every characterized unit matched its reference output in every cell: no compared "
            "output differs."
        )
    flagged = unconfirmed_units(inp)
    if flagged:
        out += [
            "",
            (
                f"Kept units whose reference output was not confirmed on Microsoft Access "
                f"({len(flagged)}; flagged needs_access_confirmation in `goldens/manifest.json` "
                f"because {CONFIRMATION_WHY}). Their goldens come from UCanAccess alone and "
                f"{no_access_run_words(inp)}, so a pass shows agreement with UCanAccess, not "
                "with Access:"
            ),
            "",
            table(
                ["Unit", "Area", "Kind"],
                [
                    [u, units[u].get("area") or "-", units[u].get("kind") or "-"]
                    for u in flagged
                ],
            ),
        ]
    uncharacterized = []
    for unit in sorted(units):
        for d in a.datasets:
            entry = ((units[unit].get("datasets") or {}).get(d)) or {}
            if entry.get("status") != "ok":
                uncharacterized.append(
                    [
                        unit,
                        units[unit].get("area") or "-",
                        d,
                        entry.get("status", "missing"),
                        entry.get("reason") or "-",
                    ]
                )
    out.append("")
    if uncharacterized:
        out += [
            (
                "Kept units that are not characterized on a dataset, so no cell tests them there "
                "(status and reason from `goldens/manifest.json`; `empty` means the dataset holds no "
                "rows for the unit):"
            ),
            "",
            table(["Unit", "Area", "Dataset", "Status", "Reason"], uncharacterized),
        ]
    else:
        out.append("Every kept unit is characterized on every dataset.")
    if a.stale_known:
        out += [
            "",
            "Entries in `docs/known-differences.json` that no current result needs: "
            + ", ".join(f"{u} ({cl})" for u, cl in a.stale_known)
            + ".",
        ]
    return "\n".join(out)


def rerun(inp: Inputs, a: Analysis) -> str:
    dbs = result_dbs(inp, a) or a.dbs
    seeds = [d for d in a.datasets if d != "engagement"]
    first_seed = seeds[0] if seeds else "seed-1"
    first_db = dbs[0] if dbs else "postgresql"
    out = [
        (
            "**Synthetic seeds (anyone, any machine, CI included).** The harness is vendored in "
            "`equivalence/` and runs without our toolkit. From `app/`, one cell at a time:"
        ),
        "",
        "```",
        "uv run python -m pytest -c pyproject.toml --rootdir . ../equivalence/tests -p equivalence.plugin \\",
        f"  --create-db --eq-adapter ../equivalence/adapter.toml --eq-dataset {first_seed} --eq-db {first_db} \\",
        f"  --junitxml junit-equivalence-{first_seed}-{first_db}.xml -p no:cacheprovider",
        "```",
        "",
        (
            f"Repeat with `--eq-dataset` {join_words([f'`{s}`' for s in seeds]) or '`seed-1`'} and "
            f"`--eq-db` {join_words([f'`{d}`' for d in dbs])}. `equivalence/README.md` explains the "
            "server URL variables and the options."
        ),
        "",
        (
            "**Rule and end-to-end tests.** From `app/`: `uv run pytest -q --create-db` runs the whole "
            "suite, end-to-end tests included, on the database in `DATABASE_URL`"
        ),
    ]
    if "mssql" in dbs:
        out[-1] += "; on SQL Server add `--ignore=tests/e2e`."
    else:
        out[-1] += "."
    out += [
        "",
        (
            "**Engagement dataset.** Its goldens and outputs hold your data, so it runs only on our "
            "side, through our data plane (`mp-data equivalence <records>/engagement.json --adapter "
            "django --datasets all --dbs from-engagement --min-pass-per-cell 0.9`), with redacted "
            "output. You can re-run it on your own copy of the data as `equivalence/README.md` "
            "describes under Re-running on your own data."
        ),
        "",
        (
            "**This report.** `mp-data results summarize` turns the JUnit files of a full run into "
            "`equivalence/results.json`; `toolkit report <records>/engagement.json --results "
            "equivalence/results.json --out docs/equivalence-report.md` renders this file, and "
            "`--check` confirms it is current."
        ),
    ]
    return "\n".join(out)


# ------------------------------------------------------------------ render


def render(inp: Inputs) -> tuple[str, Analysis]:
    a = analyse(inp)
    template = inp.template
    for heading in SECTIONS:
        if f"\n## {heading}\n" not in template:
            raise ReportError(f"template: section {heading} missing")
    kind = (inp.eng.get("source") or {}).get("kind")
    if REPORT_COVERAGE_SENTENCE not in template:
        raise ReportError("template: the report-coverage sentence is missing")
    if kind not in ACCESS_KINDS:
        template = template.replace(REPORT_COVERAGE_SENTENCE + "\n\n", "")
    text = Template(template).substitute(
        project_name=(inp.eng.get("app") or {}).get("project_name")
        or inp.eng.get("id"),
        intro=intro(inp, a),
        summary=summary(inp, a),
        method=method(inp, a),
        results_by_cell=results_by_cell(a),
        results_by_kind=results_by_kind(inp, a),
        coverage=coverage(inp, a),
        tests=tests_section(inp, a),
        known_differences=known_differences(inp, a),
        rerun=rerun(inp, a),
    )
    text = re.sub(r"\n{3,}", "\n\n", text).rstrip("\n") + "\n"
    return text, a
