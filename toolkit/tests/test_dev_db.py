"""scripts/dev_db.sh with a stub docker on PATH (docs/contracts.md#scripts)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "dev_db.sh"
PG_PW = "pg-agent-pw-1"
MS_PW = "0123abcdAa1-"
PG_URL = f"postgresql://mp_agent:{PG_PW}@127.0.0.1:55432"
MSSQL_URL = f"mssql://mp_agent:{MS_PW}@127.0.0.1:14330"


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    cfg = home / ".config" / "modernization"
    cfg.mkdir(parents=True)
    stubbin = tmp_path / "bin"
    stubbin.mkdir()
    log = tmp_path / "docker-calls.jsonl"
    (stubbin / "docker").write_text(
        "#!/usr/bin/python3 -I\n"
        "import json, os, sys\n"
        f"with open({str(log)!r}, 'a') as f:\n"
        "    f.write(json.dumps({'argv': sys.argv[1:], 'PGPASSWORD': os.environ.get('PGPASSWORD'), 'SQLCMDPASSWORD': os.environ.get('SQLCMDPASSWORD')}) + '\\n')\n"
        "if sys.argv[1] == 'inspect':\n"
        "    print('true' if sys.argv[-1] in os.environ.get('STUB_RUNNING', '').split(',') else 'false')\n"
        "elif os.environ.get('STUB_EXISTS') and sys.argv[-1].startswith('create database') or os.environ.get('STUB_EXISTS') and 'createdb' in sys.argv:\n"
        "    print('ERROR: database already exists', file=sys.stderr); sys.exit(1)\n"
    )
    (stubbin / "docker").chmod(0o755)
    (cfg / "env").write_text(
        f"export PATH={stubbin}:{os.environ['PATH']}\nexport PG_URL={PG_URL}\nexport MSSQL_URL={MSSQL_URL}\n"
    )
    (cfg / "data.env").write_text(
        "export MP_DATA_PG_URL=postgresql://postgres:never@127.0.0.1:55432\n"
    )
    return {
        "HOME": str(home),
        "PATH": os.environ["PATH"],
        "STUB_RUNNING": "mp-pg16,mp-mssql22",
        "log": log,
        "cfg": cfg,
    }


def run(env, *args, **extra):
    e = {k: v for k, v in env.items() if k not in ("log", "cfg")}
    e.update(extra)
    return subprocess.run(
        [str(SCRIPT), *args],
        env=e,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def calls(env):
    if not env["log"].exists():
        return []
    return [json.loads(line) for line in env["log"].read_text().splitlines()]


def execs(env):
    return [c for c in calls(env) if c["argv"][0] == "exec"]


def test_createdb_both_dialects(env):
    p = run(env, "createdb", "northwind_demo")
    assert p.returncode == 0, p.stderr
    ex = execs(env)
    assert ex[0]["argv"] == [
        "exec",
        "-e",
        "PGPASSWORD",
        "mp-pg16",
        "createdb",
        "-h",
        "127.0.0.1",
        "-U",
        "mp_agent",
        "northwind_demo",
    ]
    assert ex[0]["PGPASSWORD"] == PG_PW
    assert ex[1]["argv"] == [
        "exec",
        "-e",
        "SQLCMDPASSWORD",
        "mp-mssql22",
        "/opt/mssql-tools18/bin/sqlcmd",
        "-C",
        "-S",
        "localhost",
        "-U",
        "mp_agent",
        "-b",
        "-Q",
        "create database [northwind_demo]",
    ]
    assert ex[1]["SQLCMDPASSWORD"] == MS_PW
    for c in calls(env):
        assert PG_PW not in " ".join(c["argv"]) and MS_PW not in " ".join(c["argv"])
    assert PG_PW not in p.stdout + p.stderr and MS_PW not in p.stdout + p.stderr


def test_createdb_existing_is_left_unchanged(env):
    p = run(env, "createdb", "northwind_demo", STUB_EXISTS="1")
    assert p.returncode == 0, p.stderr


def test_dropdb_both_dialects(env):
    assert run(env, "dropdb", "northwind_demo").returncode == 0
    ex = execs(env)
    assert ex[0]["argv"] == [
        "exec",
        "-e",
        "PGPASSWORD",
        "mp-pg16",
        "dropdb",
        "--if-exists",
        "-h",
        "127.0.0.1",
        "-U",
        "mp_agent",
        "northwind_demo",
    ]
    assert ex[1]["argv"][-1] == "drop database if exists [northwind_demo]"
    assert ex[1]["argv"][:4] == ["exec", "-e", "SQLCMDPASSWORD", "mp-mssql22"]


def test_only_running_dialects(env):
    assert run(env, "createdb", "x_dev", STUB_RUNNING="mp-pg16").returncode == 0
    assert [c["argv"][3] for c in execs(env)] == ["mp-pg16"]


@pytest.mark.parametrize(
    "name", ["mpdata_northwind_demo_stage1", "Bad", "x", "a;b", "1abc", "a" * 64]
)
def test_bad_names_exit_2_without_docker(env, name):
    assert run(env, "createdb", name).returncode == 2
    assert run(env, "dropdb", name).returncode == 2
    assert calls(env) == []


def test_url(env):
    p = run(env, "url", "pg")
    assert p.returncode == 0 and p.stdout.strip() == PG_URL
    assert run(env, "url", "mssql").stdout.strip() == MSSQL_URL
    (env["cfg"] / "env").write_text(f"export PATH={os.environ['PATH']}\n")
    assert run(env, "url", "pg").returncode == 1


@pytest.mark.parametrize("sub", ["up", "check", "psql", ""])
def test_other_subcommands_exit_2(env, sub):
    assert run(env, sub).returncode == 2
    assert calls(env) == []


def test_never_reads_data_env():
    assert "data.env" not in "\n".join(
        line
        for line in SCRIPT.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )
