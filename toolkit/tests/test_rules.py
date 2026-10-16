"""rules-lint, rules-coverage, rules-render and schema-lint on synthetic fixtures
(docs/contracts.md#rules-json). The fixtures in fixtures/rules/ describe an invented book-lending
app and an invented fines-ledger workbook; nothing comes from a client or Microsoft file."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from toolkit import cli
from toolkit.rules import render_keep_drop

FIX = Path(__file__).resolve().parent / "fixtures" / "rules"
ROOT = Path(__file__).resolve().parents[2]
SUMMARY_SCHEMA = json.loads(
    (ROOT / "toolkit" / "schemas" / "summary.schema.json").read_text()
)

# fixture -> a JSON pointer its rules-lint output must name
INVALID_RULES = {
    "invalid-area-id.json": "/areas/2/id",
    "invalid-extra-key.json": "(root)",
    "invalid-duplicate-id.json": "/rules/2/id",
    "invalid-rule-area.json": "/rules/2/id",
    "invalid-rule-area-missing.json": "/rules/0/area",
    "invalid-reserved-area.json": "/areas/2/id",
    "invalid-keyword-area.json": "/areas/2/id",
    "invalid-question-ref.json": "/rules/2/questions/0",
    "invalid-rule-ref.json": "/relations/1/rule",
    "invalid-kept-area-empty.json": "/areas/0",
    "invalid-dropped-area-kept.json": "/objects/12/decision",
    "invalid-duplicate-object.json": "/objects/14",
    "invalid-calculated-column.json": "/objects/14/name",
    "invalid-source-name.json": "/rules/2/sources/2/ref",
    "invalid-e2e-workflow.json": "/rules/3/test_kind",
    "invalid-lookup-values.json": "/lookups/1/values",
    "invalid-area-file.json": "/areas",
    "invalid-signed.json": "/signed",
}
INVALID_DATA_MODEL = {
    "invalid-dm-type.json": "/tables/1/columns/3/type",
    "invalid-dm-name.json": "/tables/0/name",
    "invalid-dm-duplicate-column.json": "/tables/2/columns/3/name",
    "invalid-dm-key.json": "/tables/0/primary_key/0",
    "invalid-dm-relationship.json": "/relationships/1/from_columns/0",
    "invalid-dm-mapping.json": "/mappings/4/column",
    "invalid-dm-mapped-computed.json": "/computed/1",
}


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setenv("MP_QUIET", "1")


def run(args, capfd) -> tuple[int, dict, str]:
    code = cli.main([str(a) for a in args])
    out, err = capfd.readouterr()
    lines = out.splitlines()
    assert len(lines) == 1, out
    summary = json.loads(lines[0])
    jsonschema.validate(summary, SUMMARY_SCHEMA)
    assert summary["exit_code"] == code
    assert summary["status"] == {0: "ok", 1: "failed"}.get(code, "error")
    return code, summary, err


def write(path: Path, doc) -> Path:
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- rules-lint


@pytest.mark.parametrize("name", ["valid.json", "valid-area.json", "valid-xlsm.json"])
def test_valid_fixtures_lint(name, capfd):
    code, summary, _ = run(["rules-lint", FIX / name], capfd)
    assert code == 0, summary
    assert summary["counts"]["problems"] == 0


def test_spec_version_minor_bump_lints(tmp_path, capfd):
    doc = load("valid.json")
    doc["spec_version"] = "1.1"
    code, summary, _ = run(["rules-lint", write(tmp_path / "rules.json", doc)], capfd)
    assert code == 0, summary


@pytest.mark.parametrize("bad", ["1", "1.0.1", "v1.0", 1.0])
def test_spec_version_shape(bad, tmp_path, capfd):
    doc = load("valid.json")
    doc["spec_version"] = bad
    code, summary, _ = run(["rules-lint", write(tmp_path / "rules.json", doc)], capfd)
    assert code == 1
    assert any(m.startswith("/spec_version:") for m in summary["messages"])


@pytest.mark.parametrize("name", sorted(INVALID_RULES), ids=sorted(INVALID_RULES))
def test_invalid_fixture_names_its_pointer(name, capfd):
    code, summary, err = run(["rules-lint", FIX / name], capfd)
    assert code == 1
    ptr = INVALID_RULES[name]
    assert any(m.startswith(ptr + ":") for m in summary["messages"]), summary[
        "messages"
    ]
    assert ptr + ":" in err


def test_every_invalid_rules_fixture_is_listed():
    on_disk = {
        p.name
        for p in FIX.glob("invalid-*.json")
        if not p.name.startswith("invalid-dm-")
    }
    assert on_disk == set(INVALID_RULES)


def test_lint_missing_file_exits_2(tmp_path, capfd):
    code, summary, _ = run(["rules-lint", tmp_path / "nope.json"], capfd)
    assert code == 2 and summary["status"] == "error"


def test_lint_human_output(monkeypatch, capfd):
    monkeypatch.delenv("MP_QUIET")
    code = cli.main(["rules-lint", str(FIX / "invalid-signed.json")])
    out, err = capfd.readouterr()
    assert code == 1
    assert "rules-lint: failed (exit 1)" in out
    assert "/signed:" in err


# --------------------------------------------------------------------------- rules-coverage


@pytest.mark.parametrize(
    "rules,inventory",
    [("valid.json", "inventory.json"), ("valid-xlsm.json", "inventory-xlsm.json")],
)
def test_coverage_full(rules, inventory, capfd):
    code, summary, err = run(
        ["rules-coverage", FIX / rules, FIX / inventory, "--require", "100"], capfd
    )
    assert code == 0, summary
    c = summary["counts"]
    assert c["covered"] == c["total"] > 0 and c["uncovered"] == 0
    assert re.search(r"\b(\d+)/\1 = 100\.0%", summary["messages"][0])
    assert "100.0%" in err


def test_xlsm_denominator():
    inv = load("inventory-xlsm.json")
    kinds = {o["type"] for o in inv["objects"]}
    assert {
        "table",
        "calculated_column",
        "formula_block",
        "data_validation",
        "userform",
        "chart",
    } <= kinds


def test_coverage_drops_when_object_removed(tmp_path, capfd):
    doc = load("valid.json")
    doc["objects"] = [o for o in doc["objects"] if o["name"] != "qryYearlyStats"]
    path = write(tmp_path / "rules.json", doc)
    code, summary, err = run(
        ["rules-coverage", path, FIX / "inventory.json", "--require", "100"], capfd
    )
    assert code == 1
    assert summary["counts"]["covered"] == summary["counts"]["total"] - 1
    assert "13/14 = 92.9%" in summary["messages"][0]
    assert "uncovered: qryYearlyStats" in summary["messages"]
    assert "uncovered: qryYearlyStats" in err
    # the same file passes a lower bar
    code, _, _ = run(
        ["rules-coverage", path, FIX / "inventory.json", "--require", "90"], capfd
    )
    assert code == 0


def test_coverage_drops_without_reason_for_uncited_kept_object(tmp_path, capfd):
    doc = load("valid.json")
    uncited = [
        o
        for o in doc["objects"]
        if o["decision"] == "keep" and o["type"] != "table" and o["reason"]
    ]
    assert [o["name"] for o in uncited] == ["qryAvailableBooks"]
    uncited[0]["reason"] = None
    path = write(tmp_path / "rules.json", doc)
    code, summary, _ = run(
        ["rules-coverage", path, FIX / "inventory.json", "--require", "100"], capfd
    )
    assert code == 1
    assert "uncovered: qryAvailableBooks" in summary["messages"]


def test_coverage_hard_fails_on_unknown_source_ref(tmp_path, capfd):
    doc = load("valid.json")
    doc["rules"][1]["sources"].append({"type": "query", "ref": "qryNotThere"})
    path = write(tmp_path / "rules.json", doc)
    code, summary, _ = run(
        ["rules-coverage", path, FIX / "inventory.json", "--require", "0"], capfd
    )
    assert code == 1
    assert summary["counts"]["hard_failures"] == 1
    assert any(m.startswith("/rules/1/sources/2/ref:") for m in summary["messages"])


def test_coverage_hard_fails_on_unknown_object_and_duplicate(tmp_path, capfd):
    doc = load("valid.json")
    doc["objects"].append(dict(doc["objects"][0]))
    doc["objects"].append(
        {
            "type": "form",
            "name": "frmGhost",
            "decision": "drop",
            "area": "lending",
            "target_component": None,
            "reason": "x",
        }
    )
    path = write(tmp_path / "rules.json", doc)
    code, summary, _ = run(
        ["rules-coverage", path, FIX / "inventory.json", "--require", "0"], capfd
    )
    assert code == 1
    assert summary["counts"]["hard_failures"] == 2
    assert "uncovered: Book" in " ".join(summary["messages"])


def test_coverage_missing_input_exits_2(tmp_path, capfd):
    code, _, _ = run(
        [
            "rules-coverage",
            FIX / "valid.json",
            tmp_path / "none.json",
            "--require",
            "100",
        ],
        capfd,
    )
    assert code == 2


# --------------------------------------------------------------------------- rules-render


def test_render_is_deterministic_and_matches_fixture(tmp_path, capfd):
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    assert run(["rules-render", FIX / "valid.json", "--keep-drop", a], capfd)[0] == 0
    assert run(["rules-render", FIX / "valid.json", "--keep-drop", b], capfd)[0] == 0
    assert a.read_bytes() == b.read_bytes() == (FIX / "keep-drop.md").read_bytes()
    text = a.read_text()
    first = text.splitlines()[0]
    assert "generated from analysis/rules.json" in first.lower()
    assert "do not edit by hand" in first.lower()
    assert text.index("## Keep") < text.index("## Drop") < text.index("## Out of scope")


def test_render_sort_order():
    doc = load("valid.json")
    doc["objects"].reverse()
    assert render_keep_drop(doc) == (FIX / "keep-drop.md").read_text()
    keep_rows = [
        line.split(" | ")
        for line in render_keep_drop(doc).split("## Drop")[0].splitlines()
        if line.startswith("| ") and not line.startswith("| type")
    ]
    keys = [(r[2], r[0].lstrip("| "), r[1]) for r in keep_rows]
    assert keys == sorted(keys)


def test_render_check_passes_on_fixture(capfd):
    code, summary, _ = run(
        [
            "rules-render",
            FIX / "valid.json",
            "--keep-drop",
            FIX / "keep-drop.md",
            "--check",
        ],
        capfd,
    )
    assert code == 0, summary


def test_render_check_fails_after_edit_and_never_writes(tmp_path, capfd):
    md = tmp_path / "keep-drop.md"
    shutil.copy(FIX / "keep-drop.md", md)
    lines = md.read_text().splitlines(keepends=True)
    lines[10] = lines[10].replace("catalog.saved_queries", "catalog.views")
    md.write_text("".join(lines))
    before = md.read_bytes()
    mtime = md.stat().st_mtime_ns
    code, summary, err = run(
        ["rules-render", FIX / "valid.json", "--keep-drop", md, "--check"], capfd
    )
    assert code == 1
    assert "line(s) 11" in summary["messages"][0]
    assert "line(s) 11" in err
    assert md.read_bytes() == before and md.stat().st_mtime_ns == mtime


def test_render_check_missing_output_fails_without_writing(tmp_path, capfd):
    md = tmp_path / "keep-drop.md"
    code, _, _ = run(
        ["rules-render", FIX / "valid.json", "--keep-drop", md, "--check"], capfd
    )
    assert code == 1 and not md.exists()


def test_render_missing_rules_exits_2(tmp_path, capfd):
    code, _, _ = run(
        ["rules-render", tmp_path / "x.json", "--keep-drop", tmp_path / "k.md"], capfd
    )
    assert code == 2


# --------------------------------------------------------------------------- schema-lint


def test_schema_lint_valid(capfd):
    code, summary, _ = run(["schema-lint", FIX / "data-model.json"], capfd)
    assert code == 0, summary


@pytest.mark.parametrize(
    "name", sorted(INVALID_DATA_MODEL), ids=sorted(INVALID_DATA_MODEL)
)
def test_schema_lint_invalid(name, capfd):
    code, summary, _ = run(["schema-lint", FIX / name], capfd)
    assert code == 1
    ptr = INVALID_DATA_MODEL[name]
    assert any(m.startswith(ptr + ":") for m in summary["messages"]), summary[
        "messages"
    ]


def test_every_invalid_data_model_fixture_is_listed():
    assert {p.name for p in FIX.glob("invalid-dm-*.json")} == set(INVALID_DATA_MODEL)


def test_schema_lint_rejects_non_xlsm_and_extra_keys(tmp_path, capfd):
    doc = load("data-model.json")
    doc["source_kind"] = "accdt"
    doc["extra"] = []
    code, summary, _ = run(["schema-lint", write(tmp_path / "dm.json", doc)], capfd)
    assert code == 1
    assert any(m.startswith("/source_kind:") for m in summary["messages"])
    assert any("unknown key(s) extra" in m for m in summary["messages"])


def test_schema_lint_missing_file(tmp_path, capfd):
    assert run(["schema-lint", tmp_path / "dm.json"], capfd)[0] == 2


def test_data_model_schema_reuses_model_shapes():
    schema = json.loads(
        (ROOT / "toolkit" / "schemas" / "data-model.schema.json").read_text()
    )
    assert (
        schema["properties"]["tables"]["items"]["$ref"]
        == "model.schema.json#/$defs/table"
    )
    assert (
        schema["properties"]["relationships"]["items"]["$ref"]
        == "model.schema.json#/$defs/relationship"
    )


# --------------------------------------------------------------------------- quiet output


def test_quiet_subprocess_one_line():
    env = {**os.environ, "MP_QUIET": "1"}
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from toolkit.cli import main; sys.exit(main())",
            "rules-lint",
            str(FIX / "invalid-signed.json"),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 1
    lines = proc.stdout.splitlines()
    assert len(lines) == 1
    summary = json.loads(lines[0])
    jsonschema.validate(summary, SUMMARY_SCHEMA)
    assert summary["status"] == "failed" and summary["command"] == "rules-lint"
    assert "/signed:" in proc.stderr


# --------------------------------------------------------------------------- items_from (jq)


def _jq() -> str:
    found = shutil.which("jq") or str(Path.home() / ".local" / "bin" / "jq")
    assert Path(found).exists(), "environment item jq is missing"
    return found


def _items_from(task_id: str) -> dict:
    pipeline = json.loads((ROOT / "pipeline.json").read_text())
    for stage in pipeline["stages"]:
        for task in stage.get("tasks", []):
            if task.get("id") == task_id:
                return task["items_from"]
    raise AssertionError(f"{task_id} has no items_from")


def _run_jq(program: str, path: Path):
    proc = subprocess.run(
        [_jq(), "-c", program, str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(proc.stdout)


def test_jq_version():
    out = subprocess.run(
        [_jq(), "--version"], capture_output=True, text=True, check=True
    ).stdout
    assert re.match(r"^jq-1\.[78]", out)


def test_s1_areas_items_from():
    spec = _items_from("S1.areas")
    assert spec["file"].endswith("/analysis/rules.json")
    items = _run_jq(spec["jq"], FIX / "valid.json")
    assert items == [
        {
            "id": "catalog",
            "title": "Book catalogue",
            "scope": "table: Book, query: qryAvailableBooks",
        },
        {
            "id": "lending",
            "title": "Loans and fines",
            "scope": "table: Member, table: Loan, table: Fine, query: qryOverdueLoans, "
            "query: qryMemberFines, query: qryLoanCopy, form: frmLoan, report: rptOverdueNotice, "
            "procedure: modLoans.CheckOutBook, data_macro: Loan.BeforeChange",
        },
        {
            "id": "housekeeping",
            "title": "Housekeeping jobs",
            "scope": "query: qryYearlyStats, macro: mcrNightlyCleanup",
        },
    ]


def test_s5_features_items_from():
    spec = _items_from("S5.features")
    assert spec["file"].endswith("/analysis/rules.json")
    items = _run_jq(spec["jq"], FIX / "valid.json")
    assert items == [
        {
            "id": "catalog",
            "title": "Book catalogue",
            "scope": "queries: qryAvailableBooks; rules: R-catalog-001",
        },
        {
            "id": "lending",
            "title": "Loans and fines",
            "scope": "queries: qryOverdueLoans, qryMemberFines; "
            "rules: R-lending-001, R-lending-002, R-lending-003",
        },
    ]


def _contract_program(heading: str) -> str:
    text = (ROOT / "docs" / "contracts.md").read_text(encoding="utf-8")
    section = text.split('<a id="rules-json"></a>', 1)[1]
    after = section.split(heading, 1)[1]
    m = re.search(r"^jq -c '([^']*)' \S+$", after, re.MULTILINE)
    assert m, f"no jq line after {heading}"
    return m.group(1)


def test_s5_program_equals_contract():
    assert _items_from("S5.features")["jq"] == _contract_program("**S5 items**")


def test_s1_program_equals_contract():
    assert _items_from("S1.areas")["jq"] == _contract_program("**S1 areas**")
