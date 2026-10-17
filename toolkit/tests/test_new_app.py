"""toolkit new-app (docs/contracts.md#feature-app-contract): template answers, the template pin,
--check, the copy into --out, --only core, the pre-commit hook merge and the CI workflow.

Synthetic fixtures only (toolkit/tests/fixtures/new_app/). The cookiecutter run itself is replaced
by a fake generator, so these tests need no network and no template checkout; the real
generation is proven by the S4.template acceptance on the engagement.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from toolkit import cli
from toolkit.commands import new_app
from toolkit.engagement import load, pipeline_json

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "new_app"
S0_CONFIG = FIXTURES / "s0-pre-commit-config.yaml"
FORBIDDEN_CI_VARS = {
    "E2E_SCREENSHOT_DIR",
    "E2E_VIDEO_DIR",
    "MP_ONLY_AREAS",
    "MP_DB_SUFFIX",
    "MP_TEST_DB_PREFIX",
}
SLUG = "demoapp"


# ---------------------------------------------------------------- helpers


@pytest.fixture
def eng_path(make_engagement, tmp_path, monkeypatch):
    monkeypatch.setenv("MP_QUIET", "1")
    monkeypatch.setenv("PG_URL", "postgresql://agent:agent@127.0.0.1:55432")
    path = make_engagement(tmp_path, target={"root": "demo"})
    repo = tmp_path / "target"
    shutil.copyfile(S0_CONFIG, repo / ".pre-commit-config.yaml")
    return path


def out_dir(tmp_path: Path) -> Path:
    return tmp_path / "target" / "demo" / "app"


def run_cli(capfd, *argv) -> tuple[int, dict]:
    capfd.readouterr()
    rc = cli.main(["new-app", *argv])
    out, _err = capfd.readouterr()
    return rc, json.loads(out.strip().splitlines()[-1])


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def fake_generate(ctx, eng, template, packages, checkout, workdir):
    """Stand-in for the cookiecutter run: a small app with every pin, a venv and caches."""
    app = workdir / "app"
    (app / "config" / "settings").mkdir(parents=True)
    (app / "manage.py").write_text("print('manage')\n", encoding="utf-8")
    (app / "config" / "settings" / "base.py").write_text(
        f"PROJECT = {ctx['slug']!r}\n", encoding="utf-8"
    )
    pkg = app / ctx["slug"] / "users"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__" / "models.cpython-313.pyc").write_bytes(b"\0compiled")
    (app / ".venv" / "bin").mkdir(parents=True)
    (app / ".venv" / "bin" / "python").write_text(
        f"#!{workdir}/python\n", encoding="utf-8"
    )
    main, dev = new_app.app_pins(packages)
    deps = ",\n".join(f'  "{p}"' for p in main)
    devs = ",\n".join(f'  "{p}"' for p in dev)
    (app / "pyproject.toml").write_text(
        f'[project]\nname = "x"\ndependencies = [\n{deps},\n]\n\n'
        f"[dependency-groups]\ndev = [\n{devs},\n]\n",
        encoding="utf-8",
    )
    lock = []
    for pin in main + dev:
        name, version = new_app.split_pin(pin)
        lock.append(f'[[package]]\nname = "{name}"\nversion = "{version}"\n')
    (app / "uv.lock").write_text("version = 1\n\n" + "\n".join(lock), encoding="utf-8")
    (app / ".gitignore").write_text(".env\nbackups/\n", encoding="utf-8")
    files = new_app.manifest_files(app)
    (app / new_app.MANIFEST).write_text(
        new_app.manifest_json(files, template["commit"]), encoding="utf-8"
    )
    return {"app": app, "djlint_rev": "v1.36.4"}


@pytest.fixture
def fake_generation(monkeypatch, tmp_path):
    monkeypatch.setattr(new_app, "generate", fake_generate)
    monkeypatch.setattr(new_app, "ensure_template", lambda template: tmp_path)


# ---------------------------------------------------------------- answers


def test_answers_fill_the_app_block_and_map_hosting_to_cloud_provider(
    make_engagement, tmp_path
):
    eng = load(make_engagement(tmp_path))
    template = pipeline_json()["stack"]["app_template"]
    answers = new_app.template_answers(template, eng)
    assert answers["project_name"] == eng["app"]["project_name"]
    assert answers["project_slug"] == eng["app"]["slug"]
    assert answers["timezone"] == eng["app"]["timezone"]
    assert answers["description"] == f"Django app for {eng['app']['project_name']}"
    assert answers["cloud_provider"] == "None"
    assert not any("{{" in str(v) for v in answers.values())
    assert set(answers) == {k for k in template["answers"] if not k.startswith("_")}

    azure = copy.deepcopy(eng)
    azure["hosting"]["provider"] = "azure-app-service"
    assert new_app.template_answers(template, azure)["cloud_provider"] == "Azure"
    other = copy.deepcopy(eng)
    other["hosting"]["provider"] = "self-hosted"
    assert new_app.template_answers(template, other)["cloud_provider"] == "None"


def test_unknown_engagement_placeholder_is_a_usage_error(make_engagement, tmp_path):
    eng = load(make_engagement(tmp_path))
    template = {"answers": {"project_name": "{{e.app.no_such_field}}"}}
    with pytest.raises(new_app.NewAppError, match="no_such_field"):
        new_app.template_answers(template, eng)


# ---------------------------------------------------------------- template pin


def test_a_checkout_at_another_commit_exits_2(
    eng_path, tmp_path, monkeypatch, make_git_repo, capfd
):
    cache = tmp_path / "cache"
    template = pipeline_json()["stack"]["app_template"]
    checkout = make_git_repo(cache, f"cookiecutter-django-{template['tag']}")
    assert checkout == new_app.checkout_dir(template, cache)
    monkeypatch.setattr(new_app, "CACHE_ROOT", cache)
    with pytest.raises(new_app.NewAppError, match="pinned"):
        new_app.ensure_template(template)
    rc, summary = run_cli(capfd, str(eng_path), "--out", str(out_dir(tmp_path)))
    assert rc == 2
    assert summary["status"] == "error"
    assert any(template["commit"] in m for m in summary["messages"])
    assert not out_dir(tmp_path).exists()


def test_the_pinned_commit_passes(tmp_path, make_git_repo):
    cache = tmp_path / "cache"
    repo = make_git_repo(cache, "cookiecutter-django-t1")
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    template = {"tag": "t1", "commit": head, "repo": "unused"}
    assert new_app.ensure_template(template, cache) == repo


# ---------------------------------------------------------------- copy into --out


@pytest.mark.usefixtures("fake_generation")
def test_copy_leaves_out_venv_and_pycache_and_writes_manifest_env_ci_and_hooks(
    eng_path, tmp_path, capfd
):
    out = out_dir(tmp_path)
    rc, summary = run_cli(capfd, str(eng_path), "--out", str(out))
    assert rc == 0, summary
    assert (out / "manage.py").is_file()
    assert not (out / ".venv").exists()
    assert not list(out.rglob("__pycache__"))
    manifest = json.loads((out / new_app.MANIFEST).read_text())
    assert manifest["format_version"] == 1
    assert ".env" not in manifest["files"]
    skipped = (".venv/", f"{SLUG}/users/__pycache__")
    assert not any(p.startswith(skipped) for p in manifest["files"])
    for rel, digest in manifest["files"].items():
        assert hashlib.sha256((out / rel).read_bytes()).hexdigest() == digest
    assert (out / ".env").read_text() == (
        "DATABASE_URL=postgresql://agent:agent@127.0.0.1:55432/demo_eng\n"
    )
    repo = tmp_path / "target"
    assert (repo / ".github" / "workflows" / f"{SLUG}.yml").is_file()
    config = (repo / ".pre-commit-config.yaml").read_text()
    assert new_app.missing_app_hooks(config, "demo") == []

    rc, summary = run_cli(capfd, str(eng_path), "--out", str(out), "--check")
    assert rc == 0, summary


# ---------------------------------------------------------------- --check


@pytest.mark.usefixtures("fake_generation")
def test_check_flags_a_changed_manifest_file_ignores_unlisted_and_never_writes(
    eng_path, tmp_path, capfd
):
    out = out_dir(tmp_path)
    rc, _ = run_cli(capfd, str(eng_path), "--out", str(out))
    assert rc == 0
    (out / "config" / "settings" / "base.py").write_text("changed = True\n")
    (out / SLUG / "orders").mkdir()
    (out / SLUG / "orders" / "apps.py").write_text("# a feature area, not generated\n")
    (out / "pyproject.toml").write_text(
        (out / "pyproject.toml").read_text()
        + '\n[tool.extra]\nadded = "by a request"\n'
    )
    repo = tmp_path / "target"
    before = tree_hashes(repo)
    rc, summary = run_cli(capfd, str(eng_path), "--out", str(out), "--check")
    assert rc == 1
    assert summary["status"] == "failed"
    assert summary["messages"] == ["differs: config/settings/base.py"]
    assert tree_hashes(repo) == before


@pytest.mark.usefixtures("fake_generation")
def test_check_flags_a_missing_pin_and_a_drifted_workflow(eng_path, tmp_path, capfd):
    out = out_dir(tmp_path)
    run_cli(capfd, str(eng_path), "--out", str(out))
    lock = out / "uv.lock"
    lock.write_text(lock.read_text().replace('version = "5.2.18"', 'version = "5.2.1"'))
    workflow = tmp_path / "target" / ".github" / "workflows" / f"{SLUG}.yml"
    workflow.write_text(workflow.read_text() + "# edited by hand\n")
    rc, summary = run_cli(capfd, str(eng_path), "--out", str(out), "--check")
    assert rc == 1
    assert "uv.lock: pin django==5.2.18 missing" in summary["messages"]
    assert f"differs: .github/workflows/{SLUG}.yml" in summary["messages"]


@pytest.mark.usefixtures("fake_generation")
def test_check_on_an_empty_out_reports_missing_files(eng_path, tmp_path, capfd):
    out = out_dir(tmp_path)
    rc, summary = run_cli(capfd, str(eng_path), "--out", str(out), "--check")
    assert rc == 1
    assert "missing: manage.py" in summary["messages"]
    assert not out.exists()


# ---------------------------------------------------------------- --only core


def test_only_core_leaves_core_local_alone(eng_path, tmp_path, monkeypatch, capfd):
    monkeypatch.setattr(new_app, "OVERLAY", FIXTURES / "overlay")
    out = out_dir(tmp_path)
    local = out / SLUG / "core" / "local"
    local.mkdir(parents=True)
    (local / "roles.py").write_text('ROLES = ["engagement-specific"]\n')
    rc, summary = run_cli(capfd, str(eng_path), "--out", str(out), "--only", "core")
    assert rc == 0, summary
    core = out / SLUG / "core"
    assert (core / "registry.py").read_text().endswith(f'APP = "{SLUG}"\n')
    assert (local / "roles.py").read_text() == 'ROLES = ["engagement-specific"]\n'
    manifest = json.loads((core / new_app.MANIFEST).read_text())
    assert sorted(manifest["files"]) == ["__init__.py", "registry.py"]
    assert not (out / "manage.py").exists()

    rc, _ = run_cli(
        capfd, str(eng_path), "--out", str(out), "--only", "core", "--check"
    )
    assert rc == 0
    (local / "roles.py").write_text('ROLES = ["changed locally"]\n')
    rc, _ = run_cli(
        capfd, str(eng_path), "--out", str(out), "--only", "core", "--check"
    )
    assert rc == 0
    (core / "registry.py").write_text("APP = 'edited'\n")
    before = tree_hashes(out)
    rc, summary = run_cli(
        capfd, str(eng_path), "--out", str(out), "--only", "core", "--check"
    )
    assert rc == 1
    assert summary["messages"] == [f"differs: {SLUG}/core/registry.py"]
    assert tree_hashes(out) == before


def test_only_core_without_an_overlay_core_exits_2(
    eng_path, tmp_path, monkeypatch, capfd
):
    monkeypatch.setattr(new_app, "OVERLAY", tmp_path / "no-overlay")
    rc, summary = run_cli(
        capfd, str(eng_path), "--out", str(out_dir(tmp_path)), "--only", "core"
    )
    assert rc == 2
    assert summary["status"] == "error"


# ---------------------------------------------------------------- pre-commit hooks


REVS = {new_app.RUFF_HOOKS_REPO: "v0.17.1", new_app.DJLINT_REPO: "v1.36.4"}


def s0_hooks(config: dict) -> list:
    """The repo entries that hold no app-scoped hook."""
    return [
        entry
        for entry in config["repos"]
        if not any("/app/" in hook.get("files", "") for hook in entry["hooks"])
    ]


def test_hook_merge_is_idempotent_with_two_apps_in_one_repo(tmp_path):
    original = S0_CONFIG.read_text()
    once = new_app.merge_app_hooks(original, "northwind", "northwind", REVS)
    assert once.startswith(original)
    assert new_app.merge_app_hooks(once, "northwind", "northwind", REVS) == once
    both = new_app.merge_app_hooks(once, "invoicegen", "invoicegen", REVS)
    assert new_app.merge_app_hooks(both, "invoicegen", "invoicegen", REVS) == both
    assert new_app.merge_app_hooks(both, "northwind", "northwind", REVS) == both
    assert new_app.missing_app_hooks(both, "northwind") == []
    assert new_app.missing_app_hooks(both, "invoicegen") == []

    config = yaml.safe_load(both)
    assert s0_hooks(config) == yaml.safe_load(original)["repos"]
    added = [
        (e["repo"], e["rev"], h["id"], h["files"])
        for e in config["repos"]
        for h in e["hooks"]
        if "/app/" in h.get("files", "")
    ]
    assert added == [
        (new_app.RUFF_HOOKS_REPO, "v0.17.1", "ruff-format", "^northwind/app/"),
        (new_app.DJLINT_REPO, "v1.36.4", "djlint-reformat-django", "^northwind/app/"),
        (new_app.DJLINT_REPO, "v1.36.4", "djlint-django", "^northwind/app/"),
        (new_app.RUFF_HOOKS_REPO, "v0.17.1", "ruff-format", "^invoicegen/app/"),
        (new_app.DJLINT_REPO, "v1.36.4", "djlint-reformat-django", "^invoicegen/app/"),
        (new_app.DJLINT_REPO, "v1.36.4", "djlint-django", "^invoicegen/app/"),
    ]
    assert all(e["repo"] != "local" for e in config["repos"][2:])
    path = tmp_path / ".pre-commit-config.yaml"
    path.write_text(both)
    result = subprocess.run(
        [sys.executable, "-m", "pre_commit", "validate-config", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_hooks_for_an_app_at_the_repo_root_use_the_app_pattern():
    merged = new_app.merge_app_hooks(S0_CONFIG.read_text(), "solo", ".", REVS)
    patterns = {
        h.get("files") for e in yaml.safe_load(merged)["repos"] for h in e["hooks"]
    }
    assert "^app/" in patterns


def test_ruff_rev_follows_the_s0_ruff_check_hook():
    text = S0_CONFIG.read_text().replace("rev: v0.17.1", "rev: v0.99.0")
    assert new_app.ruff_rev(text, "v0.17.1") == "v0.99.0"


# ---------------------------------------------------------------- CI workflow


def workflow(target_root: str) -> dict:
    stack = pipeline_json()["stack"]
    ctx = {
        "slug": "northwind",
        "project_name": "Northwind",
        "db_name": "northwind_demo",
        "target_root": target_root,
        "timezone": "UTC",
        "use_tz": False,
        "cloud_provider": "None",
        "uv_version": stack["uv"],
        "postgres_image": stack["docker_images"]["postgres"]["image"],
        "mssql_image": stack["docker_images"]["mssql"]["image"],
    }
    text = new_app.render_workflow(ctx)
    assert "{{" not in text.replace("${{", "")
    return yaml.safe_load(text)


def triggers(wf: dict) -> dict:
    return wf.get("on", wf.get(True))


@pytest.mark.parametrize(
    ("root", "app_dir"), [("northwind", "northwind/app"), (".", "app")]
)
def test_workflow_renders_for_a_subdirectory_and_for_the_repo_root(root, app_dir):
    wf = workflow(root)
    assert {"lint", "test-postgres", "test-mssql", "harness", "e2e"} <= set(wf["jobs"])
    assert wf["defaults"]["run"]["working-directory"] == app_dir
    on = triggers(wf)
    assert on["push"]["branches"] == ["main"]
    if root == ".":
        assert "paths" not in on["push"]
        assert "paths" not in (on["pull_request"] or {})
    else:
        expected = [f"{root}/**", ".github/workflows/northwind.yml"]
        assert on["push"]["paths"] == expected
        assert on["pull_request"]["paths"] == expected
    for name, job in wf["jobs"].items():
        runs = [s.get("run") for s in job["steps"] if s.get("run")]
        assert runs[0] == "uv sync --locked", name
        uses = [s["uses"] for s in job["steps"] if "uses" in s]
        assert uses[0] == "actions/checkout@v7"
        assert "astral-sh/setup-uv@v10.3.0" in uses
        assert "actions/upload-artifact@v7" in uses or name == "lint"
        assert not any("toolkit" in r for r in runs), name
        assert "secrets." not in json.dumps(job)


@pytest.mark.parametrize("root", ["northwind", "."])
def test_e2e_steps_after_checkout_carry_the_hashfiles_condition(root):
    glob = ("app" if root == "." else f"{root}/app") + "/tests/e2e/test_*.py"
    steps = workflow(root)["jobs"]["e2e"]["steps"]
    assert steps[0]["uses"] == "actions/checkout@v7"
    assert "if" not in steps[0]
    for step in steps[1:]:
        assert f"hashFiles('{glob}') != ''" in step["if"]
    runs = " ".join(s.get("run", "") for s in steps)
    assert "uv run playwright install --with-deps chromium" in runs
    assert "--create-db tests/e2e" in runs


def env_names(job: dict) -> set[str]:
    names = set(job.get("env") or {})
    for step in job["steps"]:
        names |= set(step.get("env") or {})
    for service in (job.get("services") or {}).values():
        names |= set(service.get("env") or {})
    return names


def test_no_job_sets_local_only_variables_and_the_harness_sets_no_database_url():
    wf = workflow("northwind")
    for name, job in wf["jobs"].items():
        assert not env_names(job) & FORBIDDEN_CI_VARS, name
        text = json.dumps(job)
        for var in FORBIDDEN_CI_VARS:
            assert var not in text, (name, var)
    harness = wf["jobs"]["harness"]
    assert "DATABASE_URL" not in env_names(harness)
    assert "DATABASE_URL" not in json.dumps(harness)
    assert harness["env"]["PG_URL"] == "postgres://postgres:postgres@localhost:5432"
    assert harness["env"]["MSSQL_URL"] == "mssql://sa:Ci-0nly-Throwaway@localhost:1433"
    run = " ".join(s.get("run", "") for s in harness["steps"])
    assert "--eq-allow-not-implemented" in run
    assert "--eq-only-areas" not in run
    for seed in ("seed-1", "seed-2", "seed-3"):
        assert seed in run
    assert wf["jobs"]["test-postgres"]["env"]["DATABASE_URL"].endswith(
        "/northwind_demo"
    )
    assert wf["jobs"]["test-mssql"]["env"]["DATABASE_URL"].startswith("mssql://sa:")


def test_sql_server_jobs_install_unixodbc_and_app_jobs_install_pango():
    wf = workflow("northwind")
    for name, job in wf["jobs"].items():
        runs = [s.get("run", "") for s in job["steps"]]
        assert any("libpangoft2-1.0-0 libharfbuzz-subset0" in r for r in runs), name
        uses_mssql = "mssql" in (job.get("services") or {})
        has_odbc = any("ACCEPT_EULA=Y apt-get install -y unixodbc" in r for r in runs)
        assert has_odbc == uses_mssql, name


def test_lint_job_runs_djlint_like_the_app_hooks():
    runs = [s.get("run", "") for s in workflow("northwind")["jobs"]["lint"]["steps"]]
    assert "uv run djlint . --check --profile=django" in runs
    assert "uv run djlint . --lint --profile=django" in runs


# ---------------------------------------------------------------- template formatting


class FakeDjlint:
    """Records djLint calls; returns the exit code given for each mode."""

    def __init__(self, codes: dict[str, int]):
        self.codes = codes
        self.calls: list[tuple[list[str], Path, str]] = []

    def __call__(self, cmd, cwd, env, **_kwargs):
        self.calls.append((cmd, cwd, env["UV_EXCLUDE_NEWER"]))
        mode = next(a for a in cmd if a in ("--reformat", "--check", "--lint"))
        return subprocess.CompletedProcess(cmd, self.codes.get(mode, 0))


def test_templates_are_reformatted_with_the_hook_version_then_checked(
    tmp_path, monkeypatch
):
    fake = FakeDjlint({"--reformat": 1})  # 1: files were reformatted
    monkeypatch.setattr(new_app.subprocess, "run", fake)
    new_app.format_templates(tmp_path, "v1.36.4", "2026-10-16T00:00:00Z")
    modes = [cmd[-2] for cmd, _cwd, _env in fake.calls]
    assert modes == ["--reformat", "--check", "--lint"]
    for cmd, cwd, exclude_newer in fake.calls:
        assert cmd[:6] == ["uv", "tool", "run", "--from", "djlint==1.36.4", "djlint"]
        assert cmd[-1] == "--profile=django"
        assert cwd == tmp_path
        assert exclude_newer == "2026-10-16T00:00:00Z"


def test_the_djlint_version_defaults_to_the_hook_rev():
    assert new_app.djlint_version(None) == "1.36.4"
    assert new_app.djlint_version("v1.40.0") == "1.40.0"


@pytest.mark.parametrize("codes", [{"--lint": 1}, {"--check": 1}, {"--reformat": 2}])
def test_templates_that_still_fail_djlint_stop_generation(tmp_path, monkeypatch, codes):
    monkeypatch.setattr(new_app.subprocess, "run", FakeDjlint(codes))
    with pytest.raises(new_app.NewAppError, match="djlint"):
        new_app.format_templates(tmp_path, "v1.36.4", "2026-10-16T00:00:00Z")
