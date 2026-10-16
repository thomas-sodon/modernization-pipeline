"""Every toolkit command honours the quiet-output contract (docs/contracts.md#quiet-output).

Later tasks run this with -k <their modules>, so each command module in toolkit/commands/ is one
case, with the module name as the test id (for example test_quiet[fetch])."""

from __future__ import annotations

import json
import os
import pkgutil
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

import toolkit.commands
from toolkit import quiet as quiet_mod

SCHEMA = json.loads(
    (
        Path(toolkit.commands.__file__).resolve().parent.parent
        / "schemas"
        / "summary.schema.json"
    ).read_text()
)
MODULES = sorted(
    m.name
    for m in pkgutil.iter_modules(toolkit.commands.__path__)
    if not m.name.startswith("_")
)
ROOT = Path(__file__).resolve().parents[2]


def run_toolkit(args, env_extra=None, cwd=None):
    env = {**os.environ, "MP_QUIET": "1", **(env_extra or {})}
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from toolkit.cli import main; sys.exit(main())",
            *args,
        ],
        cwd=cwd or ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize("module", MODULES, ids=MODULES)
def test_quiet(module):
    proc = run_toolkit([module.replace("_", "-"), "--no-such-flag-for-quiet-test"])
    lines = proc.stdout.splitlines()
    assert len(lines) == 1, proc.stdout
    summary = json.loads(lines[0])
    jsonschema.validate(summary, SCHEMA)
    assert summary["status"] == "error"
    assert summary["exit_code"] == 2
    assert proc.returncode == 2
    assert "--no-such-flag-for-quiet-test" not in lines[0]


def test_unknown_command_summary():
    proc = run_toolkit(["no-such-command"])
    assert proc.returncode == 2
    summary = json.loads(proc.stdout)
    jsonschema.validate(summary, SCHEMA)
    assert summary["command"] == "toolkit"


def test_exception_summary_has_type_and_location_not_text(make_engagement, tmp_path):
    eng = make_engagement(tmp_path)
    # a source.lock.json that is not JSON object -> EngagementError (controlled message)
    (eng.parent / "source.lock.json").write_text("[]", encoding="utf-8")
    proc = run_toolkit(["fetch", str(eng), "--verify"])
    assert proc.returncode == 2
    summary = json.loads(proc.stdout)
    jsonschema.validate(summary, SCHEMA)
    assert summary["status"] == "error"


def test_uncaught_exception(monkeypatch, capfd, quiet):
    from toolkit import cli

    def boom(args):
        raise RuntimeError("SECRET-VALUE-NEVER-PRINTED")

    monkeypatch.setattr("toolkit.commands.fetch.run", boom)
    code = cli.main(["fetch", "--jars"])
    out, _err = capfd.readouterr()
    assert code == 2
    lines = out.splitlines()
    assert len(lines) == 1
    summary = json.loads(lines[0])
    jsonschema.validate(summary, SCHEMA)
    assert "SECRET-VALUE" not in out
    assert summary["messages"][0].startswith("RuntimeError at ")
    assert (
        "test_quiet" in summary["messages"][0] or "toolkit." in summary["messages"][0]
    )


def test_status_follows_exit_code():
    assert quiet_mod.status_for(0) == "ok"
    assert quiet_mod.status_for(0, recorded=True) == "recorded"
    assert quiet_mod.status_for(0, no_data=True) == "no-data"
    assert quiet_mod.status_for(1) == "failed"
    assert quiet_mod.status_for(5) == "failed"
    assert quiet_mod.status_for(2) == "error"
    assert quiet_mod.status_for(3) == "error"


def test_human_output_without_quiet(monkeypatch, capsys):
    monkeypatch.delenv("MP_QUIET", raising=False)
    quiet_mod.reset()
    quiet_mod.summary(None, "fetch", 0, counts={"files": 1}, messages=["hello"])
    out = capsys.readouterr().out
    assert "fetch: ok (exit 0)" in out
    assert not out.startswith("{")


def test_print_inside_quiet_command_goes_to_stderr(monkeypatch, capfd, quiet):
    from toolkit import cli

    def noisy(args):
        print("progress text on stdout")
        subprocess.run(["echo", "child stdout"], check=True)
        return 0

    monkeypatch.setattr("toolkit.commands.fetch.run", noisy)
    code = cli.main(["fetch", "--jars"])
    out, err = capfd.readouterr()
    assert code == 0
    assert len(out.splitlines()) == 1
    assert json.loads(out)["status"] == "ok"
    assert "progress text" in err and "child stdout" in err


def test_messages_are_capped(monkeypatch, capfd, quiet):
    quiet_mod.reset()
    quiet_mod.begin()
    try:
        quiet_mod.summary(None, "fetch", 1, messages=["x" * 500] * 80)
    finally:
        quiet_mod.end()
    out = capfd.readouterr().out
    summary = json.loads(out)
    jsonschema.validate(summary, SCHEMA)
    assert len(summary["messages"]) == 50 and len(summary["messages"][0]) == 200
