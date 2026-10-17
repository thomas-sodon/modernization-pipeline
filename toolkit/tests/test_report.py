"""S6.report: `results summarize` and `report` (docs/contracts.md#harness, #pass-rate).

Fixtures are hand-written and synthetic (toolkit/tests/fixtures/report/, see its README): six
kept units, two seeds, two databases. Every summary is checked against summary.schema.json, the
schema mp-data validates.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from toolkit import cli
from toolkit import report as rp

FIX = Path(__file__).parent / "fixtures" / "report"
SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "schemas" / "summary.schema.json").read_text(
        encoding="utf-8"
    )
)
HEADINGS = [
    "## Summary",
    "## Method",
    "## Results by dataset and database",
    "## Results by output kind",
    "## Coverage",
    "## Rule tests and end-to-end tests",
    "## Known differences",
    "## How to re-run",
]


def run_cli(capfd, *argv) -> tuple[int, dict]:
    capfd.readouterr()
    code = cli.main([str(a) for a in argv])
    out, _err = capfd.readouterr()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1, out
    summary = json.loads(lines[0])
    jsonschema.validate(summary, SCHEMA)
    assert summary["exit_code"] == code
    return code, summary


@pytest.fixture
def fx(tmp_path, quiet):
    records = tmp_path / "records" / "fixture-eng"
    records.mkdir(parents=True)
    shutil.copy(FIX / "engagement.json", records / "engagement.json")
    (records / "paths.local.json").write_text(
        (FIX / "paths.local.json")
        .read_text(encoding="utf-8")
        .replace("/ROOT", str(tmp_path)),
        encoding="utf-8",
    )
    target = tmp_path / "target" / "shop"
    shutil.copytree(FIX / "target", target)
    private = tmp_path / "home" / "engagements" / "fixture-eng" / "private"
    shutil.copytree(FIX / "private", private)
    after = target / "docs" / "img" / "after"
    after.mkdir(parents=True)
    for name in (
        "order_flow-01-start.png",
        "order_flow-02-invoice.png",
        "orders-main.png",
    ):
        (after / name).write_bytes(b"")
    eq_dir = private / "results" / "S6"
    app_dir = private / "synthetic" / "results" / "S6"
    shutil.copytree(FIX / "junit" / "equivalence", eq_dir)
    shutil.copytree(FIX / "junit" / "app", app_dir)
    return SimpleNamespace(
        eng=records / "engagement.json",
        target=target,
        private=private,
        eq=eq_dir,
        app=app_dir,
        results=target / "equivalence" / "results.json",
        report=target / "docs" / "equivalence-report.md",
        known=target / "docs" / "known-differences.json",
    )


def summarize(capfd, fx, *dirs):
    dirs = dirs or (fx.eq, fx.app)
    args = ["results", "summarize", fx.eng]
    for d in dirs:
        args += ["--junit-dir", d]
    return run_cli(capfd, *args, "--out", fx.results)


def report(capfd, fx, *extra):
    return run_cli(
        capfd, "report", fx.eng, "--results", fx.results, "--out", fx.report, *extra
    )


def use_variant(fx):
    shutil.copy(FIX / "variants" / "junit-equivalence-seed-2-mssql.xml", fx.eq)


def cells(fx) -> dict:
    doc = json.loads(fx.results.read_text(encoding="utf-8"))
    return {(c["suite"], c["dataset"], c["db"]): c for c in doc["cells"]}


# ------------------------------------------------------------------ results summarize


def test_summarize_writes_the_cells_the_engagement_and_manifest_require(fx, capfd):
    code, s = summarize(capfd, fx)
    assert code == 0, s
    doc = json.loads(fx.results.read_text(encoding="utf-8"))
    assert (
        fx.results.read_text(encoding="utf-8")
        == json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    )
    assert doc["format_version"] == 1
    assert doc["engagement"] == "fixture-eng"
    assert doc["date"] == "2026-03-02"
    got = [(c["suite"], c["dataset"], c["db"]) for c in doc["cells"]]
    assert got == [
        ("equivalence", "engagement", "postgresql"),
        ("equivalence", "engagement", "mssql"),
        ("equivalence", "seed-1", "postgresql"),
        ("equivalence", "seed-1", "mssql"),
        ("equivalence", "seed-2", "postgresql"),
        ("equivalence", "seed-2", "mssql"),
        ("rules", "fixtures", "postgresql"),
        ("rules", "fixtures", "mssql"),
        ("e2e", "seed-1", "postgresql"),
    ]
    c = cells(fx)
    assert c[("equivalence", "engagement", "postgresql")]["total"] == 6
    assert c[("equivalence", "seed-1", "mssql")]["total"] == 5
    assert all("not_passed" in v for k, v in c.items() if k[0] == "equivalence")
    assert all("not_passed" not in v for k, v in c.items() if k[0] != "equivalence")
    # rule tests: 3 cases of */tests/test_rules.py; the view test counts in neither split
    assert c[("rules", "fixtures", "postgresql")]["total"] == 3
    assert c[("e2e", "seed-1", "postgresql")]["total"] == 1
    assert doc["gate"] == {"min_cell_rate": 1.0, "pooled_rate": 1.0, "threshold": 0.9}
    assert s["rates"] == {"min_cell": 1.0, "pooled": 1.0}
    assert all("not_passed" not in x for x in s["cells"])
    assert s["paths"] == [str(fx.results.resolve())]


def test_app_file_without_e2e_cases_gives_no_e2e_cell(fx, capfd):
    code, _s = summarize(capfd, fx)
    assert code == 0
    assert ("e2e", "seed-1", "mssql") not in cells(fx)
    assert ("rules", "fixtures", "mssql") in cells(fx)


def test_required_cells_follow_the_engagement_databases(fx, capfd):
    eng = json.loads(fx.eng.read_text(encoding="utf-8"))
    eng["database"]["verify_also"] = []
    fx.eng.write_text(json.dumps(eng), encoding="utf-8")
    code, s = summarize(capfd, fx)
    assert code == 0, s
    assert {k[2] for k in cells(fx)} == {"postgresql"}
    # the SQL Server files are now other cells: ignored and named
    assert any("junit-equivalence-seed-1-mssql.xml" in m for m in s["messages"])
    assert any("junit-app-fixtures-mssql.xml" in m for m in s["messages"])


def test_required_cells_follow_the_manifest_seeds(fx, capfd):
    manifest_path = fx.target / "goldens" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for unit in manifest["units"].values():
        unit["datasets"]["seed-3"] = dict(unit["datasets"]["seed-1"])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    code, s = summarize(capfd, fx)
    assert code == 1
    assert "junit-equivalence-seed-3-postgresql.xml" in s["messages"][0]


def test_missing_required_file_exits_1(fx, capfd):
    (fx.eq / "junit-equivalence-seed-2-postgresql.xml").unlink()
    code, s = summarize(capfd, fx)
    assert code == 1
    assert s["status"] == "failed"
    assert "junit-equivalence-seed-2-postgresql.xml" in s["messages"][0]
    assert not fx.results.exists()


def test_file_in_two_directories_exits_2(fx, capfd):
    shutil.copy(fx.app / "junit-app-fixtures-postgresql.xml", fx.eq)
    code, s = summarize(capfd, fx)
    assert code == 2
    assert "junit-app-fixtures-postgresql.xml" in s["messages"][0]
    assert not fx.results.exists()


def test_malformed_xml_exits_2(fx, capfd):
    (fx.eq / "junit-equivalence-seed-1-mssql.xml").write_text(
        "<testsuites>", encoding="utf-8"
    )
    code, s = summarize(capfd, fx)
    assert code == 2
    assert "junit-equivalence-seed-1-mssql.xml" in s["messages"][0]


def test_not_passed_outcomes_and_rate_maths(fx, capfd):
    use_variant(fx)
    code, s = summarize(capfd, fx)
    assert code == 0
    cell = cells(fx)[("equivalence", "seed-2", "mssql")]
    assert (cell["passed"], cell["failed"], cell["errors"], cell["skipped"]) == (
        1,
        1,
        1,
        1,
    )
    assert cell["not_implemented"] == 1
    assert cell["total"] == 5
    assert cell["rate"] == 0.2
    assert cell["not_passed"] == [
        {"unit": "qA", "outcome": "failed"},
        {"unit": "qB", "outcome": "error"},
        {"unit": "qC", "outcome": "skipped"},
        {"unit": "qD", "outcome": "not_implemented"},
    ]
    doc = json.loads(fx.results.read_text(encoding="utf-8"))
    # lowest cell 1/5; pooled (6+6+5+5+5+1) / (6+6+5+5+5+5) = 28/32
    assert doc["gate"] == {"min_cell_rate": 0.2, "pooled_rate": 0.875, "threshold": 0.9}
    assert s["rates"] == {"min_cell": 0.2, "pooled": 0.875}


def test_canary_reaches_neither_results_nor_report(fx, capfd):
    use_variant(fx)
    entries = {
        "format_version": 1,
        "entries": [
            {
                "unit": u,
                "cells": ["seed-2:mssql"],
                "reason": "a synthetic difference for the test",
                "rule_ids": [],
            }
            for u in ("qA", "qB", "qC", "qD")
        ],
    }
    fx.known.write_text(json.dumps(entries), encoding="utf-8")
    code, s = summarize(capfd, fx)
    assert code == 0
    assert "CANARY" not in json.dumps(s)
    assert "CANARY" not in fx.results.read_text(encoding="utf-8")
    code, s = report(capfd, fx)
    assert code == 1  # the gate fails at 0.2, but the file is written
    assert "CANARY" not in fx.report.read_text(encoding="utf-8")
    assert "CANARY" not in json.dumps(s)


# ------------------------------------------------------------------ report


def test_report_renders_the_eight_sections_and_passes(fx, capfd):
    assert summarize(capfd, fx)[0] == 0
    code, s = report(capfd, fx)
    assert code == 0, s
    text = fx.report.read_text(encoding="utf-8")
    found = [ln for ln in text.splitlines() if ln in HEADINGS]
    assert found == HEADINGS
    assert "**Gate: PASS.**" in text
    # coverage-adjusted: 32 passed over 6 kept units x 6 cells = 36
    assert s["rates"]["coverage_adjusted"] == 0.8889
    assert "0.8889" in text
    assert s["rates"]["min_cell"] == 1.0 and s["rates"]["pooled"] == 1.0
    assert s["counts"]["kept_units"] == 6 and s["counts"]["cells"] == 6
    # the Access report-coverage sentence stays for Access kinds
    assert "render_document raises NotImplementedError" in text
    # SQL Server wording: local runs in Docker, CI a cross-check
    assert "Docker (mp-mssql22)" in text and "cross-check" in text
    # screenshots of the workflow as links
    assert "(img/after/order_flow-01-start.png)" in text
    assert "(img/after/orders-main.png)" in text
    assert "/ROOT" not in text and str(fx.target) not in text


def test_report_claims_no_more_than_the_comparison_covers(fx, capfd):
    assert summarize(capfd, fx)[0] == 0
    assert report(capfd, fx)[0] == 0
    text = fx.report.read_text(encoding="utf-8")
    lowered = text.lower()
    for claim in ("proves that", "the same results as", "is proven equivalent"):
        assert claim not in lowered
    # the scope names what is compared and what is not
    assert "The comparison covers 6 saved-query outputs (6 kept units)" in text
    assert "it compares no formula outputs or documents." in text
    assert "not Microsoft Access itself" in text
    assert (
        "- **What the rates cover:** 6 saved-query outputs (6 kept units); "
        "no formula outputs or documents are compared." in text
    )


def test_report_and_template_never_say_proven(fx, capfd):
    """Nothing compares Access report layouts, so nothing in the report may say 'proven'."""
    template = rp.TEMPLATE.read_text(encoding="utf-8")
    for source in (template, rp.REPORT_COVERAGE_SENTENCE):
        assert (
            re.search(r"\bprov(e|es|en|ing)\b|\bproof\b", source, re.IGNORECASE) is None
        )
    assert summarize(capfd, fx)[0] == 0
    assert report(capfd, fx)[0] == 0
    text = fx.report.read_text(encoding="utf-8")
    assert re.search(r"\bprov(e|es|en|ing)\b|\bproof\b", text, re.IGNORECASE) is None
    assert "same results" not in text.lower()
    # the Access report-coverage sentence says checked, and that Access is not compared
    assert (
        "Access report layouts and report-level totals are checked by the S5 view tests "
        "(test_views.py), rule tests and end-to-end tests (not compared with Access)"
        in text
    )
    coverage = text.split("## Coverage", 1)[1].split("## Rule tests", 1)[0]
    assert "| Test kind | Kept rules | With a test | Checked by |" in coverage
    assert "Proven by" not in coverage


def test_units_needing_access_confirmation_are_disclosed(fx, capfd):
    assert summarize(capfd, fx)[0] == 0
    assert report(capfd, fx)[0] == 0
    text = fx.report.read_text(encoding="utf-8")
    assert (
        "2 of the 6 kept units have reference outputs that were not confirmed" in text
    )
    assert "- **Not confirmed on Microsoft Access:** 2 of the 6 kept units" in text
    assert "this engagement had no Windows + Access machine" in text
    known = text.split("## Known differences", 1)[1].split("## How to re-run", 1)[0]
    assert "| qB | orders | saved_query |" in known
    assert "| qE | stock | saved_query |" in known
    assert "| qA | orders | saved_query |" not in known
    # an Excel source has no Access confirmation to disclose
    eng = json.loads(fx.eng.read_text(encoding="utf-8"))
    eng["source"]["kind"] = "xlsm"
    eng["source"]["path"] = "{{p.private}}/sources/shop.xlsm"
    fx.eng.write_text(json.dumps(eng), encoding="utf-8")
    report(capfd, fx)
    text = fx.report.read_text(encoding="utf-8")
    assert "needs_access_confirmation" not in text
    assert "Not confirmed on Microsoft Access" not in text


def test_no_flagged_units_means_no_confirmation_paragraph(fx, capfd):
    manifest_path = fx.target / "goldens" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for unit in manifest["units"].values():
        unit["needs_access_confirmation"] = False
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert summarize(capfd, fx)[0] == 0
    assert report(capfd, fx)[0] == 0
    text = fx.report.read_text(encoding="utf-8")
    assert "Not confirmed on Microsoft Access" not in text
    assert "not confirmed on Microsoft Access (" not in text


def test_report_names_only_databases_with_cells(fx, capfd):
    eng = json.loads(fx.eng.read_text(encoding="utf-8"))
    eng["database"]["verify_also"] = []
    fx.eng.write_text(json.dumps(eng), encoding="utf-8")
    assert summarize(capfd, fx)[0] == 0
    code, s = report(capfd, fx)
    assert code == 0, s
    text = fx.report.read_text(encoding="utf-8")
    assert "SQL Server" not in text
    assert "both databases" not in text


def test_uncharacterized_unit_shows_the_manifest_reason(fx, capfd):
    assert summarize(capfd, fx)[0] == 0
    report(capfd, fx)
    text = fx.report.read_text(encoding="utf-8")
    assert "| qF | stock | seed-2 | unsupported | crosstab |" in text
    assert "| qE | stock | seed-1 | empty | empty |" in text


def test_not_passed_unit_without_entry_exits_1(fx, capfd):
    use_variant(fx)
    assert summarize(capfd, fx)[0] == 0
    code, s = report(capfd, fx)
    assert code == 1
    assert any(
        "qA in cell seed-2:mssql has no known-differences entry" in m
        for m in s["messages"]
    )
    text = fx.report.read_text(encoding="utf-8")
    assert "no entry in docs/known-differences.json" in text


def test_known_difference_reason_is_shown(fx, capfd):
    use_variant(fx)
    fx.known.write_text(
        json.dumps(
            {
                "format_version": 1,
                "entries": [
                    {
                        "unit": u,
                        "cells": ["seed-2:mssql"],
                        "reason": f"synthetic reason for {u}",
                        "rule_ids": ["R-orders-002"],
                    }
                    for u in ("qA", "qB", "qC", "qD")
                ],
            }
        ),
        encoding="utf-8",
    )
    assert summarize(capfd, fx)[0] == 0
    code, s = report(capfd, fx)
    assert code == 1
    assert not any("known-differences" in m for m in s["messages"])
    assert any("lowest cell rate 0.2000" in m for m in s["messages"])
    text = fx.report.read_text(encoding="utf-8")
    assert "synthetic reason for qA" in text
    assert "**Gate: FAIL.**" in text


def test_cell_total_differing_from_manifest_exits_1(fx, capfd):
    assert summarize(capfd, fx)[0] == 0
    doc = json.loads(fx.results.read_text(encoding="utf-8"))
    for c in doc["cells"]:
        if (
            c["suite"] == "equivalence"
            and c["dataset"] == "seed-1"
            and c["db"] == "postgresql"
        ):
            c["total"] = 4
            c["passed"] = 4
    fx.results.write_text(json.dumps(doc), encoding="utf-8")
    code, s = report(capfd, fx)
    assert code == 1
    assert any(
        "seed-1:postgresql: total 4 differs from |C_d| 5" in m for m in s["messages"]
    )
    assert fx.report.is_file()


def test_missing_e2e_cell_or_failed_rule_test_exits_1(fx, capfd):
    assert summarize(capfd, fx)[0] == 0
    doc = json.loads(fx.results.read_text(encoding="utf-8"))
    doc["cells"] = [c for c in doc["cells"] if c["suite"] != "e2e"]
    for c in doc["cells"]:
        if c["suite"] == "rules" and c["db"] == "mssql":
            c["passed"], c["failed"], c["rate"] = 2, 1, 0.6667
    fx.results.write_text(json.dumps(doc), encoding="utf-8")
    code, s = report(capfd, fx)
    assert code == 1
    assert "results.json has no e2e cell" in s["messages"]
    assert any(m.startswith("rules tests on mssql: 2 of 3") for m in s["messages"])


def test_kept_rule_without_a_test_exits_1(fx, capfd):
    path = fx.target / "app" / "shop" / "stock" / "tests" / "test_rules.py"
    path.write_text('def test_other():\n    """No rule id here."""\n', encoding="utf-8")
    assert summarize(capfd, fx)[0] == 0
    code, s = report(capfd, fx)
    assert code == 1
    assert "kept rule without a test: R-stock-001 (rule)" in s["messages"]


def test_characterization_below_the_minimum_exits_1(fx, capfd):
    manifest_path = fx.target / "goldens" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for uid in ("qE", "qF"):
        manifest["units"][uid]["datasets"]["engagement"] = {
            "digest": None,
            "reason": "action-query",
            "rows": None,
            "status": "unsupported",
        }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    for db in ("postgresql", "mssql"):
        path = fx.eq / f"junit-equivalence-engagement-{db}.xml"
        text = path.read_text(encoding="utf-8")
        text = "\n".join(
            ln for ln in text.splitlines() if "::qE::" not in ln and "::qF::" not in ln
        )
        path.write_text(text, encoding="utf-8")
    assert summarize(capfd, fx)[0] == 0
    code, s = report(capfd, fx)
    assert code == 1
    assert any(m.startswith("characterization rate 0.6667") for m in s["messages"])
    text = fx.report.read_text(encoding="utf-8")
    assert "| qE | stock | engagement | unsupported | action-query |" in text


def test_two_runs_give_identical_bytes_and_check_passes(fx, capfd):
    assert summarize(capfd, fx)[0] == 0
    first_results = fx.results.read_bytes()
    assert summarize(capfd, fx)[0] == 0
    assert fx.results.read_bytes() == first_results
    assert report(capfd, fx)[0] == 0
    first = fx.report.read_bytes()
    assert report(capfd, fx)[0] == 0
    assert fx.report.read_bytes() == first
    code, s = report(capfd, fx, "--check")
    assert code == 0, s
    assert "paths" not in s


def test_check_catches_a_one_line_change_and_writes_nothing(fx, capfd):
    assert summarize(capfd, fx)[0] == 0
    assert report(capfd, fx)[0] == 0
    lines = fx.report.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[2] = "edited by hand\n"
    changed = "".join(lines)
    fx.report.write_text(changed, encoding="utf-8")
    code, s = report(capfd, fx, "--check")
    assert code == 1
    assert "differs from a fresh render" in s["messages"][0]
    assert fx.report.read_text(encoding="utf-8") == changed
    fx.report.unlink()
    code, s = report(capfd, fx, "--check")
    assert code == 1
    assert not fx.report.exists()


def test_xlsm_leaves_out_the_access_sentence(fx, capfd):
    eng = json.loads(fx.eng.read_text(encoding="utf-8"))
    eng["source"]["kind"] = "xlsm"
    eng["source"]["path"] = "{{p.private}}/sources/shop.xlsm"
    fx.eng.write_text(json.dumps(eng), encoding="utf-8")
    assert summarize(capfd, fx)[0] == 0
    report(capfd, fx)
    text = fx.report.read_text(encoding="utf-8")
    assert "render_document raises NotImplementedError" not in text
    assert [ln for ln in text.splitlines() if ln in HEADINGS] == HEADINGS


def test_missing_input_exits_2(fx, capfd):
    assert summarize(capfd, fx)[0] == 0
    fx.known.unlink()
    code, s = report(capfd, fx)
    assert code == 2
    assert "known-differences.json" in s["messages"][0]
