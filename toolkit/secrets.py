"""Secret references (docs/contracts.md#secret-refs; decisions L3.5, L12.2).

`resolve(ref, field)` returns the value of an engagement secret reference:
- outside mp-data's jail (toolkit/dataplane.py, the operator): `env:NAME` is the line
  `export NAME=value` of $HOME/.config/modernization/data.env (parsed, never sourced, never the
  process environment), and `pass:<path>` is the first line of `pass show <path>`;
- inside the jail (MP_SANDBOX=mp-data), where data.env and the pass store are hidden, only the
  variable MP_SECRET_<FIELD> that dataplane.py set for that field.

Failures raise SecretError (exit 2) naming the field, never the value. Values go only to the
caller in memory.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REF_RE = re.compile(r"^(env:[A-Z_][A-Z0-9_]*|pass:\S+)$")
ENV_LINE = re.compile(r"^export ([A-Z_][A-Z0-9_]*)=(.*)$")


class SecretError(Exception):
    exit_code = 2


def field_env_name(field: str) -> str:
    """secrets.github_push.ref -> MP_SECRET_SECRETS_GITHUB_PUSH_REF."""
    return "MP_SECRET_" + re.sub(r"[^A-Z0-9]+", "_", field.upper()).strip("_")


def data_env_path() -> Path:
    return (
        Path(os.environ.get("HOME", str(Path.home())))
        / ".config"
        / "modernization"
        / "data.env"
    )


def parse_env_file(path: Path) -> dict[str, str]:
    """`export NAME=value` lines, values literal (no quotes, no expansion)."""
    values = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = ENV_LINE.match(line.rstrip("\r\n"))
            if m:
                values[m.group(1)] = m.group(2)
    return values


def in_jail() -> bool:
    return os.environ.get("MP_SANDBOX") == "mp-data"


def resolve(ref: str, field: str) -> str:
    if not isinstance(ref, str) or not REF_RE.match(ref):
        raise SecretError(f"secret {field}: not an env:NAME or pass:<path> reference")
    if in_jail():
        name = field_env_name(field)
        value = os.environ.get(name)
        if value is None or value == "":
            raise SecretError(f"secret {field}: {name} is not set inside mp-data")
        return value
    scheme, _, rest = ref.partition(":")
    if scheme == "env":
        path = data_env_path()
        try:
            values = parse_env_file(path)
        except FileNotFoundError:
            raise SecretError(f"secret {field}: data.env is missing") from None
        except OSError as exc:
            raise SecretError(
                f"secret {field}: cannot read data.env ({type(exc).__name__})"
            ) from None
        value = values.get(rest)
        if value is None or value == "":
            raise SecretError(f"secret {field}: {rest} is not set in data.env")
        return value
    if scheme == "pass":
        try:
            proc = subprocess.run(
                ["pass", "show", rest],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise SecretError(
                f"secret {field}: pass is not available ({type(exc).__name__})"
            ) from None
        if proc.returncode != 0:
            raise SecretError(
                f"secret {field}: pass show failed (exit {proc.returncode})"
            )
        first = proc.stdout.decode("utf-8", "replace").splitlines()
        value = first[0] if first else ""
        if not value:
            raise SecretError(f"secret {field}: pass entry is empty")
        return value
    raise SecretError(f"secret {field}: unknown scheme")
