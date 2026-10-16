"""scripts/mp-run (docs/contracts.md#mp-run). Needs bubblewrap (environment item bwrap).

It runs where mp-run is not already around it (S0 bootstrap acceptance, pipeline CI, the runbook's
first-session probe): Ubuntu's AppArmor profile for bwrap refuses a user namespace inside bwrap,
so with MP_SANDBOX=mp-run it skips."""

from __future__ import annotations

import os
import socket
import subprocess
import threading
from pathlib import Path

import pytest

from .conftest import create_git_repo, write_engagement

ROOT = Path(__file__).resolve().parents[2]
MP_RUN = ROOT / "scripts" / "mp-run"

pytestmark = pytest.mark.skipif(
    os.environ.get("MP_SANDBOX") == "mp-run",
    reason="inside mp-run: a nested bwrap is refused by AppArmor; run where mp-run is not around it",
)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cfg = home / ".config" / "modernization"
    cfg.mkdir(parents=True)
    (cfg / "env").write_text(
        f"export PATH={os.environ['PATH']}\nexport PG_URL=postgresql://mp_agent:mp-agent@127.0.0.1:55432\n"
    )
    (cfg / "data.env").write_text(
        "export MP_DATA_PG_URL=postgresql://postgres:never-seen@127.0.0.1:55432\n"
    )
    (cfg / "data.env").chmod(0o600)
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".claude").mkdir()
    pipeline = create_git_repo(tmp_path, "pipeline")
    (pipeline / ".claude").mkdir()
    (pipeline / ".claude" / "settings.json").write_text("{}")
    create_git_repo(tmp_path, "target")
    eng = write_engagement(
        tmp_path,
        paths={
            "pipeline": str(pipeline),
            "records": str(pipeline / "engagements" / "demo-eng"),
        },
    )
    private = home / "engagements" / "demo-eng" / "private"
    (private / "sources").mkdir(parents=True)
    (private / "sources" / "app.accdb").write_bytes(b"x" * 1000)
    (private / "scrub-map.json").write_text('{"<SECRET-1>": "value"}')
    (private / "out").mkdir()
    (private / "out" / "model.json").write_text("{}")
    other = home / "engagements" / "c2026002"
    (other / "private").mkdir(parents=True)
    (other / "private" / "x.txt").write_text("other")
    (other / "record").mkdir()
    (other / "record" / "engagement.json").write_text("{}")
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_RUNTIME_DIR": str(xdg),
    }
    env.pop("MP_DB_SUFFIX", None)
    return {
        "home": home,
        "eng": eng,
        "env": env,
        "pipeline": pipeline,
        "private": private,
        "xdg": xdg,
        "tmp": tmp_path,
    }


def mp_run(s, command, slot="probe_slot", cwd=None):
    return subprocess.run(
        [str(MP_RUN), "--eng", str(s["eng"]), "--slot", slot, "--", command],
        env=s["env"],
        cwd=cwd or s["tmp"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_data_zone_reads_empty(setup):
    p = mp_run(setup, f"cat {setup['private']}/sources/* 2>/dev/null | wc -c")
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "0"
    p = mp_run(setup, f"cat {setup['private']}/scrub-map.json")
    assert p.returncode != 0 and "value" not in p.stdout
    # the design zone stays readable
    p = mp_run(setup, f"cat {setup['private']}/out/model.json")
    assert p.returncode == 0 and p.stdout == "{}"


def test_writes_into_hidden_dirs_are_discarded(setup):
    p = mp_run(
        setup,
        f"echo hi > {setup['private']}/datasets/new.csv && cat {setup['private']}/datasets/new.csv",
    )
    assert p.returncode == 0
    assert not (setup["private"] / "datasets" / "new.csv").exists()


def test_other_engagement_hidden(setup):
    other = setup["home"] / "engagements" / "c2026002"
    p = mp_run(setup, f"ls -A {other}/private | wc -l; ls -A {other}/record | wc -l")
    assert p.stdout.split() == ["0", "0"]


def test_data_env_unreadable_despite_ro_bind_of_its_directory(setup):
    p = mp_run(setup, f"cat {setup['home']}/.config/modernization/data.env")
    assert p.returncode != 0
    assert "never-seen" not in p.stdout
    assert "Permission denied" in p.stderr
    # the directory itself is bound read-only (before the mask) and stays readable
    p = mp_run(setup, f"cat {setup['home']}/.config/modernization/env | wc -l")
    assert p.stdout.strip() == "2"


@pytest.mark.parametrize(
    "target",
    [
        "{home}/.local/bin/evil",
        "{pipeline}/.claude/settings.json",
        "{pipeline}/.git/hooks/pre-commit",
        "{pipeline}/.git/config",
        "{home}/.claude/x",
    ],
)
def test_protected_paths_read_only(setup, target):
    path = target.format(home=setup["home"], pipeline=setup["pipeline"])
    p = mp_run(setup, f"echo x >> {path}")
    assert p.returncode != 0
    assert not Path(path).exists() or "x\n" not in Path(path).read_text()


def test_docker_unreachable(setup):
    p = mp_run(setup, "docker info >/dev/null 2>&1")
    assert p.returncode != 0
    # the operator's rootless Docker socket: named by DOCKER_HOST outside the runtime dir (masked
    # with /dev/null), and inside the real runtime dir (hidden by its tmpfs)
    real_xdg = Path(f"/run/user/{os.getuid()}")
    sock = real_xdg / "docker.sock"
    if sock.exists():
        env = {**setup["env"], "DOCKER_HOST": f"unix://{sock}"}
        p = mp_run(
            {**setup, "env": env},
            f"DOCKER_HOST=unix://{sock} docker info >/dev/null 2>&1",
        )
        assert p.returncode != 0
        env = {**setup["env"], "XDG_RUNTIME_DIR": str(real_xdg)}
        p = mp_run(
            {**setup, "env": env},
            f"test -e {sock} || echo hidden; DOCKER_HOST=unix://{sock} docker info >/dev/null 2>&1",
        )
        assert p.returncode != 0 and p.stdout.strip() == "hidden"


def test_unix_socket_in_runtime_dir_unreachable(setup):
    sock_path = setup["xdg"] / "bus"
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(sock_path))
    srv.listen(1)
    try:
        p = mp_run(
            setup,
            f"python3 -c 'import socket; s=socket.socket(socket.AF_UNIX); s.connect(\"{sock_path}\")'",
        )
        assert p.returncode != 0
    finally:
        srv.close()


def test_loopback_tcp_reachable(setup):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        try:
            c, _ = srv.accept()
            c.sendall(b"ok")
            c.close()
        except OSError:
            pass

    threading.Thread(target=serve, daemon=True).start()
    try:
        p = mp_run(
            setup,
            f"python3 -c 'import socket; print(socket.create_connection((\"127.0.0.1\", {port}), timeout=5).recv(2).decode())'",
        )
        assert p.stdout.strip() == "ok", p.stderr
    finally:
        srv.close()


def test_env_and_cwd(setup):
    p = mp_run(
        setup,
        'echo "$MP_DB_SUFFIX $MP_SANDBOX ${DOCKER_HOST:-unset}"; pwd',
        slot="s1_test",
    )
    assert p.stdout.splitlines() == ["s1_test mp-run unset", str(setup["pipeline"])]
    p = mp_run(setup, f"cd {setup['tmp']} && pwd")
    assert p.stdout.strip() == str(setup["tmp"])


@pytest.mark.parametrize("code", [0, 1, 7])
def test_exit_codes_pass_through(setup, code):
    assert mp_run(setup, f"exit {code}").returncode == code


@pytest.mark.parametrize(
    "args",
    [
        ["--eng", "{eng}", "--slot", "ok"],
        ["--eng", "{eng}", "--slot", "Bad-Slot", "--", "true"],
        ["--eng", "{eng}", "--slot", "ok", "--", "true", "extra"],
        ["--slot", "ok", "--eng", "{eng}", "--", "true"],
        ["--eng", "/nonexistent/engagement.json", "--slot", "ok", "--", "true"],
    ],
)
def test_bad_forms_exit_2(setup, args):
    argv = [a.format(eng=setup["eng"]) for a in args]
    p = subprocess.run(
        [str(MP_RUN), *argv],
        env=setup["env"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert p.returncode == 2
