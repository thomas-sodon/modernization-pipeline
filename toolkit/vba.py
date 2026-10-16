"""VBA modules: kinds, line counts, a thin procedure scanner and per-procedure references.

- `normalize(text)`: CRLF and CR become LF, a trailing newline is ensured.
- `nonblank_lines(code)`: lines that are non-blank after strip and do not start with
  'Attribute ' (comment lines count). This is the inventory's `vba_nonblank_lines` rule.
- `module_kind(code, default)`: 'class' when the text carries a class header (`VERSION 1.0 CLASS`)
  or the class attributes (`VB_PredeclaredId`, `VB_Exposed`, `VB_Creatable`); else `default`.
- `module_name(code, default)`: the `Attribute VB_Name` value when present.
- `scan(code, known)`: procedures (Sub, Function, Property Get/Let/Set, with Public, Private,
  Friend and Static, declarations continued with ' _') with 1-based start and end lines in the
  normalized text, and their references to known tables, queries, forms and reports, DoCmd calls
  and DAO use. `Declare` statements are not procedures.
"""

from __future__ import annotations

import re

_DECL = re.compile(
    r"^\s*(?:(?:Public|Private|Friend|Static)\s+)*"
    r"(Sub|Function|Property\s+Get|Property\s+Let|Property\s+Set)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_SCOPE = re.compile(r"^\s*((?:(?:Public|Private|Friend|Static)\s+)*)", re.IGNORECASE)
_END = re.compile(r"^\s*End\s+(Sub|Function|Property)\b", re.IGNORECASE)
_DOCMD = re.compile(r"\bDoCmd\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
_DAO = re.compile(
    r"\b(CurrentDb|DBEngine|OpenRecordset|QueryDefs?|TableDefs?|Recordset2?|DAO\.|"
    r"Workspace|\.Execute\b|CreateQueryDef|DLookup|DCount|DSum|DMax|DMin|DAvg|DFirst|DLast)",
    re.IGNORECASE,
)
_STRING = re.compile(r'"((?:[^"]|"")*)"')
_BRACKET = re.compile(r"\[([^\[\]]+)\]")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CLASS_MARKERS = re.compile(
    r"^(VERSION\s+1\.0\s+CLASS\b|Attribute\s+VB_(PredeclaredId|Exposed|Creatable)\b)",
    re.IGNORECASE | re.MULTILINE,
)
_VB_NAME = re.compile(r'^Attribute\s+VB_Name\s*=\s*"([^"]+)"', re.MULTILINE)


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.removeprefix("﻿")
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def nonblank_lines(code: str) -> int:
    n = 0
    for line in normalize(code).split("\n"):
        s = line.strip()
        if s and not s.startswith("Attribute "):
            n += 1
    return n


def module_kind(code: str, default: str = "standard") -> str:
    return "class" if _CLASS_MARKERS.search(normalize(code)) else default


def module_name(code: str, default: str) -> str:
    m = _VB_NAME.search(normalize(code))
    return m.group(1) if m else default


def _strip_comment(line: str) -> str:
    """Drop a trailing ' comment (outside strings)."""
    in_str = False
    for i, c in enumerate(line):
        if c == '"':
            in_str = not in_str
        elif c == "'" and not in_str:
            return line[:i]
    stripped = line.lstrip()
    if stripped[:4].lower() == "rem " or stripped.lower() == "rem":
        return ""
    return line


def logical_lines(code: str):
    """Yield (start_line, end_line, text) for each logical line (' _' continuations joined)."""
    lines = normalize(code).split("\n")
    i = 0
    n = len(lines)
    while i < n:
        start = i
        parts = [lines[i]]
        while parts[-1].rstrip().endswith(" _") and i + 1 < n:
            parts[-1] = parts[-1].rstrip()[:-1]
            i += 1
            parts.append(lines[i])
        yield start + 1, i + 1, " ".join(p.strip() for p in parts)
        i += 1


def _refs(body: str, known: dict[str, set[str]]) -> dict:
    strings = []
    code_parts = []
    for line in body.split("\n"):
        line = _strip_comment(line)
        strings.extend(m.group(1).replace('""', '"') for m in _STRING.finditer(line))
        code_parts.append(_STRING.sub(" ", line))
    candidates: set[str] = set()
    for text in strings + code_parts:
        candidates.update(m.group(1).strip().lower() for m in _BRACKET.finditer(text))
        candidates.update(w.lower() for w in _WORD.findall(text))
    candidates.update(s.strip().lower() for s in strings)
    out = {}
    for kind in ("tables", "queries", "forms", "reports"):
        names = known.get(kind, set())
        out[kind] = sorted(n for n in names if n.lower() in candidates)
    code_only = "\n".join(code_parts)
    out["docmd"] = sorted({m.group(1) for m in _DOCMD.finditer(code_only)})
    out["dao"] = bool(_DAO.search(code_only))
    return out


def scan(code: str, known: dict[str, set[str]] | None = None) -> list[dict]:
    known = known or {}
    norm = normalize(code)
    physical = norm.split("\n")
    procs: list[dict] = []
    current: dict | None = None
    for start, end, text in logical_lines(norm):
        if current is None:
            m = _DECL.match(text)
            if m and not re.match(r"^\s*(?:\w+\s+)*Declare\b", text, re.IGNORECASE):
                kind = (
                    " ".join(m.group(1).split())
                    .title()
                    .replace("Property ", "Property ")
                )
                scope_words = _SCOPE.match(text).group(1).split()
                scope = next(
                    (w.title() for w in scope_words if w.lower() != "static"), "Public"
                )
                current = {
                    "name": m.group(2),
                    "kind": kind,
                    "scope": scope,
                    "static": any(w.lower() == "static" for w in scope_words),
                    "start": start,
                }
        elif _END.match(text):
            current["end"] = end
            body = "\n".join(physical[current["start"] - 1 : end])
            current["lines"] = sum(1 for line in body.split("\n") if line.strip())
            current["refs"] = _refs(body, known)
            procs.append(current)
            current = None
    if current is not None:
        current["end"] = len(physical)
        body = "\n".join(physical[current["start"] - 1 :])
        current["lines"] = sum(1 for line in body.split("\n") if line.strip())
        current["refs"] = _refs(body, known)
        current["unterminated"] = True
        procs.append(current)
    return procs
