"""The object model of an Access front end, shared by the accdt, vcs-export and accdb readers.

A reader fills an `AccessDesign` with the design parts it found (texts and bytes, never rows);
`build_model(design)` turns it into the four model files of docs/contracts.md#object-model plus
the module code texts. Parse errors and warnings are collected per object with a code, never with
source text.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath

from toolkit import datamacros as dm
from toolkit import saveastext as sat
from toolkit import vba
from toolkit.schema import SchemaError


@dataclass
class AccessDesign:
    source_kind: str
    tables: list[dict] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)
    linked_tables: list[dict] = field(
        default_factory=list
    )  # {name, connect, source_table}
    queries: list[tuple[str, str]] = field(default_factory=list)  # SaveAsText
    sql_queries: list[tuple[str, str]] = field(default_factory=list)  # plain SQL text
    forms: list[tuple[str, str]] = field(default_factory=list)
    reports: list[tuple[str, str]] = field(default_factory=list)
    modules: list[tuple[str, str, str | None]] = field(default_factory=list)
    macros: list[tuple[str, object]] = field(
        default_factory=list
    )  # str (SaveAsText) or bytes (AXL)
    data_macros: list[tuple[str, bytes]] = field(default_factory=list)
    properties: list[tuple[str, bytes]] = field(default_factory=list)
    parts: dict = field(default_factory=dict)  # folder -> count
    encodings: dict = field(default_factory=dict)  # "folder:encoding" -> count
    package: dict = field(
        default_factory=dict
    )  # structure of parts outside the object folders
    unknown_parts: dict = field(default_factory=dict)  # folder -> count
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    compiled_only: bool = False
    sample_data_tables: int = 0
    extra: dict = field(default_factory=dict)  # vba_references, database_properties

    def error(self, code, obj, **detail):
        self.errors.append({"code": code, "object": obj, **detail})

    def warn(self, code, obj, **detail):
        self.warnings.append({"code": code, "object": obj, **detail})


# ---------------------------------------------------------------- linked tables

BACKEND_KINDS = ("access", "odbc", "sharepoint", "excel", "text")


def _connect_parts(connect: str) -> dict:
    out = {}
    for piece in connect.split(";"):
        k, sep, v = piece.partition("=")
        if sep:
            out[k.strip().upper()] = v.strip()
    return out


def classify_connect(connect: str | None) -> tuple[str | None, str | None]:
    """(back-end kind, back-end key) of a linked table's connect string, or (None, None)."""
    if not connect:
        return None, None
    c = connect.strip()
    up = c.upper()
    parts = _connect_parts(c)
    if up.startswith("ODBC;"):
        key = parts.get("DSN") or "|".join(
            filter(None, [parts.get("SERVER"), parts.get("DATABASE")])
        )
        return "odbc", (key or "odbc").lower()
    if up.startswith(("ACEWSS;", "WSS;")) or "LIST=" in up:
        return "sharepoint", (parts.get("DATABASE") or "sharepoint").lower()
    if up.startswith("EXCEL"):
        return "excel", (parts.get("DATABASE") or "excel").lower()
    if up.startswith("TEXT;"):
        return "text", (parts.get("DATABASE") or "text").lower()
    if (
        up.startswith((";DATABASE=", "MS ACCESS;"))
        or "DATABASE" in parts
        and not up.split(";", 1)[0].strip()
    ):
        return "access", (parts.get("DATABASE") or "").lower()
    return None, None


def backend_file(key: str | None) -> str | None:
    if not key:
        return None
    if "\\" in key:
        return PureWindowsPath(key).name.lower()
    return PurePosixPath(key).name.lower()


def linked_entries(design: AccessDesign) -> list[dict]:
    """objects.json linked_tables: kind and back-end number, never the connect string."""
    numbers: dict[tuple[str, str], int] = {}
    out = []
    for lt in sorted(design.linked_tables, key=lambda t: t["name"]):
        kind, key = classify_connect(lt.get("connect"))
        entry = {
            "name": lt["name"],
            "backend_kind": kind or "unresolved",
            "backend": None,
            "backend_file": None,
            "source_table": lt.get("source_table"),
        }
        if kind is not None:
            ident = (kind, key or "")
            if ident not in numbers:
                numbers[ident] = len(numbers) + 1
            entry["backend"] = numbers[ident]
            if kind in ("access", "excel", "text"):
                entry["backend_file"] = backend_file(key)
        out.append(entry)
    return out


# ---------------------------------------------------------------- build


def _known(design: AccessDesign) -> dict[str, set[str]]:
    return {
        "tables": {t["name"] for t in design.tables}
        | {t["name"] for t in design.linked_tables},
        "queries": {n for n, _ in design.queries} | {n for n, _ in design.sql_queries},
        "forms": {n for n, _ in design.forms},
        "reports": {n for n, _ in design.reports},
    }


def _detail(exc) -> dict:
    out = {}
    for k in ("line", "detail"):
        v = getattr(exc, k, None)
        if v not in (None, ""):
            out[k] = v
    return out


def _sql_query(name: str, sql: str, known) -> dict:
    qtype = sat.classify_sql(sql) or "select"
    exprs = [sql]
    return {
        "name": name,
        "storage": "sql",
        "query_type": qtype,
        "design": {},
        "sql": sql,
        "params": [],
        "functions": sat.functions_in(exprs, set().union(*known.values())),
        "form_refs": sat.form_refs_in(exprs),
    }


def build_model(design: AccessDesign) -> dict:
    """Model dict: {schema, objects, procedures, datamacros, code, counts, errors, warnings}."""
    known = _known(design)
    all_names = set().union(*known.values())
    queries, forms, reports, macros, modules = [], [], [], [], []
    code: dict[str, str] = {}

    for name, text in design.queries:
        try:
            doc = sat.parse(text)
            queries.append(sat.query_model(doc, name, all_names))
        except sat.SaveAsTextError as exc:
            design.error(f"query-{exc.code}", f"query:{name}", **_detail(exc))
    for name, sql in design.sql_queries:
        queries.append(_sql_query(name, sql, known))

    for owner, items, sink in (
        ("form", design.forms, forms),
        ("report", design.reports, reports),
    ):
        for name, text in items:
            try:
                doc = sat.parse(text)
                entry = sat.form_layout(doc, name, owner)
            except sat.SaveAsTextError as exc:
                design.error(f"{owner}-{exc.code}", f"{owner}:{name}", **_detail(exc))
                continue
            except ET.ParseError:
                design.error(f"{owner}-embedded-macro-xml", f"{owner}:{name}")
                continue
            for w in entry.pop("_warnings"):
                design.warn(w["code"], w["object"])
            macros.extend(entry.pop("_macros"))
            sink.append(entry)
            if doc.code is not None:
                prefix = "Form_" if owner == "form" else "Report_"
                modules.append((prefix + name, doc.code, owner))

    for name, text, kind_hint in design.modules:
        kind = kind_hint or vba.module_kind(text)
        modules.append((name, text, kind))

    for name, body in design.macros:
        try:
            if isinstance(body, (bytes, bytearray)):
                macros.append(sat.macro_model_from_xml(bytes(body), name))
            elif body.lstrip().startswith("<"):
                macros.append(
                    sat.macro_model_from_xml(body.encode("utf-16"), name)
                    if sat._declares_utf16(body)
                    else sat.macro_model_from_xml(body.encode("utf-8"), name)
                )
            else:
                macros.append(sat.macro_model(sat.parse(body), name))
        except sat.SaveAsTextError as exc:
            design.error(f"macro-{exc.code}", f"macro:{name}", **_detail(exc))
        except ET.ParseError as exc:
            design.error(
                "macro-xml",
                f"macro:{name}",
                detail=f"expat {exc.code} at {exc.position}",
            )

    data_macros, dm_files = [], []
    for name, body in design.data_macros:
        try:
            found = dm.parse_data_macros(body, name)
        except ET.ParseError:
            design.error("datamacro-xml", f"data_macro_file:{name}")
            continue
        data_macros.extend(found)
        dm_files.append({"name": name, "table": name, "macros": len(found)})
        if name not in known["tables"]:
            design.warn("datamacro-table-unknown", f"data_macro_file:{name}")

    props = []
    for name, body in design.properties:
        try:
            props.append(dm.parse_properties(body, name))
        except ET.ParseError:
            design.error("properties-xml", f"properties:{name}")

    proc_modules = []
    vba_lines = 0
    kinds_count: dict[str, int] = {}
    for name, text, kind in sorted(modules, key=lambda m: m[0]):
        norm = vba.normalize(text)
        lines = vba.nonblank_lines(norm)
        vba_lines += lines
        kinds_count[kind] = kinds_count.get(kind, 0) + 1
        procs = vba.scan(norm, known)
        for p in procs:
            if p.pop("unterminated", False):
                design.warn("procedure-unterminated", f"procedure:{name}.{p['name']}")
        code[name] = norm
        proc_modules.append(
            {
                "name": name,
                "kind": kind,
                "nonblank_lines": lines,
                "code_file": None,  # filled by model.write
                "procedures": procs,
            }
        )

    linked = linked_entries(design)
    for lt in linked:
        if lt["backend_kind"] == "unresolved":
            design.warn("linked-table-unresolved", f"table:{lt['name']}")

    tables = sorted(design.tables, key=lambda t: t["name"])
    queries.sort(key=lambda q: q["name"])
    forms.sort(key=lambda f: f["name"])
    reports.sort(key=lambda r: r["name"])
    macros.sort(key=lambda m: (m["kind"] != "standalone", m["name"]))
    data_macros.sort(key=lambda d: (d["table"], d["event"], d["name"] or ""))

    code_behind = sorted(
        [f"form:{f['name']}" for f in forms if f["has_code_behind"]]
        + [f"report:{r['name']}" for r in reports if r["has_code_behind"]]
    )
    counts = {
        "tables": len(tables),
        "linked_tables": len(linked),
        "relationships": len(design.relationships),
        "queries": len(queries),
        "queries_design_grid": sum(1 for q in queries if q["storage"] == "design"),
        "queries_sql_text": sum(1 for q in queries if q["storage"] == "sql"),
        "forms": len(forms),
        "reports": len(reports),
        "modules": kinds_count.get("standard", 0) + kinds_count.get("class", 0),
        "class_modules": kinds_count.get("class", 0),
        "macros": sum(1 for m in macros if m["kind"] == "standalone"),
        "embedded_macros": sum(1 for m in macros if m["kind"] == "embedded"),
        "data_macro_files": len(dm_files),
        "data_macros": len(data_macros),
        "objects_with_code_behind": len(code_behind),
        "vba_nonblank_lines": vba_lines,
        "procedures": sum(len(m["procedures"]) for m in proc_modules),
    }
    schema = {
        "format_version": 1,
        "source_kind": design.source_kind,
        "tables": tables,
        "relationships": sorted(design.relationships, key=lambda r: r["name"]),
    }
    objects = {
        "format_version": 1,
        "source_kind": design.source_kind,
        "queries": queries,
        "forms": forms,
        "reports": reports,
        "macros": macros,
        "linked_tables": linked,
        "object_properties": sorted(props, key=lambda p: p["name"]),
        "code_behind": code_behind,
        "vba_references": design.extra.get("vba_references", []),
        "database_properties": design.extra.get("database_properties", {}),
        "facts": {
            "compiled_only": design.compiled_only,
            "vba_present": bool(code),
            "parts": dict(sorted(design.parts.items())),
            "encodings": dict(sorted(design.encodings.items())),
            "sample_data_tables": design.sample_data_tables,
            "data_backends": len(
                {lt["backend"] for lt in linked if lt["backend_kind"] == "access"}
            ),
            "linked_tables": _count_by(linked, "backend_kind"),
        },
    }
    procedures = {"format_version": 1, "modules": proc_modules}
    datamacros_doc = {
        "format_version": 1,
        "datamacros": data_macros,
        "files": sorted(dm_files, key=lambda f: f["name"]),
    }
    return {
        "schema": schema,
        "objects": objects,
        "procedures": procedures,
        "datamacros": datamacros_doc,
        "code": code,
        "counts": counts,
        "package": design.package,
        "unknown_parts": dict(sorted(design.unknown_parts.items())),
    }


def _count_by(items, key) -> dict:
    out: dict[str, int] = {}
    for it in items:
        out[it[key]] = out.get(it[key], 0) + 1
    return dict(sorted(out.items()))


_SAFE = re.compile(r"[^A-Za-z0-9._-]")


__all__ = [
    "AccessDesign",
    "SchemaError",
    "build_model",
    "classify_connect",
    "linked_entries",
]
