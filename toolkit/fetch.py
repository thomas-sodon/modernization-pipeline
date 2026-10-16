"""Source fetch and pins (docs/contracts.md#source-lock).

- URL sources: HEAD first (drift on last_modified, etag or content_md5 exits 1 before any
  download), then download into source.path and check bytes and SHA-256. A present file is only
  hashed, with no network.
- Git sources: clone at the pinned branch, detach at the pinned commit, check HEAD, then hash
  source.path inside the clone.
- Local sources: hash source.path, source.data_copy and every source.backends[].path.

Pins come from engagement.json (the loader fills null pins from source.lock.json). With
--record-if-unset and no pin and no lock, the lock is written (status recorded). Any mismatch
exits 1 and never rewrites a pin. Every fetch also restores the reference-engine jars in the
toolkit root's vendor/.

Nothing here prints a data value: summaries carry sizes, hashes and statuses only.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from toolkit.engagement import pipeline_json, relative_to_private, toolkit_root

REPO1 = "https://repo1.maven.org/maven2"
TIMEOUT = 60
CHUNK = 1 << 20


class FetchError(Exception):
    """A usage or input problem: exit 2."""

    exit_code = 2


@dataclass
class Result:
    code: int = 0
    recorded: bool = False
    counts: dict = field(default_factory=dict)
    sha256s: dict = field(default_factory=dict)
    messages: list = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.code = max(self.code, 1)
        self.messages.append(message)


# ---------------------------------------------------------------- hashing


def hash_file(path: Path) -> tuple[int, str]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
            size += len(chunk)
    return size, h.hexdigest()


def hash_path(path: Path) -> tuple[int, str]:
    """A file: its size and SHA-256. A directory (vcs-export): total size and the SHA-256 of the
    sorted lines '<sha256>  <relative path>' of its files (symlinks are refused)."""
    if path.is_symlink():
        raise FetchError(f"{path.name}: symlinks are not hashed")
    if path.is_file():
        return hash_file(path)
    if path.is_dir():
        lines, total = [], 0
        for p in sorted(path.rglob("*")):
            if p.is_symlink():
                raise FetchError("source directory holds a symlink")
            if p.is_file():
                size, digest = hash_file(p)
                total += size
                lines.append(f"{digest}  {p.relative_to(path).as_posix()}\n")
        return total, hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
    raise FileNotFoundError(str(path))


# ---------------------------------------------------------------- jars


def jar_specs(stack: dict | None = None) -> list[dict]:
    stack = stack if stack is not None else pipeline_json().get("stack", {})
    engine = stack.get("reference_engine") or {}
    return [j for j in engine.get("jars", []) if isinstance(j, dict) and "coord" in j]


def mirrors(stack: dict | None = None) -> list[str]:
    stack = stack if stack is not None else pipeline_json().get("stack", {})
    engine = stack.get("reference_engine") or {}
    first = engine.get("maven_mirror")
    out = [first.rstrip("/")] if first else []
    if REPO1 not in out:
        out.append(REPO1)
    return out


def jar_path(coord: str) -> tuple[str, str]:
    group, artifact, version = coord.split(":")
    name = f"{artifact}-{version}.jar"
    return f"{group.replace('.', '/')}/{artifact}/{version}/{name}", name


def default_vendor_dir() -> Path:
    return toolkit_root() / "vendor"


def _download(url: str, dest: Path, timeout: int = TIMEOUT) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "modernization-toolkit"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out, CHUNK)


def ensure_jars(
    vendor: Path | None = None, specs=None, bases=None, result: Result | None = None
) -> Result:
    """Download any missing or wrong jar into vendor/, mirror first, check every SHA-256."""
    vendor = Path(vendor) if vendor is not None else default_vendor_dir()
    specs = jar_specs() if specs is None else specs
    bases = mirrors() if bases is None else bases
    result = result or Result()
    vendor.mkdir(parents=True, exist_ok=True)
    fetched = present = 0
    for spec in specs:
        rel, name = jar_path(spec["coord"])
        dest = vendor / name
        want = spec["sha256"]
        if dest.is_file() and hash_file(dest)[1] == want:
            present += 1
            result.sha256s[name] = want
            continue
        ok = False
        for base in bases:
            fd, tmp = tempfile.mkstemp(prefix=f".{name}.", dir=vendor)
            os.close(fd)
            tmp = Path(tmp)
            try:
                _download(f"{base}/{rel}", tmp)
                if hash_file(tmp)[1] == want:
                    os.replace(tmp, dest)
                    ok = True
                    break
                result.messages.append(
                    f"jar {name}: SHA-256 mismatch from {base.split('/')[2]}"
                )
            except (urllib.error.URLError, OSError, ValueError) as exc:
                result.messages.append(
                    f"jar {name}: {base.split('/')[2]}: {type(exc).__name__}"
                )
            finally:
                if tmp.exists():
                    tmp.unlink()
        if ok:
            fetched += 1
            result.sha256s[name] = want
        else:
            result.fail(f"jar {name}: not available with the pinned SHA-256")
    result.counts["jars"] = len(specs)
    result.counts["jars_fetched"] = fetched
    result.counts["jars_present"] = present
    return result


# ---------------------------------------------------------------- sources


def _pin(value):
    return value if value not in ("", None) else None


def head_check(url: str, source: dict, result: Result) -> bool:
    """HEAD the URL and compare the pinned header facts. False on drift."""
    pins = {k: _pin(source.get(k)) for k in ("last_modified", "etag", "content_md5")}
    if not any(pins.values()):
        return True
    req = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": "modernization-toolkit"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            headers = resp.headers
    except (urllib.error.URLError, OSError) as exc:
        raise FetchError(
            f"source: HEAD request failed ({type(exc).__name__})"
        ) from None
    ok = True
    if pins["last_modified"] and headers.get("Last-Modified") != pins["last_modified"]:
        result.fail("source: drift: Last-Modified differs from the pin")
        ok = False
    if pins["etag"]:
        got = (headers.get("ETag") or "").strip()
        got = got.removeprefix("W/")
        if got.strip('"') != str(pins["etag"]).strip('"'):
            result.fail("source: drift: ETag differs from the pin")
            ok = False
    if pins["content_md5"]:
        got = headers.get("Content-MD5")
        try:
            got_hex = base64.b64decode(got, validate=True).hex() if got else None
        except ValueError:
            got_hex = None
        if got_hex != str(pins["content_md5"]).lower():
            result.fail("source: drift: Content-MD5 differs from the pin")
            ok = False
    return ok


def download(url: str, dest: Path, want_bytes, want_sha, result: Result) -> bool:
    """Download into a temp file beside dest; move it into place only when it matches the pins
    (or when there are none). False when it does not match (the file is not kept)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{dest.name}.", dir=dest.parent)
    os.close(fd)
    try:
        try:
            _download(url, Path(tmp))
        except (urllib.error.URLError, OSError) as exc:
            raise FetchError(
                f"source: download failed ({type(exc).__name__})"
            ) from None
        size, digest = hash_file(Path(tmp))
        if (want_sha is not None and digest != want_sha) or (
            want_bytes is not None and size != want_bytes
        ):
            result.sha256s["download"] = digest
            result.counts["download_bytes"] = size
            result.fail("source: downloaded file differs from the pins; not kept")
            return False
        os.replace(tmp, dest)
        return True
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _git(*args, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


def git_checkout(source: dict, result: Result) -> str | None:
    """Clone (when missing) and detach at the pinned commit. Returns HEAD."""
    clone_dir = source.get("clone_dir")
    url, branch, commit = (
        source.get("git"),
        source.get("branch"),
        _pin(source.get("commit")),
    )
    if not clone_dir or not url:
        raise FetchError("source: git sources need source.git and source.clone_dir")
    clone = Path(clone_dir)
    if not (clone / ".git").exists():
        clone.parent.mkdir(parents=True, exist_ok=True)
        args = ["clone", "--quiet"]
        if branch:
            args += ["--branch", branch]
        proc = _git(*args, url, str(clone))
        print(proc.stderr, end="", file=sys.stderr)
        if proc.returncode != 0:
            raise FetchError(f"source: git clone failed (exit {proc.returncode})")
    if commit:
        proc = _git("-C", str(clone), "checkout", "--quiet", "--detach", commit)
        if proc.returncode != 0:
            result.fail("source: pinned commit not found in the clone")
            return None
    head = _git("-C", str(clone), "rev-parse", "HEAD")
    if head.returncode != 0:
        raise FetchError("source: git rev-parse HEAD failed")
    head_sha = head.stdout.strip()
    if commit and head_sha != commit:
        result.fail("source: HEAD differs from the pinned commit")
    return head_sha


def _check(name: str, path: Path, want_bytes, want_sha, result: Result) -> dict:
    size, digest = hash_path(path)
    result.sha256s[name] = digest
    result.counts[f"{name}_bytes"] = size
    if want_sha is not None and digest != want_sha:
        result.fail(f"{name}: SHA-256 differs from the pin")
    if want_bytes is not None and size != want_bytes:
        result.fail(f"{name}: size differs from the pin")
    return {"bytes": size, "sha256": digest}


def verify(eng: dict, record_if_unset: bool, vendor: Path | None = None) -> Result:
    result = Result()
    source = eng.get("source") or {}
    paths = eng["paths"]
    private = paths["private"]
    kind = source.get("kind")
    lock_path = Path(paths["records"]) / "source.lock.json"
    have_lock = lock_path.exists()

    src_path = Path(source["path"])
    pinned = _pin(source.get("sha256")) is not None
    git_url = source.get("git")
    head = None

    # what still lacks a pin
    unpinned = []
    if not pinned:
        unpinned.append("source.sha256")
    if git_url and not _pin(source.get("commit")):
        unpinned.append("source.commit")
    if source.get("data_copy") and not _pin(source.get("data_copy_sha256")):
        unpinned.append("source.data_copy_sha256")
    for i, b in enumerate(source.get("backends") or []):
        if isinstance(b, dict) and not _pin(b.get("sha256")):
            unpinned.append(f"source.backends[{i}].sha256")
    if unpinned and (have_lock or not record_if_unset):
        raise FetchError(
            "no pin and no lock for "
            + ", ".join(unpinned)
            + ("" if have_lock else "; run with --record-if-unset")
        )
    record = bool(unpinned)

    if git_url:
        head = git_checkout(source, result)
        if result.code:
            return result
    elif source.get("url") and not src_path.exists():
        if not head_check(source["url"], source, result):
            return result  # drift: nothing downloaded
        if not download(
            source["url"],
            src_path,
            _pin(source.get("bytes")),
            _pin(source.get("sha256")),
            result,
        ):
            return result
    if not src_path.exists():
        raise FetchError(
            f"source.path: missing ({kind} sources are copied in by the operator)"
        )

    entries = {}
    entries["source"] = _check(
        "source",
        src_path,
        _pin(source.get("bytes")),
        _pin(source.get("sha256")),
        result,
    )
    if source.get("data_copy"):
        dc = Path(source["data_copy"])
        if not dc.exists():
            raise FetchError("source.data_copy: missing")
        entries["data_copy"] = _check(
            "data_copy", dc, None, _pin(source.get("data_copy_sha256")), result
        )
    backends = []
    for i, b in enumerate(source.get("backends") or []):
        bp = Path(b["path"])
        if not bp.exists():
            raise FetchError(f"source.backends[{i}].path: missing")
        info = _check(f"backends[{i}]", bp, None, _pin(b.get("sha256")), result)
        backends.append({"path": relative_to_private(b["path"], private), **info})
    result.counts["files"] = 1 + (1 if source.get("data_copy") else 0) + len(backends)

    if record and result.code == 0:
        lock = {
            "format_version": 1,
            "recorded": dt.datetime.now(dt.UTC).date().isoformat(),
            "source": {
                "path": relative_to_private(str(src_path), private),
                "bytes": entries["source"]["bytes"],
                "sha256": entries["source"]["sha256"],
                "git": (
                    {"url": git_url, "branch": source.get("branch"), "commit": head}
                    if git_url
                    else None
                ),
            },
            "data_copy": (
                {
                    "path": relative_to_private(source["data_copy"], private),
                    **entries["data_copy"],
                }
                if "data_copy" in entries
                else None
            ),
            "backends": backends,
        }
        lock_path.write_text(
            json.dumps(lock, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        result.recorded = True
        result.messages.append("recorded; approve at the S0 gate")
    elif result.code == 0:
        result.messages.append("source matches the pins")

    ensure_jars(vendor, result=result)
    return result
