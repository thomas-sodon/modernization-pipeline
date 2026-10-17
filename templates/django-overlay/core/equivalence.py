"""DjangoAdapter: the app as seen by the vendored equivalence harness
(docs/contracts.md#adapter-protocol; adapter.toml entrypoint
"<slug>.core.equivalence:DjangoAdapter").

- load_dataset(dir): flushes the legacy tables and loads the dataset through the
  import_dataset loader (core/loader.py), inside the harness's test database.
- run_saved_query(unit_id, params): rows of a @saved_query unit, in the manifest's
  column order.
- render_document(unit_id, key, params): the canonical fields of a @document unit.
- Units nobody registered raise equivalence.adapter.NotImplementedUnit
  ("not implemented: <unit id>"), which the harness records as not-implemented.

The harness instantiates the adapter in a session fixture that does not request
pytest-django's database fixtures. When pytest-django blocks database access, the
adapter creates the test database itself (Django's setup_databases, named by
config/settings/test.py: <MP_TEST_DB_PREFIX><NAME>_<MP_DB_SUFFIX>), opens access
only for its own calls, and drops the database in close(). Inside a test that already
has database access (a db or transactional_db test) it uses that database as it is.

It reads only the database: never golden files, never the dataset's identity, and it
writes no files (under mp-data the app's tree is read-only).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING
from typing import Any

from django.db import DEFAULT_DB_ALIAS
from django.db import connections

from . import loader
from . import saved_queries

if TYPE_CHECKING:
    from collections.abc import Iterator


def pytest_db_blocker():
    """pytest-django's database blocker when it currently blocks access, else None.

    While blocked, BaseDatabaseWrapper.ensure_connection is the blocker's bound
    _blocking_wrapper, so the blocker is that method's __self__."""
    from django.db.backends.base.base import BaseDatabaseWrapper  # noqa: PLC0415

    owner = getattr(BaseDatabaseWrapper.ensure_connection, "__self__", None)
    if owner is not None and callable(getattr(owner, "unblock", None)):
        return owner
    return None


class DjangoAdapter:
    """Adapter of the Django app for the equivalence harness."""

    def __init__(self, using: str = DEFAULT_DB_ALIAS):
        self.using = using
        self._blocker = None
        self._old_config = None

    # -------------------------------------------------------------- database

    @contextmanager
    def _access(self) -> Iterator[None]:
        if self._blocker is None:
            self._blocker = pytest_db_blocker()
        if self._blocker is None:
            yield
            return
        with self._blocker.unblock():
            yield

    def _setup_test_database(self) -> None:
        """Create the test database when the harness runs under pytest-django."""
        if self._old_config is not None or pytest_db_blocker() is None:
            return
        from django.test.utils import setup_databases  # noqa: PLC0415

        with self._access():
            self._old_config = setup_databases(
                verbosity=0,
                interactive=False,
                aliases={self.using},
                serialized_aliases=set(),
            )

    def close(self) -> None:
        """Drop the test database this adapter created (the harness calls it)."""
        if self._old_config is None:
            return
        from django.test.utils import teardown_databases  # noqa: PLC0415

        with self._access():
            teardown_databases(self._old_config, verbosity=0)
        self._old_config = None
        connections[self.using].close()

    # -------------------------------------------------------------- protocol

    def load_dataset(self, directory) -> loader.LoadResult:
        self._setup_test_database()
        with self._access():
            return loader.load_dataset(directory, flush=True, using=self.using)

    def run_saved_query(self, unit_id: str, params: dict[str, str]) -> list[tuple]:
        with self._access():
            return saved_queries.run_saved_query(unit_id, params)

    def render_document(
        self,
        unit_id: str,
        key: str,
        params: dict[str, str],
    ) -> dict[str, Any]:
        with self._access():
            return saved_queries.render_document(unit_id, key, params)
