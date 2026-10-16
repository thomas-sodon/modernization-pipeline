"""pytest plugin of the equivalence harness (docs/contracts.md#harness).

Load it with `-p equivalence.plugin` (vendored) or `-p harness.plugin` (pipeline). Options:

- `--eq-adapter <adapter.toml>`: the adapter config (entrypoint `module:Class`); activates the runner.
- `--eq-dataset <name>`: `engagement` or `seed-N`.
- `--eq-db <db>`: the database label of the cell (`reference`, `postgresql`, `mssql`).
- `--eq-allow-not-implemented`: not-implemented outcomes do not fail the session.
- `--eq-only-areas <area,...>`: select only units of these areas and set `MP_ONLY_AREAS`.

The runner (`tests/test_units.py`) gets one test per manifest unit whose golden has status ok on
the dataset, id `test_unit[<area>::<unit>::<dataset>::<db>]`. Inputs:

- `goldens/manifest.json` and `goldens/params.json` next to the package directory;
- seed datasets `goldens/synthetic/seed-N/input`, expected files under `goldens/`;
- engagement only: `$EQUIVALENCE_PRIVATE/datasets/engagement`, `$EQUIVALENCE_PRIVATE/goldens/`
  (params.json, and the private goldens for a mismatch report); logs go to
  `$EQUIVALENCE_PRIVATE/logs/`. The digest key comes from `EQUIVALENCE_DIGEST_KEY` (hex) or is
  derived from `EQUIVALENCE_DATA_FILE`.

Environment before Django starts (a `pytest_load_initial_conftests` wrapper, which runs before
pytest-django reads the settings): `MP_ONLY_AREAS` from `--eq-only-areas`; when `DATABASE_URL`
is unset and adapter.toml lists `--eq-db` under `[databases]`, `DATABASE_URL` = that variable's
value + `/` + the app slug (a usage error names the variable when it is unset too), and
`DJANGO_SETTINGS_MODULE` defaults to adapter.toml `settings`.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import pytest

from . import adapter as adapter_mod
from . import canonical as canon
from . import compare
from .redact import NOT_IMPLEMENTED, Redactor

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT = PACKAGE_DIR.parent
GOLDENS = ROOT / "goldens"
TESTS_DIR = PACKAGE_DIR / "tests"
RUNNER_FILE = "test_units.py"
RUNNER_NAME = "equivalence-runner"
REDACT_NAME = "equivalence-redact"
NI_TYPE = "equivalence.not-implemented"
MISMATCH_PROPERTY = "equivalence.mismatch"
AREA_RE = re.compile(r"^[a-z][a-z0-9_]{1,29}$")
DATASET_RE = re.compile(r"^(engagement|seed-\d+)$")


def pytest_addoption(parser):
    group = parser.getgroup("equivalence", "equivalence harness")
    group.addoption(
        "--eq-adapter",
        dest="eq_adapter",
        default=None,
        help="adapter.toml of the implementation under test (activates the runner)",
    )
    group.addoption(
        "--eq-dataset", dest="eq_dataset", default=None, help="engagement or seed-N"
    )
    group.addoption(
        "--eq-db",
        dest="eq_db",
        default=None,
        help="database label of the cell (reference, postgresql, mssql)",
    )
    group.addoption(
        "--eq-allow-not-implemented",
        dest="eq_allow_not_implemented",
        action="store_true",
        default=False,
        help="not-implemented outcomes do not fail the session (they still count as not passed)",
    )
    group.addoption(
        "--eq-only-areas",
        dest="eq_only_areas",
        default=None,
        help="comma-separated area ids: select only their units and set MP_ONLY_AREAS",
    )


# ------------------------------------------------------------------ environment


def parse_areas(value) -> list[str] | None:
    if value is None:
        return None
    areas = [a.strip() for a in str(value).split(",") if a.strip()]
    bad = [a for a in areas if not AREA_RE.fullmatch(a)]
    if bad or not areas:
        raise pytest.UsageError(
            "--eq-only-areas takes area ids matching ^[a-z][a-z0-9_]{1,29}$, comma-separated"
        )
    return areas


def _resolve(base, path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else Path(base) / p


def prepare_environment(options, cwd, environ=None) -> None:
    """Set MP_ONLY_AREAS, DATABASE_URL and DJANGO_SETTINGS_MODULE before Django starts."""
    environ = os.environ if environ is None else environ
    areas = parse_areas(getattr(options, "eq_only_areas", None))
    if areas is not None:
        environ["MP_ONLY_AREAS"] = ",".join(areas)
    path = getattr(options, "eq_adapter", None)
    if not path:
        return
    try:
        config = adapter_mod.read_config(_resolve(cwd, path))
    except adapter_mod.AdapterConfigError as exc:
        raise pytest.UsageError(str(exc)) from None
    db = getattr(options, "eq_db", None)
    databases = config.get("databases") or {}
    if db not in databases:
        return
    if not environ.get("DATABASE_URL"):
        var = databases[db]
        server = environ.get(var)
        if not server:
            raise pytest.UsageError(
                f"{var} is not set: --eq-db {db} takes its server URL from {var} "
                f"(adapter.toml databases.{db}), or set DATABASE_URL"
            )
        environ["DATABASE_URL"] = (
            server.rstrip("/") + "/" + adapter_mod.app_slug(config)
        )
    if config.get("settings"):
        environ.setdefault("DJANGO_SETTINGS_MODULE", str(config["settings"]))


def redacting(options, environ=None) -> bool:
    """Whether a run with these options redacts: the runner is active and the dataset is the
    engagement's, or EQUIVALENCE_REDACT=1."""
    environ = os.environ if environ is None else environ
    if not getattr(options, "eq_adapter", None):
        return False
    return (
        getattr(options, "eq_dataset", None) == "engagement"
        or environ.get("EQUIVALENCE_REDACT") == "1"
    )


def _ini(getini, name):
    try:
        return getini(name)
    except (ValueError, KeyError):
        return None


def uncaptured_outputs(options, getini) -> list[str]:
    """The options of this run that send output past pytest's capture (and so past redaction):
    no capture, tee-sys, live logging, a log file, the debugger and the debug log."""
    found = []
    if getattr(options, "capture", None) in ("no", "tee-sys"):
        found.append(f"--capture={options.capture} (-s)")
    if getattr(options, "log_cli_level", None) is not None:
        found.append("--log-cli-level")
    if str(_ini(getini, "log_cli") or "").strip().lower() in ("1", "true", "yes", "on"):
        found.append("log_cli")
    if getattr(options, "log_file", None) or _ini(getini, "log_file"):
        found.append("--log-file / log_file")
    if getattr(options, "usepdb", False) or getattr(options, "trace", False):
        found.append("--pdb / --trace")
    if getattr(options, "debug", None):
        found.append("--debug")
    return found


def refuse_uncaptured_output(options, getini, environ=None) -> None:
    """Redacted runs refuse every output path that skips pytest's capture: a value printed or
    logged by the adapter or the app would otherwise reach the terminal or a file outside
    $EQUIVALENCE_PRIVATE/logs/."""
    if not redacting(options, environ):
        return
    found = uncaptured_outputs(options, getini)
    if found:
        raise pytest.UsageError(
            "redacted runs refuse output that skips capture: "
            + ", ".join(found)
            + " (details of a redacted run go only to $EQUIVALENCE_PRIVATE/logs/)"
        )


@pytest.hookimpl(hookwrapper=True)
def pytest_load_initial_conftests(early_config, parser, args):
    refuse_uncaptured_output(early_config.known_args_namespace, early_config.getini)
    prepare_environment(
        early_config.known_args_namespace, early_config.invocation_params.dir
    )
    yield


def pytest_configure(config):
    if not config.getoption("eq_adapter", default=None):
        return
    # again on the final options, for a plugin loaded after the initial conftests
    refuse_uncaptured_output(config.option, config.getini)
    if config.getoption("force_regen", default=False) or config.getoption(
        "regen_all", default=False
    ):
        raise pytest.UsageError(
            "--force-regen and --regen-all are refused: goldens change only through golden record"
        )
    runner = Runner(config)
    config.pluginmanager.register(runner, RUNNER_NAME)
    if runner.redactor is not None:
        config.pluginmanager.register(runner.redactor, REDACT_NAME)
        # a warning message may quote values: warnings are not shown on redacted runs
        config.addinivalue_line("filterwarnings", "ignore")


# ------------------------------------------------------------------ runner


class GoldenMissing(Exception):
    """A listed dataset has no golden for a selected unit (an error, never a skip)."""


@dataclass
class Case:
    area: str | None
    unit_id: str
    unit: dict
    entry: dict | None
    dataset: str
    db: str

    @property
    def id(self) -> str:
        return f"{self.area}::{self.unit_id}::{self.dataset}::{self.db}"


def _read_json(path: Path, what: str, required: bool = True) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if not required:
            return {}
        raise pytest.UsageError(f"{what} missing: {path}") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise pytest.UsageError(f"{what} is not valid JSON") from None
    if not isinstance(doc, dict) or doc.get("format_version") != 1:
        raise pytest.UsageError(f"{what}: unknown format_version")
    return doc


def digest_key(environ=None) -> bytes:
    """The engagement digest key: EQUIVALENCE_DIGEST_KEY (hex), else derived from the data file
    in EQUIVALENCE_DATA_FILE (docs/contracts.md#goldens-manifest). Never printed or written."""
    environ = os.environ if environ is None else environ
    hexkey = environ.get("EQUIVALENCE_DIGEST_KEY")
    if hexkey:
        try:
            return bytes.fromhex(hexkey.strip())
        except ValueError:
            raise pytest.UsageError("EQUIVALENCE_DIGEST_KEY is not hex") from None
    data_file = environ.get("EQUIVALENCE_DATA_FILE")
    if data_file:
        if not Path(data_file).is_file():
            raise pytest.UsageError("EQUIVALENCE_DATA_FILE does not name a file")
        return canon.key_from_file(data_file)
    raise pytest.UsageError(
        "the engagement dataset needs EQUIVALENCE_DIGEST_KEY or EQUIVALENCE_DATA_FILE"
    )


class Runner:
    """State of one cell run; registered as the `equivalence-runner` plugin."""

    def __init__(self, config):
        self.config = config
        cwd = config.invocation_params.dir
        self.dataset = config.getoption("eq_dataset")
        self.db = config.getoption("eq_db")
        if not self.dataset or not self.db:
            raise pytest.UsageError("--eq-adapter needs --eq-dataset and --eq-db")
        if not DATASET_RE.fullmatch(self.dataset):
            raise pytest.UsageError("--eq-dataset must be engagement or seed-N")
        self.allow_ni = bool(config.getoption("eq_allow_not_implemented"))
        self.only_areas = parse_areas(config.getoption("eq_only_areas"))
        self.collect_only = bool(config.option.collectonly)
        try:
            self.adapter_config = adapter_mod.read_config(
                _resolve(cwd, config.getoption("eq_adapter"))
            )
        except adapter_mod.AdapterConfigError as exc:
            raise pytest.UsageError(str(exc)) from None
        self.manifest = _read_json(GOLDENS / "manifest.json", "goldens/manifest.json")
        self.units = self.manifest.get("units") or {}
        if not any(
            self.dataset in ((u or {}).get("datasets") or {})
            for u in self.units.values()
        ):
            raise pytest.UsageError(
                f"dataset {self.dataset} is not in goldens/manifest.json"
            )
        self.params = _read_json(
            GOLDENS / "params.json", "goldens/params.json", required=False
        ).get("units", {})
        self.engagement = self.dataset == "engagement"
        self.redacting = self.engagement or os.environ.get("EQUIVALENCE_REDACT") == "1"
        private = os.environ.get("EQUIVALENCE_PRIVATE")
        self.private = Path(private) if private else None
        if (self.engagement or self.redacting) and self.private is None:
            raise pytest.UsageError(
                "EQUIVALENCE_PRIVATE is not set (needed for the engagement dataset and redaction)"
            )
        if self.engagement:
            self.dataset_dir = self.private / "datasets" / "engagement"
        else:
            self.dataset_dir = GOLDENS / "synthetic" / self.dataset / "input"
        self.key = None
        self.private_params: dict = {}
        if not self.collect_only:
            if not (self.dataset_dir / "dataset.json").is_file():
                raise pytest.UsageError(
                    f"dataset directory of {self.dataset} missing: {self.dataset_dir}"
                )
            if self.engagement:
                self.key = digest_key()
                needs = any(
                    p.get("params") or p.get("keys") for p in self.params.values()
                )
                self.private_params = _read_json(
                    self.private / "goldens" / "params.json",
                    "$EQUIVALENCE_PRIVATE/goldens/params.json",
                    required=needs,
                ).get("units", {})
        self.not_implemented: dict[str, str] = {}
        self.mismatches: dict[str, compare.Mismatch] = {}
        self.redactor = None
        if self.redacting:
            self.redactor = Redactor(
                self.private / "logs", f"{self.dataset}-{self.db}", self.mismatches
            )
        self._stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")

    # -------------------------------------------------------------- selection

    def cases(self) -> list[Case]:
        out = []
        for uid in sorted(self.units):
            unit = self.units[uid] or {}
            area = unit.get("area")
            if self.only_areas is not None and area not in self.only_areas:
                continue
            entry = (unit.get("datasets") or {}).get(self.dataset)
            if entry is None or entry.get("status") == "ok":
                out.append(Case(area, uid, unit, entry, self.dataset, self.db))
        return out

    def pytest_ignore_collect(self, collection_path, config):
        p = Path(collection_path)
        if (
            p.suffix == ".py"
            and p.name.startswith("test_")
            and p.name != RUNNER_FILE
            and p.resolve().parent == TESTS_DIR
        ):
            return True
        return None

    def pytest_generate_tests(self, metafunc):
        if "eq_case" not in metafunc.fixturenames:
            return
        cases = self.cases()
        metafunc.parametrize("eq_case", cases, ids=[c.id for c in cases])

    def pytest_collection_modifyitems(self, session, config, items):
        # an empty parameter set leaves one placeholder item: drop it, so the cell is empty
        def placeholder(item) -> bool:
            if "eq_case" not in getattr(item, "fixturenames", ()):
                return False
            params = getattr(getattr(item, "callspec", None), "params", {})
            return not isinstance(params.get("eq_case"), Case)

        items[:] = [i for i in items if not placeholder(i)]

    # -------------------------------------------------------------- running

    def make_adapter(self):
        cls = adapter_mod.load_entrypoint(self.adapter_config["entrypoint"])
        inst = cls()
        inst.load_dataset(self.dataset_dir)
        return inst

    def golden(self, case: Case):
        if case.entry is None:
            raise GoldenMissing(
                f"{case.unit_id}: goldens/manifest.json has no entry for {case.dataset}"
            )
        if self.engagement:
            if not (case.entry.get("digest") or {}).get("value"):
                raise GoldenMissing(f"{case.unit_id}: no engagement digest")
            return case.entry
        rel = case.entry.get("expected") or (
            f"synthetic/{case.dataset}/expected/{compare.golden_name(case.unit_id, case.unit)}"
        )
        path = GOLDENS / rel
        if not path.is_file():
            raise GoldenMissing(f"{case.unit_id}: expected file missing: goldens/{rel}")
        return path

    def params_for(self, uid: str) -> tuple[dict, list]:
        if self.engagement:
            p = self.private_params.get(uid) or {}
            return (p.get("values") or {}).get("engagement") or {}, (
                p.get("keys") or {}
            ).get("engagement") or []
        p = self.params.get(uid) or {}
        return (p.get("values") or {}).get(self.dataset) or {}, (
            p.get("keys") or {}
        ).get(self.dataset) or []

    def check(self, case: Case, adapter, golden, request) -> None:
        params, keys = self.params_for(case.unit_id)
        not_implemented = False
        try:
            data = compare.unit_bytes(case.unit, adapter, case.unit_id, params, keys)
        except NotImplementedError:
            not_implemented = True
        if not_implemented:
            self.not_implemented[request.node.nodeid] = case.unit_id
            pytest.skip(NOT_IMPLEMENTED + case.unit_id)
        if self.engagement:
            self._check_engagement(case, data, request)
        else:
            self._check_seed(case, data, golden, request)

    def _record(self, request, m: compare.Mismatch) -> None:
        self.mismatches[request.node.nodeid] = m
        request.node.user_properties.append(
            (
                MISMATCH_PROPERTY,
                json.dumps({"rows": m.rows, "columns": m.columns}, sort_keys=True),
            )
        )

    def _check_engagement(self, case: Case, data: bytes, request) -> None:
        if compare.digest_matches(data, case.entry, self.key):
            return
        name = compare.golden_name(case.unit_id, case.unit)
        path = self.private / "goldens" / "engagement" / name
        if path.is_file():
            m = compare.mismatch(data, path.read_bytes(), case.unit.get("kind"))
        else:
            n = case.entry.get("rows") or 0
            m = compare.Mismatch(
                n, {"(private golden missing)": n}, "private golden missing\n"
            )
        logs = self.private / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        report = (
            logs
            / f"{self._stamp}-equivalence-{self.dataset}-{self.db}-{canon.file_safe(case.unit_id)}.datacompy.txt"
        )
        report.write_text(f"{request.node.nodeid}\n{m.report}", encoding="utf-8")
        self._record(request, m)
        pytest.fail(m.message(), pytrace=False)

    def _check_seed(self, case: Case, data: bytes, expected: Path, request) -> None:
        obtained_dir = os.environ.get("EQUIVALENCE_OBTAINED")
        directory = (
            Path(obtained_dir) if obtained_dir else request.getfixturevalue("tmp_path")
        )
        directory.mkdir(parents=True, exist_ok=True)
        ext = compare.extension(case.unit)
        obtained = directory / f"{canon.file_safe(case.unit_id)}.obtained{ext}"
        regression = request.getfixturevalue("file_regression")
        failed = False
        try:
            regression.check(
                data, binary=True, fullpath=expected, obtained_filename=obtained
            )
        except AssertionError:
            failed = True
        if failed:
            want = expected.read_bytes()
            m = compare.mismatch(data, want, case.unit.get("kind"))
            self._record(request, m)
            pytest.fail(
                m.message() + "\n" + compare.text_diff(data, want), pytrace=False
            )

    # -------------------------------------------------------------- session end

    def pytest_sessionfinish(self, session, exitstatus):
        if self.not_implemented and not self.allow_ni and session.exitstatus == 0:
            session.exitstatus = 1
            reporter = session.config.pluginmanager.get_plugin("terminalreporter")
            if reporter is not None:
                reporter.write_line(
                    f"{len(self.not_implemented)} not-implemented outcomes fail the session "
                    "(--eq-allow-not-implemented not given)"
                )

    def pytest_unconfigure(self, config):
        path = getattr(config.option, "xmlpath", None)
        if path and Path(path).is_file():
            rewrite_junit(Path(path))


def rewrite_junit(path: Path) -> int:
    """Turn pytest's skips with reason `not-implemented: <unit>` into
    `<skipped type="equivalence.not-implemented" message="not-implemented: <unit>">`."""
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return 0
    n = 0
    for skipped in tree.iter("skipped"):
        message = skipped.get("message") or ""
        if message.startswith(NOT_IMPLEMENTED):
            skipped.set("type", NI_TYPE)
            skipped.text = message
            n += 1
    if n:
        tree.write(path, encoding="utf-8", xml_declaration=True)
    return n


# ------------------------------------------------------------------ fixtures


@pytest.fixture(scope="session")
def eq_runner(request) -> Runner:
    runner = request.config.pluginmanager.get_plugin(RUNNER_NAME)
    if runner is None:
        pytest.fail("the equivalence runner needs --eq-adapter", pytrace=False)
    return runner


@pytest.fixture(scope="session")
def eq_adapter(eq_runner):
    """The adapter, with the dataset loaded once per process."""
    inst = eq_runner.make_adapter()
    yield inst
    closer = getattr(inst, "close", None)
    if callable(closer):
        closer()


@pytest.fixture
def eq_golden(eq_runner, eq_case):
    """The golden of the case: the manifest entry (engagement) or the expected file (seeds)."""
    return eq_runner.golden(eq_case)
