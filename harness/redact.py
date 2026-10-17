"""Redaction of test reports (docs/contracts.md#harness), always on for the engagement dataset.

A hookwrapper on `pytest_runtest_makereport` writes each report's longrepr and captured sections
to a log under `$EQUIVALENCE_PRIVATE/logs/`, then replaces the longrepr of a failed report with
`<nodeid>: <n> rows differ in columns [...]` (counts and column names only), or for a set-up error
`<nodeid>: load_dataset failed: <exception classes> (table T, column C, ...)` (class and design
names only, never the exception message), and empties the captured sections, so no value
reaches the terminal, the JUnit file or the toolkit's output. A skip keeps its reason only when it is `not-implemented: <unit>`.

The plugin also turns off warning capture for redacted runs (a warning message may quote values)
and runs with `-p no:cacheprovider`. Before any conftest loads, it refuses (usage error, exit 4)
every option of a redacted run that sends output past pytest's capture: `-s` / `--capture=no`,
`--capture=tee-sys`, live logging (`log_cli` in an ini file, `-o log_cli=true`,
`--log-cli-level`), a log file (`--log-file`, `log_file`), `--pdb` / `--trace` and `--debug`.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest

from .adapter import failure_text

NOT_IMPLEMENTED = "not-implemented: "


class Redactor:
    """Registered by the plugin as `equivalence-redact` when redaction is on."""

    def __init__(self, log_dir, label: str, mismatches: dict, setup_failure=None):
        self.log_dir = Path(log_dir)
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        self.path = self.log_dir / f"{stamp}-equivalence-{label}-{os.getpid()}.log"
        self.mismatches = mismatches  # nodeid -> compare.Mismatch
        # call -> failure description (adapter.describe_failure) of a set-up error, or None
        self.setup_failure = setup_failure
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

    def summary_line(self, nodeid: str, failure: dict | None = None) -> str:
        if failure is not None:
            return f"{nodeid}: {failure_text(failure)} (details in the private log)"
        m = self.mismatches.get(nodeid)
        if m is None:
            return f"{nodeid}: 0 rows differ in columns [] (an error; details in the private log)"
        return f"{nodeid}: {m.message()}"

    def redact(self, report, call=None) -> None:
        if report.longrepr is not None or report.sections:
            self.write_report(report)
        if report.failed:
            failure = None
            if report.when == "setup" and call is not None and self.setup_failure:
                failure = self.setup_failure(call)
            report.longrepr = self.summary_line(report.nodeid, failure)
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
        self.redact(report, call)
