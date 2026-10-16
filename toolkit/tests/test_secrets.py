"""toolkit.secrets: env: and pass: refs outside the jail, MP_SECRET_<FIELD> inside it."""

from __future__ import annotations

import builtins
import os

import pytest

from toolkit import secrets

VALUE = "synthetic-token-value-1234"


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".config" / "modernization").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("MP_SANDBOX", raising=False)
    return h


def write_data_env(home, text):
    p = home / ".config" / "modernization" / "data.env"
    p.write_text(text, encoding="utf-8")
    p.chmod(0o600)
    return p


def test_field_env_name():
    assert (
        secrets.field_env_name("secrets.github_push.ref")
        == "MP_SECRET_SECRETS_GITHUB_PUSH_REF"
    )
    assert (
        secrets.field_env_name("source.backends[0].password_ref")
        == "MP_SECRET_SOURCE_BACKENDS_0_PASSWORD_REF"
    )
    assert (
        secrets.field_env_name("secrets.stage1_target_url_ref")
        == "MP_SECRET_SECRETS_STAGE1_TARGET_URL_REF"
    )


def test_env_ref_reads_data_env_not_process_env(home, monkeypatch, capfd):
    write_data_env(
        home, f"export OTHER=x\nexport GITHUB_PUSH_C2026001={VALUE}\n# comment\n"
    )
    monkeypatch.setenv("GITHUB_PUSH_C2026001", "from-process-env")
    assert (
        secrets.resolve("env:GITHUB_PUSH_C2026001", "secrets.github_push.ref") == VALUE
    )
    out, err = capfd.readouterr()
    assert VALUE not in out + err


def test_env_ref_missing_name_and_file(home):
    with pytest.raises(secrets.SecretError) as exc:
        secrets.resolve("env:NOPE", "secrets.github_push.ref")
    assert "secrets.github_push.ref" in str(exc.value)
    write_data_env(home, "export OTHER=x\n")
    with pytest.raises(secrets.SecretError) as exc:
        secrets.resolve("env:NOPE", "secrets.github_push.ref")
    assert (
        "secrets.github_push.ref" in str(exc.value)
        and "x" not in str(exc.value).split(":")[-1]
    )


def test_pass_ref_through_stub(home, tmp_path, monkeypatch, capfd):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "pass"
    stub.write_text(
        "#!/bin/sh\n"
        '[ "$1" = show ] || exit 9\n'
        'case "$2" in clients/c1/token) printf "%s\\nsecond line\\n" "'
        + VALUE
        + '";; *) echo "not in store" >&2; exit 1;; esac\n'
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    assert secrets.resolve("pass:clients/c1/token", "secrets.github_push.ref") == VALUE
    with pytest.raises(secrets.SecretError) as exc:
        secrets.resolve("pass:clients/c1/missing", "secrets.stage1_target_url_ref")
    assert "secrets.stage1_target_url_ref" in str(exc.value)
    out, err = capfd.readouterr()
    assert VALUE not in out + err


def test_in_jail_reads_only_mp_secret(home, monkeypatch):
    write_data_env(home, f"export GITHUB_PUSH_C2026001={VALUE}-file\n")
    monkeypatch.setenv("MP_SANDBOX", "mp-data")
    monkeypatch.setenv("MP_SECRET_SECRETS_GITHUB_PUSH_REF", VALUE)
    real_open = builtins.open

    def guarded_open(path, *a, **k):
        assert "data.env" not in str(path), (
            "data.env must not be opened inside the jail"
        )
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", guarded_open)
    assert (
        secrets.resolve("env:GITHUB_PUSH_C2026001", "secrets.github_push.ref") == VALUE
    )
    monkeypatch.delenv("MP_SECRET_SECRETS_GITHUB_PUSH_REF")
    with pytest.raises(secrets.SecretError) as exc:
        secrets.resolve("env:GITHUB_PUSH_C2026001", "secrets.github_push.ref")
    assert "MP_SECRET_SECRETS_GITHUB_PUSH_REF" in str(exc.value)


@pytest.mark.parametrize(
    "ref", ["file:/etc/passwd", "env:lower", "vault:x", "", "pass:"]
)
def test_unknown_scheme_exits_2(home, ref, quiet, make_engagement, tmp_path, capfd):
    with pytest.raises(secrets.SecretError) as exc:
        secrets.resolve(ref, "secrets.github_push.ref")
    assert exc.value.exit_code == 2
    assert "secrets.github_push.ref" in str(exc.value)


def test_secret_error_through_cli_is_exit_2_without_value(
    home, tmp_path, monkeypatch, capfd, make_engagement
):
    """push resolves the token; a missing MP_SECRET_ variable in the jail exits 2 naming the field."""
    from toolkit.cli import main

    eng = make_engagement(
        tmp_path,
        secrets={"github_push": {"ref": "env:TOKEN_X", "scope": None, "expires": None}},
    )
    monkeypatch.setenv("MP_SANDBOX", "mp-data")
    monkeypatch.setenv("MP_QUIET", "1")
    import subprocess

    target = tmp_path / "target"
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(target),
            "remote",
            "add",
            "origin",
            "https://github.com/example-org/example-repo",
        ],
        check=True,
    )
    code = main(["push", str(eng), "--branch", "main"])
    out = capfd.readouterr().out
    assert code == 2
    assert "secrets.github_push.ref" in out
