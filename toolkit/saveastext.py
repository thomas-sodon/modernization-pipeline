"""Access SaveAsText reader (forms, reports, queries and macros).

The format is line oriented:

- `Begin` or `Begin <Kind>` opens a block and `End` closes it;
- `Name =value` is a property; a quoted value may continue on the following lines, each a quoted
  piece (`"..."`) that is appended to it;
- `Name = Begin` opens a property block, which holds hex lines (`0x...`, optionally followed by
  `,`) or further properties and blocks (embedded macros);
- `dbText "Name" ="value"` is a typed property (`dbText`, `dbMemo`, `dbBinary`, `dbLong`,
  `dbInteger`, `dbBoolean`, `dbByte`, and any other `db<Type>`), used by queries;
- a line `CodeBehindForm` or `CodeBehindReport` at the outer level starts the object's VBA, which
  runs to the end of the text.

Quoted strings accept both escape styles: backslash escapes (`\\"`, `\\\\` and three-digit octal
such as `\\015\\012`) and doubled quotes (`""`).

`parse(text)` returns a `Document`. `form_layout`, `query_model` and `macro_model` turn documents
into the object-model entries of docs/contracts.md#object-model. Nothing here reads files.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from toolkit.datamacros import axl_actions, local

TYPED_PROPS = (
    "dbText",
    "dbMemo",
    "dbBinary",
    "dbLong",
    "dbInteger",
    "dbBoolean",
    "dbByte",
)
CODE_MARKERS = ("CodeBehindForm", "CodeBehindReport")
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_RE_BEGIN = re.compile(rf"^Begin(?:\s+({_IDENT}))?$")
_RE_TYPED = re.compile(r'^(db[A-Za-z]+)\s+"((?:[^"]|"")*)"\s*=\s*(.*)$')
_RE_PROP = re.compile(rf"^({_IDENT})\s*=\s*(.*)$")
_RE_HEX = re.compile(r"^0x([0-9A-Fa-f]*)\s*,?\s*$")
_OCTAL = re.compile(r"[0-7]{3}")


class SaveAsTextError(Exception):
    """A line the parser cannot read. `code` is a short error code, `line` 1-based."""

    def __init__(self, code: str, line: int, detail: str = ""):
        self.code = code
        self.line = line
        self.detail = detail
        super().__init__(f"{code} at line {line}" + (f" ({detail})" if detail else ""))


@dataclass
class Prop:
    name: str
    value: object  # str (quoted), str (bare token), or Block (property block)
    quoted: bool = False
    type: str | None = None  # dbText, dbMemo ... for typed properties
    line: int = 0


@dataclass
class Block:
    kind: str  # "Form", "Section", "" (anonymous), or "=Name" for a property block
    line: int = 0
    props: list[Prop] = field(default_factory=list)
    children: list[Block] = field(default_factory=list)
    hex: bytearray = field(default_factory=bytearray)

    def get(self, name: str, default=None):
        low = name.lower()
        for p in self.props:
            if p.name.lower() == low and p.type is None:
                return p.value
        return default

    def typed(self, name: str, default=None):
        low = name.lower()
        for p in self.props:
            if p.type is not None and p.name.lower() == low:
                return p.value
        return default

    def text(self, name: str) -> str | None:
        v = self.get(name)
        return v if isinstance(v, str) else None

    def int(self, name: str, default: int | None = None) -> int | None:
        v = self.get(name)
        if isinstance(v, str):
            try:
                return int(v.strip())
            except ValueError:
                return default
        return default


@dataclass
class Document:
    root: Block
    code: str | None = None
    code_marker: str | None = None
    code_line: int = 0  # 1-based line of the marker

    @property
    def blocks(self) -> list[Block]:
        return self.root.children

    @property
    def props(self) -> list[Prop]:
        return self.root.props

    def first(self, *kinds: str) -> Block | None:
        for b in self.root.children:
            if b.kind in kinds:
                return b
        return None


def read_string(s: str, pos: int = 0) -> tuple[str, str]:
    """Read the quoted string that starts at s[pos]; return (value, rest of line)."""
    if pos >= len(s) or s[pos] != '"':
        raise ValueError("not a string")
    out: list[str] = []
    i = pos + 1
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            nx = s[i + 1]
            if nx in '"\\':
                out.append(nx)
                i += 2
                continue
            if _OCTAL.fullmatch(s[i + 1 : i + 4] or ""):
                out.append(chr(int(s[i + 1 : i + 4], 8)))
                i += 4
                continue
            out.append(c)
            i += 1
            continue
        if c == '"':
            if i + 1 < n and s[i + 1] == '"':
                out.append('"')
                i += 2
                continue
            return "".join(out), s[i + 1 :]
        out.append(c)
        i += 1
    raise ValueError("unterminated string")


def _shape(s: str) -> str:
    """A value-free description of a line for error details: its leading word and length."""
    m = re.match(_IDENT, s)
    word = m.group(0)[:40] if m else (s[:1] if s[:1] in '"0=' else "?")
    return f"starts {word!s}, {len(s)} chars"


def parse(text: str) -> Document:
    """Parse SaveAsText. Raises SaveAsTextError on any line it cannot read."""
    text = text.removeprefix("﻿")
    lines = text.splitlines()
    root = Block("#root", 0)
    stack = [root]
    last: Prop | None = None
    doc = Document(root)
    for idx, raw in enumerate(lines):
        lineno = idx + 1
        s = raw.strip()
        if not s:
            last = None
            continue
        if len(stack) == 1 and s in CODE_MARKERS:
            doc.code_marker = s
            doc.code_line = lineno
            doc.code = "\n".join(lines[idx + 1 :])
            if lines[idx + 1 :]:
                doc.code += "\n"
            break
        if s.startswith('"'):
            if last is None or not last.quoted:
                raise SaveAsTextError("orphan-continuation", lineno)
            try:
                value, rest = read_string(s)
            except ValueError:
                raise SaveAsTextError("unterminated-string", lineno) from None
            if rest.strip():
                raise SaveAsTextError("trailing-text", lineno, _shape(rest.strip()))
            last.value = str(last.value) + value
            continue
        last = None
        m = _RE_HEX.match(s)
        if m:
            digits = m.group(1)
            if len(digits) % 2:
                raise SaveAsTextError("odd-hex", lineno)
            stack[-1].hex += bytes.fromhex(digits)
            continue
        m = _RE_BEGIN.match(s)
        if m:
            block = Block(m.group(1) or "", lineno)
            stack[-1].children.append(block)
            stack.append(block)
            continue
        if s == "End":
            if len(stack) == 1:
                raise SaveAsTextError("unbalanced-end", lineno)
            stack.pop()
            continue
        m = _RE_TYPED.match(s)
        if m:
            ptype, pname, rhs = m.group(1), m.group(2).replace('""', '"'), m.group(3)
            last = _prop(stack, pname, rhs, lineno, ptype)
            continue
        m = _RE_PROP.match(s)
        if m:
            last = _prop(stack, m.group(1), m.group(2), lineno, None)
            continue
        raise SaveAsTextError("unrecognized-line", lineno, _shape(s))
    if len(stack) != 1:
        raise SaveAsTextError(
            "unclosed-block", stack[-1].line, stack[-1].kind or "anon"
        )
    return doc


def _prop(stack, name, rhs, lineno, ptype) -> Prop | None:
    rhs = rhs.strip()
    if rhs == "Begin":
        block = Block("=" + name, lineno)
        stack[-1].props.append(Prop(name, block, False, ptype, lineno))
        stack.append(block)
        return None
    if rhs.startswith('"'):
        try:
            value, rest = read_string(rhs)
        except ValueError:
            raise SaveAsTextError("unterminated-string", lineno) from None
        if rest.strip():
            raise SaveAsTextError("trailing-text", lineno, _shape(rest.strip()))
        prop = Prop(name, value, True, ptype, lineno)
        stack[-1].props.append(prop)
        return prop
    stack[-1].props.append(Prop(name, rhs, False, ptype, lineno))
    return None


# ---------------------------------------------------------------- forms and reports

SECTION_KINDS = {
    "FormHeader": "form_header",
    "PageHeader": "page_header",
    "Section": "detail",
    "PageFooter": "page_footer",
    "FormFooter": "form_footer",
    "BreakHeader": "group_header",
    "BreakFooter": "group_footer",
}
_EVENT_RE = re.compile(r"^(On[A-Z][A-Za-z]*|Before[A-Z][A-Za-z]*|After[A-Z][A-Za-z]*)$")
_EM_SUFFIX = "EmMacro"


def event_name(prop: str) -> str:
    return prop.removeprefix("On")


def _events(block: Block) -> tuple[list[str], dict[str, Block]]:
    """(events, embedded macro blocks by event name) of a form, report or control block."""
    events: list[str] = []
    embedded: dict[str, Block] = {}
    for p in block.props:
        if p.type is not None:
            continue
        if p.name.endswith(_EM_SUFFIX) and isinstance(p.value, Block):
            base = p.name[: -len(_EM_SUFFIX)]
            if _EVENT_RE.match(base):
                embedded[event_name(base)] = p.value
            continue
        if not _EVENT_RE.match(p.name) or not p.quoted:
            continue
        value = str(p.value).strip()
        if not value:
            continue
        ev = event_name(p.name)
        if value == "[Event Procedure]":
            events.append(ev)
        elif value == "[Embedded Macro]":
            events.append(f"{ev}:macro")
        elif value.startswith("="):
            events.append(f"{ev}:expression")
        else:
            events.append(f"{ev}:named-macro")
    for ev in embedded:
        if f"{ev}:macro" not in events:
            events.append(f"{ev}:macro")
    return events, embedded


def _int(block: Block, name: str, default: int) -> int:
    v = block.int(name)
    return default if v is None else v


def _strip_source_object(value: str | None) -> str | None:
    if not value:
        return None
    for prefix in ("Form.", "Report."):
        if value.startswith(prefix):
            return value[len(prefix) :]
    return value


def _find_top(doc: Document, kinds: tuple[str, ...]) -> Block | None:
    return doc.first(*kinds)


def _container_children(top: Block):
    """Blocks under the anonymous container(s) of the form or report block."""
    for child in top.children:
        if child.kind == "":
            yield from child.children
        else:
            yield child


def form_layout(doc: Document, name: str, owner: str) -> dict:
    """Layout, events and embedded macros of a form (owner 'form') or report (owner 'report').

    Returns the objects.json entry plus a private key `_macros` (embedded macros) that the
    caller moves to objects.json `macros`.
    """
    top = _find_top(doc, ("Form", "Report"))
    if top is None:
        raise SaveAsTextError("no-form-block", 1, owner)
    defaults: dict[str, Block] = {}
    sections: list[dict] = []
    controls: list[dict] = []
    tab_pages: list[dict] = []
    groups: list[dict] = []
    macros: list[dict] = []
    warnings: list[dict] = []

    form_events, form_em = _events(top)
    for ev, mblock in sorted(form_em.items()):
        macros.append(_embedded_macro(name, owner, ev, mblock, warnings))

    sec_index = 0
    for child in _container_children(top):
        if child.kind in SECTION_KINDS:
            kind = SECTION_KINDS[child.kind]
            sec_name = child.text("Name") or f"{kind}{sec_index}"
            sec_index += 1
            sections.append(
                {"name": sec_name, "kind": kind, "height": _int(child, "Height", 0)}
            )
            _sec_events, sec_em = _events(child)
            for ev, mblock in sorted(sec_em.items()):
                macros.append(
                    _embedded_macro(name, owner, f"{sec_name}.{ev}", mblock, warnings)
                )
            _walk_controls(
                child,
                name,
                owner,
                sec_name,
                None,
                None,
                defaults,
                controls,
                tab_pages,
                macros,
                warnings,
            )
        elif child.kind == "BreakLevel":
            groups.append(
                {
                    "control_source": child.text("ControlSource"),
                    "group_header": child.get("GroupHeader") is not None,
                    "group_footer": child.get("GroupFooter") is not None,
                    "descending": child.get("SortOrder") is not None,
                }
            )
        elif child.kind and not child.kind.startswith("="):
            defaults.setdefault(child.kind, child)

    entry = {
        "name": name,
        "record_source": top.text("RecordSource") or None,
        "caption": top.text("Caption") or None,
        "has_code_behind": doc.code is not None,
        "events": form_events,
        "width": _int(top, "Width", 0),
        "sections": sections,
        "tab_pages": tab_pages,
        "controls": controls,
        "_macros": macros,
        "_warnings": warnings,
    }
    if owner == "report":
        entry["groups"] = groups
    else:
        entry["default_view"] = top.int("DefaultView")
    return entry


def _walk_controls(
    block: Block,
    owner_name: str,
    owner: str,
    section: str,
    page: str | None,
    parent: str | None,
    defaults: dict[str, Block],
    controls: list[dict],
    tab_pages: list[dict],
    macros: list[dict],
    warnings: list[dict],
) -> None:
    for container in block.children:
        if container.kind != "":
            continue
        page_index = 0
        for ctl in container.children:
            if not ctl.kind or ctl.kind.startswith("="):
                continue
            if ctl.kind == "Page":
                pname = ctl.text("Name") or f"Page{page_index}"
                tab_pages.append(
                    {
                        "tab_control": parent,
                        "name": pname,
                        "caption": ctl.text("Caption"),
                        "index": _int(ctl, "PageIndex", page_index),
                    }
                )
                page_index += 1
                _walk_controls(
                    ctl,
                    owner_name,
                    owner,
                    section,
                    pname,
                    pname,
                    defaults,
                    controls,
                    tab_pages,
                    macros,
                    warnings,
                )
                continue
            default = defaults.get(ctl.kind)
            cname = ctl.text("Name")
            if not cname:
                cname = f"{ctl.kind}@{ctl.line}"
                warnings.append(
                    {"code": "control-without-name", "object": f"{owner}:{owner_name}"}
                )
            events, em = _events(ctl)
            for ev, mblock in sorted(em.items()):
                macros.append(
                    _embedded_macro(
                        owner_name, owner, f"{cname}.{ev}", mblock, warnings
                    )
                )
            controls.append(
                {
                    "name": cname,
                    "type": ctl.kind,
                    "section": section,
                    "parent": parent,
                    "caption": ctl.text("Caption"),
                    "left": _int(ctl, "Left", 0),
                    "top": _int(ctl, "Top", 0),
                    "width": _int(
                        ctl, "Width", _int(default, "Width", 0) if default else 0
                    ),
                    "height": _int(
                        ctl, "Height", _int(default, "Height", 0) if default else 0
                    ),
                    "control_source": ctl.text("ControlSource") or None,
                    "row_source": ctl.text("RowSource") or None,
                    "source_object": _strip_source_object(ctl.text("SourceObject")),
                    "page": page,
                    "visible": ctl.get("Visible") is None,
                    "events": events,
                }
            )
            _walk_controls(
                ctl,
                owner_name,
                owner,
                section,
                page,
                cname,
                defaults,
                controls,
                tab_pages,
                macros,
                warnings,
            )


# ---------------------------------------------------------------- macros

_AXL_PREFIX = "_AXL:"


def _axl_from_block(block: Block) -> str | None:
    """The AXL text of a macro: the Comment that starts with _AXL: and the Comment values of
    the blocks after it (Access splits long AXL over several comment blocks)."""
    parts: list[str] = []
    own = block.text("Comment")
    if own and own.startswith(_AXL_PREFIX):
        parts.append(own[len(_AXL_PREFIX) :])
    for child in block.children:
        comment = child.text("Comment")
        if comment is None:
            continue
        if comment.startswith(_AXL_PREFIX):
            parts.append(comment[len(_AXL_PREFIX) :])
        elif parts:
            parts.append(comment)
    return "".join(parts) or None


def _positional_actions(block: Block) -> list[dict]:
    actions = []
    for child in block.children:
        action = child.text("Action")
        if not action:
            continue
        args = {}
        n = 0
        for p in child.props:
            if p.name == "Argument" and isinstance(p.value, str):
                n += 1
                args[str(n)] = p.value
        cond = child.text("Condition")
        if cond:
            args["Condition"] = cond
        entry = {"action": action, "args": args, "depth": 0}
        mname = child.text("MacroName")
        if mname:
            entry["submacro"] = mname
        actions.append(entry)
    return actions


def actions_from_axl(xml_text: str) -> list[dict]:
    """Actions of a UI macro given as AXL XML (one macro, or the first of several)."""
    root = ET.fromstring(
        xml_text.encode("utf-16") if _declares_utf16(xml_text) else xml_text
    )
    return axl_actions(root)


def _declares_utf16(text: str) -> bool:
    head = text[:100].lower()
    return 'encoding="utf-16"' in head or "encoding='utf-16'" in head


def macro_actions(block: Block) -> tuple[list[dict], str]:
    """Actions of a macro block (standalone document root or `<Event>EmMacro` block).

    Returns (actions, how) where how is 'axl' or 'positional'."""
    xml_text = _axl_from_block(block)
    if xml_text:
        return actions_from_axl(xml_text), "axl"
    return _positional_actions(block), "positional"


def _embedded_macro(owner_name, owner, event, block, warnings) -> dict:
    try:
        actions, how = macro_actions(block)
    except ET.ParseError:
        actions, how = _positional_actions(block), "positional"
        warnings.append(
            {"code": "embedded-macro-axl-unreadable", "object": f"{owner}:{owner_name}"}
        )
    if how == "positional" and actions:
        warnings.append(
            {
                "code": "embedded-macro-positional-args",
                "object": f"{owner}:{owner_name}",
            }
        )
    return {
        "name": f"{owner_name}.{event}",
        "kind": "embedded",
        "owner": f"{owner}:{owner_name}.{event}",
        "actions": actions,
    }


def macro_model(doc: Document, name: str) -> dict:
    """A standalone macro object from its SaveAsText document."""
    actions, _how = macro_actions(doc.root)
    return {"name": name, "kind": "standalone", "owner": None, "actions": actions}


def macro_model_from_xml(xml_bytes: bytes, name: str) -> dict:
    root = ET.fromstring(xml_bytes)
    return {
        "name": name,
        "kind": "standalone",
        "owner": None,
        "actions": axl_actions(root),
    }


# ---------------------------------------------------------------- queries

_OPERATION = {
    1: "select",
    2: "action",
    3: "action",
    4: "action",
    5: "action",
    6: "crosstab",
    7: "action",
    8: "pass-through",
    9: "union",
}
_SQL_ONLY_OPERATIONS = {7, 8, 9}
SQL_WORDS = {
    "SELECT",
    "FROM",
    "WHERE",
    "AND",
    "OR",
    "NOT",
    "IN",
    "EXISTS",
    "VALUES",
    "AS",
    "ON",
    "JOIN",
    "INNER",
    "LEFT",
    "RIGHT",
    "OUTER",
    "GROUP",
    "BY",
    "ORDER",
    "HAVING",
    "UNION",
    "ALL",
    "INTO",
    "INSERT",
    "UPDATE",
    "DELETE",
    "SET",
    "TRANSFORM",
    "PIVOT",
    "DISTINCT",
    "DISTINCTROW",
    "TOP",
    "PARAMETERS",
    "BETWEEN",
    "LIKE",
    "IS",
    "NULL",
    "ASC",
    "DESC",
    "CREATE",
    "TABLE",
    "ALTER",
    "DROP",
    "INDEX",
    "WITH",
    "OWNERACCESS",
    "OPTION",
    "ANY",
    "SOME",
    "MOD",
    "XOR",
    "EQV",
    "IMP",
}
_FUNC_RE = re.compile(r"(?<![\w.\]!])([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_FORM_REF_RE = re.compile(
    r"\[?Forms\]?\s*!\s*\[?([^\]!.\s]+(?:[^\]!.]*[^\]!.\s])?)\]?\s*!\s*\[?([^\]!.\s(),]+)\]?",
    re.IGNORECASE,
)
_PARAM_DECL_RE = re.compile(r"^\s*PARAMETERS\s+(.*?);", re.IGNORECASE | re.DOTALL)


def _strip_strings(expr: str) -> str:
    return re.sub(r'"[^"]*"|\'[^\']*\'', '""', expr)


def functions_in(exprs, exclude: set[str]) -> list[str]:
    found = set()
    low_ex = {e.lower() for e in exclude}
    for expr in exprs:
        if not expr:
            continue
        for m in _FUNC_RE.finditer(_strip_strings(expr)):
            word = m.group(1)
            if word.upper() in SQL_WORDS or word.lower() in low_ex:
                continue
            found.add(word)
    return sorted(found)


def form_refs_in(exprs) -> list[str]:
    refs = set()
    for expr in exprs:
        if not expr:
            continue
        for m in _FORM_REF_RE.finditer(expr):
            refs.add(f"{m.group(1)}!{m.group(2)}")
    return sorted(refs)


def _sequence(
    block: Block | None, starts: str, fields: dict[str, str], before=()
) -> list:
    """Group a block's repeated properties into entries. A property named `starts` closes
    the entry under construction (properties in `before` that came earlier belong to it)."""
    out: list[dict] = []
    if block is None:
        return out
    pending: dict = {}
    current: dict | None = None
    for p in block.props:
        key = fields.get(p.name)
        if key is None:
            continue
        value = p.value if isinstance(p.value, str) else None
        if p.name == starts:
            current = {k: None for k in fields.values()}
            current.update(pending)
            pending = {}
            current[key] = value
            out.append(current)
        elif p.name in before:
            pending[key] = value
        elif current is not None:
            current[key] = value
        else:
            pending[key] = value
    if pending and out:
        for k, v in pending.items():
            if out[-1].get(k) is None:
                out[-1][k] = v
    return out


def _int_or_none(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def classify_sql(sql: str | None) -> str | None:
    if not sql:
        return None
    s = re.sub(r"^\s*PARAMETERS\s+.*?;", "", sql, flags=re.IGNORECASE | re.DOTALL)
    s = s.strip().lstrip("(").strip()
    head = s.split(None, 1)[0].upper() if s else ""
    if head == "TRANSFORM":
        return "crosstab"
    if head in ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"):
        return "action"
    body = _strip_strings(s)
    if head == "SELECT":
        if re.search(r"\bINTO\b", body.split("FROM", 1)[0], re.IGNORECASE):
            return "action"
        if re.search(r"\bUNION\b", body, re.IGNORECASE):
            return "union"
        return "select"
    return None


def query_model(doc: Document, name: str, known: set[str] | None = None) -> dict:
    root = doc.root
    blocks = {b.kind: b for b in root.children if b.kind}
    operation = _int_or_none(root.get("Operation"))
    sql = root.typed("SQL")
    sql = sql if isinstance(sql, str) and sql.strip() else None
    connect = root.typed("Connect")
    inputs = _sequence(
        blocks.get("InputTables"), "Name", {"Name": "name", "Alias": "alias"}
    )
    outputs = _sequence(
        blocks.get("OutputColumns"),
        "Expression",
        {"Expression": "expression", "Alias": "alias"},
        before=("Alias",),
    )
    joins = _sequence(
        blocks.get("Joins"),
        "LeftTable",
        {
            "LeftTable": "left",
            "RightTable": "right",
            "Expression": "expression",
            "Flag": "flag",
        },
    )
    order_by = _sequence(
        blocks.get("OrderBy"),
        "Expression",
        {"Expression": "expression", "Flag": "flag"},
    )
    groups = _sequence(
        blocks.get("Groups"),
        "Expression",
        {"Expression": "expression", "GroupLevel": "level"},
    )
    params_block = _sequence(
        blocks.get("Parameters"), "Name", {"Name": "name", "Flag": "flag"}
    )
    for entries in (joins, order_by, groups, params_block):
        for e in entries:
            for k in ("flag", "level"):
                if k in e:
                    e[k] = _int_or_none(e[k])
    where = root.text("Where")
    having = root.text("Having")
    has_grid = "InputTables" in blocks or "OutputColumns" in blocks
    if operation in _SQL_ONLY_OPERATIONS or (not has_grid and sql):
        storage = "sql"
    else:
        storage = "design"
    qtype = classify_sql(sql)
    if operation == 8 or (
        isinstance(connect, str) and connect.upper().startswith("ODBC")
    ):
        qtype = "pass-through"
    if qtype is None:
        qtype = _OPERATION.get(operation or 1, "select")
    params = [p["name"] for p in params_block if p.get("name")]
    if sql:
        m = _PARAM_DECL_RE.match(sql)
        if m:
            for part in m.group(1).split(","):
                part = part.strip()
                pm = re.match(r"^(\[[^\]]*\]|\S+)", part)
                if pm and pm.group(1) not in params:
                    params.append(pm.group(1))
    exprs = [where, having, sql]
    exprs += [o.get("expression") for o in outputs]
    exprs += [j.get("expression") for j in joins]
    exprs += [o.get("expression") for o in order_by]
    exprs += [g.get("expression") for g in groups]
    exclude = set(known or ()) | {i.get("name") or "" for i in inputs}
    design = {
        "operation": operation,
        "input_tables": inputs,
        "output_columns": outputs,
        "joins": joins,
        "where": where,
        "having": having,
        "group_by": groups,
        "order_by": order_by,
        "option": _int_or_none(root.get("Option")),
    }
    return {
        "name": name,
        "storage": storage,
        "query_type": qtype,
        "design": design if has_grid else {},
        "sql": sql,
        "params": params,
        "functions": functions_in(exprs, exclude),
        "form_refs": form_refs_in(exprs),
    }


__all__ = [
    "Block",
    "Document",
    "Prop",
    "SaveAsTextError",
    "form_layout",
    "local",
    "macro_model",
    "parse",
    "query_model",
    "read_string",
]
