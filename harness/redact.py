"""Redaction of test reports (docs/contracts.md#harness), always on for the engagement dataset.

A hookwrapper on `pytest_runtest_makereport` writes each report's longrepr and captured sections
to a log under `$EQUIVALENCE_PRIVATE/logs/`, then replaces the longrepr of a failed report with
`<nodeid>: <n> rows differ in columns [...]` (counts and column names only) and empties the
captured sections, so no value reaches the terminal, the JUnit file or the toolkit's output.
A skip keeps its reason only when it is `not-implemented: <unit>`.

The plugin also turns off warning capture for redacted runs (a warning message may quote values)
and runs with `-p no:cacheprovider`.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest

NOT_IMPLEMENTED = "not-implemented: "


class Redactor:
    """Registered by the plugin as `equivalence-redact` when redaction is on."""

    def __init__(self, log_dir, label: str, mismatches: dict):
        self.log_dir = Path(log_dir)
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        self.path = self.log_dir / f"{stamp}-equivalence-{label}-{os.getpid()}.log"
        self.mismatches = mismatches  # nodeid -> compare.Mismatch
        self._opened = False

    def _write(self, text: str) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if not self._opened:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.close(fd)
            self._opened = True
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(text)

    def write_report(self, report) -> None:
        parts = [f"=== {report.nodeid} [{report.when}] {report.outcome}\n"]
        if report.longrepr is not None:
            try:
                parts.append(str(report.longreprtext) + "\n")
            except Exception:  # noqa: BLE001
                parts.append(repr(report.longrepr) + "\n")
        for name, content in report.sections:
            parts.append(f"--- {name}\n{content}\n")
        self._write("".join(parts))

    def summary_line(self, nodeid: str) -> str:
        m = self.mismatches.get(nodeid)
        if m is None:
            return f"{nodeid}: 0 rows differ in columns [] (an error; details in the private log)"
        return f"{nodeid}: {m.message()}"

    def redact(self, report) -> None:
        if report.longrepr is not None or report.sections:
            self.write_report(report)
        if report.failed:
            report.longrepr = self.summary_line(report.nodeid)
        elif report.skipped and report.longrepr is not None:
            reason = ""
            if isinstance(report.longrepr, tuple) and len(report.longrepr) == 3:
                reason = str(report.longrepr[2])
            reason = reason.removeprefix("Skipped: ")
            if not reason.startswith(NOT_IMPLEMENTED) or "\n" in reason:
                reason = "skipped (details in the private log)"
            report.longrepr = ("equivalence", 0, reason)
        report.sections = []

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        report = outcome.get_result()
        self.redact(report)
