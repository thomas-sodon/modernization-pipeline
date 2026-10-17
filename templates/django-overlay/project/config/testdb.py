"""Test-database names (one helper for config/settings/test.py and app/conftest.py).

Every TEST NAME becomes <prefix><NAME>_<suffix>, or <prefix><NAME> when the suffix
is empty. prefix is MP_TEST_DB_PREFIX (default "test_"; mp-data sets "mpdata_test_"),
suffix is MP_DB_SUFFIX (the agent's slot). A name over 63 characters becomes its first
54 characters, "_" and 8 hex characters of its sha1, so with a db_name of at most 41
characters the cut keeps the prefix, the db_name and the "_" after it. The result
depends only on NAME, so applying the helper twice gives the same name.
"""

from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

MAX_LEN = 63
KEEP = 54
DEFAULT_PREFIX = "test_"


def make_test_db_name(name: str, prefix: str, suffix: str) -> str:
    full = f"{prefix}{name}_{suffix}" if suffix else f"{prefix}{name}"
    if len(full) > MAX_LEN:
        digest = hashlib.sha1(full.encode("utf-8")).hexdigest()[:8]  # noqa: S324
        full = f"{full[:KEEP]}_{digest}"
    return full


def prefix_and_suffix(environ_: Mapping[str, str] | None = None) -> tuple[str, str]:
    environ_ = os.environ if environ_ is None else environ_
    prefix = environ_.get("MP_TEST_DB_PREFIX") or DEFAULT_PREFIX
    suffix = environ_.get("MP_DB_SUFFIX") or ""
    return prefix, suffix


def apply_test_db_names(databases: dict, prefix: str, suffix: str) -> dict:
    for config in databases.values():
        config.setdefault("TEST", {})["NAME"] = make_test_db_name(
            str(config["NAME"]),
            prefix,
            suffix,
        )
    return databases
