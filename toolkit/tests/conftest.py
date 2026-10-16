"""Shared fixtures for toolkit tests (synthetic fixtures only; no client or Microsoft content).

- make_engagement(tmp_path, paths=None, **fields) writes <records>/engagement.json and
  paths.local.json under tmp_path and returns the engagement.json path. Fields override the
  defaults (dicts are merged one level deep). Layout: HOME is tmp_path/home, the private directory
  is HOME/engagements/<id>/private, records are tmp_path/pipeline/engagements/<id>.
- make_git_repo(tmp_path, name='repo', origin=None) creates a git repo with one commit on main.
- quiet sets MP_QUIET=1.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

PIPELINE = Path(__file__).resolve().parents[2]

DEFAULT_ENGAGEMENT = {
    "id": "demo-eng",
    "client": "Synthetic test engagement",
    "bootstrap": True,
    "db_name": None,
    "source": {
        "kind": "accdb",
        "path": "{{p.private}}/sources/app.accdb",
        "bytes": None,
        "sha256": None,
        "data_copy": None,
        "data_copy_sha256": None,
        "saved_with_data": True,
        "compiled_only": False,
        "layout": "single-file",
        "backends": [],
        "external_links": [],
        "licence": "synthetic",
    },
    "target": {
        "repo": "example-org/example-repo",
        "root": "app",
        "mode": "github",
        "local_path": None,
    },
    "records": None,
    "app": {
        "slug": "demoapp",
        "project_name": "Demo",
        "timezone": "UTC",
        "use_tz": False,
    },
    "database": {"primary": "postgresql", "verify_also": ["mssql"], "stage1": None},
    "auth": {"mode": "local"},
    "hosting": {"provider": "none", "managed_by": "us"},
    "data_policy": {
        "redact": "always",
        "execution": "cloud-allowed",
        "scrub_design": True,
        "commit_data": False,
        "ai_terms": None,
        "client_consent": None,
        "retention_days_after_acceptance": None,
    },
    "secrets": {
        "github_push": None,
        "accdb_password_ref": None,
        "stage1_target_url_ref": None,
        "staging_db_url_ref": None,
        "production_db_url_ref": None,
        "entra": None,
    },
    "scope": {"areas_hint": [], "must_keep": [], "foundation": []},
    "assets": {"screenshots": None},
    "commercial": {"tier": "demo", "quote_is_simulated": True, "milestones": []},
    "care_plan": None,
    "environment": {
        "windows_box": {
            "available": False,
            "host": None,
            "access_build": None,
            "client_data_allowed": False,
            "disk_encrypted": None,
            "copies": [],
        }
    },
    "expected_inventory": {},
}


def write_engagement(tmp_path: Path, paths: dict | None = None, **fields) -> Path:
    eng = copy.deepcopy(DEFAULT_ENGAGEMENT)
    for key, value in fields.items():
        if isinstance(value, dict) and isinstance(eng.get(key), dict):
            eng[key] = {**eng[key], **value}
        else:
            eng[key] = value
    ident = eng["id"]
    home = tmp_path / "home"
    p = {
        "pipeline": str(tmp_path / "pipeline"),
        "target_repo": str(tmp_path / "target"),
        "target": str(tmp_path / "target" / eng["target"].get("root", "app")),
        "private": str(home / "engagements" / ident / "private"),
        "records": str(tmp_path / "pipeline" / "engagements" / ident),
        "env_file": str(home / ".config" / "modernization" / "env"),
    }
    p.update(paths or {})
    records = Path(p["records"])
    records.mkdir(parents=True, exist_ok=True)
    for key in ("pipeline", "target_repo", "private"):
        Path(p[key]).mkdir(parents=True, exist_ok=True)
    (records / "engagement.json").write_text(
        json.dumps(eng, indent=2) + "\n", encoding="utf-8"
    )
    (records / "paths.local.json").write_text(
        json.dumps({"format_version": 1, **p}, indent=2) + "\n", encoding="utf-8"
    )
    return records / "engagement.json"


@pytest.fixture
def make_engagement():
    return write_engagement


def git(*args, cwd=None, check=True) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=check
    )


def create_git_repo(
    tmp_path: Path, name: str = "repo", origin: str | None = None
) -> Path:
    repo = tmp_path / name
    repo.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "main", str(repo))
    (repo / "README.md").write_text("synthetic\n", encoding="utf-8")
    git("-C", str(repo), "add", "README.md")
    git("-C", str(repo), "commit", "-q", "-m", "init")
    if origin:
        git("-C", str(repo), "remote", "add", "origin", origin)
    return repo


@pytest.fixture
def make_git_repo():
    return create_git_repo


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setenv("MP_QUIET", "1")
    return True
