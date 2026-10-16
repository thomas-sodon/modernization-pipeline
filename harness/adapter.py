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
"""

from __future__ import annotations

import importlib
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
