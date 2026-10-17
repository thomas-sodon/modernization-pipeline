"""`toolkit equivalence`: run the vendored harness per (dataset, db) cell and gate on the pass rate
(docs/contracts.md#harness, #equivalence-cli, #pass-rate).

One pytest process per cell, with the plugin options of docs/contracts.md#harness:

- reference: the toolkit's own interpreter (`sys.executable`), cwd `<target>`,
  `-m pytest equivalence/tests -p equivalence.plugin --eq-adapter <temp adapter.toml> ...`, whose
  entrypoint is `toolkit.commands.reference:ReferenceAdapter`; the cell db is `reference`.
- django: cwd `<target>/app`, the adapter.toml `python` command (inside mp-data the app's own
  `.venv/bin/python`), `-m pytest -c pyproject.toml --rootdir . ../equivalence/tests ...`.

Counts come from each cell's JUnit file (`junit-equivalence-<dataset>-<db>.xml`). Nothing here
reads golden values; summaries and the matrix carry counts, unit ids and column names only.

A unit whose set-up failed carries the harness's `equivalence.setup-error` property (class and
design names, never the exception message); its summary `units[].reason` is
`<phase> failed: <classes> (table T, column C, constraint K, code X, sqlstate S)` and the summary
`messages` get one line per cell and distinct failure, `cell <dataset>/<db>: <same text>`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from harness import adapter as harness_adapter
from toolkit import quiet

REFERENCE_ENTRYPOINT = "toolkit.commands.reference:ReferenceAdapter"
AREA_RE = re.compile(r"^[a-z][a-z0-9_]{1,29}$")
SEED_RE = re.compile(r"^seed-\d+$")
NI_TYPE = "equivalence.not-implemented"
MISMATCH_PROPERTY = "equivalence.mismatch"
SETUP_PROPERTY = "equivalence.setup-error"
DATA_PLANE_PREFIX = "mpdata_test_"
FORMAT_VERSION = 1
DB_LABELS = ("postgresql", "mssql")


class EquivalenceError(Exception):
    """A usage or input problem: exit 2 (the message never holds values)."""

    exit_code = 2


@dataclass
class Options:
    adapter: str
    datasets: str
    dbs: str | None = None
    keyword: str | None = None
    only_areas: str | None = None
    collect_only: bool = False
    allow_not_implemented: bool = False
    allow_empty: bool = False
    min_pass_per_cell: float | None = None
    junit_dir: str | None = None
    matrix: str | None = None


@dataclass
class Cell:
    dataset: str
    db: str
    argv: list[str]
    cwd: Path
    env: dict
    junit: Path
    obtained: Path


@dataclass
class CellResult:
    dataset: str
    db: str
    status: str = "empty"
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    not_implemented: int = 0
    total: int = 0
    rate: float | None = None
    units: list = field(default_factory=list)
    mismatches: list = field(default_factory=list)
    collected: list = field(default_factory=list)
    # distinct set-up failure texts of the cell (class and design names only), in first-seen order
    setup_failures: list = field(default_factory=list)

    def matrix_entry(self) -> dict:
        return {
            "dataset": self.dataset,
            "db": self.db,
            "status": self.status,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "skipped": self.skipped,
            "not_implemented": self.not_implemented,
            "total": self.total,
            "rate": self.rate,
        }


# ------------------------------------------------------------------ inputs


def in_data_plane(environ=None) -> bool:
    environ = os.environ if environ is None else environ
    return environ.get("MP_SANDBOX") == "mp-data"


def read_manifest(target: Path) -> dict:
    path = target / "goldens" / "manifest.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EquivalenceError(f"goldens/manifest.json missing: {path}") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EquivalenceError("goldens/manifest.json is not valid JSON") from None
    if not isinstance(doc, dict) or doc.get("format_version") != FORMAT_VERSION:
        raise EquivalenceError("goldens/manifest.json: unknown format_version")
    return doc


def manifest_datasets(manifest: dict) -> list[str]:
    names = set()
    for unit in (manifest.get("units") or {}).values():
        names.update(((unit or {}).get("datasets") or {}).keys())
    seeds = sorted((n for n in names if SEED_RE.fullmatch(n)), key=lambda n: int(n[5:]))
    return (["engagement"] if "engagement" in names else []) + seeds


def resolve_datasets(spec: str, manifest: dict) -> list[str]:
    known = manifest_datasets(manifest)
    seeds = [n for n in known if n != "engagement"]
    if spec == "synthetic":
        if not seeds:
            raise EquivalenceError("goldens/manifest.json lists no seed dataset")
        return seeds
    if spec == "all":
        return ["engagement"] + seeds
    names = [n.strip() for n in spec.split(",") if n.strip()]
    if not names:
        raise EquivalenceError(
            "--datasets: synthetic, engagement, all or a comma-separated list"
        )
    for n in names:
        if n != "engagement" and not SEED_RE.fullmatch(n):
            raise EquivalenceError(f"--datasets: unknown dataset name {n}")
        if n not in known:
            raise EquivalenceError(f"--datasets: {n} is not in goldens/manifest.json")
    return list(dict.fromkeys(names))


def parse_areas(spec: str | None) -> list[str] | None:
    if spec is None:
        return None
    areas = [a.strip() for a in spec.split(",") if a.strip()]
    if not areas or any(not AREA_RE.fullmatch(a) for a in areas):
        raise EquivalenceError(
            "--only-areas takes area ids matching ^[a-z][a-z0-9_]{1,29}$, comma-separated"
        )
    return areas


def resolve_dbs(adapter: str, spec: str | None, eng: dict) -> list[str]:
    if adapter == "reference":
        if spec is not None:
            raise EquivalenceError(
                "--dbs is refused with --adapter reference (its cell db is reference)"
            )
        return ["reference"]
    if spec is None:
        raise EquivalenceError(
            "--adapter django needs --dbs postgresql|mssql|from-engagement"
        )
    if spec == "from-engagement":
        db = eng.get("database") or {}
        names = [db.get("primary")] + list(db.get("verify_also") or [])
    else:
        names = [n.strip() for n in spec.split(",") if n.strip()]
    names = [n for n in dict.fromkeys(names) if n]
    bad = [n for n in names if n not in DB_LABELS]
    if bad or not names:
        raise EquivalenceError("--dbs: postgresql, mssql or from-engagement")
    return names


def read_adapter_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as fh:
            doc = tomllib.load(fh)
    except FileNotFoundError:
        raise EquivalenceError(f"adapter.toml missing: {path}") from None
    except tomllib.TOMLDecodeError:
        raise EquivalenceError(f"{path} is not valid TOML") from None
    if doc.get("format_version") != FORMAT_VERSION:
        raise EquivalenceError(f"{path}: unknown format_version")
    for k in ("entrypoint", "settings"):
        if not isinstance(doc.get(k), str):
            raise EquivalenceError(f"{path}: {k} missing")
    return doc


# ------------------------------------------------------------------ cells


def obtained_dir(
    private: Path, junit_dir: Path | None, work: Path, dataset: str, db: str, environ
) -> Path:
    """pytest-regressions obtained files: under <private>/synthetic/results/obtained/ for
    synthetic-only runs whose --junit-dir lies under <private>/synthetic/ (outside mp-data),
    else a temp dir removed at exit."""
    name = f"{dataset}-{db}"
    synthetic = (private / "synthetic").resolve()
    if (
        junit_dir is not None
        and not in_data_plane(environ)
        and junit_dir.resolve().is_relative_to(synthetic)
    ):
        return private / "synthetic" / "results" / "obtained" / name
    return work / "obtained" / name


def plugin_extras(dataset: str, opts: Options, areas: list[str] | None) -> list[str]:
    extra: list[str] = []
    if opts.keyword is not None:
        extra += ["-k", opts.keyword]
    if areas:
        extra += ["--eq-only-areas", ",".join(areas)]
    if opts.allow_not_implemented:
        extra.append("--eq-allow-not-implemented")
    if opts.collect_only:
        extra += ["--collect-only", "-q"]
    return extra


def base_env(
    environ, private: Path, obtained: Path, dataset: str, key: bytes | None
) -> dict:
    env = dict(environ)
    env["EQUIVALENCE_PRIVATE"] = str(private)
    env["EQUIVALENCE_OBTAINED"] = str(obtained)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if dataset == "engagement":
        env["EQUIVALENCE_REDACT"] = "1"
        if key is not None:
            env["EQUIVALENCE_DIGEST_KEY"] = key.hex()
        else:
            env.pop("EQUIVALENCE_DIGEST_KEY", None)
    else:
        env.pop("EQUIVALENCE_REDACT", None)
        env.pop("EQUIVALENCE_DIGEST_KEY", None)
    return env


def reference_cell(
    eng: dict,
    adapter_toml: Path,
    dataset: str,
    junit: Path,
    obtained: Path,
    opts: Options,
    areas,
    environ,
    key: bytes | None,
) -> Cell:
    target = Path(eng["paths"]["target"])
    private = Path(eng["paths"]["private"])
    if dataset == "engagement" and not in_data_plane(environ):
        raise EquivalenceError(
            "the engagement dataset runs only on the data plane: run through mp-data "
            "(mp-data equivalence <eng> ...)"
        )
    argv = [
        sys.executable,
        "-m",
        "pytest",
        "--rootdir",
        ".",
        "equivalence/tests",
        "-p",
        "equivalence.plugin",
        "--eq-adapter",
        str(adapter_toml),
        "--eq-dataset",
        dataset,
        "--eq-db",
        "reference",
        "--junitxml",
        str(junit),
        "-p",
        "no:cacheprovider",
    ] + plugin_extras(dataset, opts, areas)
    env = base_env(environ, private, obtained, dataset, key)
    return Cell(dataset, "reference", argv, target, env, junit, obtained)


def django_cell(
    eng: dict,
    adapter_toml: Path,
    config: dict,
    dataset: str,
    db: str,
    junit: Path,
    obtained: Path,
    opts: Options,
    areas,
    environ,
    key: bytes | None,
) -> Cell:
    private = Path(eng["paths"]["private"])
    eq_dir = adapter_toml.parent
    project = Path(
        os.path.normpath(eq_dir / str(config.get("project_dir") or "../app"))
    )
    databases = config.get("databases") or {}
    var = databases.get(db)
    if not var:
        raise EquivalenceError(f"adapter.toml has no databases.{db}")
    data_var = f"MP_DATA_{var}"
    if dataset == "engagement" and not (
        environ.get("MP_TEST_DB_PREFIX") == DATA_PLANE_PREFIX and environ.get(data_var)
    ):
        raise EquivalenceError(
            f"the engagement dataset on {db} needs the data plane ({DATA_PLANE_PREFIX} test "
            f"databases, {data_var}): run through mp-data (mp-data equivalence <eng> ...)"
        )
    if in_data_plane(environ):
        python = [str(project / ".venv" / "bin" / "python")]
        if not Path(python[0]).exists():
            raise EquivalenceError(
                f"{python[0]} is missing: run `cd {project} && uv sync --locked`"
            )
    else:
        python = [str(p) for p in (config.get("python") or [])]
        if not python:
            raise EquivalenceError(f"{adapter_toml}: python missing")
    server = environ.get(data_var) or environ.get(var)
    if not server:
        raise EquivalenceError(f"{var} is not set (adapter.toml databases.{db})")
    tests = os.path.relpath(eq_dir / "tests", project)
    toml_rel = os.path.relpath(adapter_toml, project)
    argv = (
        python
        + [
            "-m",
            "pytest",
            "-c",
            "pyproject.toml",
            "--rootdir",
            ".",
            tests,
            "-p",
            "equivalence.plugin",
            "--create-db",
            "--eq-adapter",
            toml_rel,
            "--eq-dataset",
            dataset,
            "--eq-db",
            db,
            "--junitxml",
            str(junit),
            "-p",
            "no:cacheprovider",
        ]
        + plugin_extras(dataset, opts, areas)
    )
    env = base_env(environ, private, obtained, dataset, key)
    env["DJANGO_SETTINGS_MODULE"] = str(config["settings"])
    env["DATABASE_URL"] = server.rstrip("/") + "/" + eng["db_name"]
    if areas:
        env["MP_ONLY_AREAS"] = ",".join(areas)
    return Cell(dataset, db, argv, project, env, junit, obtained)


# ------------------------------------------------------------------ results


def parse_junit(path: Path, dataset: str, db: str) -> CellResult:
    res = CellResult(dataset, db)
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        raise EquivalenceError(
            f"junit-equivalence-{dataset}-{db}.xml is missing or malformed"
        ) from None
    for tc in root.iter("testcase"):
        name = tc.get("name") or ""
        if not name.startswith("test_unit[") or not name.endswith("]"):
            continue
        test_id = name[len("test_unit[") : -1]
        parts = test_id.split("::")
        unit = parts[1] if len(parts) >= 4 else test_id
        res.total += 1
        skipped = tc.find("skipped")
        if tc.find("failure") is not None:
            status = "failed"
            res.failed += 1
        elif tc.find("error") is not None:
            status = "error"
            res.errors += 1
        elif skipped is not None and skipped.get("type") == NI_TYPE:
            status = "not-implemented"
            res.not_implemented += 1
        elif skipped is not None:
            status = "skipped"
            res.skipped += 1
        else:
            status = "passed"
            res.passed += 1
        reason = setup_reason(tc) if status == "error" else None
        if reason is not None and reason not in res.setup_failures:
            res.setup_failures.append(reason)
        if status != "passed":
            res.units.append(
                {
                    "id": unit,
                    "status": status,
                    "reason": reason,
                    "dataset": dataset,
                    "db": db,
                }
            )
        for prop in tc.iter("property"):
            if prop.get("name") != MISMATCH_PROPERTY:
                continue
            try:
                info = json.loads(prop.get("value") or "{}")
            except json.JSONDecodeError:
                continue
            cols = {
                str(k): int(v)
                for k, v in (info.get("columns") or {}).items()
                if isinstance(v, int) and v >= 0
            }
            res.mismatches.append(
                {"unit": unit, "dataset": dataset, "db": db, "columns": cols}
            )
    if res.total:
        res.status = "ok"
        res.rate = round(res.passed / res.total, 4)
    return res


def setup_reason(tc) -> str | None:
    """The set-up failure of a JUnit test case as text, from the harness's
    `equivalence.setup-error` property, every field checked again (design names only)."""
    for prop in tc.iter("property"):
        if prop.get("name") != SETUP_PROPERTY:
            continue
        try:
            info = json.loads(prop.get("value") or "{}")
        except json.JSONDecodeError:
            continue
        info = harness_adapter.clean_failure(info)
        if info is not None:
            return harness_adapter.failure_text(info)
    return None


def rates(results: list[CellResult], kept_units: int) -> dict | None:
    full = [r for r in results if r.status == "ok" and r.total]
    if not full:
        return None
    out = {
        "min_cell": round(min(r.passed / r.total for r in full), 4),
        "pooled": round(sum(r.passed for r in full) / sum(r.total for r in full), 4),
    }
    if kept_units:
        out["coverage_adjusted"] = round(
            min(1.0, sum(r.passed for r in full) / (kept_units * len(results))), 4
        )
    return out


def table(results: list[CellResult]) -> str:
    head = (
        "dataset",
        "db",
        "status",
        "passed",
        "failed",
        "errors",
        "skipped",
        "not_impl",
        "total",
        "rate",
    )
    rows = [head]
    for r in results:
        rows.append(
            (
                r.dataset,
                r.db,
                "EMPTY" if r.status == "empty" else r.status,
                str(r.passed),
                str(r.failed),
                str(r.errors),
                str(r.skipped),
                str(r.not_implemented),
                str(r.total),
                "-" if r.rate is None else f"{r.rate:.4f}",
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(head))]
    return "\n".join(
        "  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip()
        for row in rows
    )


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


# ------------------------------------------------------------------ run


def _engagement_key(eng: dict, eng_path: str) -> bytes:
    from toolkit import golden as gl

    try:
        return gl.engagement_key(eng, eng_path)
    except gl.GoldenError as exc:
        raise EquivalenceError(str(exc).replace("\n", " ")) from None


def build_cells(
    eng: dict, eng_path: str, opts: Options, work: Path, environ
) -> tuple[list[Cell], dict]:
    target = Path(eng["paths"]["target"])
    private = Path(eng["paths"]["private"])
    if opts.adapter not in ("reference", "django"):
        raise EquivalenceError("--adapter must be reference or django")
    if opts.min_pass_per_cell is not None and not 0 <= opts.min_pass_per_cell <= 1:
        raise EquivalenceError("--min-pass-per-cell takes a rate from 0 to 1")
    areas = parse_areas(opts.only_areas)
    dbs = resolve_dbs(opts.adapter, opts.dbs, eng)
    manifest = read_manifest(target)
    datasets = resolve_datasets(opts.datasets, manifest)
    eq_dir = target / "equivalence"
    if opts.adapter == "reference":
        if not (eq_dir / "plugin.py").is_file():
            raise EquivalenceError(
                f"{eq_dir} has no harness: run uv run toolkit vendor-harness --target {target}"
            )
        adapter_toml = work / "adapter-reference.toml"
        adapter_toml.write_text(
            "format_version = 1\n"
            'adapter = "reference"\n'
            f'entrypoint = "{REFERENCE_ENTRYPOINT}"\n',
            encoding="utf-8",
        )
        config = None
    else:
        adapter_toml = eq_dir / "adapter.toml"
        config = read_adapter_toml(adapter_toml)
    if not opts.collect_only:
        for ds in datasets:
            d = (
                private / "datasets" / "engagement"
                if ds == "engagement"
                else target / "goldens" / "synthetic" / ds / "input"
            )
            if not (d / "dataset.json").is_file():
                raise EquivalenceError(f"dataset directory of {ds} missing: {d}")
    key = None
    if "engagement" in datasets and not opts.collect_only:
        if not in_data_plane(environ):
            raise EquivalenceError(
                "the engagement dataset runs only on the data plane: run through mp-data "
                f"(mp-data equivalence {eng_path} ...)"
            )
        key = _engagement_key(eng, eng_path)
    junit_dir = Path(opts.junit_dir) if opts.junit_dir else None
    junit_base = junit_dir if junit_dir is not None else work / "junit"
    cells = []
    for ds in datasets:
        for db in dbs:
            junit = junit_base / f"junit-equivalence-{ds}-{db}.xml"
            obtained = obtained_dir(private, junit_dir, work, ds, db, environ)
            if opts.adapter == "reference":
                cells.append(
                    reference_cell(
                        eng,
                        adapter_toml,
                        ds,
                        junit,
                        obtained,
                        opts,
                        areas,
                        environ,
                        key,
                    )
                )
            else:
                cells.append(
                    django_cell(
                        eng,
                        adapter_toml,
                        config,
                        ds,
                        db,
                        junit,
                        obtained,
                        opts,
                        areas,
                        environ,
                        key,
                    )
                )
    return cells, manifest


def _default_runner(cell: Cell, capture: bool) -> tuple[int, str]:
    if capture:
        proc = subprocess.run(
            cell.argv,
            check=False,
            cwd=cell.cwd,
            env=cell.env,
            stdout=subprocess.PIPE,
            stderr=sys.stderr.fileno(),
            text=True,
        )
        return proc.returncode, proc.stdout or ""
    proc = subprocess.run(
        cell.argv,
        check=False,
        cwd=cell.cwd,
        env=cell.env,
        stdout=sys.stderr.fileno(),
        stderr=sys.stderr.fileno(),
    )
    return proc.returncode, ""


def run(eng: dict, eng_path: str, opts: Options, environ=None, runner=None) -> int:
    environ = dict(os.environ if environ is None else environ)
    runner = runner or _default_runner
    command = "equivalence"
    eid = eng["id"]
    work = Path(tempfile.mkdtemp(prefix="mp-equivalence-"))
    try:
        try:
            cells, manifest = build_cells(eng, eng_path, opts, work, environ)
        except EquivalenceError as exc:
            quiet.progress(f"equivalence: {exc}")
            return quiet.summary(
                "error", command, 2, engagement=eid, messages=[str(exc)]
            )
        if opts.junit_dir:
            Path(opts.junit_dir).mkdir(parents=True, exist_ok=True)
        else:
            (work / "junit").mkdir(parents=True, exist_ok=True)
        kept = len(manifest.get("units") or {})
        results: list[CellResult] = []
        messages: list[str] = []
        error = False
        for cell in cells:
            shutil.rmtree(cell.obtained, ignore_errors=True)
            cell.obtained.mkdir(parents=True, exist_ok=True)
            if cell.junit.exists():
                cell.junit.unlink()
            quiet.progress(f"equivalence: cell {cell.dataset}/{cell.db}")
            rc, out = runner(cell, opts.collect_only)
            if rc not in (0, 1, 5):
                error = True
                messages.append(f"cell {cell.dataset}/{cell.db}: pytest exited {rc}")
                results.append(CellResult(cell.dataset, cell.db))
                continue
            if opts.collect_only:
                ids = [
                    ln.strip()
                    for ln in out.splitlines()
                    if "::test_unit[" in ln and not ln.startswith(" ")
                ]
                res = CellResult(cell.dataset, cell.db, collected=ids)
                if ids:
                    res.status, res.total = "ok", len(ids)
                results.append(res)
                continue
            if rc == 5:
                res = CellResult(cell.dataset, cell.db)
                results.append(res)
                continue
            try:
                res = parse_junit(cell.junit, cell.dataset, cell.db)
            except EquivalenceError as exc:
                error = True
                messages.append(str(exc))
                results.append(CellResult(cell.dataset, cell.db))
                continue
            if rc == 1 and res.passed == res.total and res.total:
                messages.append(
                    f"cell {cell.dataset}/{cell.db}: pytest exited 1 with every unit passed"
                )
            results.append(res)
        return finish(eid, opts, results, messages, error, kept)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def finish(
    eid: str,
    opts: Options,
    results: list[CellResult],
    messages: list[str],
    error: bool,
    kept: int,
) -> int:
    command = "equivalence"
    all_empty = all(r.status == "empty" for r in results)
    ni = sum(r.not_implemented for r in results)
    status = None
    # set-up failures first, so the 50-message cap never hides them
    messages[:0] = [
        f"cell {r.dataset}/{r.db}: {text}" for r in results for text in r.setup_failures
    ]
    if error:
        code = 2
    elif all_empty:
        code = 0 if opts.allow_empty else 5
        status = "no-data" if opts.allow_empty else None
        messages.append("every cell is empty: nothing selected")
    elif opts.collect_only:
        code = 0
    else:
        fail = False
        for r in results:
            if r.status == "empty":
                messages.append(f"cell {r.dataset}/{r.db} is empty")
                continue
            if opts.min_pass_per_cell is not None:
                if r.rate is not None and r.rate < opts.min_pass_per_cell:
                    fail = True
                    messages.append(
                        f"cell {r.dataset}/{r.db} rate {r.rate} is below {opts.min_pass_per_cell}"
                    )
            elif r.failed or r.errors or r.skipped:
                fail = True
                messages.append(
                    f"cell {r.dataset}/{r.db}: {r.failed} failed, {r.errors} errors, {r.skipped} skipped"
                )
        if ni and not opts.allow_not_implemented:
            fail = True
            messages.append(
                f"{ni} not-implemented outcomes (pass --allow-not-implemented to accept them)"
            )
        code = 1 if fail else 0

    if opts.matrix:
        path = Path(opts.matrix)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            dumps(
                {
                    "format_version": FORMAT_VERSION,
                    "cells": [r.matrix_entry() for r in results],
                }
            ),
            encoding="utf-8",
        )

    text = table(results)
    if opts.collect_only:
        ids = [i for r in results for i in r.collected]
        text = "\n".join(ids + ["", text]) if ids else text
    if quiet.is_quiet():
        quiet.progress(text)
    else:
        print(text, flush=True)

    counts = {
        "cells": len(results),
        "empty_cells": sum(r.status == "empty" for r in results),
        "kept_units": kept,
        "tests": sum(r.total for r in results),
        "passed": sum(r.passed for r in results),
        "failed": sum(r.failed for r in results),
        "errors": sum(r.errors for r in results),
        "skipped": sum(r.skipped for r in results),
        "not_implemented": ni,
    }
    units = [u for r in results for u in r.units]
    mismatches = [
        m for r in results for m in r.mismatches if m["dataset"] == "engagement"
    ]
    return quiet.summary(
        status,
        command,
        code,
        engagement=eid,
        counts=counts,
        rates=None if opts.collect_only else rates(results, kept),
        cells=[r.matrix_entry() for r in results],
        units=units,
        mismatches=mismatches,
        messages=messages,
    )
