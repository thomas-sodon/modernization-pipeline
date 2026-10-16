"""Push a branch of a client target repo to origin (only through `mp-data push`)."""

from __future__ import annotations

import os
import re
import subprocess
import sys

from toolkit import quiet
from toolkit.cli import UsageError
from toolkit.engagement import load
from toolkit.secrets import field_env_name, resolve

BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
TOKEN_FIELD = "secrets.github_push.ref"
TOKEN_VAR = field_env_name(TOKEN_FIELD)
# Repo config keys that could send the push (and the token) somewhere else.
FORBIDDEN_KEYS = (
    re.compile(r"^url\..*\.(insteadof|pushinsteadof)$", re.IGNORECASE),
    re.compile(r"^remote\.origin\.proxy$", re.IGNORECASE),
    re.compile(
        r"^http\.(.*\.)?(proxy|proxyauthmethod|proxysslcert|proxysslkey|proxysslcainfo|proxysslcertpasswordprotected)$",
        re.IGNORECASE,
    ),
    re.compile(r"^http\.(.*\.)?ssl[a-z]*$", re.IGNORECASE),
)
# The one-shot helper answers only for https://github.com, with the token from its environment.
HELPER = (
    '!f() { test "$1" = get || exit 0; p=; h=; '
    'while IFS= read -r l; do case "$l" in protocol=*) p=${l#protocol=};; host=*) h=${l#host=};; '
    '"") break;; esac; done; '
    'test "$p" = https && test "$h" = github.com || exit 0; '
    f"printf 'username=x-access-token\\npassword=%s\\n' \"${TOKEN_VAR}\"; }}; f"
)


def require_data_plane() -> None:
    if os.environ.get("MP_SANDBOX") != "mp-data":
        raise UsageError("runs only through mp-data (it resolves the push token)")


def git_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MP_SECRET_")}
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git_out(repo: str, *args) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        env=git_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode not in (0, 1):
        raise UsageError(
            f"git {args[0]} failed in the target repo (exit {proc.returncode})"
        )
    return [line for line in proc.stdout.splitlines() if line]


def check_origin(eng: dict) -> str:
    """The target repo's origin url and pushurl must be https://github.com/<target.repo>."""
    target = eng.get("target") or {}
    if target.get("mode") != "github":
        raise UsageError("target.mode is not github")
    repo_name = target.get("repo")
    if not repo_name:
        raise UsageError("target.repo is not set")
    repo = eng["paths"]["target_repo"]
    allowed = {f"https://github.com/{repo_name}", f"https://github.com/{repo_name}.git"}
    urls = _git_out(repo, "config", "--get-all", "remote.origin.url")
    pushurls = _git_out(repo, "config", "--get-all", "remote.origin.pushurl")
    if not urls:
        raise UsageError("target repo has no origin")
    for url in urls + pushurls:
        if url not in allowed:
            raise UsageError(
                f"origin url or pushurl is not https://github.com/{repo_name}"
            )
    return repo


def check_repo_config(repo: str) -> None:
    for key in _git_out(repo, "config", "--list", "--name-only"):
        for pattern in FORBIDDEN_KEYS:
            if pattern.match(key):
                raise UsageError(
                    f"target repo git config sets {key.split('.')[0]}.* key that redirects pushes"
                )


def token(eng: dict) -> str:
    gp = (eng.get("secrets") or {}).get("github_push") or {}
    ref = gp.get("ref") if isinstance(gp, dict) else None
    if not ref:
        raise UsageError("secrets.github_push.ref is not set")
    return resolve(ref, TOKEN_FIELD)


def run_git(argv: list[str], env: dict, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        env=env,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


def push_argv(branch: str) -> list[str]:
    return [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "credential.helper=",
        "-c",
        f"credential.helper={HELPER}",
        "push",
        "origin",
        f"refs/heads/{branch}:refs/heads/{branch}",
    ]


def add_arguments(parser) -> None:
    parser.add_argument("engagement", help="<records>/engagement.json")
    parser.add_argument(
        "--branch", required=True, help="local branch to push to origin <branch>"
    )


def run(args) -> int:
    require_data_plane()
    branch = args.branch
    if not BRANCH_RE.match(branch) or branch.startswith("-") or ".." in branch:
        raise UsageError("branch must match ^[A-Za-z0-9._/-]+$")
    eng = load(args.engagement)
    repo = check_origin(eng)
    check_repo_config(repo)
    secret = token(eng)
    heads = _git_out(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    if not heads:
        raise UsageError("no such local branch in the target repo")
    env = git_env()
    env[TOKEN_VAR] = secret
    proc = run_git(push_argv(branch), env, repo)
    for stream in (proc.stdout, proc.stderr):
        if stream:
            print(stream.replace(secret, "<SECRET>"), end="", file=sys.stderr)
    code = 0 if proc.returncode == 0 else 1
    return quiet.summary(
        None,
        "push",
        code,
        engagement=eng["id"],
        messages=[
            f"branch {branch}",
            f"commit {heads[0]}",
            "pushed" if code == 0 else "push failed; see the log",
        ],
    )
