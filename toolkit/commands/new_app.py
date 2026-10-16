"""Generate the Django app from the pinned cookiecutter-django template plus the pipeline overlay.

`new-app <eng> --out <target>/app [--only core] [--check]` (docs/contracts.md#feature-app-contract,
#cli-registry).

Full run (no --only):
1. Check out stack.app_template once into $HOME/.cache/modernization/cookiecutter-django-<tag>
   (`git clone --depth 1 --branch <tag>`); exit 2 unless `git rev-parse HEAD^{commit}` equals
   stack.app_template.commit.
2. In a temp dir, generate with the cookiecutter Python API (no_input, exactly
   stack.app_template.answers with the engagement's app values; cloud_provider "Azure" when
   hosting.provider is azure-app-service, else "None"), with UV_EXCLUDE_NEWER set to
   stack.app_template.uv_exclude_newer (the template's post-gen hook runs `uv add`).
3. Remove .github/, .pre-commit-config.yaml, .readthedocs.yml and .envs/; pin exclude-newer in
   [tool.uv]; `uv remove` redis, hiredis, django-redis, django-anymail and psycopg (main and dev);
   `uv add` every stack.app_packages pin (main, then --dev); `uv lock`.
4. Apply templates/django-overlay/: project/ mirrors app/, package/ mirrors app/<slug>/ (files
   ending .j2 are rendered with Jinja2, the rest copied); core/ only with --only core; _repo/
   holds target-repo files. Set the pytest options, add .env and backups/ to app/.gitignore and
   write app/.overlay-manifest.json (relative path -> sha256 of every file written, except .env).
5. Copy the manifest files into --out (never .venv/ or __pycache__/), remove files an older
   manifest listed that are no longer generated, write app/.env (DATABASE_URL=$PG_URL/<db_name>)
   when it is missing, render the CI workflow to <target_repo>/.github/workflows/<slug>.yml and
   add this app's ruff-format and djLint hooks to <target_repo>/.pre-commit-config.yaml.

--check generates into a temp dir and compares instead of writing: every manifest file (for
pyproject.toml and uv.lock only that every stack.app_packages pin is present), the core manifest
when app/<slug>/core/.overlay-manifest.json exists, the CI workflow and the app's hook entries.
It prints the differing paths and exits 1; it never writes to --out or the target repo.

--only core renders templates/django-overlay/core/ into app/<slug>/core/ (never core/local/) and
writes app/<slug>/core/.overlay-manifest.json; with --check it compares core only.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import yaml

from toolkit import quiet
from toolkit.engagement import load, pipeline_json, toolkit_root

COMMAND = "new-app"
OVERLAY = toolkit_root() / "templates" / "django-overlay"
CACHE_ROOT = Path.home() / ".cache" / "modernization"
MANIFEST = ".overlay-manifest.json"
DOT_ENV = ".env"
SKIP_DIRS = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
REMOVE_AFTER_GENERATION = (
    ".github",
    ".pre-commit-config.yaml",
    ".readthedocs.yml",
    ".envs",
)
REMOVE_PACKAGES = ("redis", "hiredis", "django-redis", "django-anymail")
PIN_ONLY = ("pyproject.toml", "uv.lock")
GITIGNORE_LINES = (".env", "backups/")
RUFF_HOOKS_REPO = "https://github.com/astral-sh/ruff-pre-commit"
DJLINT_REPO = "https://github.com/Riverside-Healthcare/djLint"
DJLINT_HOOKS = ("djlint-reformat-django", "djlint-django")
PLACEHOLDER = re.compile(r"\{\{e\.([\w.]+)\}\}")
PYTEST_ADDOPTS = 'addopts = "--ds=config.settings.test --import-mode=importlib"'
PYTEST_PYTHONPATH = 'pythonpath = [ ".", ".." ]'


class NewAppError(Exception):
    """Usage, input or environment problem: exit 2. The text names paths, never values."""


# ---------------------------------------------------------------- context and answers


def engagement_value(eng: dict, dotted: str):
    value = eng
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            msg = f"stack.app_template.answers: {{{{e.{dotted}}}}} is not an engagement field"
            raise NewAppError(msg)
        value = value[part]
    return value


def fill(value, eng: dict):
    if isinstance(value, str):
        return PLACEHOLDER.sub(lambda m: str(engagement_value(eng, m.group(1))), value)
    return value


def cloud_provider(eng: dict) -> str:
    provider = ((eng.get("hosting") or {}).get("provider") or "").strip()
    return "Azure" if provider == "azure-app-service" else "None"


def template_answers(template: dict, eng: dict) -> dict:
    """stack.app_template.answers with {{e.*}} filled and cloud_provider from hosting."""
    answers = {
        key: fill(value, eng)
        for key, value in (template.get("answers") or {}).items()
        if not key.startswith("_")
    }
    answers["cloud_provider"] = cloud_provider(eng)
    return answers


def context(eng: dict, stack: dict) -> dict:
    app = eng.get("app") or {}
    slug = app.get("slug")
    if not slug:
        raise NewAppError("engagement: app.slug is missing")
    images = stack.get("docker_images") or {}
    return {
        "slug": slug,
        "project_name": app.get("project_name") or slug,
        "db_name": eng["db_name"],
        "target_root": (eng.get("target") or {}).get("root") or ".",
        "timezone": app.get("timezone") or "UTC",
        "use_tz": bool(app.get("use_tz", False)),
        "cloud_provider": cloud_provider(eng),
        "uv_version": stack.get("uv", ""),
        "postgres_image": (images.get("postgres") or {}).get("image", ""),
        "mssql_image": (images.get("mssql") or {}).get("image", ""),
    }


def app_pins(packages: dict) -> tuple[list[str], list[str]]:
    return list(packages.get("main") or []), list(packages.get("dev") or [])


# ---------------------------------------------------------------- template checkout


def checkout_dir(template: dict, cache_root: Path | None = None) -> Path:
    root = CACHE_ROOT if cache_root is None else cache_root
    return root / f"cookiecutter-django-{template['tag']}"


def ensure_template(template: dict, cache_root: Path | None = None) -> Path:
    """Clone the pinned tag once; NewAppError unless HEAD^{commit} is the pinned commit."""
    path = checkout_dir(template, cache_root)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["git", "clone", "-q", "--depth", "1", "--branch", template["tag"]]
        result = subprocess.run(
            [*cmd, template["repo"], str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            msg = f"git clone of {template['repo']} at {template['tag']} failed"
            raise NewAppError(msg)
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD^{commit}"],
        capture_output=True,
        text=True,
        check=False,
    )
    head = result.stdout.strip()
    if result.returncode != 0 or head != template["commit"]:
        msg = (
            f"template checkout is not at the pinned stack.app_template.commit "
            f"{template['commit']}: HEAD^{{commit}} is {head or 'unknown'} in {path}"
        )
        raise NewAppError(msg)
    return path


# ---------------------------------------------------------------- rendering


def jinja_env():
    import jinja2

    return jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )


def render_text(text: str, ctx: dict) -> str:
    return jinja_env().from_string(text).render(**ctx)


def walk_files(root: Path, skip_top: tuple[str, ...] = ()) -> list[Path]:
    """Files under root, sorted, without SKIP_DIRS and without the top-level names in skip_top."""
    out: list[Path] = []
    if not root.is_dir():
        return out
    for d, dirs, files in os.walk(root):
        here = Path(d)
        dirs[:] = sorted(
            x
            for x in dirs
            if x not in SKIP_DIRS and not (here == root and x in skip_top)
        )
        for name in sorted(files):
            if name.endswith((".pyc", ".pyo")):
                continue
            if here == root and name in skip_top:
                continue
            out.append(here / name)
    return out


def render_tree(
    src: Path, dest: Path, ctx: dict, skip_top: tuple[str, ...] = ()
) -> list[str]:
    """Render .j2 files and copy the rest from src into dest; returns the written paths."""
    written: list[str] = []
    for path in walk_files(src, skip_top):
        rel = path.relative_to(src).as_posix()
        if rel.endswith(".j2"):
            rel = rel[: -len(".j2")]
            data = render_text(path.read_text(encoding="utf-8"), ctx).encode("utf-8")
        else:
            data = path.read_bytes()
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        shutil.copymode(path, target)
        written.append(rel)
    return written


def render_workflow(ctx: dict, overlay: Path | None = None) -> str:
    overlay = OVERLAY if overlay is None else overlay
    source = overlay / "_repo" / "workflow.yml.j2"
    return render_text(source.read_text(encoding="utf-8"), ctx)


# ---------------------------------------------------------------- manifest


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_files(app: Path, exclude_dirs: tuple[str, ...] = ()) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in walk_files(app):
        rel = path.relative_to(app).as_posix()
        if rel in (MANIFEST, DOT_ENV) or any(
            rel == d or rel.startswith(d + "/") for d in exclude_dirs
        ):
            continue
        files[rel] = sha256_file(path)
    return files


def manifest_json(files: dict[str, str], template_commit: str | None = None) -> str:
    obj: dict = {"format_version": 1, "files": files}
    if template_commit:
        obj["template_commit"] = template_commit
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def read_manifest(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        msg = f"{path}: not valid JSON"
        raise NewAppError(msg) from exc
    if data.get("format_version") != 1:
        msg = f"{path}: unknown format_version"
        raise NewAppError(msg)
    return dict(data.get("files") or {})


# ---------------------------------------------------------------- pins


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def split_pin(pin: str) -> tuple[str, str]:
    name, _, version = pin.partition("==")
    return normalize_name(name.split("[", 1)[0].strip()), version.strip()


def normalize_spec(spec: str) -> str:
    return re.sub(r"\s+", "", spec).lower()


def missing_pins(app: Path, pins: list[str]) -> list[str]:
    """Pins absent from app/pyproject.toml (as specs) or app/uv.lock (as name and version)."""
    problems: list[str] = []
    try:
        pyproject = tomllib.loads((app / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ["pyproject.toml: missing or not TOML"]
    try:
        lock = tomllib.loads((app / "uv.lock").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ["uv.lock: missing or not TOML"]
    specs = list((pyproject.get("project") or {}).get("dependencies") or [])
    for group in (pyproject.get("dependency-groups") or {}).values():
        specs.extend(s for s in group if isinstance(s, str))
    spec_set = {normalize_spec(s) for s in specs}
    locked = {
        (normalize_name(p.get("name", "")), p.get("version"))
        for p in lock.get("package") or []
    }
    for pin in pins:
        if normalize_spec(pin) not in spec_set:
            problems.append(f"pyproject.toml: pin {pin} missing")
        if split_pin(pin) not in locked:
            problems.append(f"uv.lock: pin {pin} missing")
    return problems


# ---------------------------------------------------------------- pre-commit hooks


def hooks_pattern(target_root: str) -> str:
    return "^app/" if target_root in (".", "") else f"^{target_root}/app/"


def wanted_hooks() -> tuple[tuple[str, str], ...]:
    return (
        (RUFF_HOOKS_REPO, "ruff-format"),
        *((DJLINT_REPO, hook) for hook in DJLINT_HOOKS),
    )


def missing_app_hooks(config_text: str, target_root: str) -> list[tuple[str, str]]:
    """(repo, hook id) pairs of this app's hooks the config lacks (files = the app pattern)."""
    try:
        config = yaml.safe_load(config_text) or {}
    except yaml.YAMLError as exc:
        msg = "target repo .pre-commit-config.yaml is not valid YAML"
        raise NewAppError(msg) from exc
    pattern = hooks_pattern(target_root)
    have = set()
    for entry in config.get("repos") or []:
        if not isinstance(entry, dict):
            continue
        for hook in entry.get("hooks") or []:
            if isinstance(hook, dict) and hook.get("files") == pattern:
                have.add((entry.get("repo"), hook.get("id")))
    return [pair for pair in wanted_hooks() if pair not in have]


def ruff_rev(config_text: str, fallback: str) -> str:
    """The rev of the repo that holds S0's ruff-check hook."""
    config = yaml.safe_load(config_text) or {}
    for entry in config.get("repos") or []:
        if not isinstance(entry, dict) or entry.get("repo") != RUFF_HOOKS_REPO:
            continue
        ids = {h.get("id") for h in entry.get("hooks") or [] if isinstance(h, dict)}
        if "ruff-check" in ids and entry.get("rev"):
            return str(entry["rev"])
    return fallback


def merge_app_hooks(
    config_text: str,
    slug: str,
    target_root: str,
    revs: dict[str, str],
) -> str:
    """Append this app's missing hooks as remote repo entries; existing text stays as it is."""
    missing = missing_app_hooks(config_text, target_root)
    if not missing:
        return config_text
    pattern = hooks_pattern(target_root)
    lines = [f"  # {slug} app hooks (toolkit new-app), scoped to {pattern}"]
    for repo in (RUFF_HOOKS_REPO, DJLINT_REPO):
        ids = [hook for r, hook in missing if r == repo]
        if not ids:
            continue
        lines += [f"  - repo: {repo}", f"    rev: {revs[repo]}", "    hooks:"]
        for hook in ids:
            lines += [f"      - id: {hook}", f"        files: '{pattern}'"]
    text = config_text if config_text.endswith("\n") else config_text + "\n"
    merged = text + "\n".join(lines) + "\n"
    if missing_app_hooks(merged, target_root):
        msg = "target repo .pre-commit-config.yaml: repos is not the last key; add the hooks by hand"
        raise NewAppError(msg)
    return merged


def djlint_rev_of(config_path: Path) -> str | None:
    if not config_path.is_file():
        return None
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    for entry in config.get("repos") or []:
        if isinstance(entry, dict) and entry.get("repo") == DJLINT_REPO:
            return str(entry.get("rev"))
    return None


# ---------------------------------------------------------------- generation


@contextlib.contextmanager
def environment(**values: str):
    old = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def uv(app: Path, *args: str, exclude_newer: str) -> None:
    env = {**os.environ, "UV_EXCLUDE_NEWER": exclude_newer}
    result = subprocess.run(
        ["uv", *args],
        cwd=app,
        env=env,
        stdout=sys.stderr,
        stderr=sys.stderr,
        check=False,
    )
    if result.returncode != 0:
        msg = f"uv {args[0]} failed in the generated app (exit {result.returncode})"
        raise NewAppError(msg)


def set_pytest_options(pyproject: Path) -> None:
    text = pyproject.read_text(encoding="utf-8")
    section = re.search(r"(?ms)^\[tool\.pytest\.ini_options\]\n(.*?)(?=^\[|\Z)", text)
    if not section:
        msg = "generated pyproject.toml has no [tool.pytest.ini_options]"
        raise NewAppError(msg)
    body = section.group(1)
    body = re.sub(r"(?m)^pythonpath = .*\n", "", body)
    new_body, count = re.subn(
        r"(?m)^addopts = .*$",
        PYTEST_ADDOPTS + "\n" + PYTEST_PYTHONPATH,
        body,
    )
    if count != 1:
        msg = "generated pyproject.toml: [tool.pytest.ini_options] has no addopts line"
        raise NewAppError(msg)
    pyproject.write_text(
        text[: section.start(1)] + new_body + text[section.end(1) :], encoding="utf-8"
    )


def pin_exclude_newer(pyproject: Path, exclude_newer: str) -> None:
    text = pyproject.read_text(encoding="utf-8")
    if re.search(r"(?m)^\[tool\.uv\]", text):
        msg = "generated pyproject.toml already has [tool.uv]"
        raise NewAppError(msg)
    text = text.rstrip("\n") + f'\n\n[tool.uv]\nexclude-newer = "{exclude_newer}"\n'
    pyproject.write_text(text, encoding="utf-8")


def extend_gitignore(path: Path) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    present = set(text.splitlines())
    add = [line for line in GITIGNORE_LINES if line not in present]
    if add:
        text = text if not text or text.endswith("\n") else text + "\n"
        text += "\n# toolkit new-app: local settings and database backups\n"
        text += "\n".join(add) + "\n"
        path.write_text(text, encoding="utf-8")


def cookiecutter_keys(checkout: Path) -> set[str]:
    data = json.loads((checkout / "cookiecutter.json").read_text(encoding="utf-8"))
    return {key for key in data if not key.startswith("_")}


def generate(
    ctx: dict, eng: dict, template: dict, packages: dict, checkout: Path, workdir: Path
) -> dict:
    """Generate app/ in workdir; returns {"app": Path, "djlint_rev": str | None}."""
    if shutil.which("uv") is None:
        msg = "uv is not on PATH (the template's post-gen hook runs uv add)"
        raise NewAppError(msg)
    from cookiecutter.main import cookiecutter

    answers = template_answers(template, eng)
    expected = cookiecutter_keys(checkout)
    if set(answers) != expected:
        diff = sorted(set(answers) ^ expected)
        msg = f"stack.app_template.answers keys differ from cookiecutter.json: {diff}"
        raise NewAppError(msg)
    exclude_newer = template["uv_exclude_newer"]
    config = workdir / "cookiecutter-config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "cookiecutters_dir": str(workdir / "cookiecutters"),
                "replay_dir": str(workdir / "replay"),
            }
        ),
        encoding="utf-8",
    )
    with environment(UV_EXCLUDE_NEWER=exclude_newer):
        project = Path(
            cookiecutter(
                str(checkout),
                no_input=True,
                extra_context=answers,
                output_dir=str(workdir / "generated"),
                config_file=str(config),
            )
        )
    app = workdir / "app"
    project.rename(app)
    djlint_rev = djlint_rev_of(app / ".pre-commit-config.yaml")
    for name in REMOVE_AFTER_GENERATION:
        path = app / name
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    pin_exclude_newer(app / "pyproject.toml", exclude_newer)
    main, dev = app_pins(packages)
    uv(app, "remove", "--no-sync", *REMOVE_PACKAGES, exclude_newer=exclude_newer)
    uv(app, "remove", "--no-sync", "psycopg", exclude_newer=exclude_newer)
    uv(app, "remove", "--no-sync", "--dev", "psycopg", exclude_newer=exclude_newer)
    if main:
        uv(app, "add", "--no-sync", *main, exclude_newer=exclude_newer)
    if dev:
        uv(app, "add", "--no-sync", "--dev", *dev, exclude_newer=exclude_newer)
    uv(app, "lock", exclude_newer=exclude_newer)
    render_tree(OVERLAY / "project", app, ctx)
    render_tree(OVERLAY / "package", app / ctx["slug"], ctx)
    set_pytest_options(app / "pyproject.toml")
    extend_gitignore(app / ".gitignore")
    for path in app.rglob("__pycache__"):
        shutil.rmtree(path, ignore_errors=True)
    files = manifest_files(app)
    (app / MANIFEST).write_text(
        manifest_json(files, template["commit"]), encoding="utf-8"
    )
    return {"app": app, "djlint_rev": djlint_rev}


# ---------------------------------------------------------------- compare and copy


def compare_app(fresh: Path, out: Path, pins: list[str]) -> list[str]:
    fresh_files = read_manifest(fresh / MANIFEST)
    out_files = read_manifest(out / MANIFEST)
    problems: list[str] = []
    if not (out / MANIFEST).is_file():
        problems.append(f"missing: {MANIFEST}")
    for rel in sorted(set(fresh_files) | set(out_files)):
        if rel in PIN_ONLY:
            continue
        if rel not in fresh_files:
            problems.append(f"not generated any more: {rel}")
            continue
        target = out / rel
        if not target.is_file():
            problems.append(f"missing: {rel}")
        elif sha256_file(target) != fresh_files[rel]:
            problems.append(f"differs: {rel}")
    problems.extend(missing_pins(out, pins))
    return problems


def copy_app(fresh: Path, out: Path) -> tuple[int, int]:
    """Copy the manifest files into out; remove files only an older manifest listed."""
    new = read_manifest(fresh / MANIFEST)
    old = read_manifest(out / MANIFEST)
    out.mkdir(parents=True, exist_ok=True)
    for rel in sorted(new):
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fresh / rel, target)
        shutil.copymode(fresh / rel, target)
    removed = 0
    for rel in sorted(set(old) - set(new)):
        target = out / rel
        if target.is_file():
            target.unlink()
            removed += 1
    shutil.copyfile(fresh / MANIFEST, out / MANIFEST)
    return len(new), removed


def write_dot_env(out: Path, db_name: str) -> str:
    path = out / DOT_ENV
    if path.exists():
        return "app/.env exists: left as it is"
    server = (os.environ.get("PG_URL") or "").strip().rstrip("/")
    if not server:
        return "PG_URL is not set: app/.env not written"
    path.write_text(f"DATABASE_URL={server}/{db_name}\n", encoding="utf-8")
    path.chmod(0o600)
    return "wrote app/.env (DATABASE_URL of the app dev database)"


# ---------------------------------------------------------------- core


def core_paths(out: Path, slug: str) -> tuple[Path, Path]:
    source = OVERLAY / "core"
    if not source.is_dir():
        msg = f"{source} does not exist (S4.data builds it)"
        raise NewAppError(msg)
    return source, out / slug / "core"


def render_core(source: Path, dest: Path, ctx: dict) -> dict[str, str]:
    render_tree(source, dest, ctx, skip_top=("local",))
    return manifest_files(dest, exclude_dirs=("local",))


def compare_core(out: Path, ctx: dict) -> list[str]:
    source, dest = core_paths(out, ctx["slug"])
    with tempfile.TemporaryDirectory(prefix="new-app-core-") as tmp:
        fresh = render_core(source, Path(tmp), ctx)
    listed = read_manifest(dest / MANIFEST)
    prefix = f"{ctx['slug']}/core/"
    problems: list[str] = []
    if not (dest / MANIFEST).is_file():
        problems.append(f"missing: {prefix}{MANIFEST}")
    for rel in sorted(set(fresh) | set(listed)):
        if rel == "local" or rel.startswith("local/"):
            continue
        if rel not in fresh:
            problems.append(f"not generated any more: {prefix}{rel}")
        elif not (dest / rel).is_file():
            problems.append(f"missing: {prefix}{rel}")
        elif sha256_file(dest / rel) != fresh[rel]:
            problems.append(f"differs: {prefix}{rel}")
    return problems


def apply_core(out: Path, ctx: dict) -> tuple[int, int]:
    source, dest = core_paths(out, ctx["slug"])
    old = read_manifest(dest / MANIFEST)
    with tempfile.TemporaryDirectory(prefix="new-app-core-") as tmp:
        fresh_dir = Path(tmp)
        fresh = render_core(source, fresh_dir, ctx)
        for rel in sorted(fresh):
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(fresh_dir / rel, target)
            shutil.copymode(fresh_dir / rel, target)
    removed = 0
    for rel in sorted(set(old) - set(fresh)):
        if rel.startswith("local/"):
            continue
        if (dest / rel).is_file():
            (dest / rel).unlink()
            removed += 1
    (dest / MANIFEST).write_text(manifest_json(fresh), encoding="utf-8")
    return len(fresh), removed


# ---------------------------------------------------------------- command


def add_arguments(parser) -> None:
    parser.add_argument("eng", help="the engagement.json")
    parser.add_argument("--out", required=True, help="<target>/app")
    parser.add_argument(
        "--only",
        choices=["core"],
        help="apply only templates/django-overlay/core/ to app/<slug>/core/",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare only; exit 1 on drift, write nothing",
    )


def workflow_path(eng: dict, slug: str) -> Path:
    return Path(eng["paths"]["target_repo"]) / ".github" / "workflows" / f"{slug}.yml"


def precommit_path(eng: dict) -> Path:
    return Path(eng["paths"]["target_repo"]) / ".pre-commit-config.yaml"


def repo_problems(eng: dict, ctx: dict) -> list[str]:
    problems: list[str] = []
    wf = workflow_path(eng, ctx["slug"])
    rel_wf = f".github/workflows/{ctx['slug']}.yml"
    if not wf.is_file():
        problems.append(f"missing: {rel_wf}")
    elif wf.read_text(encoding="utf-8") != render_workflow(ctx):
        problems.append(f"differs: {rel_wf}")
    pc = precommit_path(eng)
    if not pc.is_file():
        problems.append("missing: .pre-commit-config.yaml")
    else:
        for repo, hook in missing_app_hooks(
            pc.read_text(encoding="utf-8"), ctx["target_root"]
        ):
            problems.append(
                f".pre-commit-config.yaml: no {hook} hook from {repo} for this app"
            )
    return problems


def write_repo_files(
    eng: dict, ctx: dict, djlint_rev: str | None, ruff_pin: str
) -> None:
    wf = workflow_path(eng, ctx["slug"])
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text(render_workflow(ctx), encoding="utf-8")
    pc = precommit_path(eng)
    if not pc.is_file():
        msg = f"{pc} is missing (S0 writes it)"
        raise NewAppError(msg)
    text = pc.read_text(encoding="utf-8")
    revs = {
        RUFF_HOOKS_REPO: ruff_rev(text, ruff_pin),
        DJLINT_REPO: djlint_rev or "v1.36.4",
    }
    merged = merge_app_hooks(text, ctx["slug"], ctx["target_root"], revs)
    if merged != text:
        pc.write_text(merged, encoding="utf-8")


def ruff_fallback_rev(packages: dict) -> str:
    for pin in packages.get("dev") or []:
        name, version = split_pin(pin)
        if name == "ruff" and version:
            return f"v{version}"
    return "v0.17.1"


def run_core(args, ctx: dict, out: Path) -> int:
    if args.check:
        problems = compare_core(out, ctx)
        for p in problems:
            quiet.progress(p)
        return quiet.summary(
            None,
            COMMAND,
            1 if problems else 0,
            counts={"differences": len(problems)},
            paths=[str(out / ctx["slug"] / "core")],
            messages=problems or ["core matches the overlay"],
        )
    written, removed = apply_core(out, ctx)
    return quiet.summary(
        None,
        COMMAND,
        0,
        counts={"files": written, "removed": removed},
        paths=[str(out / ctx["slug"] / "core")],
        messages=[
            f"applied the overlay's core/ ({written} files); core/local/ untouched"
        ],
    )


def run(args) -> int:
    try:
        eng = load(args.eng)
        stack = pipeline_json().get("stack") or {}
        template = stack.get("app_template") or {}
        packages = stack.get("app_packages") or {}
        ctx = context(eng, stack)
        out = Path(args.out).resolve()
        if args.only == "core":
            return run_core(args, ctx, out)
        for key in ("repo", "tag", "commit", "uv_exclude_newer"):
            if not template.get(key):
                msg = f"pipeline.json stack.app_template.{key} is missing"
                raise NewAppError(msg)
        main, dev = app_pins(packages)
        checkout = ensure_template(template)
        with tempfile.TemporaryDirectory(prefix="new-app-") as tmp:
            result = generate(ctx, eng, template, packages, checkout, Path(tmp))
            fresh = result["app"]
            if args.check:
                problems = compare_app(fresh, out, main + dev)
                core_manifest = out / ctx["slug"] / "core" / MANIFEST
                if core_manifest.is_file():
                    problems.extend(compare_core(out, ctx))
                problems.extend(repo_problems(eng, ctx))
                for p in problems:
                    quiet.progress(p)
                return quiet.summary(
                    None,
                    COMMAND,
                    1 if problems else 0,
                    engagement=eng.get("id"),
                    counts={
                        "files": len(read_manifest(fresh / MANIFEST)),
                        "differences": len(problems),
                    },
                    paths=[str(out)],
                    messages=problems or ["app, CI workflow and hooks match new-app"],
                )
            copied, removed = copy_app(fresh, out)
            env_message = write_dot_env(out, ctx["db_name"])
            write_repo_files(
                eng, ctx, result["djlint_rev"], ruff_fallback_rev(packages)
            )
    except NewAppError as exc:
        print(f"{COMMAND}: {exc}", file=sys.stderr)
        return quiet.summary("error", COMMAND, 2, messages=[str(exc)])
    return quiet.summary(
        None,
        COMMAND,
        0,
        engagement=eng.get("id"),
        counts={"files": copied, "removed": removed},
        paths=[str(out), str(workflow_path(eng, ctx["slug"]))],
        messages=[f"generated {copied} files into {out}", env_message],
    )
