"""Engagement loading (docs/contracts.md#engagement-loading).

`load(path)` reads `<records>/engagement.json`, the sibling `paths.local.json` and the optional
`source.lock.json` and `inventory.lock.json`, substitutes the six `{{p.*}}` placeholders, derives
`db_name`, fills null source pins from the lock and validates the result. Keys that start with `_`
are ignored at every depth (they are dropped from the returned object). Any problem raises
`EngagementError` (exit 2) naming the field; problem texts never carry field values.

The returned dict is the engagement plus:
- `db_name` (always set),
- `paths` (the six paths of paths.local.json),
- `inventory_lock` (dict or None),
- `source_lock` (dict or None).
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

PATH_KEYS = ("pipeline", "target_repo", "target", "private", "records", "env_file")
PLACEHOLDER = re.compile(r"\{\{([\w.]+)\}\}")
MARKER = re.compile(r"<[^<>]{1,80}>")
ID_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
DB_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,29}$")
REF_RE = re.compile(r"^(env:[A-Z_][A-Z0-9_]*|pass:\S+)$")
REPO_RE = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
LINK_TYPES = ("odbc-sqlserver", "sharepoint", "excel", "text")
EXECUTION = ("local", "cloud-allowed")
DATA_ZONE_DIRS = (
    "sources",
    "datasets",
    "workbook",
    "reference",
    "goldens",
    "corpus",
    "results",
    "logs",
    "assets",
)
DESIGN_ZONE_DIRS = ("out", "review", "synthetic")


class EngagementError(Exception):
    """An engagement that does not load: exit 2. str() is safe to print (no values)."""

    exit_code = 2

    def __init__(self, problems):
        if isinstance(problems, str):
            problems = [problems]
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def toolkit_root() -> Path:
    return Path(__file__).resolve().parent.parent


_PIPELINE_CACHE: dict = {}


def pipeline_json() -> dict:
    """pipeline.json of the toolkit root (source_kinds, slug_reserved, stack)."""
    path = toolkit_root() / "pipeline.json"
    if path not in _PIPELINE_CACHE:
        try:
            _PIPELINE_CACHE[path] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise EngagementError(
                f"pipeline.json: cannot read ({type(exc).__name__})"
            ) from None
    return _PIPELINE_CACHE[path]


def derive_db_name(eng: dict) -> str:
    name = eng.get("db_name")
    if isinstance(name, str) and name:
        return name
    return re.sub(r"[^a-z0-9]", "_", str(eng.get("id", "")))


def strip_private(value):
    """Drop keys that start with _ at every depth."""
    if isinstance(value, dict):
        return {
            k: strip_private(v) for k, v in value.items() if not str(k).startswith("_")
        }
    if isinstance(value, list):
        return [strip_private(v) for v in value]
    return value


def _walk_strings(value, path=""):
    if isinstance(value, dict):
        for k, v in value.items():
            if str(k).startswith("_"):
                continue
            yield from _walk_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk_strings(v, f"{path}[{i}]")
    elif isinstance(value, str):
        yield path, value


def _substitute(value, paths):
    if isinstance(value, dict):
        return {
            k: (v if str(k).startswith("_") else _substitute(v, paths))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_substitute(v, paths) for v in value]
    if isinstance(value, str):

        def repl(m):
            key = m.group(1)
            if key.startswith("p.") and key[2:] in paths:
                return paths[key[2:]]
            return m.group(0)

        return PLACEHOLDER.sub(repl, value)
    return value


def read_json(path: Path, what: str) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise EngagementError(f"{what}: missing file {path}") from None
    except OSError as exc:
        raise EngagementError(
            f"{what}: cannot read {path} ({type(exc).__name__})"
        ) from None
    try:
        data = json.loads(text)
    except ValueError:
        raise EngagementError(f"{what}: not valid JSON") from None
    if not isinstance(data, dict):
        raise EngagementError(f"{what}: not a JSON object")
    return data


def read_paths(records: Path) -> dict:
    """paths.local.json beside engagement.json (docs/contracts.md#paths-local-json)."""
    data = read_json(records / "paths.local.json", "paths.local.json")
    problems = []
    if data.get("format_version") != 1:
        problems.append("paths.local.json: format_version: must be 1")
    paths = {}
    for key in PATH_KEYS:
        value = data.get(key)
        if not isinstance(value, str) or not value:
            problems.append(f"paths.local.json: {key}: missing")
            continue
        if (
            not value.startswith("/")
            or "~" in value
            or "$" in value
            or re.search(r"\s", value)
            or (value.endswith("/") and value != "/")
        ):
            problems.append(
                f"paths.local.json: {key}: must be absolute, expanded, without trailing / or whitespace"
            )
            continue
        paths[key] = value
    if problems:
        raise EngagementError(problems)
    return paths


def _fill_pins(eng: dict, lock: dict, private: str) -> None:
    src = eng.setdefault("source", {})
    lsrc = lock.get("source") or {}
    if src.get("bytes") is None and lsrc.get("bytes") is not None:
        src["bytes"] = lsrc["bytes"]
    if src.get("sha256") is None and lsrc.get("sha256") is not None:
        src["sha256"] = lsrc["sha256"]
    git = lsrc.get("git") or {}
    if src.get("commit") is None and git.get("commit"):
        src["commit"] = git["commit"]
    dcopy = lock.get("data_copy") or {}
    if src.get("data_copy_sha256") is None and dcopy.get("sha256"):
        src["data_copy_sha256"] = dcopy["sha256"]
    locked = {}
    for item in lock.get("backends") or []:
        if isinstance(item, dict) and item.get("path"):
            locked[item["path"]] = item
    for backend in src.get("backends") or []:
        if not isinstance(backend, dict) or backend.get("sha256") is not None:
            continue
        rel = relative_to_private(backend.get("path"), private)
        if rel in locked and locked[rel].get("sha256"):
            backend["sha256"] = locked[rel]["sha256"]


def relative_to_private(path, private: str):
    if not isinstance(path, str):
        return None
    prefix = private.rstrip("/") + "/"
    return path.removeprefix(prefix)


def check(eng: dict, paths: dict) -> list[str]:
    """Contract §2 step 6 checks on a substituted engagement (underscore keys already ignored)."""
    problems = []
    for field, value in _walk_strings(eng):
        if "{{" in value:
            m = PLACEHOLDER.search(value)
            what = m.group(0) if m else "{{"
            problems.append(f"engagement: {field}: unresolved {what}")
        elif MARKER.search(value):
            problems.append(f"engagement: {field}: template marker <...> left")
    ident = eng.get("id")
    if not isinstance(ident, str) or not ID_RE.match(ident) or len(ident) > 40:
        problems.append(
            "engagement: id: must match ^[a-z][a-z0-9]*(-[a-z0-9]+)*$, at most 40 characters"
        )
    db_name = derive_db_name(eng)
    if not DB_NAME_RE.match(db_name):
        problems.append("engagement: db_name: must match ^[a-z][a-z0-9_]{1,40}$")
    source = eng.get("source")
    if not isinstance(source, dict):
        problems.append("engagement: source: missing")
        source = {}
    try:
        kinds = pipeline_json().get("source_kinds", [])
    except EngagementError as exc:
        problems.append(str(exc))
        kinds = []
    if source.get("kind") not in kinds:
        problems.append("engagement: source.kind: not in pipeline.json source_kinds")
    app = eng.get("app") if isinstance(eng.get("app"), dict) else {}
    slug = app.get("slug")
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        problems.append("engagement: app.slug: must match ^[a-z][a-z0-9_]{1,29}$")
    policy = eng.get("data_policy") if isinstance(eng.get("data_policy"), dict) else {}
    if policy.get("execution") not in EXECUTION:
        problems.append(
            "engagement: data_policy.execution: must be local or cloud-allowed"
        )
    records = eng.get("records")
    if records is not None and records != paths.get("records"):
        problems.append("engagement: records: differs from paths.local.json records")
    secrets = eng.get("secrets") if isinstance(eng.get("secrets"), dict) else {}
    refs = []
    gp = secrets.get("github_push")
    if isinstance(gp, dict):
        refs.append(("secrets.github_push.ref", gp.get("ref")))
    for key in (
        "accdb_password_ref",
        "stage1_target_url_ref",
        "staging_db_url_ref",
        "production_db_url_ref",
    ):
        refs.append((f"secrets.{key}", secrets.get(key)))
    entra = secrets.get("entra")
    if isinstance(entra, dict):
        refs.append(("secrets.entra.client_secret_ref", entra.get("client_secret_ref")))
    for i, backend in enumerate(source.get("backends") or []):
        if isinstance(backend, dict):
            refs.append(
                (f"source.backends[{i}].password_ref", backend.get("password_ref"))
            )
    for field, ref in refs:
        if ref is not None and (not isinstance(ref, str) or not REF_RE.match(ref)):
            problems.append(
                f"engagement: {field}: must be null or env:NAME or pass:<path>"
            )
    for i, link in enumerate(source.get("external_links") or []):
        if not isinstance(link, dict) or link.get("type") not in LINK_TYPES:
            problems.append(
                f"engagement: source.external_links[{i}].type: must be one of {', '.join(LINK_TYPES)}"
            )
    target = eng.get("target") if isinstance(eng.get("target"), dict) else {}
    repo = target.get("repo")
    if repo is not None and (not isinstance(repo, str) or not REPO_RE.match(repo)):
        problems.append("engagement: target.repo: must be null or a GitHub owner/name")
    return problems


def _load(path) -> tuple[dict | None, dict | None, list[str]]:
    """Load without raising on validation problems (engagement-lint prints them all)."""
    path = Path(path)
    if path.name != "engagement.json":
        raise EngagementError(f"engagement: {path}: file must be named engagement.json")
    raw = read_json(path, "engagement")
    records = path.resolve().parent
    paths = read_paths(records)
    eng = _substitute(copy.deepcopy(raw), paths)
    eng = strip_private(eng)
    problems = check(eng, paths)
    eng["db_name"] = derive_db_name(eng)
    eng["paths"] = dict(paths)
    lock_path = records / "source.lock.json"
    eng["source_lock"] = None
    if lock_path.exists():
        lock = read_json(lock_path, "source.lock.json")
        if lock.get("format_version") != 1:
            problems.append("source.lock.json: format_version: must be 1")
        else:
            eng["source_lock"] = lock
            _fill_pins(eng, lock, paths["private"])
    inv_path = records / "inventory.lock.json"
    eng["inventory_lock"] = None
    if inv_path.exists():
        inv = read_json(inv_path, "inventory.lock.json")
        if inv.get("format_version") not in (None, 1):
            problems.append("inventory.lock.json: format_version: must be 1")
        else:
            eng["inventory_lock"] = inv
    return eng, raw, problems


def load(path) -> dict:
    eng, _raw, problems = _load(path)
    if problems:
        raise EngagementError(problems)
    return eng


def data_zone(private: str) -> list[str]:
    """Absolute data-zone paths of an engagement: the nine directories and scrub-map.json."""
    base = private.rstrip("/")
    return [f"{base}/{d}" for d in DATA_ZONE_DIRS] + [f"{base}/scrub-map.json"]
