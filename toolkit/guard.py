"""Copy guard (S0.guard; docs/contracts.md#cli-registry, `guard`).

Stops verbatim client or Microsoft text and forbidden files from reaching a repo. Two checks run
over each repo's candidate set:

(a) the repo's own pre-commit hooks `forbid-source-files`, `forbid-sample-outputs` and
    `check-added-large-files`, through `pre-commit run <hook> --files <candidates>`. Guard first
    reads the repo's `.pre-commit-config.yaml` and refuses (GuardError, exit 2) a config that
    defines those hooks differently from decisions E1, so it never runs a command a repo defines.
    History mode applies the same regexes to path names and the repo's size limit to blob sizes.
(b) copydetect (stack.guard parameters, disable_filtering, extensions ['*']) over every UTF-8 text
    candidate against a corpus rebuilt from the engagement source on every run.

Candidate sets: worktree (what `git add -A` would add), staged, or history (every blob reachable
from any ref). The corpus lives in `<private>/corpus/` (data zone): `code/<kind>-<n>.txt`,
`data/<n>.txt` and `strings.txt` (one data string per line, used by mp-data to withhold summary
lines). Results name paths, hooks, scores and corpus file names, never matched text.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

HOOK_SOURCE = "forbid-source-files"
HOOK_SAMPLE = "forbid-sample-outputs"
HOOK_LARGE = "check-added-large-files"
HOOKS = (HOOK_SOURCE, HOOK_SAMPLE, HOOK_LARGE)

# decisions E1: the forbidden-file patterns both repos carry
SOURCE_FILES = (
    r"(?i)([.](accdt|accdb|accde|mdb|mde|laccdb|ldb|mdw|xlsm|xlsb|xls|xlam|bas|cls|frm|frx|msg"
    r"|bak)$|^(private|data|vendor)/)"
)
SAMPLE_FILES = r"(?i)[.](pdf|eml|xlsx)$"
FIXTURES_EXCLUDE = "^toolkit/tests/fixtures/"
LARGE_REPO = "https://github.com/pre-commit/pre-commit-hooks"
LARGE_REV = "v6.0.0"
PIPELINE_MAXKB = 1024
TARGET_MAXKB = 500

LOCAL_KEYS = {"id", "name", "language", "entry", "files", "exclude"}
REMOTE_KEYS = {"id", "args"}

SOURCE_MISSING = "source missing: run mp-data fetch"
EMPTY_CORPUS = "corpus would be empty: the source has no code, design text or data strings"
MP_DATA_PRE_COMMIT = Path(".local") / "share" / "modernization" / "pre-commit"

# SaveAsText properties that carry no client text (names, fonts, binary layout)
SKIP_PROPS = {
    "name",
    "fontname",
    "guid",
    "picture",
    "objectpalette",
    "format",
    "inputmask",
    "prtmip",
    "prtdevmode",
    "prtdevnames",
    "prtdevmodew",
    "prtdevnamesw",
}
_NOT_A_STRING = re.compile(r"^[\s\d.,:;+\-/\\()$%TZ]*$")
_GUID = re.compile(r"^\{?[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}?$")
_LARGE_LINE = re.compile(r"^(.*) \(\d+ KB\) exceeds \d+ KB\.$")

# Format text that Access and the VBA editor write into every export (copydetect boilerplate):
# module header lines, event-procedure signatures and their End lines, and macro XML markup.
_VBA_HEADER = re.compile(r"^(VERSION \d|BEGIN\s*$|\s+MultiUse\s*=|END\s*$|Attribute VB_|Option )")
_VBA_EVENT = re.compile(
    r"^\s*(Private |Public |Friend )?(Static )?(Sub|Function|Property (Get|Let|Set)) "
    r"[A-Za-z][A-Za-z0-9]*_[A-Za-z]+\(.*\)( As \w+)?\s*$"
)
_VBA_END = re.compile(r"^\s*(End|Exit) (Sub|Function|Property)\s*$")
_XML_TEXT = re.compile(r">[^<]+<")


def _holds(text: str, strings: set[str]) -> bool:
    return any(s in text for s in strings)


class GuardError(Exception):
    """Usage error, a refused config, a missing source or repo: exit 2."""

    exit_code = 2


@dataclass
class Params:
    noise_t: int = 25
    guarantee_t: int = 40
    fail_above: float = 0.2
    disable_filtering: bool = True
    corpus_min_string_len: int = 8

    @classmethod
    def from_stack(cls, stack: dict | None) -> Params:
        g = (stack or {}).get("guard") or {}
        p = cls()
        for key in (
            "noise_t",
            "guarantee_t",
            "fail_above",
            "disable_filtering",
            "corpus_min_string_len",
        ):
            if key in g:
                setattr(p, key, g[key])
        return p


@dataclass
class Repo:
    root: Path
    name: str
    pipeline_style: bool
    maxkb: int = TARGET_MAXKB
    exclude: str | None = None
    checked: int = 0
    hits: list[tuple[str | None, str]] = field(default_factory=list)  # (path, hook or score)
    unmerged: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def fail(self, path: str | None, detail: str) -> None:
        if (path, detail) not in self.hits:
            self.hits.append((path, detail))

    @property
    def failures(self) -> list[str]:
        """Failures as `<repo>:<path>: <detail>` (unredacted; see `messages`)."""
        return [f"{self.name}:{p}: {d}" if p else f"{self.name}: {d}" for p, d in self.hits]

    def messages(self, strings: set[str]) -> list[str]:
        """Failures for the summary: a path that holds a corpus string is replaced by the
        sha256 prefix of its UTF-8 bytes, so mp-data never withholds the summary."""
        out = []
        for p, d in self.hits:
            if p and _holds(p, strings):
                digest = hashlib.sha256(p.encode("utf-8", "surrogateescape")).hexdigest()[:16]
                out.append(f"{self.name}:<path withheld, sha256 {digest}>: {d}")
            else:
                out.append(f"{self.name}:{p}: {d}" if p else f"{self.name}: {d}")
        for ref in self.unmerged:
            shown = "<ref withheld>" if _holds(ref, strings) else ref
            out.append(f"{self.name}: not merged into main: {shown}")
        return out


@dataclass
class Result:
    repos: list[Repo]
    corpus: dict = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)
    report: Path | None = None
    strings: set[str] = field(default_factory=set)

    @property
    def failed(self) -> bool:
        return any(r.hits for r in self.repos)

    def summary_messages(self) -> list[str]:
        return [m for r in self.repos for m in r.messages(self.strings)] + self.messages


# ------------------------------------------------------------------ git


def git_env() -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("GIT_")
        and k not in ("SKIP", "PRE_COMMIT_ALLOW_NO_CONFIG", "PRE_COMMIT_COLOR")
    }
    env.update(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": "false",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return env


def git(root: Path, *args: str, data: bytes | None = None, check=True) -> bytes:
    proc = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
        input=data,
        capture_output=True,
        env=git_env(),
        check=False,
    )
    if check and proc.returncode != 0:
        raise GuardError(f"{root.name}: git {args[0]} failed (exit {proc.returncode})")
    return proc.stdout


def _split_z(out: bytes) -> list[str]:
    return [p.decode("utf-8", "surrogateescape") for p in out.split(b"\0") if p]


def open_repo(path: str) -> Repo:
    p = Path(path).expanduser()
    if not p.is_dir():
        raise GuardError(f"repo missing: {path}")
    proc = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(p), "rev-parse", "--show-toplevel"],
        capture_output=True,
        env=git_env(),
        check=False,
    )
    if proc.returncode != 0:
        raise GuardError(f"repo missing: {path} is not a git repository")
    root = Path(proc.stdout.decode().strip()).resolve()
    style = (root / "pipeline.json").is_file() and (root / "toolkit").is_dir()
    return Repo(root=root, name=root.name, pipeline_style=style)


# ------------------------------------------------------------------ config


def expected_hooks(pipeline_style: bool) -> dict:
    exclude = FIXTURES_EXCLUDE if pipeline_style else None
    maxkb = PIPELINE_MAXKB if pipeline_style else TARGET_MAXKB
    return {
        HOOK_SOURCE: {"files": SOURCE_FILES, "exclude": exclude},
        HOOK_SAMPLE: {"files": SAMPLE_FILES, "exclude": exclude},
        HOOK_LARGE: {"args": ["--enforce-all", f"--maxkb={maxkb}"], "maxkb": maxkb},
    }


def check_config(repo: Repo) -> None:
    """Refuse (GuardError naming the hook) a config whose guard hooks differ from decisions E1."""
    path = repo.root / ".pre-commit-config.yaml"
    where = f"{repo.name}: .pre-commit-config.yaml"
    if not path.is_file():
        raise GuardError(f"{where} missing")
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError):
        raise GuardError(f"{where} is not valid YAML") from None
    if not isinstance(cfg, dict) or not isinstance(cfg.get("repos"), list):
        raise GuardError(f"{where} has no repos list")
    if cfg.get("files") not in (None, ""):
        raise GuardError(f"{where}: top-level files narrows every hook")
    if cfg.get("exclude") not in (None, "", "^$"):
        raise GuardError(f"{where}: top-level exclude narrows every hook")
    stages = cfg.get("default_stages")
    if stages is not None and "pre-commit" not in stages:
        raise GuardError(f"{where}: default_stages leaves out pre-commit")
    found: dict[str, list[tuple[dict, dict]]] = {h: [] for h in HOOKS}
    for entry in cfg["repos"]:
        if not isinstance(entry, dict):
            raise GuardError(f"{where}: a repos entry is not a mapping")
        for hook in entry.get("hooks") or []:
            if not isinstance(hook, dict):
                raise GuardError(f"{where}: a hook entry is not a mapping")
            for name in {hook.get("id"), hook.get("alias")}:
                if name in found:
                    found[name].append((entry, hook))
    want = expected_hooks(repo.pipeline_style)
    for hid in HOOKS:
        if len(found[hid]) != 1:
            raise GuardError(f"{where}: hook {hid} must be defined exactly once")
        entry, hook = found[hid][0]
        if hook.get("id") != hid:
            raise GuardError(f"{where}: hook {hid} is an alias of another hook")
        if hid == HOOK_LARGE:
            if entry.get("repo") != LARGE_REPO or entry.get("rev") != LARGE_REV:
                raise GuardError(
                    f"{where}: hook {hid} must come from {LARGE_REPO} at {LARGE_REV}"
                )
            if set(hook) - REMOTE_KEYS:
                raise GuardError(f"{where}: hook {hid} overrides {sorted(set(hook) - REMOTE_KEYS)}")
            if hook.get("args") != want[hid]["args"]:
                raise GuardError(f"{where}: hook {hid} args must be exactly {want[hid]['args']}")
            continue
        if entry.get("repo") != "local":
            raise GuardError(f"{where}: hook {hid} must be a repo local hook")
        if set(hook) - LOCAL_KEYS:
            raise GuardError(f"{where}: hook {hid} sets {sorted(set(hook) - LOCAL_KEYS)}")
        if hook.get("language") != "fail":
            raise GuardError(f"{where}: hook {hid} must have language fail")
        if hook.get("files") != want[hid]["files"]:
            raise GuardError(f"{where}: hook {hid} files differs from decisions E1")
        if hook.get("exclude") != want[hid]["exclude"]:
            raise GuardError(f"{where}: hook {hid} exclude differs from decisions E1")
    repo.maxkb = want[HOOK_LARGE]["maxkb"]
    repo.exclude = want[HOOK_SOURCE]["exclude"]


# ------------------------------------------------------------------ candidates


def worktree_candidates(repo: Repo) -> list[str]:
    names = _split_z(git(repo.root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"))
    out = []
    for n in dict.fromkeys(names):
        p = repo.root / n
        if os.path.lexists(p) and (p.is_symlink() or not p.is_dir()):
            out.append(n)
    return out


def staged_entries(repo: Repo) -> list[tuple[str, str]]:
    """(path, staged blob sha) of every added, copied, modified or retyped staged path."""
    out = git(
        repo.root,
        "diff",
        "--cached",
        "--raw",
        "-z",
        "--no-renames",
        "--no-abbrev",
        "--diff-filter=ACMT",
    )
    parts = out.split(b"\0")
    entries = []
    i = 0
    while i + 1 < len(parts):
        meta = parts[i].decode()
        path = parts[i + 1].decode("utf-8", "surrogateescape")
        i += 2
        if not meta.startswith(":"):
            continue
        fields = meta[1:].split()
        mode, sha = fields[1], fields[3]
        if mode.startswith("16"):  # a submodule (gitlink)
            continue
        entries.append((path, sha))
    return entries


def read_blobs(repo: Repo, shas: list[str]) -> dict[str, bytes]:
    if not shas:
        return {}
    out = git(repo.root, "cat-file", "--batch", data=("\n".join(shas) + "\n").encode())
    blobs: dict[str, bytes] = {}
    pos = 0
    while pos < len(out):
        nl = out.index(b"\n", pos)
        header = out[pos:nl].decode().split()
        pos = nl + 1
        if len(header) < 3 or header[1] == "missing":
            continue
        size = int(header[2])
        blobs[header[0]] = out[pos : pos + size]
        pos += size + 1
    return blobs


def history_blobs(repo: Repo) -> list[tuple[str, list[str], int]]:
    """(sha, paths, size) of every blob reachable from any ref."""
    paths: dict[str, list[str]] = {}
    for line in git(repo.root, "rev-list", "--all", "--objects").decode(
        "utf-8", "surrogateescape"
    ).splitlines():
        sha, _, path = line.partition(" ")
        if path:
            paths.setdefault(sha, [])
            if path not in paths[sha]:
                paths[sha].append(path)
    if not paths:
        return []
    check = git(
        repo.root,
        "cat-file",
        "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        data=("\n".join(paths) + "\n").encode(),
    ).decode()
    out = []
    for line in check.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] == "blob":
            out.append((parts[0], paths[parts[0]], int(parts[2])))
    return out


def unmerged_refs(repo: Repo) -> list[str]:
    """Local and remote branches and tags not merged into main (listed, never a failure)."""
    base = None
    for ref in ("refs/heads/main", "refs/remotes/origin/main"):
        if git(repo.root, "rev-parse", "--verify", "-q", ref, check=False):
            base = ref
            break
    args = ["for-each-ref", "--format=%(refname)"]
    if base:
        args.append(f"--no-merged={base}")
    else:
        repo.notes.append(f"{repo.name}: no main branch; every ref listed")
    refs = git(repo.root, *args, "refs/heads", "refs/remotes", "refs/tags").decode().split()
    return [r for r in refs if r != base and not r.endswith("/HEAD")]


# ------------------------------------------------------------------ hooks


def pre_commit_env() -> dict:
    env = git_env()
    if os.environ.get("MP_SANDBOX") == "mp-data":
        home = Path(os.environ.get("HOME") or Path.home())
        env["PRE_COMMIT_HOME"] = str(home / MP_DATA_PRE_COMMIT)
        env["VIRTUALENV_OVERRIDE_APP_DATA"] = tempfile.mkdtemp(prefix="guard-venv-")
    return env


def run_hooks(repo: Repo, files: list[str], chunk: int = 400) -> None:
    """Run the three guard hooks over `files` (paths relative to the repo root)."""
    if not files:
        return
    env = pre_commit_env()
    wanted = set(files)
    for hook in HOOKS:
        failed_paths: list[str] = []
        failed = False
        for i in range(0, len(files), chunk):
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pre_commit",
                    "run",
                    hook,
                    "--color",
                    "never",
                    "--config",
                    ".pre-commit-config.yaml",
                    "--files",
                    *files[i : i + chunk],
                ],
                cwd=repo.root,
                env=env,
                capture_output=True,
                text=True,
                errors="replace",
                check=False,
            )
            if proc.returncode == 0:
                continue
            failed = True
            if "Failed" not in proc.stdout:
                # pre-commit itself could not run the hook (install or config problem)
                print(proc.stdout[-2000:], proc.stderr[-2000:], file=sys.stderr)
                raise GuardError(
                    f"{repo.name}: pre-commit could not run {hook} (exit {proc.returncode})"
                )
            for line in proc.stdout.splitlines():
                m = _LARGE_LINE.match(line)
                name = m.group(1) if m else line.strip()
                if name in wanted and name not in failed_paths:
                    failed_paths.append(name)
        if failed and not failed_paths:
            repo.fail(None, f"{hook} failed")
        for p in failed_paths:
            repo.fail(p, hook)


def history_name_checks(repo: Repo, blobs) -> None:
    source = re.compile(SOURCE_FILES)
    sample = re.compile(SAMPLE_FILES)
    exclude = re.compile(repo.exclude) if repo.exclude else None
    for sha, paths, size in blobs:
        for p in paths:
            if exclude and exclude.search(p):
                continue
            if source.search(p):
                repo.fail(f"{p}@{sha[:12]}", HOOK_SOURCE)
            if sample.search(p):
                repo.fail(f"{p}@{sha[:12]}", HOOK_SAMPLE)
        if math.ceil(size / 1024) > repo.maxkb:
            repo.fail(f"{paths[0]}@{sha[:12]}", HOOK_LARGE)


# ------------------------------------------------------------------ corpus


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").removeprefix("﻿")


def is_data_string(value: str) -> bool:
    v = value.strip()
    return bool(v) and not _NOT_A_STRING.match(v) and not _GUID.match(v)


def _design_items(text: str) -> list[tuple[str, str]]:
    """Corpus items of one SaveAsText or XML design text: code behind, expressions and SQL,
    macro XML. Layout properties are left out (every SaveAsText file shares them)."""
    from toolkit import saveastext as sat

    body = _norm(text).strip()
    if body.startswith("<"):
        return [("macro", body)]
    try:
        doc = sat.parse(body)
    except sat.SaveAsTextError:
        return [("design", body)]
    items: list[tuple[str, str]] = []
    if doc.code and doc.code.strip():
        items.append(("codebehind", doc.code))
    exprs: list[str] = []
    macros: list[str] = []

    def walk(block, skip_comments: bool) -> None:
        axl = sat._axl_from_block(block)
        if axl:
            macros.append(axl)
            skip_comments = True
        for prop in block.props:
            if isinstance(prop.value, sat.Block):
                walk(prop.value, False)
                continue
            if not prop.quoted or not isinstance(prop.value, str):
                continue
            low = prop.name.lower()
            if low in SKIP_PROPS or (skip_comments and low == "comment"):
                continue
            if prop.value.strip() and is_data_string(prop.value):
                exprs.append(prop.value)
        for child in block.children:
            walk(child, skip_comments)

    walk(doc.root, False)
    if exprs:
        items.append(("expr", "\n".join(exprs)))
    for m in macros:
        items.append(("macro", m))
    return items


def corpus_items(eng: dict, messages: list[str], names: set[str]):
    """Yield ('code', kind, text) and ('data', object, value) from the engagement source.

    `names` collects object, table and column names: design names, which summaries may carry,
    so they never enter strings.txt."""
    from toolkit import dataset as ds
    from toolkit.sources import SourceError, decode_text, open_reader

    reader = open_reader(eng)
    for table in reader.tables():
        names.add(table)
        for row in reader.iter_rows(table):
            names.update(k for k in row if isinstance(k, str))
            break
    for kind, _obj, text in reader.iter_texts():
        if isinstance(_obj, str):
            names.add(_obj)
        if not isinstance(text, str) or not text.strip():
            continue
        if kind == "data":
            yield "data", _obj, text
        elif kind == "code":
            yield "code", "module", text
        else:
            for k, t in _design_items(text):
                yield "code", k, t
    if hasattr(reader, "design"):  # table data macros are not among iter_texts()
        design = reader.design()
        for t in design.tables:
            names.add(t.get("name") or "")
            names.update(c.get("name") or "" for c in t.get("columns") or [])
        for group in (design.queries, design.sql_queries, design.forms, design.reports):
            names.update(n for n, _t in group)
        names.update(m[0] for m in design.modules)
        names.update(m[0] for m in design.macros)
        for _table, xml_bytes in design.data_macros:
            text, _e, _l = decode_text(xml_bytes, "utf-8")
            yield "code", "datamacro", text
    wb = getattr(reader, "wb", None)
    if wb is not None:  # xlsm formulas (the reader's formula workbook)
        for ws in wb.worksheets:
            formulas = []
            for row in ws.iter_rows():
                for cell in row:
                    v = cell.value
                    if isinstance(v, str) and v.startswith("="):
                        formulas.append(v)
                    elif getattr(v, "text", None):
                        formulas.append(str(v.text))
            if formulas:
                yield "code", "formula", "\n".join(formulas)
    src = eng["source"]
    if src.get("data_copy"):
        try:
            dreader = ds.source_reader(eng)
            for table in dreader.tables():
                for row in dreader.iter_rows(table):
                    for value in row.values():
                        if isinstance(value, str) and value:
                            yield "data", table, value
        except SourceError as exc:
            messages.append(f"data copy not read: {exc}")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _vba_format_line(line: str) -> bool:
    return bool(_VBA_HEADER.match(line) or _VBA_EVENT.match(line) or _VBA_END.match(line))


def format_boilerplate(kind: str, body: str) -> list[str]:
    """The format-generated parts of a corpus item, never its content.

    VBA (modules, code behind): every run of consecutive lines that are module header lines,
    event-procedure signatures, End or Exit lines, or blank, as long as it holds at least one
    such line (so the k-grams that join a header to the first event signature, or an End Sub to
    the next signature, count as format too). Macro XML: the markup with every text node
    masked, so conditions, messages and comments stay out."""
    if kind == "macro" or kind == "datamacro" or body.lstrip().startswith("<"):
        return [_XML_TEXT.sub(">\n<", body)]
    if kind not in ("module", "codebehind"):
        return []
    out: list[str] = []
    run: list[str] = []
    for line in [*body.split("\n"), None]:
        if line is not None and (not line.strip() or _vba_format_line(line)):
            run.append(line)
            continue
        if any(x.strip() for x in run):
            out.append("\n".join(run) + ("\n" if line is not None else ""))
        run = []
    return out


# Words every summary may carry: the quiet summary fields and the fixed count keys and message
# fragments of the commands mp-data runs. A data string inside any of them would withhold it.
SUMMARY_VOCABULARY = (
    "status command exit_code engagement counts rates cells sha256s columns tables units "
    "mismatches paths messages log ok no-data failed error not-built "
    "rows omitted_columns skipped_tables cycles_broken self_references unknown_columns "
    "problems differences dataset export check validate guard corpus_code_files "
    "corpus_data_files corpus_strings files_checked "
    "load order: cycles broken; tables skipped: their back end was not received; "
    "written dataset is invalid; files differ; summary withheld: it contains a corpus string"
)


def _inside_design_text(s: str, names: set[str], eng: dict) -> bool:
    """True when `s` occurs inside a design name, the engagement's own identifiers and paths, or
    the summary vocabulary: text a summary line may carry, which mp-data's substring check
    (dataplane.corpus_hit) would otherwise match and withhold."""
    if s in names:
        return True
    texts = [SUMMARY_VOCABULARY, *(n for n in names if len(n) > len(s))]
    texts += [str(eng.get(k) or "") for k in ("id", "client", "db_name")]
    texts += [str(v) for v in (eng.get("paths") or {}).values()]
    texts.append(str((eng.get("source") or {}).get("path") or ""))
    return any(s in t for t in texts)


def build_corpus(
    eng: dict, dest: Path, params: Params, messages: list[str], strings: set[str] | None = None
) -> dict:
    """Write the corpus into `dest` (code/<kind>-<n>.txt, data/<n>.txt, strings.txt).

    strings.txt holds the distinct data strings of at least corpus_min_string_len characters
    (each line of a multi-line value on its own), leaving out numbers, dates, GUIDs and values
    that occur inside an object, table or column name, the engagement's identifiers and paths or
    the summary vocabulary, so mp-data never withholds a summary for carrying design text.
    `strings`, when given, receives them."""
    src = Path(eng["source"]["path"])
    if not src.exists():
        raise GuardError(SOURCE_MISSING)
    from toolkit.sources import SourceError

    code_n: dict[str, int] = {}
    data: dict[str, list[str]] = {}
    found: set[str] = set()
    names: set[str] = set()
    boiler: set[str] = set()
    try:
        for what, key, text in corpus_items(eng, messages, names):
            if what == "data":
                if not is_data_string(text):
                    continue
                data.setdefault(key, []).append(_norm(text))
                for line in _norm(text).split("\n"):
                    line = line.strip()
                    if len(line) >= params.corpus_min_string_len and is_data_string(line):
                        found.add(line)
                continue
            body = _norm(text).strip("\n")
            if not body.strip():
                continue
            code_n[key] = code_n.get(key, 0) + 1
            _atomic_write(dest / "code" / f"{key}-{code_n[key]}.txt", body + "\n")
            boiler.update(format_boilerplate(key, body))
    except SourceError as exc:
        raise GuardError(str(exc)) from None
    for n, table in enumerate(data, start=1):
        _atomic_write(dest / "data" / f"{n}.txt", "\n".join(data[table]) + "\n")
    if boiler:
        _atomic_write(dest / "boilerplate" / "format.txt", "\n".join(sorted(boiler)) + "\n")
    found = {s for s in found if not _inside_design_text(s, names, eng)}
    _atomic_write(dest / "strings.txt", "".join(s + "\n" for s in sorted(found)))
    if strings is not None:
        strings.update(found)
    counts = {
        "corpus_code_files": sum(code_n.values()),
        "corpus_data_files": len(data),
        "corpus_strings": len(found),
    }
    if counts["corpus_code_files"] + counts["corpus_data_files"] == 0:
        raise GuardError(EMPTY_CORPUS)
    return counts


def corpus_files(build: Path) -> list[Path]:
    return sorted(
        p for d in ("code", "data") if (build / d).is_dir() for p in (build / d).rglob("*.txt")
    )


def publish_corpus(build: Path, corpus: Path) -> None:
    """Move the built files into `corpus` one atomic rename at a time; drop stale files."""
    corpus.mkdir(parents=True, exist_ok=True)
    keep = set()
    for p in [*corpus_files(build), build / "strings.txt"]:
        rel = p.relative_to(build)
        keep.add(rel)
        target = corpus / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(p, target)
    for d in ("code", "data"):
        if not (corpus / d).is_dir():
            continue
        for p in (corpus / d).glob("*.txt"):
            if p.relative_to(corpus) not in keep:
                p.unlink(missing_ok=True)


# ------------------------------------------------------------------ copy check


def text_of(data: bytes) -> str | None:
    """UTF-8 text, or None for binary or non-UTF-8 content."""
    if not data or b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


HASH_SEED = "0"


def copy_check(
    tests: list[tuple[Repo, str, Path]],
    build: Path,
    params: Params,
    report: Path | None,
) -> None:
    """copydetect every test file against the corpus; record failures on each repo.

    copydetect fingerprints k-grams with Python's hash(), which is salted per process, so the
    winnowed fingerprints (and scores near fail_above) would change from run to run. It
    therefore runs in a child interpreter with PYTHONHASHSEED fixed, which makes every run of
    the same inputs give the same scores."""
    if not tests or not corpus_files(build):
        return
    labels: dict[str, tuple[Repo, str]] = {str(path): (repo, label) for repo, label, path in tests}
    request = {
        "tests": list(labels),
        "ref_dirs": [str(build / d) for d in ("code", "data") if (build / d).is_dir()],
        "boilerplate_dirs": [str(build / "boilerplate")]
        if (build / "boilerplate").is_dir()
        else [],
        "noise_t": params.noise_t,
        "guarantee_t": params.guarantee_t,
        "fail_above": params.fail_above,
        "disable_filtering": params.disable_filtering,
        "out_file": str(report / "report.html") if report is not None else None,
    }
    req = build / "copydetect-request.json"
    resp = build / "copydetect-response.json"
    req.write_text(json.dumps(request), encoding="utf-8")
    env = {**os.environ, "PYTHONHASHSEED": HASH_SEED}
    proc = subprocess.run(
        [sys.executable, "-m", "toolkit.guard", "copydetect", str(req), str(resp)],
        env=env,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    if proc.returncode != 0 or not resp.is_file():
        print(proc.stderr[-2000:], file=sys.stderr)
        raise GuardError(f"copydetect failed (exit {proc.returncode})")
    worst: dict[str, tuple[float, str]] = {}
    for test_f, score, ref_f in json.loads(resp.read_text(encoding="utf-8"))["pairs"]:
        if score <= params.fail_above:
            continue
        rel = str(Path(ref_f).relative_to(build))
        if test_f not in worst or score > worst[test_f][0]:
            worst[test_f] = (score, rel)
    for test_f in sorted(worst):
        repo, label = labels[test_f]
        score, rel = worst[test_f]
        repo.fail(label, f"similarity {score:.2f} to corpus {rel}")


def _copydetect_child(request_path: str, response_path: str) -> int:
    """`python -m toolkit.guard copydetect REQ RESP` (run by copy_check with a fixed hash seed)."""
    from copydetect import CopyDetector

    req = json.loads(Path(request_path).read_text(encoding="utf-8"))
    det = CopyDetector(
        test_dirs=[],
        ref_dirs=req["ref_dirs"],
        boilerplate_dirs=req["boilerplate_dirs"],
        extensions=["*"],
        noise_t=req["noise_t"],
        guarantee_t=req["guarantee_t"],
        display_t=req["fail_above"],
        disable_filtering=req["disable_filtering"],
        autoopen=False,
        silent=True,
        out_file=req["out_file"] or str(Path(response_path).with_suffix(".html")),
    )
    for t in req["tests"]:
        det.add_file(t, type="test")
    det.run()
    pairs = [
        [test_f, float(test_sim), ref_f]
        for test_sim, _ref_sim, test_f, ref_f, *_rest in det.get_copied_code_list()
    ]
    if req["out_file"] and len(det.similarity_matrix):
        det.generate_html_report()
    Path(response_path).write_text(json.dumps({"pairs": pairs}), encoding="utf-8")
    return 0


# ------------------------------------------------------------------ run


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def run(
    repo_paths: list[str],
    mode: str = "worktree",
    eng: dict | None = None,
    params: Params | None = None,
    report: str | None = None,
    hooks_only: bool = False,
) -> Result:
    """Run the guard. mode: worktree, staged or history. Raises GuardError for exit 2."""
    if not repo_paths:
        raise GuardError("usage: at least one --repo")
    params = params or Params()
    repos = [open_repo(p) for p in repo_paths]
    report_dir = None
    if report is not None:
        if hooks_only:
            raise GuardError("usage: --report needs the copy check (not --hooks-only)")
        report_dir = Path(report).expanduser().resolve()
        for r in repos:
            if _inside(report_dir, r.root):
                raise GuardError(f"--report must lie outside the repos ({r.name})")
    for r in repos:
        check_config(r)
    result = Result(repos=repos, report=report_dir)

    build = None
    corpus_dir = None
    work = Path(tempfile.mkdtemp(prefix="guard-"))
    try:
        if not hooks_only:
            if eng is None:
                raise GuardError("usage: an engagement is needed unless --hooks-only")
            corpus_dir = Path(eng["paths"]["private"]) / "corpus"
            corpus_dir.mkdir(parents=True, exist_ok=True)
            build = Path(tempfile.mkdtemp(prefix=".build-", dir=corpus_dir))
            result.corpus = build_corpus(eng, build, params, result.messages, result.strings)
        tests: list[tuple[Repo, str, Path]] = []
        for n, r in enumerate(repos):
            scratch = work / str(n)
            if mode == "history":
                blobs = history_blobs(r)
                r.checked = len(blobs)
                history_name_checks(r, blobs)
                r.unmerged = unmerged_refs(r)
                if build is not None:
                    contents = read_blobs(r, [b[0] for b in blobs])
                    for sha, paths, _size in blobs:
                        text = text_of(contents.get(sha, b""))
                        if text is None:
                            continue
                        f = scratch / f"{sha}.txt"
                        f.parent.mkdir(parents=True, exist_ok=True)
                        f.write_text(text, encoding="utf-8")
                        tests.append((r, f"{paths[0]}@{sha[:12]}", f))
                continue
            if mode == "staged":
                entries = staged_entries(r)
                files = [p for p, _sha in entries if os.path.lexists(r.root / p)]
                r.checked = len(entries)
                run_hooks(r, files)
                if build is not None:
                    contents = read_blobs(r, [sha for _p, sha in entries])
                    for i, (p, sha) in enumerate(entries):
                        text = text_of(contents.get(sha, b""))
                        if text is None:
                            continue
                        f = scratch / f"{i}.txt"
                        f.parent.mkdir(parents=True, exist_ok=True)
                        f.write_text(text, encoding="utf-8")
                        tests.append((r, p, f))
                continue
            files = worktree_candidates(r)
            r.checked = len(files)
            run_hooks(r, files)
            if build is not None:
                for p in files:
                    full = r.root / p
                    if full.is_symlink() or not full.is_file():
                        continue
                    if text_of(full.read_bytes()) is not None:
                        tests.append((r, p, full))
        if build is not None:
            if report_dir is not None:
                report_dir.mkdir(parents=True, exist_ok=True)
            copy_check(tests, build, params, report_dir)
            publish_corpus(build, corpus_dir)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if build is not None:
            shutil.rmtree(build, ignore_errors=True)
    for r in repos:
        result.messages.extend(r.notes)
    return result


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "copydetect":
        raise SystemExit(_copydetect_child(sys.argv[2], sys.argv[3]))
    raise SystemExit(2)
