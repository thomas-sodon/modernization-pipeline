"""toolkit vendor-harness: copy harness/ into <target>/equivalence/, keep adapter.toml and
results.json, and --check for drift (docs/contracts.md#harness). Synthetic content only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from toolkit import cli
from toolkit.commands import vendor_harness as vh

ADAPTER = b'format_version = 1\nentrypoint = "widgets.core.equivalence:DjangoAdapter"\n'
RESULTS = b'{"format_version": 1, "cells": []}\n'


@pytest.fixture
def target(tmp_path, monkeypatch):
    monkeypatch.setenv("MP_QUIET", "1")
    t = tmp_path / "widgets"
    (t / "equivalence").mkdir(parents=True)
    (t / "equivalence" / "adapter.toml").write_bytes(ADAPTER)
    (t / "equivalence" / "results.json").write_bytes(RESULTS)
    return t


def run_cli(capfd, *argv) -> tuple[int, dict]:
    capfd.readouterr()
    rc = cli.main(["vendor-harness", *argv])
    out, _err = capfd.readouterr()
    return rc, json.loads(out.strip().splitlines()[-1])


def test_vendor_copies_every_harness_file_and_keeps_the_two_target_files(target, capfd):
    rc, s = run_cli(capfd, "--target", str(target))
    assert rc == 0
    dest = target / "equivalence"
    src = vh.harness_files(vh.SOURCE)
    for rel in (
        "plugin.py",
        "adapter.py",
        "compare.py",
        "redact.py",
        "README.md",
        "canonical.py",
        "dataset.py",
        "__init__.py",
        "tests/test_units.py",
    ):
        assert rel in src
        assert (dest / rel).read_bytes() == src[rel].read_bytes()
    assert (dest / "adapter.toml").read_bytes() == ADAPTER
    assert (dest / "results.json").read_bytes() == RESULTS
    assert not list(dest.rglob("__pycache__"))
    assert s["counts"]["files"] == len(src)
    rc, s = run_cli(capfd, "--target", str(target), "--check")
    assert rc == 0


def test_check_reports_drift_and_writes_nothing(target, capfd):
    run_cli(capfd, "--target", str(target))
    dest = target / "equivalence"
    (dest / "compare.py").write_text("# edited in the target\n", encoding="utf-8")
    (dest / "tests" / "test_extra.py").write_text("", encoding="utf-8")
    (dest / "redact.py").unlink()
    snapshot = {p: p.read_bytes() for p in dest.rglob("*") if p.is_file()}
    rc, s = run_cli(capfd, "--target", str(target), "--check")
    assert rc == 1
    assert s["counts"]["differences"] == 3
    assert {
        "differs: compare.py",
        "missing: redact.py",
        "not in harness: tests/test_extra.py",
    } <= set(s["messages"])
    assert {p: p.read_bytes() for p in dest.rglob("*") if p.is_file()} == snapshot
    # vendoring again repairs the drift and removes the stray file
    rc, s = run_cli(capfd, "--target", str(target))
    assert rc == 0
    assert s["counts"]["removed"] == 1
    assert not (dest / "tests" / "test_extra.py").exists()
    rc, _ = run_cli(capfd, "--target", str(target), "--check")
    assert rc == 0
    assert (dest / "adapter.toml").read_bytes() == ADAPTER


def test_check_on_a_target_without_the_harness_fails(tmp_path, capfd, monkeypatch):
    monkeypatch.setenv("MP_QUIET", "1")
    (tmp_path / "t").mkdir()
    rc, _s = run_cli(capfd, "--target", str(tmp_path / "t"), "--check")
    assert rc == 1
    assert not (tmp_path / "t" / "equivalence").exists()
    rc, _s = run_cli(capfd, "--target", str(tmp_path / "missing"))
    assert rc == 2


def test_the_vendored_copy_imports_without_toolkit(target, capfd):
    run_cli(capfd, "--target", str(target))
    code = (
        "import sys\n"
        "sys.modules['toolkit'] = None\n"
        "import equivalence.plugin, equivalence.compare, equivalence.redact\n"
        "from equivalence.adapter import NotImplementedUnit\n"
        "assert issubclass(NotImplementedUnit, NotImplementedError)\n"
        "assert 'toolkit' not in [m.split('.')[0] for m in sys.modules if sys.modules[m]]\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=target,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    sources = [
        p.read_text(encoding="utf-8") for p in (target / "equivalence").glob("*.py")
    ]
    assert not any("import toolkit" in s or "from toolkit" in s for s in sources)


def test_harness_files_skips_caches(tmp_path):
    root = tmp_path / "h"
    (root / "__pycache__").mkdir(parents=True)
    (root / "__pycache__" / "x.cpython-313.pyc").write_bytes(b"")
    (root / "a.py").write_text("", encoding="utf-8")
    (root / "adapter.toml").write_text("", encoding="utf-8")
    assert sorted(vh.harness_files(root)) == ["a.py"]
    assert isinstance(vh.SOURCE, Path)
