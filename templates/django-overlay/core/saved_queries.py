"""Registries of the equivalence units the app implements (docs/contracts.md#harness).

- `@saved_query("<unit id>")` registers a function `fn(params) -> rows` for a
  saved_query or formula_output unit: `params` maps parameter names to canonical
  strings (docs/contracts.md#canonical-output) and the rows are tuples in the
  manifest's column order, as Python values (int, Decimal, float, str, bool,
  datetime, None).
- `@document("<unit id>")` registers `fn(key, params) -> dict` for a document unit:
  the canonical fields of docs/contracts.md#documents for one document key.

Each feature area registers its own units in `<area>/saved_queries.py`, which
CoreConfig.ready() imports through autodiscover_modules("saved_queries"). A unit
nobody registered raises equivalence.adapter.NotImplementedUnit
("not implemented: <unit id>"), which the harness records as not-implemented.
Registered functions compute from the database only: they never read golden files
and never branch on the dataset.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.core.exceptions import ImproperlyConfigured

if TYPE_CHECKING:
    from collections.abc import Callable

try:
    from equivalence.adapter import NotImplementedUnit
except ModuleNotFoundError:  # pragma: no cover - the harness is not on sys.path

    class NotImplementedUnit(NotImplementedError):  # noqa: N818 (the harness's name)
        """Stand-in where the vendored harness is not importable (a deployed app
        started without the target root on sys.path); same message."""

        def __init__(self, unit_id: str):
            super().__init__(f"not implemented: {unit_id}")
            self.unit_id = unit_id


SAVED_QUERIES: dict[str, Callable[..., Any]] = {}
DOCUMENTS: dict[str, Callable[..., Any]] = {}


def _same_function(a: Callable[..., Any], b: Callable[..., Any]) -> bool:
    """Whether a and b are one function imported twice (module reloads)."""
    return (a.__module__, a.__qualname__) == (b.__module__, b.__qualname__)


def _register(
    registry: dict[str, Callable[..., Any]],
    kind: str,
    unit_id: str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    if not isinstance(unit_id, str) or not unit_id.strip():
        msg = f"@{kind} needs a unit id"
        raise ImproperlyConfigured(msg)

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        known = registry.get(unit_id)
        if known is not None and not _same_function(known, fn):
            msg = (
                f"unit {unit_id} is registered twice "
                f"({known.__module__}.{known.__qualname__} and "
                f"{fn.__module__}.{fn.__qualname__})"
            )
            raise ImproperlyConfigured(msg)
        registry[unit_id] = fn
        return fn

    return decorator


def saved_query(unit_id: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register `fn(params) -> rows` for a saved_query or formula_output unit."""
    return _register(SAVED_QUERIES, "saved_query", unit_id)


def document(unit_id: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register `fn(key, params) -> dict` for a document unit."""
    return _register(DOCUMENTS, "document", unit_id)


def registered_units() -> dict[str, str]:
    """Every registered unit id with its registry ("saved_query" or "document")."""
    out = dict.fromkeys(SAVED_QUERIES, "saved_query")
    out.update(dict.fromkeys(DOCUMENTS, "document"))
    return dict(sorted(out.items()))


def run_saved_query(unit_id: str, params: dict[str, str] | None = None) -> list[tuple]:
    """The rows of a registered saved_query or formula_output unit."""
    fn = SAVED_QUERIES.get(unit_id)
    if fn is None:
        raise NotImplementedUnit(unit_id)
    return [tuple(row) for row in fn(dict(params or {}))]


def render_document(
    unit_id: str,
    key: str,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The canonical fields of one document of a registered document unit."""
    fn = DOCUMENTS.get(unit_id)
    if fn is None:
        raise NotImplementedUnit(unit_id)
    return dict(fn(str(key), dict(params or {})))
