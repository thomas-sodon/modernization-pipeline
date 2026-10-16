"""Redaction canary (docs/contracts.md#harness): a synthetic engagement dataset whose text cells are
CANARY-<n>, with forced mismatches, printed output and an exception that quotes a cell. Nothing
CANARY- may reach stdout, stderr or the JUnit file; the private log must hold them."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ._support import KEY, make_target, run_pytest

GOLDEN = [[1, "CANARY-1"], [2, "CANARY-2"], [3, "CANARY-3"]]
OBTAINED = [[1, "CANARY-1"], [2, "CANARY-22"], [4, "CANARY-4"]]


def canary_target(tmp_path):
    private = tmp_path / "private"
    units = {
        "qryCanary": {"area": "orders", "golden": {"engagement": GOLDEN}},
        "qryLoud": {"area": "orders", "golden": {"engagement": [[5, "CANARY-5"]]}},
        "qryBoom": {"area": "orders", "golden": {"engagement": [[6, "CANARY-6"]]}},
        "qryQuiet": {"area": "orders", "golden": {"engagement": [[7, "CANARY-7"]]}},
    }
    stub = {
        "rows": {
            "engagement": {
                "qryCanary": OBTAINED,
                "qryLoud": [[5, "CANARY-50"]],
                "qryQuiet": [[7, "CANARY-7"]],
            }
        },
        "print": {"qryLoud": "CANARY-51 printed by the app", "qryCanary": "CANARY-52"},
        "raise": {"qryBoom": "value CANARY-60 broke the query"},
    }
    target = make_target(tmp_path / "t", units, private=private, stub=stub)
    return target, private


def test_no_canary_reaches_stdout_stderr_or_junit(tmp_path):
    target, private = canary_target(tmp_path)
    junit = tmp_path / "junit.xml"
    proc = run_pytest(
        target,
        "--eq-adapter",
        "adapter.toml",
        "--eq-dataset",
        "engagement",
        "--eq-db",
        "stub",
        "--junitxml",
        str(junit),
        "-v",
        "-rA",
        "-l",
        "--tb=long",
        "-o",
        "junit_logging=all",
        env={"EQUIVALENCE_PRIVATE": str(private), "EQUIVALENCE_DIGEST_KEY": KEY.hex()},
    )
    assert proc.returncode == 1
    assert "CANARY-" not in proc.stdout
    assert "CANARY-" not in proc.stderr
    xml = junit.read_text(encoding="utf-8")
    assert "CANARY-" not in xml
    # the failures say how many rows differ, in which columns
    cases = {tc.get("name"): tc for tc in ET.parse(junit).getroot().iter("testcase")}
    msg = (
        cases["test_unit[orders::qryCanary::engagement::stub]"]
        .find("failure")
        .get("message")
    )
    assert "rows differ in columns [" in msg
    assert "Label" in msg
    assert (
        cases["test_unit[orders::qryBoom::engagement::stub]"].find("failure")
        is not None
    )
    quiet_case = cases["test_unit[orders::qryQuiet::engagement::stub]"]
    assert quiet_case.find("failure") is None and quiet_case.find("error") is None
    # the private log holds the details
    logs = list((private / "logs").iterdir())
    assert logs
    text = "".join(p.read_text(encoding="utf-8") for p in logs)
    assert "CANARY-60" in text
    assert "CANARY-51" in text
    # the datacompy report has counts and column names, never values
    reports = [p for p in logs if p.name.endswith(".datacompy.txt")]
    assert reports
    assert all("CANARY-" not in p.read_text(encoding="utf-8") for p in reports)


def test_redaction_is_on_for_any_dataset_with_equivalence_redact(tmp_path):
    private = tmp_path / "private"
    units = {"qryCanary": {"area": "orders", "golden": {"seed-1": GOLDEN}}}
    stub = {"rows": {"seed-1": {"qryCanary": OBTAINED}}}
    target = make_target(tmp_path / "t", units, private=private, stub=stub)
    args = (
        "--eq-adapter",
        "adapter.toml",
        "--eq-dataset",
        "seed-1",
        "--eq-db",
        "stub",
        "-v",
    )
    # synthetic seeds show the diff when redaction is off
    proc = run_pytest(target, *args)
    assert proc.returncode == 1
    assert "CANARY-22" in proc.stdout
    proc = run_pytest(
        target,
        *args,
        env={"EQUIVALENCE_REDACT": "1", "EQUIVALENCE_PRIVATE": str(private)},
    )
    assert proc.returncode == 1
    assert "CANARY-" not in proc.stdout + proc.stderr
