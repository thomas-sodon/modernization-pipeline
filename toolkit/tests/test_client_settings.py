"""scripts/client-settings.sh (docs/contracts.md#client-session-settings)."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from .conftest import create_git_repo

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "client-settings.sh"
DATA = [
    "sources",
    "datasets",
    "workbook",
    "reference",
    "goldens",
    "corpus",
    "results",
    "logs",
    "assets",
]


def settings(
    home: Path, ident: str = "c2026001", piped: bool = False
) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    if piped:
        return subprocess.run(
            ["bash", "-s", "--", ident],
            input=SCRIPT.read_text(),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    return subprocess.run(
        [str(SCRIPT), ident], env=env, capture_output=True, text=True, check=False
    )


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    for e in ("c2026001", "c2026002"):
        (h / "engagements" / e / "private").mkdir(parents=True)
        (h / "engagements" / e / "record").mkdir(parents=True)
    return h


def test_settings_content(home):
    p = settings(home)
    assert p.returncode == 0, p.stderr
    s = json.loads(p.stdout)
    h = str(home)
    priv = f"/{h}/engagements/c2026001/private/"
    assert sorted(s) == ["disableAllHooks", "hooks", "permissions", "sandbox"]
    assert s["disableAllHooks"] is False
    assert s["sandbox"] == {"enabled": False}
    assert s["permissions"]["disableBypassPermissionsMode"] == "disable"
    assert s["permissions"]["allow"] == ["Bash(mp-run:*)", "Bash(mp-data:*)"]
    assert s["hooks"] == {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {"type": "command", "command": f"{h}/.local/bin/mp-bash-hook"}
                ],
            }
        ]
    }
    deny = set(s["permissions"]["deny"])
    for v in ("Read", "Edit", "Write"):
        for d in DATA:
            assert f"{v}({priv}{d}/**)" in deny
        assert f"{v}({priv}scrub-map.json)" in deny
        for sub in ("private", "record"):
            assert f"{v}(/{h}/engagements/c2026002/{sub}/**)" in deny
        assert f"{v}(/{h}/.config/modernization/data.env)" in deny
    for v in ("Edit", "Write"):
        for path in (
            f"{h}/.local/bin/**",
            f"{h}/.local/share/modernization/**",
            f"{h}/.config/modernization/**",
            f"{h}/.claude/**",
            f"{h}/.claude.json",
            f"{h}/engagements/c2026001/client-session.json",
        ):
            assert f"{v}(/{path})" in deny
    assert "Read(//proc/**)" in deny and "Monitor" in deny
    assert not [
        d
        for d in deny
        if re.search(r"/c2026001/(private/(out|review|synthetic)|record)", d)
    ]
    for d in deny:
        m = re.match(r"^(Read|Edit|Write)\((.*)\)$", d)
        if m:
            assert m.group(2).startswith("//")
            assert not re.search(r"[*?\[]", re.sub(r"/\*\*$", "", m.group(2)))
            assert "~" not in m.group(2)


def test_repo_rules_when_paths_local_exists(home, tmp_path):
    pipeline = create_git_repo(tmp_path, "pipeline")
    target = create_git_repo(tmp_path, "target")
    (home / "engagements" / "c2026001" / "record" / "paths.local.json").write_text(
        json.dumps(
            {"format_version": 1, "pipeline": str(pipeline), "target_repo": str(target)}
        )
    )
    s = json.loads(settings(home).stdout)
    deny = set(s["permissions"]["deny"])
    for v in ("Edit", "Write"):
        assert f"{v}(/{pipeline}/.claude/**)" in deny
        assert f"{v}(/{pipeline}/.mcp.json)" in deny
        for repo in (pipeline, target):
            assert f"{v}(/{repo}/.git/hooks/**)" in deny
            assert f"{v}(/{repo}/.git/config)" in deny
    # without paths.local.json there are no repo rules
    (home / "engagements" / "c2026001" / "record" / "paths.local.json").unlink()
    deny = set(json.loads(settings(home).stdout)["permissions"]["deny"])
    assert not any(".git/hooks" in d for d in deny)


def test_piped_form(home):
    p = settings(home, piped=True)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["permissions"]["allow"] == [
        "Bash(mp-run:*)",
        "Bash(mp-data:*)",
    ]


@pytest.mark.parametrize(
    "ident", ["C2026001", "c2026001;ls", "../x", "c2026009", "a" * 41]
)
def test_bad_or_missing_id_exits_2(home, ident):
    assert settings(home, ident).returncode == 2
