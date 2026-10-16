"""Access templates (.accdt): an OPC zip.

Layout under `template/database/`:

- `objects/<part>.txt` (queries, forms, reports and macros as SaveAsText in UTF-16LE with a BOM;
  VBA modules in cp1252) and `objects/<part>.xsd` (table designs), each with a relationship part
  `objects/_rels/<part>.rels` that points to
  - `properties/<x>.xml` (object metadata: the object's `Name` and Access `Type`),
  - `properties/<y>.axl` (object properties; metadata, never data macros),
  - `dataMacros/<z>.axl` (the table's data macros) and
  - `sampleData/<w>.xml` (the table's rows; data, reachable only through `iter_rows`);
- `relationships.xml` (the relationships, as MSysRelationships rows), `vbaReferences.xml`,
  `databaseProperties.xml` and `navpane.xml`.

Object folders that the parser does not know, and object parts it cannot classify, are reported
by name and count (parse --strict fails on them). Every text part is decoded by its BOM (UTF-16LE
with BOM for SaveAsText, cp1252 for modules), with CRLF or LF line ends.
"""

from __future__ import annotations

import posixpath
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from toolkit.datamacros import local
from toolkit.schema import SchemaError, parse_xsd, xml_name
from toolkit.sources import EncryptedSource, SourceError, decode_text, xml_row_elements
from toolkit.sources.access import AccessDesign

DATABASE = "template/database/"
OBJECTS = DATABASE + "objects/"
KNOWN_FOLDERS = ("", "_rels", "dataMacros", "properties", "sampleData")
KNOWN_PACKAGE = (
    "[Content_Types].xml",
    "_rels/.rels",
    "docProps/",
    "template/template.xml",
    "template/_rels/",
    "template/icon/",
    "template/preview/",
    DATABASE + "resources/",
    DATABASE + "relationships.xml",
    DATABASE + "vbaReferences.xml",
    DATABASE + "databaseProperties.xml",
    DATABASE + "navpane.xml",
)
REL_SHORT = {
    "object-metadata": "metadata",
    "ObjectProperties": "properties",
    "DataMacros": "datamacros",
    "table-data": "sampledata",
}
# Access object types: the metadata names them in words; MSysObjects.Type numbers are accepted
TYPES = {
    "table": "table",
    "query": "query",
    "form": "form",
    "report": "report",
    "macro": "macro",
    "module": "module",
    "linkedtable": "linked",
    "linkedodbctable": "linked_odbc",
    "1": "table",
    "4": "linked_odbc",
    "6": "linked",
    "5": "query",
    "-32768": "form",
    "-32764": "report",
    "-32766": "macro",
    "-32761": "module",
}


def object_kind(type_value: str | None) -> str | None:
    if not type_value:
        return None
    return TYPES.get(type_value.strip().lower().replace(" ", ""))


def _filename(member: str) -> str:
    return member.rsplit("/", 1)[-1]


def _ext(member: str) -> str:
    name = _filename(member)
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _stem(member: str) -> str:
    name = urllib.parse.unquote(_filename(member))
    return name.rsplit(".", 1)[0] if "." in name else name


def _sniff(text: str) -> str | None:
    """Object kind from the first lines of a text part (used when metadata is missing)."""
    head = "\n".join(text.splitlines()[:40])
    if re.search(r"^\s*Begin\s+Form\b", head, re.MULTILINE):
        return "form"
    if re.search(r"^\s*Begin\s+Report\b", head, re.MULTILINE):
        return "report"
    if re.match(r"\s*(Operation\s*=|dbMemo\s)", text):
        return "query"
    if re.match(r"\s*Version\s*=", text):
        return "macro"
    return None


class AccdtReader:
    kind = "accdt"

    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise SourceError("source file missing")
        try:
            self.zip = zipfile.ZipFile(self.path)
        except zipfile.BadZipFile:
            raise SourceError("source is not an accdt (zip) file") from None
        if any(info.flag_bits & 0x1 for info in self.zip.infolist()):
            raise EncryptedSource()
        self.members = sorted(
            i.filename for i in self.zip.infolist() if not i.filename.endswith("/")
        )
        self.member_set = set(self.members)
        self.folders: dict[str, list[str]] = {}
        self.package: list[str] = []
        for name in self.members:
            if name.startswith(OBJECTS):
                rest = name[len(OBJECTS) :]
                folder = rest.rsplit("/", 1)[0] if "/" in rest else ""
                self.folders.setdefault(folder, []).append(name)
            else:
                self.package.append(name)
        self.objects = [self._object(m) for m in self.folders.get("", [])]

    # ------------------------------------------------------------ structure

    def _rels(self, member: str) -> dict[str, list[str]]:
        rels_member = f"{OBJECTS}_rels/{_filename(member)}.rels"
        out: dict[str, list[str]] = {}
        if rels_member not in self.member_set:
            return out
        root = ET.fromstring(self.zip.read(rels_member))
        for rel in root:
            rtype = (rel.get("Type") or "").rsplit("/", 1)[-1]
            target = urllib.parse.unquote(rel.get("Target") or "")
            if target.startswith("/"):
                resolved = target[1:]
            else:
                resolved = posixpath.normpath(
                    posixpath.join(OBJECTS.rstrip("/"), target)
                )
            if resolved not in self.member_set:
                # OPC targets are percent-encoded; zip names may not be
                alt = rel.get("Target") or ""
                alt = (
                    alt[1:]
                    if alt.startswith("/")
                    else posixpath.normpath(posixpath.join(OBJECTS.rstrip("/"), alt))
                )
                resolved = alt if alt in self.member_set else resolved
            out.setdefault(REL_SHORT.get(rtype, rtype), []).append(resolved)
        return out

    def _object(self, member: str) -> dict:
        rels = self._rels(member)
        obj = {
            "member": member,
            "ext": _ext(member),
            "name": _stem(member),
            "type": None,
            "kind": None,
            "rels": rels,
        }
        for meta in rels.get("metadata", []):
            if meta not in self.member_set:
                continue
            root = ET.fromstring(self.zip.read(meta))
            for child in root:
                tag = local(child.tag)
                if tag == "Name" and (child.text or "").strip():
                    obj["name"] = child.text.strip()
                elif tag == "Type" and (child.text or "").strip():
                    obj["type"] = child.text.strip()
        obj["kind"] = object_kind(obj["type"])
        return obj

    def _table_objects(self) -> list[dict]:
        return [o for o in self.objects if o["ext"] == "xsd"]

    # ------------------------------------------------------------ data access

    def tables(self) -> list[str]:
        out = []
        for o in self._table_objects():
            try:
                tables, _w = parse_xsd(self.zip.read(o["member"]))
            except (ET.ParseError, SchemaError):
                continue
            out.extend(t["name"] for t in tables)
        return sorted(out)

    def _sample_member(self, table: str) -> str | None:
        for o in self._table_objects():
            if o["name"] == table:
                for m in o["rels"].get("sampledata", []):
                    if m in self.member_set:
                        return m
        for m in self.folders.get("sampleData", []):
            if _stem(m) == table:
                return m
        return None

    def iter_rows(self, table: str):
        """Rows of a table's sampleData part as {column: text or None}. Data plane only."""
        member = self._sample_member(table)
        if member is None:
            return
        root = ET.fromstring(self.zip.read(member))
        for row in xml_row_elements(root):
            yield {xml_name(local(c.tag)): c.text for c in row}

    def iter_texts(self):
        """(kind, object, text) for guard: data values ('data'), VBA ('code') and design texts
        of queries, forms, reports and macros ('design')."""
        for table in self.tables():
            for row in self.iter_rows(table):
                for value in row.values():
                    if value:
                        yield "data", table, value
        for o in self.objects:
            if o["ext"] != "txt":
                continue
            text, _enc, _lossy = decode_text(self.zip.read(o["member"]))
            kind = o["kind"] or _sniff(text) or "module"
            yield ("code" if kind == "module" else "design"), o["name"], text

    # ------------------------------------------------------------ design

    def design(self) -> AccessDesign:
        d = AccessDesign(source_kind="accdt")
        for folder, members in sorted(self.folders.items()):
            for m in members:
                key = f"{folder or 'objects'}/*.{_ext(m)}"
                d.parts[key] = d.parts.get(key, 0) + 1
            if folder not in KNOWN_FOLDERS:
                d.unknown_parts[folder] = len(members)
                d.error("unknown-part-folder", f"folder:{folder}", count=len(members))
        for name in self.package:
            folder = name.rsplit("/", 1)[0] if "/" in name else "(package root)"
            d.package[folder] = d.package.get(folder, 0) + 1
            if not any(
                name == k or (k.endswith("/") and name.startswith(k))
                for k in KNOWN_PACKAGE
            ):
                key = name.rsplit("/", 1)[0] if "/" in name else "(package root)"
                d.unknown_parts[key] = d.unknown_parts.get(key, 0) + 1
        for key in sorted(set(d.unknown_parts) - set(self.folders)):
            d.error("unknown-package-part", f"folder:{key}", count=d.unknown_parts[key])

        table_names: dict[str, str] = {}
        for o in self.objects:
            if o["ext"] == "xsd":
                self._table(d, o, table_names)
        for o in self.objects:
            if o["ext"] == "xsd":
                continue
            if o["ext"] != "txt":
                d.error("unknown-object-part", f"part:*.{o['ext']}")
                continue
            data = self.zip.read(o["member"])
            text, enc, lossy = decode_text(data, "cp1252")
            kind = o["kind"]
            if kind is None:
                kind = _sniff(text) if enc.startswith("utf-16") else "module"
                d.warn("object-type-from-content", f"part:{o['name']}")
            if kind is None or kind.startswith("linked") or kind == "table":
                d.error(
                    "unknown-object-type",
                    f"part:{o['name']}",
                    detail=str(o["type"])[:30],
                )
                continue
            key = f"{kind}:{enc}"
            d.encodings[key] = d.encodings.get(key, 0) + 1
            if lossy:
                d.warn("cp1252-unmapped-byte", f"{kind}:{o['name']}")
            if kind == "query":
                d.queries.append((o["name"], text))
            elif kind == "form":
                d.forms.append((o["name"], text))
            elif kind == "report":
                d.reports.append((o["name"], text))
            elif kind == "macro":
                d.macros.append((o["name"], text))
            elif kind == "module":
                d.modules.append((o["name"], text, None))
            for prop in o["rels"].get("properties", []):
                if prop in self.member_set:
                    d.properties.append((f"{kind}:{o['name']}", self.zip.read(prop)))
        d.sample_data_tables = len(self.folders.get("sampleData", []))
        d.relationships = self._relationships(d)
        d.extra["vba_references"] = self._vba_references()
        d.extra["database_properties"] = self._database_properties()
        return d

    def _table(self, d: AccessDesign, o: dict, table_names: dict) -> None:
        try:
            tables, warnings = parse_xsd(self.zip.read(o["member"]))
        except ET.ParseError:
            d.error("table-xsd-xml", f"table:{o['name']}")
            return
        except SchemaError as exc:
            d.error(f"table-{exc.code}", f"table:{o['name']}", detail=exc.detail)
            return
        for w in warnings:
            d.warn(w["code"], w["object"])
        for t in tables:
            if o["kind"] in ("linked", "linked_odbc"):
                d.linked_tables.append(
                    {"name": t["name"], "connect": None, "source_table": None}
                )
            d.tables.append(t)
        for m in o["rels"].get("datamacros", []):
            if m in self.member_set:
                d.data_macros.append((o["name"], self.zip.read(m)))
        for prop in o["rels"].get("properties", []):
            if prop in self.member_set:
                d.properties.append((f"table:{o['name']}", self.zip.read(prop)))

    def _relationships(self, d: AccessDesign) -> list[dict]:
        member = DATABASE + "relationships.xml"
        if member not in self.member_set:
            return []
        try:
            root = ET.fromstring(self.zip.read(member))
        except ET.ParseError:
            d.error("relationships-xml", "relationships")
            return []
        rows = []
        for row in root:
            if local(row.tag) != "MSysRelationships":
                continue
            rows.append({local(c.tag): (c.text or "") for c in row})
        by_name: dict[str, dict] = {}
        for r in rows:
            name = r.get("szRelationship", "")
            try:
                grbit = int(r.get("grbit") or 0)
                icol = int(r.get("icolumn") or 0)
            except ValueError:
                d.error("relationships-number", f"relationship:{name}")
                continue
            rel = by_name.setdefault(
                name,
                {
                    "name": name,
                    "from_table": r.get("szObject"),
                    "to_table": r.get("szReferencedObject"),
                    "_cols": [],
                    "enforce": not (grbit & 0x2),
                    "cascade_update": bool(grbit & 0x100),
                    "cascade_delete": bool(grbit & 0x1000),
                    "one_to_one": bool(grbit & 0x1),
                    "join": "left"
                    if grbit & 0x1000000
                    else ("right" if grbit & 0x2000000 else "inner"),
                },
            )
            rel["_cols"].append((icol, r.get("szColumn"), r.get("szReferencedColumn")))
        out = []
        for rel in by_name.values():
            cols = sorted(rel.pop("_cols"))
            rel["from_columns"] = [c[1] for c in cols]
            rel["to_columns"] = [c[2] for c in cols]
            out.append(rel)
        return sorted(out, key=lambda r: r["name"])

    def _vba_references(self) -> list[dict]:
        member = DATABASE + "vbaReferences.xml"
        if member not in self.member_set:
            return []
        root = ET.fromstring(self.zip.read(member))
        out = []
        for ref in root:
            entry = {local(c.tag): (c.text or "").strip() for c in ref}
            entry.update({k: v for k, v in ref.attrib.items()})
            out.append(entry)
        return sorted(out, key=lambda e: (e.get("GUID", ""), e.get("Name", "")))

    def _database_properties(self) -> dict:
        """Database property names (the values are left out)."""
        member = DATABASE + "databaseProperties.xml"
        if member not in self.member_set:
            return {}
        root = ET.fromstring(self.zip.read(member))
        names = {e.get("Name") for e in root.iter() if local(e.tag) == "Property"}
        return {"names": sorted(n for n in names if n)}
