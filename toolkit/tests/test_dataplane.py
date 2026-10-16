"""mp-data and toolkit/dataplane.py (docs/contracts.md#mp-data, #data-plane-jail).

Most cases inject a runner, so they need no bubblewrap; the real-jail case needs it and skips when
MP_SANDBOX is set (a bwrap inside mp-run is refused). The bash half (scripts/mp-data) runs with a
stub `uv` on PATH that records how it was called."""

from __future__ import annotations

import datetime as dt
import json
import os
import socket
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import jsonschema
import pytest

from toolkit import dataplane
from toolkit.commands import gh as gh_cmd
from toolkit.commands import push as push_cmd
from toolkit.engagement import load

from .conftest import git

ROOT = Path(__file__).resolve().parents[2]
MP_DATA = ROOT / "scripts" / "mp-data"
SCHEMA = json.loads((ROOT / "toolkit" / "schemas" / "summary.schema.json").read_text())
TOKEN = "synthetic-push-token-5678"
DATA_ENV = {
    "MP_DATA_PG_URL": "postgresql://postgres:superpw@127.0.0.1:55432",
    "MP_RUNNER_PG_URL": "postgresql://mp_runner:runnerpw@127.0.0.1:55432",
    "MP_MSSQL_SA_PASSWORD": "sapw-unrelated-value",
    "MP_DATA_MSSQL_URL": "mssql://sa:sapw@127.0.0.1:14330",
    "MP_RUNNER_MSSQL_URL": "mssql://mp_runner:runnerms@127.0.0.1:14330",
    "GITHUB_PUSH_DEMO": TOKEN,
}
DB_KEYS = (
    "PG_URL",
    "MSSQL_URL",
    "MP_DATA_PG_URL",
    "MP_DATA_MSSQL_URL",
    "MP_RUNNER_PG_URL",
    "MP_RUNNER_MSSQL_URL",
)


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    cfg = h / ".config" / "modernization"
    cfg.mkdir(parents=True)
    (cfg / "env").write_text(
        f"export PATH={tmp_path / 'stubbin'}:{os.environ['PATH']}\n"
        "export PG_URL=postgresql://mp_agent:mp-agent@127.0.0.1:55432\n",
        encoding="utf-8",
    )
    write_data_env(h, DATA_ENV)
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg))
    monkeypatch.setenv("PG_URL", "postgresql://mp_agent:mp-agent@127.0.0.1:55432")
    monkeypatch.setenv("MSSQL_URL", "mssql://mp_agent:agentpw@127.0.0.1:14330")
    monkeypatch.setenv("MP_DB_SUFFIX", "test_slot")
    monkeypatch.delenv("MP_SANDBOX", raising=False)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("PASSWORD_STORE_DIR", raising=False)
    monkeypatch.delenv("MP_QUIET", raising=False)
    return h


def write_data_env(home: Path, values: dict) -> None:
    p = home / ".config" / "modernization" / "data.env"
    p.write_text(
        "".join(f"export {k}={v}\n" for k, v in values.items()), encoding="utf-8"
    )
    p.chmod(0o600)


@pytest.fixture
def eng(tmp_path, home, make_engagement):
    return make_engagement(
        tmp_path,
        secrets={
            "github_push": {
                "ref": "env:GITHUB_PUSH_DEMO",
                "scope": None,
                "expires": None,
            }
        },
    )


class FakeRunner:
    """Records each call and prints a valid summary for the command."""

    def __init__(self, rc=0, stdout=None, stderr=b"", extra=None):
        self.calls = []
        self.rc = rc
        self.stdout = stdout
        self.stderr = stderr
        self.extra = extra or {}

    def __call__(self, argv, env, cwd, log):
        self.calls.append(
            {
                "argv": list(argv),
                "env": dict(env),
                "cwd": cwd,
                "tmpdir": env.get("TMPDIR"),
            }
        )
        self.tmpdir_existed = Path(env["TMPDIR"]).is_dir()
        if self.stdout is not None:
            out = self.stdout
        else:
            sep = argv.index("--")
            child = argv[sep + 1 :]
            words = dataplane.command_words(child[1], child[2:])
            status = (
                "ok" if self.rc == 0 else ("failed" if self.rc in (1, 5) else "error")
            )
            out = (
                json.dumps(
                    {
                        "status": status,
                        "command": words,
                        "exit_code": self.rc,
                        **self.extra,
                    }
                )
                + "\n"
            ).encode()
        return self.rc, out, self.stderr

    @property
    def env(self):
        return self.calls[-1]["env"]


def no_owners(dialect, url):
    return []


def run_dp(argv, runner, capfd=None, lister=no_owners, **kw):
    code = dataplane.run(
        argv, runner=runner, owner_lister=lister, child=["toolkit-stub"], **kw
    )
    return code


def last_summary(capfd):
    out = capfd.readouterr().out
    lines = out.splitlines()
    assert len(lines) == 1, out
    obj = json.loads(lines[0])
    jsonschema.validate(obj, SCHEMA)
    return obj


# ------------------------------------------------------------------ python half


def test_unknown_command_exits_2(capfd):
    runner = FakeRunner()
    assert dataplane.run(["bogus"], runner=runner) == 2
    assert runner.calls == []
    assert last_summary(capfd)["status"] == "error"


def test_slot_reaches_child(eng, capfd):
    runner = FakeRunner()
    assert run_dp(["parse", str(eng), "--strict"], runner) == 0
    assert runner.env["MP_DB_SUFFIX"] == "test_slot"
    assert runner.env["MP_QUIET"] == "1"
    assert runner.env["MP_SANDBOX"] == "mp-data"
    s = last_summary(capfd)
    assert s["log"].startswith(str(load(eng)["paths"]["private"]) + "/logs/")
    assert "-test_slot-" in s["log"]


def test_bad_slot_in_python_half(eng, monkeypatch, capfd):
    monkeypatch.setenv("MP_DB_SUFFIX", "Bad-Slot")
    runner = FakeRunner()
    assert run_dp(["parse", str(eng)], runner) == 2
    assert runner.calls == []


@pytest.mark.parametrize(
    "argv,kind",
    [
        (["equivalence", "{eng}", "--adapter", "django"], "runner"),
        (["reference", "build", "{eng}", "--dataset", "engagement"], "runner"),
        (["reference", "run", "{eng}", "--dataset", "engagement"], "runner"),
        (["reference", "selfcheck", "{eng}"], "runner"),
        (["golden", "record", "{eng}"], "runner"),
        (["stage1", "{eng}", "--target-db", "local:mpdata_demo_eng_stage1"], "super"),
        (["purge", "{eng}", "--dry-run"], "super"),
        (["fetch", "{eng}", "--verify"], None),
        (["parse", "{eng}", "--strict"], None),
        (["dataset", "export", "{eng}"], None),
        (["guard", "{eng}"], None),
        (["reference", "sql", "{eng}"], None),
        (["golden", "status", "{eng}"], None),
        (["results", "summarize", "{eng}"], None),
    ],
    ids=lambda v: "-".join(v) if isinstance(v, list) else str(v),
)
def test_database_urls_per_command(eng, capfd, argv, kind):
    argv = [a.replace("{eng}", str(eng)) for a in argv]
    runner = FakeRunner()
    assert run_dp(argv, runner) == 0, capfd.readouterr().out
    env = runner.env
    if kind == "runner":
        assert env["MP_DATA_PG_URL"] == env["PG_URL"] == DATA_ENV["MP_RUNNER_PG_URL"]
        assert (
            env["MP_DATA_MSSQL_URL"]
            == env["MSSQL_URL"]
            == DATA_ENV["MP_RUNNER_MSSQL_URL"]
        )
    elif kind == "super":
        assert env["MP_DATA_PG_URL"] == env["PG_URL"] == DATA_ENV["MP_DATA_PG_URL"]
        assert (
            env["MP_DATA_MSSQL_URL"]
            == env["MSSQL_URL"]
            == DATA_ENV["MP_DATA_MSSQL_URL"]
        )
    else:
        for k in DB_KEYS:
            assert k not in env
    assert "MP_RUNNER_PG_URL" not in env and "MP_RUNNER_MSSQL_URL" not in env
    # no other data.env value reaches the child
    values = set(env.values())
    for k in ("MP_MSSQL_SA_PASSWORD", "GITHUB_PUSH_DEMO"):
        assert DATA_ENV[k] not in values
    assert not any(k.startswith("MP_SECRET_") for k in env)
    assert env["MP_TEST_DB_PREFIX"] == "mpdata_test_"
    if argv[0] == "purge":
        assert "MP_OTHER_DB_NAMES" in env
    else:
        assert "MP_OTHER_DB_NAMES" not in env


def test_missing_url_leaves_both_names_unset(eng, home, capfd):
    write_data_env(
        home, {k: v for k, v in DATA_ENV.items() if k != "MP_RUNNER_MSSQL_URL"}
    )
    runner = FakeRunner()
    assert run_dp(["equivalence", str(eng)], runner) == 0
    assert runner.env["PG_URL"] == DATA_ENV["MP_RUNNER_PG_URL"]
    assert "MSSQL_URL" not in runner.env and "MP_DATA_MSSQL_URL" not in runner.env
    (home / ".config" / "modernization" / "data.env").unlink()
    assert run_dp(["stage1", str(eng)], runner) == 0
    for k in DB_KEYS:
        assert k not in runner.env


def test_purge_gets_other_db_names(eng, home, tmp_path, capfd):
    runner = FakeRunner()
    assert run_dp(["purge", str(eng)], runner) == 0
    assert runner.env["MP_OTHER_DB_NAMES"] == ""
    other = home / "engagements" / "c2026002" / "record"
    other.mkdir(parents=True)
    (other / "engagement.json").write_text(
        json.dumps({"id": "c2026002", "db_name": None})
    )
    demo = tmp_path / "pipeline" / "engagements" / "other-demo"
    demo.mkdir(parents=True)
    (demo / "engagement.json").write_text(
        json.dumps({"id": "other-demo", "db_name": "demo_eng_extra"})
    )
    tpl = tmp_path / "pipeline" / "engagements" / "_template"
    tpl.mkdir(parents=True)
    (tpl / "engagement.json").write_text(json.dumps({"id": "<x>", "db_name": None}))
    assert run_dp(["purge", str(eng)], runner) == 0
    assert runner.env["MP_OTHER_DB_NAMES"] == "c2026002,demo_eng_extra"
    assert run_dp(["parse", str(eng)], runner) == 0
    assert "MP_OTHER_DB_NAMES" not in runner.env


def test_tmpdir_in_logs_and_removed(eng, capfd):
    runner = FakeRunner()
    assert run_dp(["parse", str(eng)], runner) == 0
    tmpdir = Path(runner.calls[-1]["tmpdir"])
    logs = Path(load(eng)["paths"]["private"]) / "logs"
    assert tmpdir.parent == logs and tmpdir.name.startswith("tmp-")
    assert runner.tmpdir_existed
    assert not tmpdir.exists()


@pytest.mark.parametrize("command", ["stage1", "equivalence"])
def test_mpdata_database_owned_by_agent_refused(eng, capfd, command):
    def lister(dialect, url):
        assert url in (DATA_ENV["MP_DATA_PG_URL"], DATA_ENV["MP_DATA_MSSQL_URL"])
        return (
            [("postgres", "postgres"), ("mpdata_demo_eng_stage1", "mp_agent")]
            if dialect == "postgresql"
            else []
        )

    runner = FakeRunner()
    assert run_dp([command, str(eng)], runner, lister=lister) == 2
    assert runner.calls == []
    s = last_summary(capfd)
    assert "mp_agent" in s["messages"][0]


def test_allowed_owners_pass(eng, capfd):
    def lister(dialect, url):
        if dialect == "postgresql":
            return [
                ("mpdata_a", "postgres"),
                ("mpdata_b", "mp_runner"),
                ("other", "mp_agent"),
            ]
        return [("mpdata_c", "sa"), ("mpdata_d", "mp_runner")]

    for command in ("stage1", "equivalence"):
        runner = FakeRunner()
        assert run_dp([command, str(eng)], runner, lister=lister) == 0
        assert len(runner.calls) == 1
        capfd.readouterr()


def test_unanswering_server_left_to_command(eng, capfd):
    def lister(dialect, url):
        raise ConnectionRefusedError("down")

    runner = FakeRunner()
    assert run_dp(["stage1", str(eng)], runner, lister=lister) == 0
    s = last_summary(capfd)
    assert any("did not answer" in m for m in s["messages"])


@pytest.mark.parametrize(
    "target",
    [
        "postgresql://u:p@127.0.0.1:55432/x",
        "local:northwind_demo",
        "local:mpdata_bad-name",
        "mssql://x",
        "other:thing",
    ],
)
def test_target_db_refused(eng, capfd, target):
    runner = FakeRunner()
    assert run_dp(["stage1", str(eng), "--target-db", target], runner) == 2
    assert runner.calls == []


def test_target_db_secret_resolved(eng, home, tmp_path, make_engagement, capfd):
    write_data_env(
        home,
        {**DATA_ENV, "STAGE1_URL_X": "mssql://client:pw@db.example.invalid:1433/app"},
    )
    e = make_engagement(tmp_path, secrets={"stage1_target_url_ref": "env:STAGE1_URL_X"})
    runner = FakeRunner()
    assert (
        run_dp(
            ["stage1", str(e), "--target-db", "secret:stage1_target_url_ref"], runner
        )
        == 0
    )
    assert runner.env["MP_SECRET_SECRETS_STAGE1_TARGET_URL_REF"].startswith(
        "mssql://client:"
    )
    assert not any("pw@" in a for a in runner.calls[-1]["argv"])
    # an unset ref is refused
    assert (
        run_dp(["stage1", str(e), "--target-db", "secret:staging_db_url_ref"], runner)
        == 2
    )


def test_push_gets_token_only_as_mp_secret(eng, capfd):
    runner = FakeRunner()
    assert run_dp(["push", str(eng), "--branch", "main"], runner) == 0
    env = runner.env
    assert env["MP_SECRET_SECRETS_GITHUB_PUSH_REF"] == TOKEN
    assert [k for k, v in env.items() if v == TOKEN] == [
        "MP_SECRET_SECRETS_GITHUB_PUSH_REF"
    ]
    assert not any(TOKEN in a for a in runner.calls[-1]["argv"])
    s = capfd.readouterr().out
    assert TOKEN not in s
    log = Path(json.loads(s)["log"]).read_text()
    assert TOKEN not in log
    runner = FakeRunner()
    assert run_dp(["equivalence", str(eng)], runner) == 0
    assert not any(k.startswith("MP_SECRET_") for k in runner.env)


def test_log_names_differ_by_slot_and_pid(eng, monkeypatch, capfd):
    now = dt.datetime(2026, 10, 16, 12, 0, 0, tzinfo=dt.UTC)
    logs = []
    for slot, pid in (("slot_a", 100), ("slot_b", 100), ("slot_a", 101)):
        monkeypatch.setenv("MP_DB_SUFFIX", slot)
        assert run_dp(["parse", str(eng)], FakeRunner(), now=now, pid=pid) == 0
        logs.append(last_summary(capfd)["log"])
    assert len(set(logs)) == 3
    assert Path(logs[0]).name == "20261016T120000Z-slot_a-100-parse.log"
    assert oct(Path(logs[0]).stat().st_mode & 0o777) == "0o600"


def test_out_parent_created_and_out_rules(eng, tmp_path, capfd):
    private = Path(load(eng)["paths"]["private"])
    runner = FakeRunner()
    out = private / "out" / "model" / "deep" / "x.json"
    assert run_dp(["parse", str(eng), "--out", str(out)], runner) == 0
    assert out.parent.is_dir()
    argv = runner.calls[-1]["argv"]
    assert ["--bind", str(out.parent), str(out.parent)] == argv[
        argv.index(str(out.parent)) - 1 : argv.index(str(out.parent)) + 2
    ]
    capfd.readouterr()
    runner = FakeRunner()
    outside = tmp_path / "elsewhere" / "x.json"
    assert run_dp(["parse", str(eng), "--out", str(outside)], runner) == 2
    assert runner.calls == [] and not outside.parent.exists()
    capfd.readouterr()
    # dataset export --out must be in the data zone
    assert (
        run_dp(
            ["dataset", "export", str(eng), "--out", str(private / "out" / "dataset")],
            runner,
        )
        == 2
    )
    assert runner.calls == []
    capfd.readouterr()
    assert (
        run_dp(
            [
                "dataset",
                "export",
                str(eng),
                "--out",
                str(private / "datasets" / "engagement"),
            ],
            runner,
        )
        == 0
    )
    capfd.readouterr()
    # guard --report as well; --junit-dir is created as a directory
    assert (
        run_dp(
            ["guard", str(eng), "--report", str(tmp_path / "pipeline" / "rep")], runner
        )
        == 2
    )
    capfd.readouterr()
    junit = private / "results" / "S2"
    assert run_dp(["equivalence", str(eng), f"--junit-dir={junit}"], runner) == 0
    assert junit.is_dir()


def test_symlinked_out_resolved(eng, tmp_path, capfd):
    private = Path(load(eng)["paths"]["private"])
    (private / "review").mkdir(parents=True, exist_ok=True)
    link = private / "review" / "link"
    link.symlink_to(tmp_path / "outside-dir")
    (tmp_path / "outside-dir").mkdir()
    runner = FakeRunner()
    assert run_dp(["parse", str(eng), "--out", str(link / "x.json")], runner) == 2
    assert runner.calls == []


def jail_for(eng, words, tmp_path, output_dirs=()):
    e = load(eng)
    jail = dataplane.plan(
        words, e, Path(os.environ["HOME"]), ROOT, tmp_path / "tmpdir", list(output_dirs)
    )
    return e, jail, dataplane.jail_argv(jail, ["toolkit", *words.split()])


def _order_ok(argv):
    i_ro = argv.index("--ro-bind")
    assert argv[i_ro : i_ro + 3] == ["--ro-bind", "/", "/"]
    binds = [i for i, a in enumerate(argv) if a == "--bind"]
    tmpfs = [i for i, a in enumerate(argv) if a == "--tmpfs"]
    nulls = [
        i for i, a in enumerate(argv) if a == "--ro-bind" and argv[i + 1] == "/dev/null"
    ]
    assert binds, argv
    assert max(binds) < min(tmpfs + nulls)
    if tmpfs and nulls:
        assert max(tmpfs) < min(nulls)
    assert i_ro < min(binds)
    return binds, tmpfs, nulls


@pytest.mark.parametrize(
    "words",
    [
        "fetch",
        "parse",
        "dataset export",
        "guard",
        "reference build",
        "golden record",
        "stage1",
        "equivalence",
        "results summarize",
        "purge",
        "push",
        "gh",
    ],
)
def test_jail_argv_order(eng, home, tmp_path, words):
    other = home / "engagements" / "c2026002"
    (other / "private").mkdir(parents=True)
    (other / "record").mkdir(parents=True)
    store = home / ".password-store"
    store.mkdir()
    target = tmp_path / "target"
    if not (target / ".git").exists():
        git("init", "-q", str(target))
    e, _jail, argv = jail_for(eng, words, tmp_path)
    _binds, _tmpfs, _nulls = _order_ok(argv)
    private = Path(e["paths"]["private"])
    pairs = [(argv[i], argv[i + 1]) for i in range(len(argv) - 1)]
    for d in (
        "sources",
        "datasets",
        "workbook",
        "reference",
        "goldens",
        "corpus",
        "results",
        "logs",
        "assets",
    ):
        assert ("--bind", str(private / d)) in pairs
    assert ("--bind", str(private / "scrub-map.json")) in pairs
    assert ("--tmpfs", str(other / "private")) in pairs and (
        "--tmpfs",
        str(other / "record"),
    ) in pairs
    assert ("--tmpfs", str(store)) in pairs
    assert ("--tmpfs", os.environ["XDG_RUNTIME_DIR"]) in pairs
    assert ("/dev/null", str(home / ".config" / "modernization" / "data.env")) in pairs
    assert argv[0] == "bwrap" and argv[1:4] == [
        "--unshare-user",
        "--unshare-pid",
        "--die-with-parent",
    ]
    assert argv[argv.index("--") + 1 :] == ["toolkit", *words.split()]
    if words == "fetch":
        assert ("--bind", e["paths"]["records"]) in pairs and (
            "--bind",
            str(ROOT / "vendor"),
        ) in pairs
    if words == "golden record":
        assert ("--bind", str(Path(e["paths"]["target"]) / "goldens")) in pairs
    if words == "guard":
        assert (
            "--bind",
            str(home / ".local" / "share" / "modernization" / "pre-commit"),
        ) in pairs
    if words in ("push", "gh"):
        assert ("--bind", str(Path(os.path.realpath(target / ".git")))) in pairs
    if words == "purge":
        assert ("--bind", str(private)) in pairs and (
            "--bind",
            e["paths"]["records"],
        ) in pairs
    else:
        assert ("--bind", str(private)) not in pairs
        assert ("--bind", e["paths"]["records"]) not in pairs or words == "fetch"


def test_docker_host_socket_masked(eng, home, tmp_path, monkeypatch):
    sock_dir = tmp_path / "dockersock"
    sock_dir.mkdir()
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(sock_dir / "docker.sock"))
    try:
        monkeypatch.setenv("DOCKER_HOST", f"unix://{sock_dir / 'docker.sock'}")
        _e, _jail, argv = jail_for(eng, "parse", tmp_path)
        assert ("/dev/null", str(sock_dir / "docker.sock")) in [
            (argv[i], argv[i + 1]) for i in range(len(argv) - 1)
        ]
    finally:
        s.close()


def test_corpus_string_withheld(eng, capfd):
    private = Path(load(eng)["paths"]["private"])
    (private / "corpus").mkdir(parents=True, exist_ok=True)
    (private / "corpus" / "strings.txt").write_text(
        "ZEBRA-CORPUS-STRING\n", encoding="utf-8"
    )
    runner = FakeRunner(extra={"messages": ["found ZEBRA-CORPUS-STRING"]})
    assert run_dp(["parse", str(eng)], runner) == 2
    out = capfd.readouterr().out
    assert "ZEBRA" not in out
    s = json.loads(out)
    jsonschema.validate(s, SCHEMA)
    assert s["status"] == "error" and "withheld" in s["messages"][0]


def test_invalid_or_missing_summary(eng, capfd):
    for stdout, rc, want in (
        (b"", 0, 2),
        (b"not json\n", 0, 2),
        (b'{"status":"ok"}\n', 0, 2),
        (b"", 1, 1),
        (b"a\nb\n", 5, 5),
    ):
        assert run_dp(["parse", str(eng)], FakeRunner(rc=rc, stdout=stdout)) == want
        s = last_summary(capfd)
        assert s["status"] == "error" and s["exit_code"] == want
    # an exit code that differs from the summary's is invalid
    bad = (
        json.dumps({"status": "ok", "command": "parse", "exit_code": 0}).encode()
        + b"\n"
    )
    assert run_dp(["parse", str(eng)], FakeRunner(rc=1, stdout=bad)) == 1
    assert last_summary(capfd)["status"] == "error"


def test_summary_paths_must_be_inside_repos(eng, tmp_path, capfd):
    runner = FakeRunner(
        extra={
            "paths": [
                str(
                    tmp_path
                    / "home"
                    / "engagements"
                    / "demo-eng"
                    / "private"
                    / "sources"
                    / "x"
                )
            ]
        }
    )
    assert run_dp(["parse", str(eng)], runner) == 2
    capfd.readouterr()
    runner = FakeRunner(extra={"paths": [str(tmp_path / "pipeline" / "x.json")]})
    assert run_dp(["parse", str(eng)], runner) == 0


def test_canary(tmp_path, home, make_engagement, capfd):
    """Engagement cells never leave mp-data except into its log."""
    eng = make_engagement(tmp_path, id="canary-eng")
    private = Path(load(eng)["paths"]["private"])
    cells = [f"CANARY-{n}" for n in range(1, 6)]
    ds = private / "datasets" / "engagement"
    ds.mkdir(parents=True)
    (ds / "Orders.csv").write_text(
        "OrderID,Customer\r\n" + "".join(f"{i},{c}\r\n" for i, c in enumerate(cells)),
        encoding="utf-8",
    )
    (private / "corpus").mkdir(parents=True)
    (private / "corpus" / "strings.txt").write_text(
        "\n".join(cells) + "\n", encoding="utf-8"
    )

    def runner(argv, env, cwd, log):
        log.write(("\n".join(cells) + "\n").encode())  # the child's stderr
        return (
            1,
            ("\n".join(cells) + "\n").encode(),
            ("stderr " + " ".join(cells)).encode(),
        )

    code = dataplane.run(
        ["parse", str(eng), "--strict"],
        runner=runner,
        owner_lister=no_owners,
        child=["toolkit-stub"],
    )
    out, err = capfd.readouterr()
    assert code == 1
    lines = out.splitlines()
    assert len(lines) == 1
    summary = json.loads(lines[0])
    jsonschema.validate(summary, SCHEMA)
    assert "CANARY-" not in out and "CANARY-" not in err
    log = Path(summary["log"]).read_text()
    for c in cells:
        assert c in log


# ------------------------------------------------------------------ push and gh (toolkit side)


@pytest.fixture
def push_env(tmp_path, home, make_engagement, monkeypatch, make_git_repo):
    e = make_engagement(
        tmp_path,
        secrets={
            "github_push": {
                "ref": "env:GITHUB_PUSH_DEMO",
                "scope": None,
                "expires": None,
            }
        },
    )
    target = make_git_repo(
        tmp_path, "target", origin="https://github.com/example-org/example-repo.git"
    )
    monkeypatch.setenv("MP_SANDBOX", "mp-data")
    monkeypatch.setenv("MP_SECRET_SECRETS_GITHUB_PUSH_REF", TOKEN)
    monkeypatch.setenv("MP_QUIET", "1")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    return e, target


def test_push_argv_env_and_no_token(push_env, monkeypatch, capfd):
    from toolkit.cli import main

    e, target = push_env
    seen = {}

    def fake(argv, env, cwd):
        seen.update(argv=argv, env=env, cwd=cwd)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(push_cmd, "run_git", fake)
    assert main(["push", str(e), "--branch", "main"]) == 0
    out = capfd.readouterr().out
    assert TOKEN not in out and not any(TOKEN in a for a in seen["argv"])
    assert (
        "core.hooksPath=/dev/null" in seen["argv"]
        and "core.fsmonitor=false" in seen["argv"]
    )
    assert seen["argv"][-3:] == ["push", "origin", "refs/heads/main:refs/heads/main"]
    i = seen["argv"].index("credential.helper=")
    assert seen["argv"][i + 2].startswith("credential.helper=!")
    assert (
        seen["env"]["GIT_CONFIG_GLOBAL"] == "/dev/null"
        and seen["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
    )
    assert seen["env"]["MP_SECRET_SECRETS_GITHUB_PUSH_REF"] == TOKEN
    assert seen["cwd"] == str(target)
    s = json.loads(out)
    assert "branch main" in s["messages"]


def test_credential_helper_answers_only_github(push_env):
    helper = push_cmd.HELPER[1:]
    env = {**os.environ, "MP_SECRET_SECRETS_GITHUB_PUSH_REF": TOKEN}
    script = helper.replace("; f", '; f "$@"')
    ok = subprocess.run(
        ["sh", "-c", script, "sh", "get"],
        input="protocol=https\nhost=github.com\n\n",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert f"password={TOKEN}" in ok.stdout and "username=x-access-token" in ok.stdout
    other = subprocess.run(
        ["sh", "-c", script, "sh", "get"],
        input="protocol=https\nhost=evil.example\n\n",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert TOKEN not in other.stdout
    plain = subprocess.run(
        ["sh", "-c", script, "sh", "get"],
        input="protocol=http\nhost=github.com\n\n",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert TOKEN not in plain.stdout
    store = subprocess.run(
        ["sh", "-c", script, "sh", "store"],
        input="protocol=https\nhost=github.com\n\n",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert TOKEN not in store.stdout


@pytest.mark.parametrize(
    "setup",
    [
        ["config", "remote.origin.pushurl", "https://github.com/someone/else"],
        ["config", "url.https://evil.example/.insteadOf", "https://github.com/"],
        ["config", "http.proxy", "http://127.0.0.1:9"],
        ["config", "http.sslVerify", "false"],
        ["config", "remote.origin.proxy", "http://127.0.0.1:9"],
        ["remote", "set-url", "origin", "git@github.com:example-org/example-repo.git"],
    ],
    ids=["pushurl", "insteadof", "http-proxy", "ssl", "remote-proxy", "ssh-origin"],
)
def test_push_refuses_redirects(push_env, monkeypatch, capfd, setup):
    from toolkit.cli import main

    e, target = push_env
    git("-C", str(target), *setup)
    called = []
    monkeypatch.setattr(push_cmd, "run_git", lambda *a: called.append(a))
    assert main(["push", str(e), "--branch", "main"]) == 2
    assert called == []


@pytest.mark.parametrize("branch", ["+main", "main:other", "a b", "--force", "x..y"])
def test_push_refuses_bad_branches_and_force(push_env, monkeypatch, capfd, branch):
    from toolkit.cli import main

    e, _target = push_env
    called = []
    monkeypatch.setattr(push_cmd, "run_git", lambda *a: called.append(a))
    assert main(["push", str(e), f"--branch={branch}"]) == 2
    assert main(["push", str(e), "--branch", "main", "--force"]) == 2
    assert called == []


def test_push_refuses_outside_mp_data(push_env, monkeypatch, capfd):
    from toolkit.cli import main

    e, _ = push_env
    monkeypatch.delenv("MP_SANDBOX")
    assert main(["push", str(e), "--branch", "main"]) == 2


def test_push_allows_repo_credential_keys(push_env, monkeypatch, capfd):
    from toolkit.cli import main

    e, target = push_env
    git("-C", str(target), "config", "credential.helper", "")
    git("-C", str(target), "config", "credential.useHttpPath", "true")
    monkeypatch.setattr(
        push_cmd,
        "run_git",
        lambda argv, env, cwd: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    assert main(["push", str(e), "--branch", "main"]) == 0


def test_push_needs_github_mode_and_ref(
    tmp_path, home, make_engagement, monkeypatch, capfd
):
    from toolkit.cli import main

    monkeypatch.setenv("MP_SANDBOX", "mp-data")
    e = make_engagement(tmp_path, target={"mode": "local", "repo": None})
    assert main(["push", str(e), "--branch", "main"]) == 2
    e = make_engagement(tmp_path)
    assert main(["push", str(e), "--branch", "main"]) == 2


def test_gh_runs_allowed_and_puts_stdout_in_messages(push_env, monkeypatch, capfd):
    from toolkit.cli import main

    e, _target = push_env
    seen = {}

    def fake(argv, env, cwd):
        seen.update(argv=argv, env=env, cwd=cwd)
        assert Path(env["GH_CONFIG_DIR"]).is_dir() and not any(
            Path(env["GH_CONFIG_DIR"]).iterdir()
        )
        return subprocess.CompletedProcess(
            argv, 0, "https://github.com/example-org/example-repo/pull/1\n" * 60, ""
        )

    monkeypatch.setattr(gh_cmd, "run_gh", fake)
    assert main(["gh", str(e), "--", "pr", "list", "--json", "number"]) == 0
    out = capfd.readouterr().out
    s = json.loads(out)
    jsonschema.validate(s, SCHEMA)
    assert len(s["messages"]) == 50
    assert seen["argv"] == ["gh", "pr", "list", "--json", "number"]
    assert (
        seen["env"]["GH_TOKEN"] == TOKEN
        and seen["env"]["GH_REPO"] == "example-org/example-repo"
    )
    assert TOKEN not in out and not any(TOKEN in a for a in seen["argv"])


@pytest.mark.parametrize(
    "args",
    [
        ["pr", "list", "-R", "x/y"],
        ["pr", "list", "--repo=x/y"],
        ["repo", "view"],
        ["api", "user"],
        ["pr", "close", "1"],
        ["run", "rerun", "1"],
        ["secret", "list"],
    ],
)
def test_gh_refusals(push_env, monkeypatch, capfd, args):
    from toolkit.cli import main

    e, _ = push_env
    called = []
    monkeypatch.setattr(gh_cmd, "run_gh", lambda *a: called.append(a))
    assert main(["gh", str(e), "--", *args]) == 2
    assert called == []


def test_gh_refuses_body_file_in_data_zone(push_env, monkeypatch, capfd, tmp_path):
    from toolkit.cli import main

    e, _ = push_env
    private = Path(load(e)["paths"]["private"])
    called = []
    monkeypatch.setattr(
        gh_cmd,
        "run_gh",
        lambda argv, env, cwd: (
            called.append(argv) or subprocess.CompletedProcess(argv, 0, "", "")
        ),
    )
    for args in (
        ["pr", "create", "--body-file", str(private / "datasets" / "x.md")],
        ["pr", "create", f"--body-file={private / 'logs' / 'x.log'}"],
        ["pr", "create", "-F", str(private / "scrub-map.json")],
        [
            "pr",
            "create",
            "--template",
            str(Path(os.environ["HOME"]) / ".config" / "modernization" / "data.env"),
        ],
        [
            "pr",
            "create",
            "-F",
            str(
                Path(os.environ["HOME"])
                / "engagements"
                / "c2026002"
                / "private"
                / "out"
                / "x"
            ),
        ],
    ):
        assert main(["gh", str(e), "--", *args]) == 2, args
    assert called == []
    # a design-zone file is fine
    assert (
        main(
            [
                "gh",
                str(e),
                "--",
                "pr",
                "create",
                "--body-file",
                str(private / "review" / "body.md"),
            ]
        )
        == 0
    )
    assert len(called) == 1


def test_gh_refuses_other_origin(push_env, monkeypatch, capfd):
    from toolkit.cli import main

    e, target = push_env
    git(
        "-C",
        str(target),
        "remote",
        "set-url",
        "origin",
        "https://github.com/other/repo",
    )
    monkeypatch.setattr(gh_cmd, "run_gh", lambda *a: pytest.fail("ran gh"))
    assert main(["gh", str(e), "--", "pr", "list"]) == 2


# ------------------------------------------------------------------ bash half (scripts/mp-data)


@pytest.fixture
def stub_uv(tmp_path):
    stubbin = tmp_path / "stubbin"
    stubbin.mkdir(exist_ok=True)
    record = tmp_path / "uv-calls.txt"
    (stubbin / "uv").write_text(
        textwrap.dedent(
            f"""\
            #!/bin/bash
            {{ printf 'cwd=%s\\n' "$PWD"; printf 'slot=%s\\n' "$MP_DB_SUFFIX"; printf 'cache=%s\\n' "${{UV_CACHE_DIR:-}}"; printf 'arg=%s\\n' "$@"; }} > {record}
            echo '{{"status":"ok","command":"stub","exit_code":0}}'
            """
        )
    )
    (stubbin / "uv").chmod(0o755)
    return record


def mp_data(*args, env=None, stdin=subprocess.DEVNULL):
    return subprocess.run(
        [str(MP_DATA), *args],
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        stdin=stdin,
        timeout=60,
        check=False,
    )


def test_bash_unknown_command_and_flags(eng, stub_uv):
    for args in (
        ["bogus", str(eng)],
        ["--slot", "Bad-Slot", "parse", str(eng)],
        ["parse", str(eng), "--slot", "x"],
        ["parse", str(eng), "--if-built"],
        ["--nope", "parse", str(eng)],
        ["parse"],
    ):
        p = mp_data(*args)
        assert p.returncode == 2, (args, p.stdout, p.stderr)
        s = json.loads(p.stdout)
        jsonschema.validate(s, SCHEMA)
        assert s["status"] == "error"
    assert not stub_uv.exists()


def test_bash_bootstrap_runs_from_pipeline_with_slot(eng, stub_uv, tmp_path):
    p = mp_data("--slot", "s0_x", "parse", str(eng), "--strict", "-k", "[orders::")
    assert p.returncode == 0, p.stderr
    calls = stub_uv.read_text().splitlines()
    assert f"cwd={tmp_path / 'pipeline'}" in calls
    assert "slot=s0_x" in calls
    args = [c[4:] for c in calls if c.startswith("arg=")]
    assert args[args.index("--project") + 1] == str(tmp_path / "pipeline")
    assert args[args.index("toolkit.dataplane") + 1 :] == [
        "parse",
        str(eng),
        "--strict",
        "-k",
        "[orders::",
    ]


def test_bash_default_slot(eng, stub_uv):
    assert mp_data("parse", str(eng)).returncode == 0
    assert "slot=orchestrator" in stub_uv.read_text().splitlines()


def test_bash_if_built(eng, stub_uv, tmp_path, make_engagement):
    p = mp_data("--if-built", "parse", str(eng))
    assert p.returncode == 0
    s = json.loads(p.stdout)
    jsonschema.validate(s, SCHEMA)
    assert s["status"] == "not-built" and s["command"] == "parse" and "log" not in s
    assert not stub_uv.exists()
    mod = tmp_path / "pipeline" / "toolkit" / "commands"
    mod.mkdir(parents=True)
    (mod / "parse.py").write_text("")
    assert mp_data("--if-built", "parse", str(eng)).returncode == 0
    assert stub_uv.exists()
    # delivery engagement: --if-built changes nothing (no pin -> exit 2)
    d = make_engagement(
        tmp_path / "d", bootstrap=False, data_policy={"execution": "local"}
    )
    p = mp_data("--if-built", "guard", str(d))
    assert p.returncode == 2


def test_bash_delivery_needs_pin(tmp_path, home, make_engagement, stub_uv):
    d = make_engagement(tmp_path, bootstrap=False, data_policy={"execution": "local"})
    p = mp_data("parse", str(d))
    assert p.returncode == 2
    assert (
        "no reviewed toolkit: run mp-data pin <sha>"
        in json.loads(p.stdout)["messages"][0]
    )
    sha = "a" * 40
    share = home / ".local" / "share" / "modernization"
    (share / f"toolkit@{sha}").mkdir(parents=True)
    (share / "toolkit.pin").write_text(sha + "\n")
    p = mp_data("parse", str(d))
    assert p.returncode == 0, p.stdout + p.stderr
    calls = stub_uv.read_text().splitlines()
    assert f"cwd={share / f'toolkit@{sha}'}" in calls
    assert f"cache={share / 'uv-cache'}" in calls
    assert "arg=--no-sync" in calls


def test_bash_missing_engagement(home, stub_uv, tmp_path):
    assert mp_data("parse", "--strict").returncode == 2
    assert mp_data("parse", str(tmp_path / "nope" / "engagement.json")).returncode == 2


def test_pin_refuses_without_terminal(home):
    p = mp_data("pin", "a" * 40)
    assert p.returncode == 2
    assert "terminal" in json.loads(p.stdout)["messages"][0]
    p = mp_data("pin", "--status")
    assert p.returncode == 1 and json.loads(p.stdout)["status"] == "failed"


# ------------------------------------------------------------------ real jail


PROBE = r"""
import json, os, socket, sys
checks = {}
def readable(p):
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        return None
def writable(p):
    try:
        with open(p, "w") as f:
            f.write("x")
        return True
    except OSError:
        return False
e = os.environ
checks["data_env_hidden"] = not readable(e["PROBE_DATA_ENV"])
checks["pass_store_hidden"] = readable(e["PROBE_PASS_FILE"]) is None
checks["other_private_hidden"] = readable(e["PROBE_OTHER_FILE"]) is None
s = socket.socket(socket.AF_UNIX)
try:
    s.connect(e["PROBE_UNIX_SOCK"]); checks["unix_socket_unreachable"] = False
except OSError:
    checks["unix_socket_unreachable"] = True
checks["design_zone_read_only"] = readable(e["PROBE_DESIGN_FILE"]) is not None and not writable(e["PROBE_DESIGN_FILE"])
checks["pipeline_read_only"] = not writable(os.path.join(e["PROBE_PIPELINE"], "probe.txt"))
checks["data_zone_writable"] = writable(os.path.join(e["PROBE_DATA_DIR"], "probe.txt"))
checks["tmpdir_writable"] = writable(os.path.join(e["TMPDIR"], "probe.txt"))
checks["out_parent_writable"] = writable(os.path.join(e["PROBE_OUT_PARENT"], "probe.txt"))
t = socket.create_connection(("127.0.0.1", int(e["PROBE_TCP_PORT"])), timeout=5)
checks["tcp_reachable"] = t.recv(2) == b"hi"
t.close()
failed = sorted(k for k, v in checks.items() if not v)
code = 1 if failed else 0
print(json.dumps({"status": "failed" if failed else "ok", "command": "parse", "exit_code": code, "messages": failed}))
sys.exit(code)
"""


@pytest.mark.skipif(
    os.environ.get("MP_SANDBOX") is not None,
    reason="a bwrap inside mp-run is refused (MP_SANDBOX is set)",
)
def test_real_jail(eng, home, tmp_path, monkeypatch, capfd):
    e = load(eng)
    private = Path(e["paths"]["private"])
    (private / "review").mkdir(parents=True)
    design = private / "review" / "design.txt"
    design.write_text("design")
    store = home / ".password-store"
    store.mkdir()
    (store / "entry.gpg").write_text("secret")
    other = home / "engagements" / "c2026002" / "private"
    other.mkdir(parents=True)
    (other / "x.txt").write_text("other engagement")
    xdg = Path(os.environ["XDG_RUNTIME_DIR"])
    usock = socket.socket(socket.AF_UNIX)
    usock.bind(str(xdg / "bus"))
    usock.listen(1)
    tcp = socket.socket()
    tcp.bind(("127.0.0.1", 0))
    tcp.listen(1)

    def serve():
        try:
            conn, _ = tcp.accept()
            conn.sendall(b"hi")
            conn.close()
        except OSError:
            pass

    threading.Thread(target=serve, daemon=True).start()
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE)
    out_parent = tmp_path / "pipeline" / "build"
    env = {
        "PROBE_DATA_ENV": str(home / ".config" / "modernization" / "data.env"),
        "PROBE_PASS_FILE": str(store / "entry.gpg"),
        "PROBE_OTHER_FILE": str(other / "x.txt"),
        "PROBE_UNIX_SOCK": str(xdg / "bus"),
        "PROBE_DESIGN_FILE": str(design),
        "PROBE_PIPELINE": str(tmp_path / "pipeline"),
        "PROBE_DATA_DIR": str(private / "datasets"),
        "PROBE_OUT_PARENT": str(out_parent),
        "PROBE_TCP_PORT": str(tcp.getsockname()[1]),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    try:
        code = dataplane.run(
            ["parse", str(eng), "--out", str(out_parent / "model.json")],
            owner_lister=no_owners,
            child=[sys.executable, "-I", str(probe)],
        )
    finally:
        usock.close()
        tcp.close()
    s = last_summary(capfd)
    assert code == 0, s
    assert s["messages"] == []
    assert design.read_text() == "design"
    assert not (tmp_path / "pipeline" / "probe.txt").exists()
    assert (private / "datasets" / "probe.txt").exists()
