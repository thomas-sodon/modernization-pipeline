"""Scrub credentials and personal data out of the object model (decision D8).

Parse runs every model string (values and keys) and every module text through `Scrubber` before
it writes anything. Hits become typed tokens:

- `<SECRET-n>`: connect-string credentials (the value after `PWD=`, `Password=`, `UID=`,
  `User ID=`), the user:password part of a credential URL (`scheme://user:password@host`),
  private-key blocks, AWS-style access key ids, and quoted values assigned to names that contain
  api_key, apikey, secret, token, password or passwd;
- `<URL-n>`: a whole URL whose query string carries a secret (token=, key=, sig=, password= ...);
- `<EMAIL-n>`: email addresses;
- `<PHONE-n>`: phone numbers (9 to 15 digits in separated groups, optional +country code).

The same original always gets the same token within an engagement: the map (token -> original)
lives in the private scrub-map.json (data zone), is loaded before scrubbing and written back
afterwards. `check_dir(dir)` finds anything a scrub would still replace and names the file and
JSON path (or line), never the value. The patterns are written for this pipeline; gitleaks rule
names (aws-access-token, private-key, generic-api-key) served only as a checklist.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import tempfile
from pathlib import Path

TOKEN_RE = re.compile(r"<(SECRET|EMAIL|PHONE|URL)-(\d+)>")

_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
    re.DOTALL,
)
_SECRET_URL = re.compile(
    r"\b[A-Za-z][A-Za-z0-9+.-]{1,15}://[^\s\"'<>]*?[?&;]"
    r"(?:access_token|token|api_?key|apikey|key|sig|signature|password|pwd|secret|client_secret)"
    r"=[^\s\"'<>&]+[^\s\"'<>]*",
    re.IGNORECASE,
)
_CRED_URL = re.compile(
    r"(\b[A-Za-z][A-Za-z0-9+.-]{1,15}://)([^\s/:@\"'<>]+:[^\s/@\"'<>]+)(@)"
)
_CONNECT = re.compile(
    r"(?<![A-Za-z0-9_])(PWD|Password|UID|User ID)(=)([^;\"'\s<>]+)", re.IGNORECASE
)
_AWS = re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA)[0-9A-Z]{16}\b")
_ASSIGNED = re.compile(
    r"(\b[A-Za-z0-9_.]*?(?:api_?key|apikey|secret|token|password|passwd)[A-Za-z0-9_]*"
    r"\s*[:=]\s*)([\"'])([^\"'\s<>]{6,})(\2)",
    re.IGNORECASE,
)
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"
)
_PHONE = re.compile(
    r"(?<![\w.+/:-])(?:\+\d{1,3}[ .-]?)?(?:\(\d{1,4}\)[ .-]?|\d{1,4}[ .-])"
    r"\d{2,4}[ .-]\d{3,4}(?:[ .-]\d{2,4})?(?![\w/:-])"
)


_DOTTED_QUAD = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")


def _phone_ok(s: str) -> bool:
    if _DOTTED_QUAD.fullmatch(s):
        return False  # an IPv4 address
    digits = sum(c.isdigit() for c in s)
    return 9 <= digits <= 15


class Scrubber:
    def __init__(self, existing: dict | None = None):
        self.by_original: dict[tuple[str, str], str] = {}
        self.next: dict[str, int] = {"SECRET": 1, "EMAIL": 1, "PHONE": 1, "URL": 1}
        self.hits: dict[str, int] = {}
        for token, original in sorted((existing or {}).items()):
            m = TOKEN_RE.fullmatch(token)
            if not m or not isinstance(original, str):
                continue
            kind, n = m.group(1), int(m.group(2))
            self.by_original[(kind, original)] = token
            self.next[kind] = max(self.next[kind], n + 1)

    def token(self, kind: str, original: str) -> str:
        key = (kind, original)
        tok = self.by_original.get(key)
        if tok is None:
            tok = f"<{kind}-{self.next[kind]}>"
            self.next[kind] += 1
            self.by_original[key] = tok
        self.hits[kind] = self.hits.get(kind, 0) + 1
        return tok

    def text(self, s: str) -> str:
        if not s:
            return s
        s = _PRIVATE_KEY.sub(lambda m: self.token("SECRET", m.group(0)), s)
        s = _SECRET_URL.sub(lambda m: self.token("URL", m.group(0)), s)
        s = _CRED_URL.sub(
            lambda m: m.group(1) + self.token("SECRET", m.group(2)) + m.group(3), s
        )
        s = _CONNECT.sub(
            lambda m: m.group(1) + m.group(2) + self.token("SECRET", m.group(3)), s
        )
        s = _AWS.sub(lambda m: self.token("SECRET", m.group(0)), s)
        s = _ASSIGNED.sub(
            lambda m: (
                m.group(1) + m.group(2) + self.token("SECRET", m.group(3)) + m.group(4)
            ),
            s,
        )
        s = _EMAIL.sub(lambda m: self.token("EMAIL", m.group(0)), s)
        s = _PHONE.sub(
            lambda m: (
                self.token("PHONE", m.group(0)) if _phone_ok(m.group(0)) else m.group(0)
            ),
            s,
        )
        return s

    def obj(self, value):
        """Scrub every string (dict keys too) in a JSON-like value; returns a new value."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {self.text(k): self.obj(value[k]) for k in sorted(value)}
        if isinstance(value, list):
            return [self.obj(v) for v in value]
        return value

    def mapping(self) -> dict:
        return {tok: orig for (_kind, orig), tok in self.by_original.items()}


def findings(s: str) -> list[str]:
    """Kinds of scrub hits remaining in s (empty when clean)."""
    kinds = []
    for kind, rx in (
        ("SECRET", _PRIVATE_KEY),
        ("URL", _SECRET_URL),
        ("SECRET", _CRED_URL),
        ("SECRET", _CONNECT),
        ("SECRET", _AWS),
        ("SECRET", _ASSIGNED),
        ("EMAIL", _EMAIL),
    ):
        if rx.search(s):
            kinds.append(kind)
    if any(_phone_ok(m.group(0)) for m in _PHONE.finditer(s)):
        kinds.append("PHONE")
    return kinds


def _walk(value, path="$"):
    if isinstance(value, dict):
        for k in value:
            yield f"{path}[key]", k
            seg = "<key with a hit>" if findings(str(k)) else k
            yield from _walk(value[k], f"{path}.{seg}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]")
    elif isinstance(value, str):
        yield path, value


def check_dir(directory: Path) -> list[tuple[str, str, str]]:
    """(relative file, JSON path or line, kind) for everything a scrub would still replace.

    JSON paths name keys, which are model names (never values); a hit in a key is reported as
    the parent path plus `[key]`."""
    directory = Path(directory)
    hits = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(directory).as_posix()
        if path.suffix == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, UnicodeDecodeError):
                hits.append((rel, "$", "unreadable"))
                continue
            for jpath, s in _walk(data):
                for kind in findings(s):
                    hits.append((rel, jpath, kind))
        elif path.suffix in (".txt", ".md", ".csv"):
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                hits.append((rel, "line 0", "unreadable"))
                continue
            for n, line in enumerate(text.split("\n"), 1):
                for kind in findings(line):
                    hits.append((rel, f"line {n}", kind))
    return hits


# ---------------------------------------------------------------- private files


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Write via a temp file and os.replace. When the target is a bind mount (mp-data's jail
    binds scrub-map.json as a file, and a rename onto it fails), rewrite it in place."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        tmp = None
        return
    except OSError:
        # read-only parent or a bind-mounted target: rewrite the file in place
        if not path.exists():
            raise
    finally:
        if tmp is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
    with open(path, "r+b") as fh:
        fh.seek(0)
        fh.write(data)
        fh.truncate()
        fh.flush()
        os.fsync(fh.fileno())


@contextlib.contextmanager
def locked_map(path: Path):
    """Hold an exclusive lock on scrub-map.json while it is read, used and written back.

    Yields (existing tokens, save(mapping)). An empty or missing file means no map yet."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            same = os.fstat(fd).st_ino == os.stat(path).st_ino
        except FileNotFoundError:
            same = False
        if same:
            break
        os.close(fd)
    try:
        raw = b""
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            raw += chunk
        existing = {}
        if raw.strip():
            doc = json.loads(raw.decode("utf-8"))
            existing = doc.get("tokens", {}) if isinstance(doc, dict) else {}

        def save(mapping: dict) -> None:
            body = (
                json.dumps(
                    {"format_version": 1, "tokens": mapping},
                    indent=2,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            atomic_write(path, body)

        yield existing, save
    finally:
        os.close(fd)
