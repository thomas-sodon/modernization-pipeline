"""Adapter protocol of the equivalence harness (docs/contracts.md#adapter-protocol).

An adapter is any class with these methods, instantiated with no arguments by the plugin from the
`entrypoint` (`module:Class`) of adapter.toml:

- `load_dataset(directory)`: load a dataset directory (docs/contracts.md#dataset-directory). The
  plugin calls it once per process.
- `run_saved_query(unit_id, params) -> rows`: row tuples in the manifest's column order, as Python
  values (int, Decimal, float, str, bool, datetime, None). `params` maps names to canonical strings.
- `render_document(unit_id, key, params) -> dict`: the canonical fields of one document.

A unit nobody implemented raises `NotImplementedError`; `NotImplementedUnit` is the subclass the
Django adapter raises (`not implemented: <unit id>`). The outcome is `not-implemented`.

When the cell set-up fails (the adapter cannot be created, or `load_dataset` raises),
`describe_failure` turns the exception into design names only: the class names of the exception
chain (`__cause__` / `__context__`), and the first `table`, `column`, `constraint` and `code` found
on an exception of the chain (attributes an adapter may set, design names only) or on a database
driver's diagnostics (psycopg `diag.table_name`, `column_name`, `constraint_name`), plus the
five-character SQLSTATE. The exception message is never read, since it may quote a row's values.
"""

from __future__ import annotations

import importlib
import re
import tomllib
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

FORMAT_VERSION = 1


class NotImplementedUnit(NotImplementedError):
    """A unit the adapter does not implement (yet)."""

    def __init__(self, unit_id: str):
        super().__init__(f"not implemented: {unit_id}")
        self.unit_id = unit_id


@runtime_checkable
class Adapter(Protocol):
    def load_dataset(self, directory) -> None: ...

    def run_saved_query(self, unit_id: str, params: dict) -> list[tuple]: ...

    def render_document(self, unit_id: str, key: str, params: dict) -> dict: ...


class AdapterConfigError(Exception):
    """adapter.toml is missing or invalid (the message names the problem, never values)."""


def read_config(path) -> dict:
    """adapter.toml as a dict, checked for format_version 1 and an entrypoint."""
    path = Path(path)
    try:
        with open(path, "rb") as fh:
            doc = tomllib.load(fh)
    except FileNotFoundError:
        raise AdapterConfigError(f"{path} is missing") from None
    except tomllib.TOMLDecodeError:
        raise AdapterConfigError(f"{path} is not valid TOML") from None
    if doc.get("format_version") != FORMAT_VERSION:
        raise AdapterConfigError(f"{path}: unknown format_version")
    entry = doc.get("entrypoint")
    if not isinstance(entry, str) or ":" not in entry:
        raise AdapterConfigError(f"{path}: entrypoint must be 'module:Class'")
    dbs = doc.get("databases", {})
    if not isinstance(dbs, dict) or not all(isinstance(v, str) for v in dbs.values()):
        raise AdapterConfigError(f"{path}: [databases] maps db names to variable names")
    return doc


def app_slug(config: dict) -> str:
    """The app slug: the first dotted part of the entrypoint module."""
    return config["entrypoint"].split(":", 1)[0].split(".", 1)[0]


def load_entrypoint(spec: str) -> Any:
    """The class named by `module:Class`."""
    module, _, name = spec.partition(":")
    obj = importlib.import_module(module)
    for part in name.split("."):
        obj = getattr(obj, part)
    return obj


# ------------------------------------------------------------------ set-up failures

FAILURE_PHASES = ("adapter", "load_dataset", "setup")
FAILURE_NAMES = ("table", "column", "constraint", "code")
DIAG_NAMES = {
    "table": "table_name",
    "column": "column_name",
    "constraint": "constraint_name",
}
# a design name: a word character or "(" first, then word characters, spaces and . $ # - / ( ) ,
NAME_RE = re.compile(r"^[\w(][\w .$#/(),-]{0,127}$")
CLASS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
SQLSTATE_RE = re.compile(r"^[0-9A-Z]{5}$")
MAX_CHAIN = 6


def _exception_chain(exc: BaseException) -> list[BaseException]:
    out: list[BaseException] = []
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen and len(out) < MAX_CHAIN:
        seen.add(id(exc))
        out.append(exc)
        nxt = exc.__cause__
        if nxt is None and not exc.__suppress_context__:
            nxt = exc.__context__
        exc = nxt
    return out


def _name(value) -> str | None:
    return value if isinstance(value, str) and NAME_RE.fullmatch(value) else None


def _sqlstate(exc: BaseException) -> str | None:
    for value in (
        getattr(exc, "sqlstate", None),
        getattr(getattr(exc, "diag", None), "sqlstate", None),
    ):
        if isinstance(value, str) and SQLSTATE_RE.fullmatch(value):
            return value
    # pyodbc errors carry (sqlstate, message) in args; only the state is read
    args = getattr(exc, "args", ())
    if (
        type(exc).__module__.split(".")[0] == "pyodbc"
        and args
        and isinstance(args[0], str)
        and SQLSTATE_RE.fullmatch(args[0])
    ):
        return args[0]
    return None


def describe_failure(exc: BaseException, phase: str = "load_dataset") -> dict:
    """A set-up failure as design names, never the message text: {"phase", "exception" (the
    raised class), "chain" (class names, raised first), and when found "table", "column",
    "constraint", "code", "sqlstate"}."""
    chain = _exception_chain(exc)
    classes = [type(e).__name__ for e in chain]
    info: dict = {
        "phase": phase if phase in FAILURE_PHASES else "setup",
        "exception": classes[0],
        "chain": classes,
    }
    for e in chain:
        diag = getattr(e, "diag", None)
        for key in FAILURE_NAMES:
            if key in info:
                continue
            value = _name(getattr(e, key, None))
            if value is None and key in DIAG_NAMES and diag is not None:
                value = _name(getattr(diag, DIAG_NAMES[key], None))
            if value is not None:
                info[key] = value
        if "sqlstate" not in info:
            state = _sqlstate(e)
            if state is not None:
                info["sqlstate"] = state
    return info


def clean_failure(info) -> dict | None:
    """A failure description read back from a file (JUnit property), with every field checked
    again: unknown keys and anything that is not a design name are dropped."""
    if not isinstance(info, dict):
        return None
    chain = [c for c in (info.get("chain") or []) if isinstance(c, str)]
    chain = [c for c in chain if CLASS_RE.fullmatch(c)][:MAX_CHAIN]
    exception = info.get("exception")
    if not isinstance(exception, str) or not CLASS_RE.fullmatch(exception):
        exception = chain[0] if chain else "Exception"
    phase = info.get("phase")
    out: dict = {
        "phase": phase if phase in FAILURE_PHASES else "setup",
        "exception": exception,
        "chain": chain or [exception],
    }
    for key in FAILURE_NAMES:
        value = _name(info.get(key))
        if value is not None:
            out[key] = value
    state = info.get("sqlstate")
    if isinstance(state, str) and SQLSTATE_RE.fullmatch(state):
        out["sqlstate"] = state
    return out


def failure_text(info: dict) -> str:
    """`load_dataset failed: LoadError <- IntegrityError (table T, column C, constraint K, code X,
    sqlstate S)`: class and design names only."""
    info = clean_failure(info) or {"phase": "setup", "chain": ["Exception"]}
    names = [f"{k} {info[k]}" for k in (*FAILURE_NAMES, "sqlstate") if k in info]
    text = f"{info['phase']} failed: " + " <- ".join(info["chain"])
    return text + (f" ({', '.join(names)})" if names else "")
