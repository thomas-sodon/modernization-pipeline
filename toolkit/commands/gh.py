"""Run an allowed GitHub CLI command in a client target repo (only through `mp-data gh`)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from toolkit import quiet
from toolkit.cli import UsageError
from toolkit.commands.push import check_origin, require_data_plane, token
from toolkit.engagement import data_zone, load

ALLOWED = {"pr": {"create", "view", "list", "checks", "merge"}, "run": {"list", "view"}}
FILE_FLAGS = ("--body-file", "-F", "--template")
MAX_LINES = 50
MAX_LEN = 200


def add_arguments(parser) -> None:
    parser.add_argument("engagement", help="<records>/engagement.json")
    parser.add_argument(
        "gh_args", nargs=argparse.REMAINDER, help="-- <pr|run> <action> [args]"
    )


def _file_args(args: list[str]) -> list[str]:
    out = []
    for i, a in enumerate(args):
        for flag in FILE_FLAGS:
            if a == flag and i + 1 < len(args):
                out.append(args[i + 1])
            elif flag.startswith("--") and a.startswith(flag + "="):
                out.append(a[len(flag) + 1 :])
            elif (
                flag == "-F"
                and a.startswith("-F")
                and len(a) > 2
                and not a.startswith("--")
            ):
                out.append(a[2:])
    return out


def forbidden_file(path: str, eng: dict) -> bool:
    if path == "-":
        return False
    base = Path(eng["paths"]["target_repo"])
    p = Path(path)
    real = Path(os.path.realpath(p if p.is_absolute() else base / p))
    home = Path(os.environ.get("HOME", str(Path.home())))
    blocked = [Path(os.path.realpath(z)) for z in data_zone(eng["paths"]["private"])]
    blocked.append(
        Path(os.path.realpath(home / ".config" / "modernization" / "data.env"))
    )
    engagements = Path(os.path.realpath(home / "engagements"))
    own = Path(os.path.realpath(home / "engagements" / eng["id"]))
    for b in blocked:
        if real == b or b in real.parents:
            return True
    in_engagements = engagements in real.parents or real == engagements
    return in_engagements and not (real == own or own in real.parents)


def run_gh(argv: list[str], env: dict, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        env=env,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


def run(args) -> int:
    require_data_plane()
    rest = list(args.gh_args or [])
    if rest and rest[0] == "--":
        rest = rest[1:]
    if len(rest) < 2 or rest[0] not in ALLOWED or rest[1] not in ALLOWED[rest[0]]:
        raise UsageError("allowed: pr create|view|list|checks|merge, run list|view")
    for a in rest:
        if (
            a in ("-R", "--repo")
            or a.startswith("--repo=")
            or (a.startswith("-R") and not a.startswith("--"))
        ):
            raise UsageError(
                "-R/--repo is not allowed; GH_REPO is the engagement's target.repo"
            )
    eng = load(args.engagement)
    repo = check_origin(eng)
    for f in _file_args(rest):
        if forbidden_file(f, eng):
            raise UsageError(
                "a file argument points into the data zone, data.env or another engagement"
            )
    secret = token(eng)
    tmp_root = os.environ.get("TMPDIR") or tempfile.gettempdir()
    config_dir = tempfile.mkdtemp(prefix="gh-config-", dir=tmp_root)
    env = {k: v for k, v in os.environ.items() if not k.startswith("MP_SECRET_")}
    env.update(
        {
            "GH_REPO": eng["target"]["repo"],
            "GH_CONFIG_DIR": config_dir,
            "GH_TOKEN": secret,
            "GH_PROMPT_DISABLED": "1",
            "GH_NO_UPDATE_NOTIFIER": "1",
            "NO_COLOR": "1",
        }
    )
    env.pop("GITHUB_TOKEN", None)
    proc = run_gh(["gh", *rest], env, repo)
    if proc.stderr:
        print(proc.stderr.replace(secret, "<SECRET>"), end="", file=sys.stderr)
    lines = [
        ln.replace(secret, "<SECRET>")[:MAX_LEN]
        for ln in (proc.stdout or "").splitlines()
    ]
    code = proc.returncode
    return quiet.summary(
        None,
        "gh",
        code if code in (0, 1) else 2,
        engagement=eng["id"],
        messages=lines[:MAX_LINES],
    )
