"""spec-check against gates.json and change orders (docs/contracts.md#records, #rules-json).
Synthetic fixtures only (fixtures/rules/analysis, gates.json, change-orders/)."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import jsonschema
import pytest

from toolkit import cli

FIX = Path(__file__).resolve().parent / "fixtures" / "rules"
ROOT = Path(__file__).resolve().parents[2]
SUMMARY_SCHEMA = json.loads(
    (ROOT / "toolkit" / "schemas" / "summary.schema.json").read_text()
)


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setenv("MP_QUIET", "1")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fake(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def run(analysis, gates, co_dir, capfd) -> tuple[int, dict]:
    code = cli.main(
        [
            "spec-check",
            str(analysis),
            "--gates",
            str(gates),
            "--change-orders",
            str(co_dir),
        ]
    )
    out, _ = capfd.readouterr()
    lines = out.splitlines()
    assert len(lines) == 1, out
    summary = json.loads(lines[0])
    jsonschema.validate(summary, SUMMARY_SCHEMA)
    assert summary["exit_code"] == code
    return code, summary


def co_md(n: int, base: str, after_json: str, after_md: str) -> str:
    return (
        f"# CO-{n:03d}: a synthetic change\n"
        "requested: 2026-11-18\n"
        f"base_rules_sha256: {base}\n"
        f"rules_sha256_after: {after_json}\n"
        f"rules_md_sha256_after: {after_md}\n"
        "approved:\n\n## Request\n## Rules added\n## Rules changed\n## Objects changed\n"
    )


@pytest.fixture
def signed(tmp_path):
    """analysis/ signed at S1 with the current hashes; no change orders directory."""
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    shutil.copy(FIX / "valid.json", analysis / "rules.json")
    shutil.copy(FIX / "analysis" / "rules.md", analysis / "rules.md")
    gates = tmp_path / "gates.json"
    gates.write_text(
        json.dumps(
            {
                "S0": {"approved": "2026-10-20"},
                "S1": {
                    "approved": "2026-11-02",
                    "signed_by": "Fixture Signer (demo)",
                    "rules_sha256": sha(analysis / "rules.json"),
                    "rules_md_sha256": sha(analysis / "rules.md"),
                    "deposit_received": True,
                },
            }
        )
    )
    return analysis, gates, tmp_path / "change-orders"


def edit_rules(analysis: Path) -> None:
    doc = json.loads((analysis / "rules.json").read_text())
    doc["spec_version"] = "1.1"
    doc["rules"][0]["confidence"] = "high"
    (analysis / "rules.json").write_text(json.dumps(doc, indent=2) + "\n")


def test_fixture_state_passes(capfd):
    code, summary = run(
        FIX / "analysis", FIX / "gates.json", FIX / "change-orders", capfd
    )
    assert code == 0, summary
    assert "matches the approved state CO-001" in summary["messages"]


def test_signed_state_passes_without_change_orders_dir(signed, capfd):
    analysis, gates, co_dir = signed
    assert not co_dir.exists()
    code, summary = run(analysis, gates, co_dir, capfd)
    assert code == 0
    assert summary["sha256s"]["rules.json"] == sha(analysis / "rules.json")
    assert summary["sha256s"]["rules.md"] == sha(analysis / "rules.md")
    assert "matches the approved state S1" in summary["messages"]


def test_edited_rules_json_fails(signed, capfd):
    analysis, gates, co_dir = signed
    edit_rules(analysis)
    code, summary = run(analysis, gates, co_dir, capfd)
    assert code == 1 and summary["status"] == "failed"


def test_edited_rules_md_fails(signed, capfd):
    analysis, gates, co_dir = signed
    (analysis / "rules.md").write_text(
        (analysis / "rules.md").read_text() + "extra line\n"
    )
    assert run(analysis, gates, co_dir, capfd)[0] == 1


def test_pending_change_order_with_right_base_passes(signed, capfd):
    analysis, gates, co_dir = signed
    base = sha(analysis / "rules.json")
    edit_rules(analysis)
    co_dir.mkdir()
    (co_dir / "CO-001.md").write_text(
        co_md(1, base, sha(analysis / "rules.json"), sha(analysis / "rules.md"))
    )
    code, summary = run(analysis, gates, co_dir, capfd)
    assert code == 0
    assert "pending CO-001: approve at the gate" in summary["messages"]


def test_pending_change_order_with_wrong_base_fails(signed, capfd):
    analysis, gates, co_dir = signed
    edit_rules(analysis)
    co_dir.mkdir()
    (co_dir / "CO-001.md").write_text(
        co_md(
            1,
            fake("another base"),
            sha(analysis / "rules.json"),
            sha(analysis / "rules.md"),
        )
    )
    code, summary = run(analysis, gates, co_dir, capfd)
    assert code == 1
    assert not any(m.startswith("pending") for m in summary["messages"])


def test_header_must_start_at_column_0(signed, capfd):
    analysis, gates, co_dir = signed
    base = sha(analysis / "rules.json")
    edit_rules(analysis)
    co_dir.mkdir()
    text = co_md(1, base, sha(analysis / "rules.json"), sha(analysis / "rules.md"))
    (co_dir / "CO-001.md").write_text(
        text.replace("\nbase_rules_sha256", "\n  base_rules_sha256")
    )
    assert run(analysis, gates, co_dir, capfd)[0] == 1


def test_approved_change_order_becomes_the_state(signed, capfd):
    analysis, gates, co_dir = signed
    base = sha(analysis / "rules.json")
    edit_rules(analysis)
    new_json, new_md = sha(analysis / "rules.json"), sha(analysis / "rules.md")
    co_dir.mkdir()
    (co_dir / "CO-001.md").write_text(co_md(1, base, new_json, new_md))
    g = json.loads(gates.read_text())
    g["CO-001"] = {
        "approved": "2026-11-20",
        "rules_sha256": new_json,
        "rules_md_sha256": new_md,
    }
    gates.write_text(json.dumps(g))
    code, summary = run(analysis, gates, co_dir, capfd)
    assert code == 0
    assert "matches the approved state CO-001" in summary["messages"]
    # the S1 files no longer pass once CO-001 is approved
    shutil.copy(FIX / "valid.json", analysis / "rules.json")
    assert run(analysis, gates, co_dir, capfd)[0] == 1


def test_highest_approved_change_order_wins(signed, capfd):
    analysis, gates, co_dir = signed
    g = json.loads(gates.read_text())
    g["CO-002"] = {
        "approved": "2026-12-01",
        "rules_sha256": sha(analysis / "rules.json"),
        "rules_md_sha256": sha(analysis / "rules.md"),
    }
    g["CO-001"] = {
        "approved": "2026-11-20",
        "rules_sha256": fake("co1 json"),
        "rules_md_sha256": fake("co1 md"),
    }
    g["S1"]["rules_sha256"] = fake("s1 json")
    gates.write_text(json.dumps(g))
    code, summary = run(analysis, gates, co_dir, capfd)
    assert code == 0
    assert "matches the approved state CO-002" in summary["messages"]


def test_unapproved_gates_entry_does_not_count(signed, capfd):
    analysis, gates, co_dir = signed
    g = json.loads(gates.read_text())
    g["CO-001"] = {
        "approved": "",
        "rules_sha256": fake("x"),
        "rules_md_sha256": fake("y"),
    }
    gates.write_text(json.dumps(g))
    assert run(analysis, gates, co_dir, capfd)[0] == 0


def test_pending_after_approved_change_order(capfd, tmp_path):
    """The fixture's CO-002 is pending on top of the approved CO-001."""
    root = tmp_path / "fx"
    shutil.copytree(FIX / "analysis", root / "analysis")
    shutil.copytree(FIX / "change-orders", root / "change-orders")
    header = {}
    for line in (root / "change-orders" / "CO-002.md").read_text().splitlines():
        if ": " in line:
            k, v = line.split(": ", 1)
            header[k] = v
    gates = json.loads((FIX / "gates.json").read_text())
    assert header["base_rules_sha256"] == gates["CO-001"]["rules_sha256"]
    # make the files hash to CO-002's _after values by pointing them at new content
    (root / "analysis" / "rules.json").write_text("{}\n")
    (root / "analysis" / "rules.md").write_text("# changed\n")
    text = (root / "change-orders" / "CO-002.md").read_text()
    text = text.replace(
        header["rules_sha256_after"], sha(root / "analysis" / "rules.json")
    )
    text = text.replace(
        header["rules_md_sha256_after"], sha(root / "analysis" / "rules.md")
    )
    (root / "change-orders" / "CO-002.md").write_text(text)
    code, summary = run(
        root / "analysis", FIX / "gates.json", root / "change-orders", capfd
    )
    assert code == 0
    assert "pending CO-002: approve at the gate" in summary["messages"]


def test_no_s1_entry_is_not_signed(signed, capfd):
    analysis, gates, co_dir = signed
    gates.write_text(json.dumps({"S0": {"approved": "2026-10-20"}}))
    code, summary = run(analysis, gates, co_dir, capfd)
    assert code == 1
    assert any(m.startswith("spec not signed") for m in summary["messages"])


def test_missing_gates_exits_2(signed, capfd):
    analysis, gates, co_dir = signed
    gates.unlink()
    code, summary = run(analysis, gates, co_dir, capfd)
    assert code == 2 and summary["status"] == "error"


@pytest.mark.parametrize("name", ["rules.json", "rules.md"])
def test_missing_analysis_file_exits_2(signed, name, capfd):
    analysis, gates, co_dir = signed
    (analysis / name).unlink()
    assert run(analysis, gates, co_dir, capfd)[0] == 2
