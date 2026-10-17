"""Results summary: JUnit files of the S6 run into the committed results.json
(docs/contracts.md#harness "JUnit, matrix, results.json and known differences", #pass-rate).

The required cells come from the engagement and the committed goldens manifest, never from the
files found in the JUnit directories:

- equivalence: `engagement` plus every `seed-N` dataset of `goldens/manifest.json`, on every
  database of `database.primary` and `database.verify_also`, one `junit-equivalence-<d>-<db>.xml`
  per cell;
- app: `junit-app-fixtures-postgresql.xml` always, `junit-app-fixtures-mssql.xml` when mssql is one
  of those databases. Each app file is split into `rules` (test cases of `*/tests/test_rules.py`,
  dataset `fixtures`) and `e2e` (test cases under `tests/e2e/`, dataset `seed-1`); other app tests
  count in neither, and a split without test cases gives no cell.

Each required file is looked up in the --junit-dir directories (not recursively): missing
everywhere exits 1, found twice or malformed exits 2. Only counts, unit ids and outcomes are read;
failure messages, system-out, system-err and properties are never copied.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from toolkit import equivalence as eq

FORMAT_VERSION = 1
THRESHOLD = 0.9
NI_TYPE = eq.NI_TYPE
SUITES = ("equivalence", "rules", "e2e")
APP_DATASET = {"rules": "fixtures", "e2e": "seed-1"}
JUNIT_RE = re.compile(r"^junit-.+\.xml$")
OUTCOMES = ("failed", "error", "skipped", "not_implemented")


class ResultsError(Exception):
    """exit 1 (a required file is missing) or 2 (duplicate, malformed or other input problem).
    The message holds file names only."""

    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class Counts:
    suite: str
    dataset: str
    db: str
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    not_implemented: int = 0
    total: int = 0
    not_passed: list = field(default_factory=list)

    @property
    def rate(self) -> float:
        return round(self.passed / self.total, 4) if self.total else 0.0

    def add(self, outcome: str, unit: str | None = None) -> None:
        self.total += 1
        if outcome == "passed":
            self.passed += 1
            return
        attr = {"failed": "failed", "error": "errors", "skipped": "skipped"}.get(
            outcome, "not_implemented"
        )
        setattr(self, attr, getattr(self, attr) + 1)
        if unit is not None:
            self.not_passed.append({"unit": unit, "outcome": outcome})

    def entry(self, with_not_passed: bool) -> dict:
        out = {
            "suite": self.suite,
            "dataset": self.dataset,
            "db": self.db,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "skipped": self.skipped,
            "not_implemented": self.not_implemented,
            "total": self.total,
            "rate": self.rate,
        }
        if with_not_passed:
            out["not_passed"] = sorted(
                self.not_passed, key=lambda x: (x["unit"], x["outcome"])
            )
        return out


# ------------------------------------------------------------------ required cells


def databases(eng: dict) -> list[str]:
    db = eng.get("database") or {}
    out = []
    for name in [db.get("primary"), *(db.get("verify_also") or [])]:
        if name and name not in out:
            out.append(name)
    if not out:
        raise ResultsError("engagement: database.primary is not set")
    return out


def datasets(manifest: dict) -> list[str]:
    seeds = [d for d in eq.manifest_datasets(manifest) if eq.SEED_RE.fullmatch(d)]
    return ["engagement", *seeds]


def required_files(eng: dict, manifest: dict) -> dict[str, tuple[str, str, str]]:
    """File name -> (suite, dataset, db) in report order."""
    dbs = databases(eng)
    out: dict[str, tuple[str, str, str]] = {}
    for d in datasets(manifest):
        for db in dbs:
            out[f"junit-equivalence-{d}-{db}.xml"] = ("equivalence", d, db)
    out["junit-app-fixtures-postgresql.xml"] = ("app", "fixtures", "postgresql")
    if "mssql" in dbs:
        out["junit-app-fixtures-mssql.xml"] = ("app", "fixtures", "mssql")
    return out


def locate(dirs: list[Path], names) -> tuple[dict[str, Path], list[str]]:
    """Find each required name in exactly one directory; return the paths and the ignored
    JUnit file names (other cells)."""
    names = list(names)
    found: dict[str, list[Path]] = {n: [] for n in names}
    ignored: list[str] = []
    seen_dirs = []
    for d in dirs:
        real = Path(os.path.realpath(d))
        if real in seen_dirs:
            continue
        seen_dirs.append(real)
        if not real.is_dir():
            raise ResultsError(f"--junit-dir {d}: not a directory")
        for entry in sorted(real.iterdir()):
            if not entry.is_file():
                continue
            if entry.name in found:
                found[entry.name].append(entry)
            elif JUNIT_RE.match(entry.name):
                ignored.append(entry.name)
    missing = [n for n in names if not found[n]]
    if missing:
        raise ResultsError(
            "required JUnit file missing: " + ", ".join(missing), exit_code=1
        )
    twice = [n for n in names if len(found[n]) > 1]
    if twice:
        raise ResultsError(
            "JUnit file found in more than one --junit-dir: " + ", ".join(twice)
        )
    return {n: found[n][0] for n in names}, sorted(set(ignored))


# ------------------------------------------------------------------ JUnit


def _parse(path: Path):
    try:
        return ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        raise ResultsError(f"{path.name}: malformed XML") from None


def outcome(tc) -> str:
    if tc.find("failure") is not None:
        return "failed"
    if tc.find("error") is not None:
        return "error"
    skipped = tc.find("skipped")
    if skipped is not None:
        return "not_implemented" if skipped.get("type") == NI_TYPE else "skipped"
    return "passed"


def timestamps(root) -> list[str]:
    suites = [root] if root.tag == "testsuite" else []
    suites += list(root.iter("testsuite")) if root.tag != "testsuite" else []
    return [s.get("timestamp") for s in suites if s.get("timestamp")]


def unit_of(name: str) -> str | None:
    """The unit id of a harness test name test_unit[<area>::<unit>::<dataset>::<db>]."""
    if not name.startswith("test_unit[") or not name.endswith("]"):
        return None
    parts = name[len("test_unit[") : -1].split("::")
    if len(parts) < 4:
        return None
    return "::".join(parts[1:-2])


def parse_equivalence(root, dataset: str, db: str) -> tuple[Counts, int]:
    """Counts of one equivalence cell; also the number of test cases that are not harness units."""
    cell = Counts("equivalence", dataset, db)
    other = 0
    for tc in root.iter("testcase"):
        unit = unit_of(tc.get("name") or "")
        if unit is None:
            other += 1
            continue
        cell.add(outcome(tc), unit)
    return cell, other


def app_suite(tc) -> str | None:
    """rules, e2e or None (other app tests) for a test case of the app's own pytest run."""
    path = (tc.get("file") or "").replace("\\", "/")
    if path:
        if path.startswith("tests/e2e/"):
            return "e2e"
        if re.search(r"(^|/)[^/]+/tests/test_rules\.py$", path):
            return "rules"
        return None
    classname = tc.get("classname") or ""
    if classname.startswith("tests.e2e."):
        return "e2e"
    if re.search(r"(^|\.)[^.]+\.tests\.test_rules($|\.)", classname):
        return "rules"
    return None


def parse_app(root, db: str) -> list[Counts]:
    cells = {s: Counts(s, APP_DATASET[s], db) for s in ("rules", "e2e")}
    for tc in root.iter("testcase"):
        suite = app_suite(tc)
        if suite is not None:
            cells[suite].add(outcome(tc))
    return [c for c in cells.values() if c.total]


def latest_date(stamps: list[str]) -> str | None:
    best = None
    for s in stamps:
        try:
            t = dt.datetime.fromisoformat(s)
        except ValueError:
            continue
        key = t.astimezone(dt.UTC) if t.tzinfo else t.replace(tzinfo=dt.UTC)
        if best is None or key > best[0]:
            best = (key, s[:10])
    return None if best is None else best[1]


# ------------------------------------------------------------------ summary


def gate(cells: list[Counts]) -> dict:
    eqc = [c for c in cells if c.suite == "equivalence"]
    total = sum(c.total for c in eqc)
    return {
        "min_cell_rate": min((c.rate for c in eqc), default=0.0),
        "pooled_rate": round(sum(c.passed for c in eqc) / total, 4) if total else 0.0,
        "threshold": THRESHOLD,
    }


@dataclass
class Summary:
    doc: dict
    cells: list[Counts]
    messages: list[str]


def summarize(eng: dict, junit_dirs: list[str]) -> Summary:
    target = Path(eng["paths"]["target"])
    try:
        manifest = eq.read_manifest(target)
    except eq.EquivalenceError as exc:
        raise ResultsError(str(exc)) from None
    files = required_files(eng, manifest)
    paths, ignored = locate([Path(d) for d in junit_dirs], files)
    cells: list[Counts] = []
    app_cells: list[Counts] = []
    stamps: list[str] = []
    messages: list[str] = []
    for name, (suite, dataset, db) in files.items():
        root = _parse(paths[name])
        stamps += timestamps(root)
        if suite == "equivalence":
            cell, other = parse_equivalence(root, dataset, db)
            cells.append(cell)
            if other:
                messages.append(
                    f"{name}: {other} test cases that are not units ignored"
                )
        else:
            app_cells += parse_app(root, db)
    cells += sorted(
        app_cells,
        key=lambda c: (SUITES.index(c.suite), files_db_order(files, c.db)),
    )
    messages += [f"ignored (not a required cell): {n}" for n in ignored]
    doc = {
        "format_version": FORMAT_VERSION,
        "engagement": eng["id"],
        "date": latest_date(stamps),
        "gate": gate(cells),
        "cells": [c.entry(c.suite == "equivalence") for c in cells],
    }
    return Summary(doc, cells, messages)


def files_db_order(files: dict, db: str) -> int:
    dbs = []
    for _suite, _d, d in files.values():
        if d not in dbs:
            dbs.append(d)
    return dbs.index(db) if db in dbs else len(dbs)


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def write(path: Path, text: str) -> None:
    """Write through a temp file in the same directory (a declared output inside the jail)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".results-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
