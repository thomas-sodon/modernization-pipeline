"""The equivalence plugin and runner on a fixture manifest with a stub adapter (synthetic data only).

Every run is a pytest subprocess on a vendored copy of this package with `toolkit` blocked from
import (tests/_support.py), so these tests also prove the harness runs without the toolkit.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from ._support import KEY, make_target, run_pytest, set_stub

ROWS_A = [[1, "red widget"], [2, "blue widget"]]
ROWS_B = [[7, "gear"]]
ROWS_C = [[3, "bolt"], [4, "nut"]]


def units() -> dict:
    return {
        "qryAlpha": {"area": "orders", "golden": {"seed-1": ROWS_A, "seed-2": ROWS_A}},
        "qryBeta": {
            "area": "purchase_orders",
            "golden": {"seed-1": ROWS_B, "seed-2": None},
        },
        "qryGamma": {
            "area": "orders",
            "golden": {"seed-1": ROWS_C, "seed-2": "needs-params"},
        },
    }


def good_stub() -> dict:
    return {
        "rows": {
            "seed-1": {"qryAlpha": ROWS_A, "qryBeta": ROWS_B, "qryGamma": ROWS_C},
            "seed-2": {"qryAlpha": ROWS_A},
        }
    }


@pytest.fixture
def target(tmp_path):
    return make_target(tmp_path / "target", units(), stub=good_stub())


def ids(proc) -> list[str]:
    return sorted(
        ln.split("::", 1)[1] for ln in proc.stdout.splitlines() if "::test_unit[" in ln
    )


def cell(*extra, dataset="seed-1"):
    return (
        "--eq-adapter",
        "adapter.toml",
        "--eq-dataset",
        dataset,
        "--eq-db",
        "stub",
        *extra,
    )


def junit_cases(path: Path) -> dict[str, ET.Element]:
    root = ET.parse(path).getroot()
    return {tc.get("name"): tc for tc in root.iter("testcase")}


def test_one_test_per_ok_unit_with_area_unit_dataset_db_ids(target):
    proc = run_pytest(target, *cell("--collect-only", "-q"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert ids(proc) == [
        "test_unit[orders::qryAlpha::seed-1::stub]",
        "test_unit[orders::qryGamma::seed-1::stub]",
        "test_unit[purchase_orders::qryBeta::seed-1::stub]",
    ]
    # seed-2: qryBeta is empty and qryGamma needs params, so only qryAlpha is selected
    proc = run_pytest(target, *cell("--collect-only", "-q", dataset="seed-2"))
    assert ids(proc) == ["test_unit[orders::qryAlpha::seed-2::stub]"]


def test_the_harness_own_tests_are_ignored_during_cell_runs(target):
    proc = run_pytest(target, *cell("--collect-only", "-q"))
    assert "test_canonical" not in proc.stdout
    assert "test_dataset" not in proc.stdout
    assert "test_plugin" not in proc.stdout


def test_k_area_bracket_selects_one_area_exactly(target):
    proc = run_pytest(target, *cell("--collect-only", "-q", "-k", "[orders::"))
    assert ids(proc) == [
        "test_unit[orders::qryAlpha::seed-1::stub]",
        "test_unit[orders::qryGamma::seed-1::stub]",
    ]
    # a bare area word also matches the area that ends with it
    proc = run_pytest(target, *cell("--collect-only", "-q", "-k", "orders"))
    assert len(ids(proc)) == 3
    proc = run_pytest(target, *cell("--collect-only", "-q", "-k", "[purchase_orders::"))
    assert ids(proc) == ["test_unit[purchase_orders::qryBeta::seed-1::stub]"]


def test_all_pass_and_junit_lists_every_unit(target, tmp_path):
    junit = tmp_path / "junit.xml"
    proc = run_pytest(target, *cell("--junitxml", str(junit)))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    cases = junit_cases(junit)
    assert len(cases) == 3
    assert all(len(tc) == 0 or tc[0].tag == "properties" for tc in cases.values())


def test_a_seed_mismatch_fails_with_counts_and_writes_obtained_only(target, tmp_path):
    stub = good_stub()
    stub["rows"]["seed-1"]["qryAlpha"] = [[1, "red widget"], [2, "green widget"]]
    set_stub(target, stub)
    expected = target / "goldens/synthetic/seed-1/expected/qryAlpha.csv"
    before = expected.read_bytes()
    obtained = tmp_path / "obtained"
    junit = tmp_path / "junit.xml"
    proc = run_pytest(
        target,
        *cell("--junitxml", str(junit)),
        env={"EQUIVALENCE_OBTAINED": str(obtained)},
    )
    assert proc.returncode == 1
    tc = junit_cases(junit)["test_unit[orders::qryAlpha::seed-1::stub]"]
    failure = tc.find("failure")
    assert failure is not None
    assert "1 rows differ in columns [Label]" in failure.get("message")
    props = {p.get("name"): p.get("value") for p in tc.iter("property")}
    assert json.loads(props["equivalence.mismatch"]) == {
        "columns": {"Label": 1},
        "rows": 1,
    }
    assert expected.read_bytes() == before
    assert (obtained / "qryAlpha.obtained.csv").is_file()
    assert not (obtained / "qryBeta.obtained.csv").exists()


def test_obtained_files_go_to_tmp_path_when_unset(target, tmp_path):
    stub = good_stub()
    stub["rows"]["seed-1"]["qryAlpha"] = [[1, "red widget"]]
    set_stub(target, stub)
    proc = run_pytest(target, *cell(f"--basetemp={tmp_path / 'bt'}"))
    assert proc.returncode == 1
    found = list((tmp_path / "bt").rglob("qryAlpha.obtained.csv"))
    assert len(found) == 1
    assert not list(target.rglob("*.obtained.csv"))


def test_a_missing_expected_file_is_an_error_never_created(target, tmp_path):
    expected = target / "goldens/synthetic/seed-1/expected/qryBeta.csv"
    expected.unlink()
    junit = tmp_path / "junit.xml"
    proc = run_pytest(target, *cell("--junitxml", str(junit)))
    assert proc.returncode == 1
    tc = junit_cases(junit)["test_unit[purchase_orders::qryBeta::seed-1::stub]"]
    assert tc.find("error") is not None
    assert not expected.exists()


def test_not_implemented_is_recorded_and_fails_without_the_option(target, tmp_path):
    stub = good_stub()
    stub["not_implemented"] = ["qryGamma"]
    set_stub(target, stub)
    junit = tmp_path / "junit.xml"
    proc = run_pytest(target, *cell("--junitxml", str(junit)))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    skipped = junit_cases(junit)["test_unit[orders::qryGamma::seed-1::stub]"].find(
        "skipped"
    )
    assert skipped.get("type") == "equivalence.not-implemented"
    assert skipped.get("message") == "not-implemented: qryGamma"
    proc = run_pytest(
        target, *cell("--junitxml", str(junit), "--eq-allow-not-implemented")
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    skipped = junit_cases(junit)["test_unit[orders::qryGamma::seed-1::stub]"].find(
        "skipped"
    )
    assert skipped.get("type") == "equivalence.not-implemented"


def test_a_plain_not_implemented_error_counts_too(target, tmp_path):
    stub = good_stub()
    stub["raise"] = {}
    set_stub(target, stub)
    (target / "stub_adapter.py").write_text(
        (target / "stub_adapter.py")
        .read_text(encoding="utf-8")
        .replace(
            "raise NotImplementedUnit(unit_id)", "raise NotImplementedError(unit_id)"
        ),
        encoding="utf-8",
    )
    stub["not_implemented"] = ["qryAlpha"]
    set_stub(target, stub)
    junit = tmp_path / "junit.xml"
    proc = run_pytest(
        target, *cell("--junitxml", str(junit), "--eq-allow-not-implemented")
    )
    assert proc.returncode == 0
    skipped = junit_cases(junit)["test_unit[orders::qryAlpha::seed-1::stub]"].find(
        "skipped"
    )
    assert skipped.get("type") == "equivalence.not-implemented"


@pytest.mark.parametrize("flag", ["--force-regen", "--regen-all"])
def test_force_regen_and_regen_all_are_refused_at_start(target, flag):
    stub = good_stub()
    stub["rows"]["seed-1"]["qryAlpha"] = [[9, "changed"]]
    set_stub(target, stub)
    expected = target / "goldens/synthetic/seed-1/expected/qryAlpha.csv"
    before = expected.read_bytes()
    proc = run_pytest(target, *cell(flag))
    assert proc.returncode == 4
    assert "refused" in proc.stderr
    assert expected.read_bytes() == before


def test_nothing_selected_exits_5(target):
    proc = run_pytest(target, *cell("-k", "zzz_no_such_unit"))
    assert proc.returncode == 5
    proc = run_pytest(target, *cell("--eq-only-areas", "returns"))
    assert proc.returncode == 5


def test_only_areas_selects_and_sets_mp_only_areas(target, tmp_path):
    out = tmp_path / "env.json"
    stub = good_stub()
    stub["env_out"] = str(out)
    set_stub(target, stub)
    proc = run_pytest(target, *cell("--eq-only-areas", "purchase_orders", "-v"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "purchase_orders::qryBeta" in proc.stdout
    assert "qryAlpha" not in proc.stdout
    assert json.loads(out.read_text())["MP_ONLY_AREAS"] == "purchase_orders"
    # without the option an MP_ONLY_AREAS in the environment passes through unchanged
    proc = run_pytest(target, *cell(), env={"MP_ONLY_AREAS": "orders,inventory"})
    assert proc.returncode == 0
    assert json.loads(out.read_text())["MP_ONLY_AREAS"] == "orders,inventory"


def test_only_areas_rejects_bad_ids(target):
    proc = run_pytest(target, *cell("--eq-only-areas", "Orders;rm"))
    assert proc.returncode == 4


DJANGO_TOML = (
    "format_version = 1\n"
    'adapter = "django"\n'
    'project_dir = "../app"\n'
    'python = ["uv", "run", "--project", "../app", "python"]\n'
    'settings = "config.settings.test"\n'
    'entrypoint = "stub_adapter:StubAdapter"\n'
    "\n[databases]\n"
    'postgresql = "PG_URL"\n'
    'mssql = "MSSQL_URL"\n'
)


def test_database_url_fallback_from_adapter_toml(tmp_path):
    out = tmp_path / "env.json"
    stub = good_stub()
    stub["env_out"] = str(out)
    target = make_target(tmp_path / "t", units(), stub=stub, adapter_toml=DJANGO_TOML)
    args = ("--eq-adapter", "adapter.toml", "--eq-dataset", "seed-1")
    proc = run_pytest(
        target,
        *args,
        "--eq-db",
        "postgresql",
        env={"PG_URL": "postgresql://u:p@127.0.0.1:1/"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    seen = json.loads(out.read_text())
    assert seen["DATABASE_URL"] == "postgresql://u:p@127.0.0.1:1/stub_adapter"
    assert seen["DJANGO_SETTINGS_MODULE"] == "config.settings.test"
    # a DATABASE_URL already set (the toolkit always sets one) is kept
    proc = run_pytest(
        target,
        *args,
        "--eq-db",
        "mssql",
        env={
            "DATABASE_URL": "mssql://x@127.0.0.1:2/other",
            "MSSQL_URL": "mssql://y@h:3",
        },
    )
    assert proc.returncode == 0
    assert json.loads(out.read_text())["DATABASE_URL"] == "mssql://x@127.0.0.1:2/other"
    # neither set: a usage error that names the variable
    proc = run_pytest(target, *args, "--eq-db", "mssql")
    assert proc.returncode == 4
    assert "MSSQL_URL" in proc.stderr


def test_engagement_digest_key_from_env_or_data_file(tmp_path):
    private = tmp_path / "private"
    data_file = tmp_path / "widgets.bin"
    data_file.write_bytes(b"synthetic widget shop database bytes")
    import hashlib
    import hmac

    key = hmac.new(
        b"modpipe-golden-v1", data_file.read_bytes(), hashlib.sha256
    ).digest()
    spec = {"qryAlpha": {"area": "orders", "golden": {"engagement": ROWS_A}}}
    stub = {"rows": {"engagement": {"qryAlpha": ROWS_A}}}
    target = make_target(tmp_path / "t", spec, private=private, stub=stub, key=key)
    args = cell(dataset="engagement")
    env = {"EQUIVALENCE_PRIVATE": str(private)}
    proc = run_pytest(
        target, *args, env={**env, "EQUIVALENCE_DATA_FILE": str(data_file)}
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    proc = run_pytest(target, *args, env={**env, "EQUIVALENCE_DIGEST_KEY": key.hex()})
    assert proc.returncode == 0
    proc = run_pytest(target, *args, env={**env, "EQUIVALENCE_DIGEST_KEY": KEY.hex()})
    assert proc.returncode == 1
    proc = run_pytest(target, *args, env=env)
    assert proc.returncode == 4
    assert "EQUIVALENCE_DIGEST_KEY" in proc.stderr


def test_engagement_params_come_from_the_private_params(tmp_path):
    private = tmp_path / "private"
    spec = {
        "qryAlpha": {
            "area": "orders",
            "golden": {"engagement": ROWS_A, "seed-1": ROWS_A},
        }
    }
    stub = {
        "rows": {"engagement": {"qryAlpha": ROWS_A}, "seed-1": {"qryAlpha": ROWS_A}},
        "params": {
            "qryAlpha": {"engagement": {"WidgetID": "5"}, "seed-1": {"WidgetID": "2"}}
        },
    }
    public = {
        "qryAlpha": {
            "params": [{"name": "WidgetID"}],
            "values": {"seed-1": {"WidgetID": "2"}},
            "keys": {},
        }
    }
    target = make_target(
        tmp_path / "t",
        spec,
        private=private,
        stub=stub,
        params=public,
        private_params={
            "qryAlpha": {"values": {"engagement": {"WidgetID": "5"}}, "keys": {}}
        },
    )
    env = {"EQUIVALENCE_PRIVATE": str(private), "EQUIVALENCE_DIGEST_KEY": KEY.hex()}
    assert run_pytest(target, *cell(dataset="engagement"), env=env).returncode == 0
    assert run_pytest(target, *cell(dataset="seed-1")).returncode == 0


def test_a_failing_load_dataset_errors_every_unit(target, tmp_path):
    stub = good_stub()
    stub["fail_load"] = "cannot load"
    set_stub(target, stub)
    junit = tmp_path / "junit.xml"
    proc = run_pytest(target, *cell("--junitxml", str(junit)))
    assert proc.returncode == 1
    assert all(tc.find("error") is not None for tc in junit_cases(junit).values())
    for tc in junit_cases(junit).values():
        props = {p.get("name"): p.get("value") for p in tc.iter("property")}
        assert json.loads(props["equivalence.setup-error"]) == {
            "phase": "load_dataset",
            "exception": "RuntimeError",
            "chain": ["RuntimeError"],
        }


def test_a_load_error_names_its_classes_and_design_names_in_junit(target, tmp_path):
    stub = good_stub()
    stub["fail_load"] = "row 3 of Widgets: ParentID 99 is not present"
    stub["fail_load_names"] = {"table": "Widgets", "column": "ParentID"}
    stub["fail_load_diag"] = {
        "constraint_name": "FK_Widgets_Parent",
        "sqlstate": "23503",
    }
    set_stub(target, stub)
    junit = tmp_path / "junit.xml"
    proc = run_pytest(target, *cell("--junitxml", str(junit)))
    assert proc.returncode == 1
    cases = junit_cases(junit)
    assert len(cases) == 3
    for tc in cases.values():
        assert tc.find("error") is not None
        props = {p.get("name"): p.get("value") for p in tc.iter("property")}
        assert json.loads(props["equivalence.setup-error"]) == {
            "phase": "load_dataset",
            "exception": "WidgetLoadError",
            "chain": ["WidgetLoadError", "WidgetDbError"],
            "table": "Widgets",
            "column": "ParentID",
            "constraint": "FK_Widgets_Parent",
            "sqlstate": "23503",
        }
        assert "99" not in props["equivalence.setup-error"]


def test_an_adapter_that_cannot_be_created_is_phase_adapter(target, tmp_path):
    (target / "adapter.toml").write_text(
        'format_version = 1\nadapter = "stub"\nentrypoint = "stub_adapter:NoSuchAdapter"\n',
        encoding="utf-8",
    )
    junit = tmp_path / "junit.xml"
    proc = run_pytest(target, *cell("--junitxml", str(junit)))
    assert proc.returncode == 1
    for tc in junit_cases(junit).values():
        props = {p.get("name"): p.get("value") for p in tc.iter("property")}
        info = json.loads(props["equivalence.setup-error"])
        assert info["phase"] == "adapter"
        assert info["exception"] == "AttributeError"


def test_without_the_plugin_options_the_runner_is_not_collected(target):
    proc = run_pytest(target, "--collect-only", "-q")
    assert "test_unit[" not in proc.stdout
