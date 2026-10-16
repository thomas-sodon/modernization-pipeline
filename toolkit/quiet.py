"""Quiet output (docs/contracts.md#quiet-output, #summary-schema).

Under MP_QUIET=1 a command prints exactly one JSON line on stdout: its summary. Everything else
goes to stderr. Without MP_QUIET the same summary is printed as human-readable text.

`toolkit.cli` calls `begin()` before a command runs: under MP_QUIET=1 it points file descriptor 1
at stderr (so print() calls and subprocesses cannot write to stdout) and keeps the real stdout for
the summary. `summary()` writes the line once; `cli` writes an error summary at exit when a
command raised or ended without one.
"""

from __future__ import annotations

import json
import os
import sys

MAX_MESSAGES = 50
MAX_MESSAGE_LEN = 200

FIELDS = (
    "engagement",
    "counts",
    "rates",
    "cells",
    "units",
    "tables",
    "sha256s",
    "columns",
    "mismatches",
    "paths",
    "messages",
)

_state = {"fd": None, "emitted": None, "stdout": None}


def is_quiet() -> bool:
    return os.environ.get("MP_QUIET") == "1"


def begin() -> None:
    """Under MP_QUIET=1, send fd 1 to stderr and keep the real stdout for the summary."""
    if not is_quiet() or _state["fd"] is not None:
        return
    sys.stdout.flush()
    _state["fd"] = os.dup(1)
    os.dup2(2, 1)
    _state["stdout"] = sys.stdout
    sys.stdout = sys.stderr


def end() -> None:
    """Restore fd 1 (used by tests that call main() several times in one process)."""
    fd = _state["fd"]
    if fd is None:
        return
    sys.stdout.flush()
    if _state["stdout"] is not None:
        sys.stdout = _state["stdout"]
        _state["stdout"] = None
    os.dup2(fd, 1)
    os.close(fd)
    _state["fd"] = None


def reset() -> None:
    _state["emitted"] = None


def emitted() -> dict | None:
    return _state["emitted"]


def progress(message: str) -> None:
    """Progress and human detail: always stderr."""
    print(message, file=sys.stderr, flush=True)


def status_for(exit_code: int, *, recorded: bool = False, no_data: bool = False) -> str:
    if exit_code == 0:
        if recorded:
            return "recorded"
        if no_data:
            return "no-data"
        return "ok"
    if exit_code in (1, 5):
        return "failed"
    return "error"


def build(status: str | None, command: str, exit_code: int, **fields) -> dict:
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise TypeError(f"unknown summary fields: {sorted(unknown)}")
    if status is None:
        status = status_for(exit_code)
    out: dict = {"status": status, "command": command, "exit_code": int(exit_code)}
    for key in FIELDS:
        value = fields.get(key)
        if value is None:
            continue
        if key == "messages":
            value = [str(m)[:MAX_MESSAGE_LEN] for m in value][:MAX_MESSAGES]
        out[key] = value
    return out


def dumps(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _write_line(line: str) -> None:
    data = (line + "\n").encode("utf-8")
    fd = _state["fd"]
    if fd is not None:
        os.write(fd, data)
    else:
        sys.stdout.flush()
        sys.stdout.buffer.write(data)
        sys.stdout.flush()


def _human(obj: dict) -> str:
    lines = [f"{obj['command']}: {obj['status']} (exit {obj['exit_code']})"]
    for key in FIELDS:
        if key not in obj or key == "engagement":
            continue
        value = obj[key]
        if key == "messages":
            lines.extend(f"  {m}" for m in value)
        else:
            lines.append(
                f"  {key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}"
            )
    return "\n".join(lines)


def summary(
    status: str | None,
    command: str,
    exit_code: int,
    engagement: str | None = None,
    counts: dict | None = None,
    rates: dict | None = None,
    cells: list | None = None,
    sha256s: dict | None = None,
    columns: dict | None = None,
    tables: list | None = None,
    units: list | None = None,
    mismatches: list | None = None,
    paths: list | None = None,
    messages: list | None = None,
) -> int:
    """Print the summary (once) and return exit_code, so a command can `return summary(...)`."""
    if _state["emitted"] is not None:
        return exit_code
    obj = build(
        status,
        command,
        exit_code,
        engagement=engagement,
        counts=counts,
        rates=rates,
        cells=cells,
        sha256s=sha256s,
        columns=columns,
        tables=tables,
        units=units,
        mismatches=mismatches,
        paths=paths,
        messages=messages,
    )
    _state["emitted"] = obj
    if is_quiet():
        _write_line(dumps(obj))
    else:
        print(_human(obj), flush=True)
    return exit_code
