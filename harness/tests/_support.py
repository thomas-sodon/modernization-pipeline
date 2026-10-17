"""Synthetic fixtures for the harness's own tests (and toolkit/tests/test_equivalence.py).

`make_target(root, units, ...)` lays out a vendored copy of this package as `equivalence/`, a
fixture `goldens/` (manifest, params, seed expected files and inputs), a private directory with an
engagement dataset and goldens, and a stub adapter module `stub_adapter.py` whose behaviour comes
from `stub.json`. Everything is invented here: a made-up widget shop, no client content.

`run_pytest(root, *args)` runs the vendored runner in a subprocess with `toolkit` blocked from
import, so the tests also prove that the harness runs without the toolkit.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
KEY = bytes(range(32))

STUB = '''"""Stub adapter for harness tests (synthetic)."""
import json
import logging
import os
import sys
from pathlib import Path

from equivalence.adapter import NotImplementedUnit

CONFIG = json.loads((Path(__file__).parent / "stub.json").read_text(encoding="utf-8"))


class WidgetDiag:
    """Driver diagnostics of a synthetic database error (psycopg's diag shape)."""

    def __init__(self, names):
        self.table_name = names.get("table_name")
        self.column_name = names.get("column_name")
        self.constraint_name = names.get("constraint_name")
        self.sqlstate = names.get("sqlstate")


class WidgetDbError(Exception):
    """A synthetic database error whose message quotes a row's values."""

    def __init__(self, message, diag):
        super().__init__(message)
        self.diag = WidgetDiag(diag)


class WidgetLoadError(Exception):
    """A synthetic load error carrying design names as attributes."""

    def __init__(self, message, names):
        super().__init__(message)
        for key, value in names.items():
            setattr(self, key, value)


class StubAdapter:
    def __init__(self):
        self.dataset = None

    def load_dataset(self, directory):
        meta = json.loads((Path(directory) / "dataset.json").read_text(encoding="utf-8"))
        self.dataset = meta["name"]
        out = CONFIG.get("env_out")
        if out:
            keys = ("DATABASE_URL", "MP_ONLY_AREAS", "DJANGO_SETTINGS_MODULE")
            Path(out).write_text(json.dumps({k: os.environ.get(k) for k in keys}), encoding="utf-8")
        if CONFIG.get("fail_load"):
            if "fail_load_diag" in CONFIG or "fail_load_names" in CONFIG:
                # a load error wrapping a database error, both messages quoting values
                try:
                    raise WidgetDbError(CONFIG["fail_load"], CONFIG.get("fail_load_diag") or {})
                except WidgetDbError as exc:
                    raise WidgetLoadError(
                        CONFIG["fail_load"], CONFIG.get("fail_load_names") or {}
                    ) from exc
            raise RuntimeError(CONFIG["fail_load"])

    def run_saved_query(self, unit_id, params):
        text = CONFIG.get("print", {}).get(unit_id)
        if text:
            print(text)
            print(text, file=sys.stderr)
        logged = CONFIG.get("log", {}).get(unit_id)
        if logged:
            logging.getLogger("stub_app").warning(logged)
        if unit_id in CONFIG.get("not_implemented", []):
            raise NotImplementedUnit(unit_id)
        if unit_id in CONFIG.get("raise", {}):
            raise RuntimeError(CONFIG["raise"][unit_id])
        want = CONFIG.get("params", {}).get(unit_id)
        if want is not None and params != want.get(self.dataset):
            raise AssertionError("unexpected parameters")
        rows = CONFIG["rows"].get(self.dataset, {}).get(unit_id, [])
        return [tuple(r) for r in rows]

    def render_document(self, unit_id, key, params):
        return CONFIG["docs"][self.dataset][unit_id][key]
'''


def _canon():
    """canonical.py of this package, loaded by path (works as harness and as equivalence)."""
    spec = importlib.util.spec_from_file_location(
        "equivalence_support_canonical", PKG / "canonical.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def vendor(root: Path) -> Path:
    dest = root / "equivalence"
    shutil.copytree(
        PKG,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        dirs_exist_ok=True,
    )
    return dest


COLUMNS = [
    {"name": "WidgetID", "type": "longinteger"},
    {"name": "Label", "type": "text"},
]


def make_target(
    root: Path,
    units: dict,
    private: Path | None = None,
    stub: dict | None = None,
    params: dict | None = None,
    private_params: dict | None = None,
    key: bytes = KEY,
    adapter_toml: str | None = None,
) -> Path:
    """units: {uid: {"area": str, "columns": [...] (optional), "golden": {dataset: rows or None
    (empty) or a status string}}}. Seeds get expected files; "engagement" gets an HMAC digest and
    a private golden under `private`. Returns root."""
    canon = _canon()
    root.mkdir(parents=True, exist_ok=True)
    vendor(root)
    goldens = root / "goldens"
    manifest = {"format_version": 1, "engagement": "widget-demo", "units": {}}
    datasets = set()
    for uid, spec in units.items():
        columns = spec.get("columns", COLUMNS)
        entry_ds = {}
        for ds, golden in spec["golden"].items():
            datasets.add(ds)
            if isinstance(golden, str):
                entry_ds[ds] = {
                    "status": golden,
                    "rows": None,
                    "reason": "other",
                    "digest": None,
                }
                continue
            rows = golden or []
            data = canon.canonical_csv(columns, rows, spec.get("order_by"))
            name = canon.file_safe(uid) + ".csv"
            entry = {
                "status": "ok" if rows else "empty",
                "rows": len(rows),
                "reason": None if rows else "empty",
            }
            if ds == "engagement":
                entry["digest"] = {
                    "alg": "hmac-sha256",
                    "value": canon.digest(data, key),
                }
                if private is not None:
                    out = private / "goldens" / "engagement" / name
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(data)
            else:
                entry["digest"] = {"alg": "sha256", "value": canon.digest(data)}
                entry["expected"] = f"synthetic/{ds}/expected/{name}"
                out = goldens / entry["expected"]
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
            entry_ds[ds] = entry
        manifest["units"][uid] = {
            "area": spec.get("area"),
            "kind": "saved_query",
            "canonical_version": 1,
            "columns": columns,
            "order_by": spec.get("order_by"),
            "needs_access_confirmation": False,
            "datasets": entry_ds,
        }
    goldens.mkdir(parents=True, exist_ok=True)
    (goldens / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    (goldens / "params.json").write_text(
        json.dumps({"format_version": 1, "units": params or {}}, indent=2),
        encoding="utf-8",
    )
    for ds in datasets:
        if ds == "engagement":
            if private is None:
                continue
            d = private / "datasets" / "engagement"
        else:
            d = goldens / "synthetic" / ds / "input"
        d.mkdir(parents=True, exist_ok=True)
        (d / "dataset.json").write_text(
            json.dumps({"format_version": 1, "name": ds, "tables": []}),
            encoding="utf-8",
        )
    if private is not None and private_params is not None:
        p = private / "goldens" / "params.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"format_version": 1, "units": private_params}), encoding="utf-8"
        )
    (root / "stub_adapter.py").write_text(STUB, encoding="utf-8")
    (root / "stub.json").write_text(json.dumps(stub or {"rows": {}}), encoding="utf-8")
    (root / "adapter.toml").write_text(
        adapter_toml
        or 'format_version = 1\nadapter = "stub"\nentrypoint = "stub_adapter:StubAdapter"\n',
        encoding="utf-8",
    )
    return root


def set_stub(root: Path, stub: dict) -> None:
    (root / "stub.json").write_text(json.dumps(stub), encoding="utf-8")


CLEARED = (
    "EQUIVALENCE_PRIVATE",
    "EQUIVALENCE_OBTAINED",
    "EQUIVALENCE_REDACT",
    "EQUIVALENCE_DIGEST_KEY",
    "EQUIVALENCE_DATA_FILE",
    "DATABASE_URL",
    "MP_ONLY_AREAS",
    "DJANGO_SETTINGS_MODULE",
    "PYTEST_ADDOPTS",
    "PG_URL",
    "MSSQL_URL",
)

RUNNER = (
    "import sys\n"
    "sys.modules['toolkit'] = None\n"
    "import pytest\n"
    "raise SystemExit(pytest.main(sys.argv[1:]))\n"
)


def run_pytest(
    root: Path, *args: str, env: dict | None = None
) -> subprocess.CompletedProcess:
    """Run the vendored runner (toolkit import blocked) with cwd `root`."""
    full = {k: v for k, v in os.environ.items() if k not in CLEARED}
    full["PYTHONDONTWRITEBYTECODE"] = "1"
    full.update(env or {})
    argv = [
        sys.executable,
        "-c",
        RUNNER,
        "--rootdir",
        ".",
        "equivalence/tests",
        "-p",
        "equivalence.plugin",
        "-p",
        "no:cacheprovider",
        *args,
    ]
    return subprocess.run(
        argv, check=False, cwd=root, env=full, capture_output=True, text=True
    )
