"""toolkit equivalence: cells, rates, exit codes and the django dispatch (docs/contracts.md#harness,
#pass-rate). Synthetic fixtures only: a fixture manifest with a stub adapter in place of the
reference adapter (harness/tests/_support.py), so no JVM is needed."""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from harness import canonical as canon
from harness.tests._support import make_target
from toolkit import cli
from toolkit import equivalence as eq
from toolkit.engagement import load

A = [[1, "red widget"], [2, "blue widget"]]
B = [[7, "gear"]]
C = [[3, "bolt"]]


def units(seed2_beta=B) -> dict:
    return {
        "qryAlpha": {"area": "orders", "golden": {"seed-1": A, "seed-2": A}},
        "qryGamma": {"area": "orders", "golden": {"seed-1": C, "seed-2": C}},
        "qryBeta": {
            "area": "purchase_orders",
            "golden": {"seed-1": B, "seed-2": seed2_beta},
        },
    }


def stub(over: dict | None = None) -> dict:
    rows = {
        "seed-1": {"qryAlpha": A, "qryGamma": C, "qryBeta": B},
        "seed-2": {"qryAlpha": A, "qryGamma": C, "qryBeta": B},
    }
    for (ds, uid), value in (over or {}).items():
        rows[ds][uid] = value
    return {"rows": rows}


@pytest.fixture
def eng(tmp_path, make_engagement, monkeypatch):
    path = make_engagement(tmp_path, id="widget-demo", target={"root": "widgets"})
    for var in (
        "MP_SANDBOX",
        "MP_ONLY_AREAS",
        "MP_TEST_DB_PREFIX",
        "MP_DATA_PG_URL",
        "MP_DATA_MSSQL_URL",
        "EQUIVALENCE_PRIVATE",
        "EQUIVALENCE_OBTAINED",
        "EQUIVALENCE_REDACT",
        "EQUIVALENCE_DIGEST_KEY",
        "PYTEST_ADDOPTS",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MP_QUIET", "1")
    monkeypatch.setattr(eq, "REFERENCE_ENTRYPOINT", "stub_adapter:StubAdapter")
    return path


def paths(eng_path) -> tuple[Path, Path]:
    e = load(eng_path)
    return Path(e["paths"]["target"]), Path(e["paths"]["private"])


def build(eng_path, spec=None, stub_cfg=None, **kw) -> Path:
    target, private = paths(eng_path)
    return make_target(
        target, spec or units(), private=private, stub=stub_cfg or stub(), **kw
    )


def run_cli(capfd, eng_path, *argv) -> tuple[int, dict, str]:
    capfd.readouterr()
    rc = cli.main(["equivalence", str(eng_path), *argv])
    out, err = capfd.readouterr()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1, out
    return rc, json.loads(lines[0]), err


def cells(summary) -> dict:
    return {(c["dataset"], c["db"]): c for c in summary["cells"]}


# ------------------------------------------------------------------ runner and rates


def test_one_cell_per_dataset_with_rates_and_matrix(eng, capfd, tmp_path):
    build(eng, stub_cfg=stub({("seed-2", "qryBeta"): [[7, "sprocket"]]}))
    junit = tmp_path / "out" / "junit"
    matrix = tmp_path / "out" / "m" / "matrix.json"
    rc, s, _ = run_cli(
        capfd,
        eng,
        "--adapter",
        "reference",
        "--datasets",
        "synthetic",
        "--min-pass-per-cell",
        "1.0",
        "--junit-dir",
        str(junit),
        "--matrix",
        str(matrix),
    )
    assert rc == 1
    assert s["status"] == "failed"
    c = cells(s)
    assert c[("seed-1", "reference")]["rate"] == 1.0
    assert c[("seed-2", "reference")] == {
        "dataset": "seed-2",
        "db": "reference",
        "status": "ok",
        "passed": 2,
        "failed": 1,
        "errors": 0,
        "skipped": 0,
        "not_implemented": 0,
        "total": 3,
        "rate": 0.6667,
    }
    assert s["rates"] == {
        "min_cell": 0.6667,
        "pooled": 0.8333,
        "coverage_adjusted": 0.8333,
    }
    assert s["counts"]["kept_units"] == 3
    assert s["units"] == [
        {
            "id": "qryBeta",
            "status": "failed",
            "reason": None,
            "dataset": "seed-2",
            "db": "reference",
        }
    ]
    assert "mismatches" not in s or s["mismatches"] == []
    assert sorted(p.name for p in junit.iterdir()) == [
        "junit-equivalence-seed-1-reference.xml",
        "junit-equivalence-seed-2-reference.xml",
    ]
    m = json.loads(matrix.read_text(encoding="utf-8"))
    assert m["format_version"] == 1
    assert [x["dataset"] for x in m["cells"]] == ["seed-1", "seed-2"]
    assert m["cells"][1]["rate"] == 0.6667
    # a lower threshold passes; without one any failed outcome fails the run
    rc, s, _ = run_cli(
        capfd,
        eng,
        "--adapter",
        "reference",
        "--datasets",
        "synthetic",
        "--min-pass-per-cell",
        "0.6",
    )
    assert rc == 0 and s["status"] == "ok"
    rc, s, _ = run_cli(capfd, eng, "--adapter", "reference", "--datasets", "synthetic")
    assert rc == 1


def test_k_selects_one_area_and_the_denominator_is_per_cell(eng, capfd):
    build(eng, stub_cfg=stub({("seed-2", "qryAlpha"): [[1, "red widget"]]}))
    rc, s, _ = run_cli(
        capfd,
        eng,
        "--adapter",
        "reference",
        "--datasets",
        "synthetic",
        "-k",
        "[orders::",
        "--min-pass-per-cell",
        "0.5",
    )
    assert rc == 0
    c = cells(s)
    assert c[("seed-1", "reference")]["total"] == 2  # purchase_orders is not selected
    assert c[("seed-1", "reference")]["rate"] == 1.0
    assert c[("seed-2", "reference")]["total"] == 2
    assert c[("seed-2", "reference")]["rate"] == 0.5
    rc, s, _ = run_cli(
        capfd,
        eng,
        "--adapter",
        "reference",
        "--datasets",
        "seed-1",
        "-k",
        "[purchase_orders::",
    )
    assert rc == 0
    assert cells(s)[("seed-1", "reference")]["total"] == 1


def test_an_empty_cell_beside_a_full_one_does_not_fail(eng, capfd, tmp_path):
    spec = {
        "qryAlpha": {"area": "orders", "golden": {"seed-1": A, "seed-2": None}},
        "qryBeta": {
            "area": "purchase_orders",
            "golden": {"seed-1": B, "seed-2": "needs-params"},
        },
    }
    build(eng, spec=spec)
    junit = tmp_path / "j"
    rc, s, _ = run_cli(
        capfd,
        eng,
        "--adapter",
        "reference",
        "--datasets",
        "synthetic",
        "--min-pass-per-cell",
        "1.0",
        "--junit-dir",
        str(junit),
    )
    assert rc == 0, s
    c = cells(s)
    assert c[("seed-2", "reference")]["status"] == "empty"
    assert c[("seed-2", "reference")]["total"] == 0
    assert c[("seed-2", "reference")]["rate"] is None
    assert s["rates"]["min_cell"] == 1.0
    assert (junit / "junit-equivalence-seed-2-reference.xml").is_file()


def test_every_cell_empty_exits_5_or_0_with_allow_empty(eng, capfd, tmp_path):
    build(eng)
    junit = tmp_path / "j"
    rc, s, _ = run_cli(
        capfd,
        eng,
        "--adapter",
        "reference",
        "--datasets",
        "synthetic",
        "-k",
        "zzz_no_such_unit",
        "--junit-dir",
        str(junit),
    )
    assert rc == 5
    assert s["status"] == "failed"
    assert all(c["status"] == "empty" and c["rate"] is None for c in s["cells"])
    assert len(list(junit.iterdir())) == 2
    rc, s, _ = run_cli(
        capfd,
        eng,
        "--adapter",
        "reference",
        "--datasets",
        "synthetic",
        "-k",
        "zzz_no_such_unit",
        "--allow-empty",
    )
    assert rc == 0
    assert s["status"] == "no-data"


def test_not_implemented_counts_as_not_passed_and_fails_without_the_flag(
    eng, capfd, tmp_path
):
    cfg = stub()
    cfg["not_implemented"] = ["qryGamma"]
    build(eng, stub_cfg=cfg)
    junit = tmp_path / "j"
    rc, s, _ = run_cli(
        capfd,
        eng,
        "--adapter",
        "reference",
        "--datasets",
        "seed-1",
        "--junit-dir",
        str(junit),
    )
    assert rc == 1
    assert s["units"] == [
        {
            "id": "qryGamma",
            "status": "not-implemented",
            "reason": None,
            "dataset": "seed-1",
            "db": "reference",
        }
    ]
    xml = ET.parse(junit / "junit-equivalence-seed-1-reference.xml")
    sk = [e for e in xml.iter("skipped")]
    assert [e.get("type") for e in sk] == ["equivalence.not-implemented"]
    assert sk[0].get("message") == "not-implemented: qryGamma"
    rc, s, _ = run_cli(
        capfd,
        eng,
        "--adapter",
        "reference",
        "--datasets",
        "seed-1",
        "--allow-not-implemented",
    )
    assert rc == 0
    assert cells(s)[("seed-1", "reference")]["not_implemented"] == 1
    assert cells(s)[("seed-1", "reference")]["rate"] == 0.6667
    rc, s, _ = run_cli(
        capfd,
        eng,
        "--adapter",
        "reference",
        "--datasets",
        "seed-1",
        "--allow-not-implemented",
        "--min-pass-per-cell",
        "1.0",
    )
    assert rc == 1


def test_only_areas_reaches_the_plugin_and_mp_only_areas(eng, capfd, tmp_path):
    out = tmp_path / "env.json"
    cfg = stub()
    cfg["env_out"] = str(out)
    build(eng, stub_cfg=cfg)
    rc, s, _ = run_cli(
        capfd,
        eng,
        "--adapter",
        "reference",
        "--datasets",
        "seed-1",
        "--only-areas",
        "purchase_orders",
    )
    assert rc == 0
    assert cells(s)[("seed-1", "reference")]["total"] == 1
    assert json.loads(out.read_text())["MP_ONLY_AREAS"] == "purchase_orders"
    rc, s, _ = run_cli(
        capfd,
        eng,
        "--adapter",
        "reference",
        "--datasets",
        "seed-1",
        "--only-areas",
        "Bad-Area",
    )
    assert rc == 2


def test_collect_only_lists_ids_and_runs_nothing(eng, capfd, monkeypatch):
    cfg = stub()
    cfg["fail_load"] = "would fail if anything ran"
    build(eng, stub_cfg=cfg)
    monkeypatch.delenv("MP_QUIET")
    capfd.readouterr()
    rc = cli.main(
        [
            "equivalence",
            str(eng),
            "--adapter",
            "reference",
            "--datasets",
            "seed-1",
            "--collect-only",
        ]
    )
    out, _ = capfd.readouterr()
    assert rc == 0
    assert "test_unit[orders::qryAlpha::seed-1::reference]" in out
    assert "test_unit[purchase_orders::qryBeta::seed-1::reference]" in out


# ------------------------------------------------------------------ outputs


def tree(root: Path) -> set[str]:
    return {
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and "logs" not in p.relative_to(root).parts
    }


def test_without_junit_dir_and_matrix_nothing_is_written(eng, capfd, tmp_path):
    build(eng, stub_cfg=stub({("seed-1", "qryAlpha"): [[1, "other"]]}))
    before = tree(tmp_path)
    rc, _s, _ = run_cli(capfd, eng, "--adapter", "reference", "--datasets", "synthetic")
    assert rc == 1
    assert tree(tmp_path) == before


def test_obtained_files_under_synthetic_only_for_synthetic_junit_dirs(eng, capfd):
    build(eng, stub_cfg=stub({("seed-2", "qryBeta"): [[7, "sprocket"]]}))
    _target, private = paths(eng)
    obtained = private / "synthetic" / "results" / "obtained"
    rc, _s, _ = run_cli(
        capfd,
        eng,
        "--adapter",
        "reference",
        "--datasets",
        "synthetic",
        "--junit-dir",
        str(private / "review" / "x"),
    )
    assert rc == 1
    assert not obtained.exists()
    rc, _s, _ = run_cli(
        capfd,
        eng,
        "--adapter",
        "reference",
        "--datasets",
        "synthetic",
        "--junit-dir",
        str(private / "synthetic" / "results" / "S2"),
    )
    assert rc == 1
    assert (obtained / "seed-2-reference" / "qryBeta.obtained.csv").is_file()
    assert sorted(p.name for p in obtained.iterdir()) == [
        "seed-1-reference",
        "seed-2-reference",
    ]


def test_obtained_dir_rule(tmp_path):
    private = tmp_path / "private"
    work = tmp_path / "work"
    syn = private / "synthetic" / "results" / "S2"
    assert eq.obtained_dir(private, syn, work, "seed-1", "reference", {}) == (
        private / "synthetic" / "results" / "obtained" / "seed-1-reference"
    )
    assert (
        eq.obtained_dir(
            private, syn, work, "seed-1", "reference", {"MP_SANDBOX": "mp-data"}
        )
        == work / "obtained" / "seed-1-reference"
    )
    assert (
        eq.obtained_dir(
            private, private / "results" / "S2", work, "seed-1", "reference", {}
        )
        == work / "obtained" / "seed-1-reference"
    )
    assert eq.obtained_dir(private, None, work, "seed-1", "reference", {}) == (
        work / "obtained" / "seed-1-reference"
    )


# ------------------------------------------------------------------ usage errors


@pytest.mark.parametrize(
    "argv,needle",
    [
        (
            [
                "--adapter",
                "reference",
                "--datasets",
                "synthetic",
                "--dbs",
                "postgresql",
            ],
            "--dbs",
        ),
        (["--adapter", "django", "--datasets", "synthetic"], "--dbs"),
        (["--adapter", "reference", "--datasets", "seed-9"], "seed-9"),
        (
            [
                "--adapter",
                "reference",
                "--datasets",
                "synthetic",
                "--min-pass-per-cell",
                "2",
            ],
            "rate",
        ),
        (["--adapter", "reference", "--datasets", "engagement"], "run through mp-data"),
    ],
)
def test_usage_errors_exit_2(eng, capfd, argv, needle):
    spec = units()
    spec["qryAlpha"]["golden"]["engagement"] = A
    target, private = paths(eng)
    make_target(target, spec, private=private, stub=stub())
    rc, s, _ = run_cli(capfd, eng, *argv)
    assert rc == 2
    assert s["status"] == "error"
    assert any(needle in m for m in s["messages"])


def test_datasets_is_required(eng, capfd):
    build(eng)
    rc, _s, _ = run_cli(capfd, eng, "--adapter", "reference")
    assert rc == 2


def test_missing_manifest_or_harness_exits_2(eng, capfd):
    target, _private = paths(eng)
    target.mkdir(parents=True, exist_ok=True)
    rc, s, _ = run_cli(capfd, eng, "--adapter", "reference", "--datasets", "synthetic")
    assert rc == 2
    assert any("manifest.json" in m for m in s["messages"])


LOAD_FAILURE = (
    "load_dataset failed: WidgetLoadError <- WidgetDbError "
    "(table Widgets, column ParentID, constraint FK_Widgets_Parent, sqlstate 23503)"
)


def failing_load_stub(cfg: dict) -> dict:
    cfg["fail_load"] = "Key (ParentID)=(CANARY-80) is not present in Widgets"
    cfg["fail_load_names"] = {"table": "Widgets", "column": "ParentID"}
    cfg["fail_load_diag"] = {
        "constraint_name": "FK_Widgets_Parent",
        "sqlstate": "23503",
    }
    return cfg


def test_a_failed_dataset_load_is_named_per_cell_without_values(eng, capfd, tmp_path):
    build(eng, stub_cfg=failing_load_stub(stub()))
    rc, s, _err = run_cli(
        capfd,
        eng,
        "--adapter",
        "reference",
        "--datasets",
        "synthetic",
        "--min-pass-per-cell",
        "0.9",
        "--matrix",
        str(tmp_path / "matrix.json"),
    )
    assert rc == 1
    line = json.dumps(s)
    assert "CANARY-" not in line
    assert all(c["errors"] == 3 and c["rate"] == 0.0 for c in s["cells"])
    assert {(u["dataset"], u["id"], u["status"], u["reason"]) for u in s["units"]} == {
        (ds, uid, "error", LOAD_FAILURE)
        for ds in ("seed-1", "seed-2")
        for uid in ("qryAlpha", "qryBeta", "qryGamma")
    }
    # one message per cell, before the rate messages
    assert s["messages"][:2] == [
        f"cell seed-1/reference: {LOAD_FAILURE}",
        f"cell seed-2/reference: {LOAD_FAILURE}",
    ]
    assert "CANARY-" not in (tmp_path / "matrix.json").read_text(encoding="utf-8")


def test_an_engagement_load_failure_is_named_and_redacted(eng, capfd, monkeypatch):
    target, private = paths(eng)
    source = private / "sources" / "app.accdb"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"synthetic widget database")
    key = canon.key_from_file(source)
    spec = {"qryAlpha": {"area": "orders", "golden": {"engagement": A, "seed-1": A}}}
    cfg = failing_load_stub({"rows": {"engagement": {"qryAlpha": A}}})
    make_target(target, spec, private=private, stub=cfg, key=key)
    monkeypatch.setenv("MP_SANDBOX", "mp-data")
    junit_dir = private / "results" / "S5"
    rc, s, err = run_cli(
        capfd,
        eng,
        "--adapter",
        "reference",
        "--datasets",
        "engagement",
        "--junit-dir",
        str(junit_dir),
    )
    assert rc == 1
    assert "CANARY-" not in json.dumps(s) + err
    assert "CANARY-" not in (
        junit_dir / "junit-equivalence-engagement-reference.xml"
    ).read_text(encoding="utf-8")
    assert s["units"] == [
        {
            "id": "qryAlpha",
            "status": "error",
            "reason": LOAD_FAILURE,
            "dataset": "engagement",
            "db": "reference",
        }
    ]
    assert s["messages"][0] == f"cell engagement/reference: {LOAD_FAILURE}"


def test_setup_reason_rechecks_the_junit_property():
    tc = ET.fromstring(
        "<testcase><properties><property name='equivalence.setup-error' value='"
        + json.dumps(
            {
                "phase": "load_dataset",
                "exception": "LoadError",
                "chain": ["LoadError", "CANARY 81"],
                "table": "CANARY-82\n",
                "column": "ParentID",
                "message": "CANARY-83",
            }
        ).replace("'", "&apos;")
        + "'/></properties><error/></testcase>"
    )
    assert eq.setup_reason(tc) == "load_dataset failed: LoadError (column ParentID)"
    assert eq.setup_reason(ET.fromstring("<testcase><error/></testcase>")) is None


def test_a_cell_crash_counts_as_2(eng, capfd):
    build(eng)
    target, _ = paths(eng)
    (target / "adapter.toml").unlink()
    rc, _s, _ = run_cli(capfd, eng, "--adapter", "reference", "--datasets", "seed-1")
    # the toolkit writes its own temp adapter.toml for the reference adapter
    assert rc == 0
    # a broken conftest makes pytest exit 4 (usage error), which counts as 2
    (target / "conftest.py").write_text(
        "raise RuntimeError('broken')\n", encoding="utf-8"
    )
    rc, _s, _ = run_cli(capfd, eng, "--adapter", "reference", "--datasets", "seed-1")
    assert rc == 2


# ------------------------------------------------------------------ engagement (data plane)


def test_engagement_reference_cell_redacts_and_counts_mismatches(
    eng, capfd, monkeypatch
):
    target, private = paths(eng)
    source = private / "sources" / "app.accdb"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"synthetic widget database")
    key = canon.key_from_file(source)
    spec = {
        "qryAlpha": {"area": "orders", "golden": {"engagement": A, "seed-1": A}},
        "qryBeta": {
            "area": "purchase_orders",
            "golden": {"engagement": B, "seed-1": B},
        },
    }
    cfg = {
        "rows": {
            "engagement": {"qryAlpha": A, "qryBeta": [[7, "CANARY-9"]]},
            "seed-1": {"qryAlpha": A, "qryBeta": B},
        }
    }
    make_target(target, spec, private=private, stub=cfg, key=key)
    monkeypatch.setenv("MP_SANDBOX", "mp-data")
    rc, s, err = run_cli(
        capfd,
        eng,
        "--adapter",
        "reference",
        "--datasets",
        "all",
        "--junit-dir",
        str(private / "results" / "S2"),
    )
    assert rc == 1
    assert cells(s)[("engagement", "reference")]["passed"] == 1
    assert cells(s)[("seed-1", "reference")]["passed"] == 2
    assert s["mismatches"] == [
        {
            "unit": "qryBeta",
            "dataset": "engagement",
            "db": "reference",
            "columns": {"Label": 1},
        }
    ]
    assert "CANARY-" not in err
    assert (
        "CANARY-"
        not in (
            private / "results" / "S2" / "junit-equivalence-engagement-reference.xml"
        ).read_text()
    )


# ------------------------------------------------------------------ django dispatch


ADAPTER_TOML = """format_version = 1
adapter = "django"
project_dir = "../app"
python = ["uv", "run", "--project", "../app", "python"]
settings = "config.settings.test"
entrypoint = "widgets.core.equivalence:DjangoAdapter"

[databases]
postgresql = "PG_URL"
mssql = "MSSQL_URL"
"""


@pytest.fixture
def django_target(eng):
    target, private = paths(eng)
    make_target(target, units(), private=private, stub=stub())
    (target / "equivalence" / "adapter.toml").write_text(ADAPTER_TOML, encoding="utf-8")
    return target


def one_cell(eng_path, environ, dataset="seed-1", db="postgresql", **opts):
    e = load(eng_path)
    toml = Path(e["paths"]["target"]) / "equivalence" / "adapter.toml"
    config = eq.read_adapter_toml(toml)
    o = eq.Options(adapter="django", datasets=dataset, dbs=db, **opts)
    areas = eq.parse_areas(o.only_areas)
    junit = Path("/j/junit-equivalence-x.xml")
    obtained = Path("/o")
    return e, eq.django_cell(
        e,
        toml,
        config,
        dataset,
        db,
        junit,
        obtained,
        o,
        areas,
        environ,
        b"\x01\x02" if dataset == "engagement" else None,
    )


def test_django_cell_argv_cwd_and_environment(eng, django_target):
    environ = {
        "PG_URL": "postgresql://agent:pw@127.0.0.1:55432",
        "MP_DB_SUFFIX": "s9",
        "MSSQL_URL": "mssql://agent:pw@127.0.0.1:14330",
        "MP_ONLY_AREAS": "inventory",
        "PATH": "/usr/bin",
    }
    e, cell = one_cell(eng, environ, keyword="[orders::")
    assert cell.cwd == django_target / "app"
    assert cell.argv == [
        "uv",
        "run",
        "--project",
        "../app",
        "python",
        "-m",
        "pytest",
        "-c",
        "pyproject.toml",
        "--rootdir",
        ".",
        "../equivalence/tests",
        "-p",
        "equivalence.plugin",
        "--create-db",
        "--eq-adapter",
        "../equivalence/adapter.toml",
        "--eq-dataset",
        "seed-1",
        "--eq-db",
        "postgresql",
        "--junitxml",
        "/j/junit-equivalence-x.xml",
        "-p",
        "no:cacheprovider",
        "-k",
        "[orders::",
    ]
    env = cell.env
    assert env["DJANGO_SETTINGS_MODULE"] == "config.settings.test"
    assert (
        env["DATABASE_URL"] == "postgresql://agent:pw@127.0.0.1:55432/" + e["db_name"]
    )
    assert env["MP_ONLY_AREAS"] == "inventory"  # passed through unchanged
    assert env["MP_DB_SUFFIX"] == "s9"
    assert env["EQUIVALENCE_PRIVATE"] == e["paths"]["private"]
    assert env["EQUIVALENCE_OBTAINED"] == "/o"
    assert "MP_TEST_DB_PREFIX" not in env
    assert "EQUIVALENCE_REDACT" not in env and "EQUIVALENCE_DIGEST_KEY" not in env
    # --only-areas sets MP_ONLY_AREAS and the plugin option
    _e, cell = one_cell(
        eng,
        environ,
        db="mssql",
        only_areas="orders,purchase_orders",
        allow_not_implemented=True,
    )
    assert cell.env["MP_ONLY_AREAS"] == "orders,purchase_orders"
    assert cell.argv[-3:] == [
        "--eq-only-areas",
        "orders,purchase_orders",
        "--eq-allow-not-implemented",
    ]
    with pytest.raises(eq.EquivalenceError, match="MSSQL_URL"):
        one_cell(eng, {"PG_URL": "postgresql://a@h:1"}, db="mssql")


def test_django_data_plane_cell_uses_the_app_venv_and_mp_data_urls(eng, django_target):
    environ = {
        "MP_SANDBOX": "mp-data",
        "MP_TEST_DB_PREFIX": "mpdata_test_",
        "MP_DATA_PG_URL": "postgresql://runner:x@127.0.0.1:55432",
        "PG_URL": "postgresql://runner:x@127.0.0.1:55432",
        "MP_DB_SUFFIX": "s9",
    }
    with pytest.raises(eq.EquivalenceError, match="uv sync --locked"):
        one_cell(eng, environ, dataset="engagement")
    python = django_target / "app" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    e, cell = one_cell(eng, environ, dataset="engagement")
    assert cell.argv[0] == str(python)
    assert cell.argv[1:4] == ["-m", "pytest", "-c"]
    assert (
        cell.env["DATABASE_URL"]
        == "postgresql://runner:x@127.0.0.1:55432/" + e["db_name"]
    )
    assert cell.env["MP_TEST_DB_PREFIX"] == "mpdata_test_"
    assert cell.env["EQUIVALENCE_REDACT"] == "1"
    assert cell.env["EQUIVALENCE_DIGEST_KEY"] == "0102"
    assert "--eq-dataset" in cell.argv and "engagement" in cell.argv


@pytest.mark.parametrize("drop", ["MP_TEST_DB_PREFIX", "MP_DATA_PG_URL"])
def test_django_engagement_cell_without_the_data_plane_is_refused(
    eng, django_target, drop
):
    environ = {
        "MP_TEST_DB_PREFIX": "mpdata_test_",
        "MP_DATA_PG_URL": "postgresql://runner:x@127.0.0.1:55432",
        "PG_URL": "postgresql://agent:pw@127.0.0.1:55432",
    }
    del environ[drop]
    with pytest.raises(eq.EquivalenceError, match="run through mp-data"):
        one_cell(eng, environ, dataset="engagement")


def test_django_cli_needs_dbs_and_validates_them(eng, django_target, capfd):
    rc, _s, _ = run_cli(
        capfd, eng, "--adapter", "django", "--datasets", "seed-1", "--dbs", "oracle"
    )
    assert rc == 2
    assert eq.resolve_dbs("django", "from-engagement", load(eng)) == [
        "postgresql",
        "mssql",
    ]


def test_reference_cell_exec_line(eng, tmp_path):
    e = load(eng)
    o = eq.Options(adapter="reference", datasets="seed-1", keyword="x")
    cell = eq.reference_cell(
        e,
        tmp_path / "a.toml",
        "seed-1",
        tmp_path / "j.xml",
        tmp_path / "o",
        o,
        None,
        dict(os.environ),
        None,
    )
    assert cell.cwd == Path(e["paths"]["target"])
    assert cell.argv[1:] == [
        "-m",
        "pytest",
        "--rootdir",
        ".",
        "equivalence/tests",
        "-p",
        "equivalence.plugin",
        "--eq-adapter",
        str(tmp_path / "a.toml"),
        "--eq-dataset",
        "seed-1",
        "--eq-db",
        "reference",
        "--junitxml",
        str(tmp_path / "j.xml"),
        "-p",
        "no:cacheprovider",
        "-k",
        "x",
    ]
