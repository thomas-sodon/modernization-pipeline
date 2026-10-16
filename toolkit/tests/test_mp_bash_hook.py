"""scripts/mp-bash-hook: the client-session PreToolUse hook for Bash (docs/contracts.md#mp-bash-hook)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "scripts" / "mp-bash-hook"
ENG = "/home/op/engagements/c2026001/record/engagement.json"
MESSAGE = "client session: run shell commands as mp-run --eng <eng> --slot <slot> -- '<command>', or a data-plane call mp-data ..."


def run_hook(payload) -> subprocess.CompletedProcess:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [str(HOOK)], input=data, capture_output=True, text=True, timeout=30, check=False
    )


def call(command: str) -> subprocess.CompletedProcess:
    return run_hook({"tool_name": "Bash", "tool_input": {"command": command}})


ALLOWED = [
    f"mp-run --eng {ENG} --slot s1_parse -- 'cd $HOME/sourcecode/x && uv run pytest -q | tail -5 && echo done'",
    f"mp-run --eng {ENG} --slot s1_parse -- 'grep -c '\\''needle'\\'' file.txt || true'",
    f"mp-run --eng {ENG} --slot s5_feature_orders -- 'cd /x/app && MP_ONLY_AREAS=orders uv run pytest -q 2>&1 | tail'",
    f"mp-data --slot s5_review_r1 equivalence {ENG} --adapter django --datasets all -k '[orders::' --only-areas orders --allow-empty",
    f"mp-data --slot s0_prepare --if-built fetch {ENG} --verify --record-if-unset",
    f"mp-data fetch {ENG} --verify",
    "mp-data pin --status",
    "mp-data --slot x pin --status",
]

BLOCKED = [
    "ls",
    "ls -la /home",
    f"mp-data fetch {ENG} | cat",
    f"mp-data fetch {ENG} && ls",
    f"mp-data fetch {ENG}; ls",
    f"mp-data fetch {ENG} &",
    f"mp-data fetch $(cat {ENG})",
    'mp-data fetch "$(cat x)"',
    "mp-data fetch `cat x`",
    'mp-data fetch "`cat x`"',
    f"mp-data fetch {ENG} > out.txt",
    f"mp-data fetch {ENG} 2>&1",
    f"mp-data fetch {ENG} < input",
    f"FOO=1 mp-data fetch {ENG}",
    f"mp-data fetch {ENG}\nls",
    f"mp-data fetch {ENG}\rls",
    f"(mp-data fetch {ENG})",
    f"{{ mp-data fetch {ENG}; }}",
    f"/home/op/.local/bin/mp-data fetch {ENG}",
    f"mp-run --eng {ENG} --slot s1 -- 'ls' extra",
    f"mp-run --eng {ENG} --slot s1 'ls'",
    f"mp-run --slot s1 --eng {ENG} -- 'ls'",
    f"mp-run --eng {ENG} --slot s1 -- 'unbalanced",
    f'mp-data fetch "{ENG}',
    "mp-data pin 0123456789abcdef0123456789abcdef01234567",
    "mp-data --slot x pin abc",
    "mp-data fetch $HOME/x/engagement.json",
    f"env mp-data fetch {ENG}",
    "bash -c 'mp-data fetch x'",
    "",
]


@pytest.mark.parametrize("command", ALLOWED)
def test_allowed(command):
    p = call(command)
    assert p.returncode == 0, p.stderr
    assert p.stdout == "" and p.stderr == ""


@pytest.mark.parametrize("command", BLOCKED)
def test_blocked(command):
    p = call(command)
    assert p.returncode == 2
    assert p.stderr.strip() == MESSAGE


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "{}",
        json.dumps({"tool_input": {}}),
        json.dumps({"tool_input": {"command": 5}}),
    ],
)
def test_unparsable_input_blocked(payload):
    p = run_hook(payload)
    assert p.returncode == 2
    assert MESSAGE in p.stderr


def test_shebang_is_isolated_python():
    assert HOOK.read_text().splitlines()[0] == "#!/usr/bin/python3 -I"
