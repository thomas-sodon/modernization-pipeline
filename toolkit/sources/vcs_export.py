"""msaccess-vcs-addin export folders (source.kind vcs-export).

Folder names follow the add-in's documentation (names only, none of its code):

- `forms/`, `reports/`: `<name>.bas` (SaveAsText); the VBA may sit in the .bas after
  CodeBehindForm or, when the add-in splits files, in a sibling `<name>.cls`;
- `modules/`: `<name>.bas` (standard) and `<name>.cls` (class);
- `queries/`: `<name>.bas` (SaveAsText, the design) and `<name>.sql` (SQL text; used alone when
  there is no .bas);
- `macros/`: `<name>.bas` (SaveAsText) or `<name>.xml` (AXL);
- `tbldefs/`: `<name>.xml` (XSD), `<name>.sql` (DDL, not read) and `<name>.json` (a linked
  table: `Connect` and `SourceTableName`);
- `tdmacros/`: `<table>.xml` (table data macros, AXL);
- `relations/`: `<name>.json` (`Name`, `Table` (one side), `ForeignTable`, `Attributes`,
  `Fields` [{`Name`, `ForeignName`}], optionally wrapped in `Items`);
- `tables/`: table data (`<name>.txt` tab-separated with a header line, or `<name>.xml`):
  data, reachable only through `iter_rows` and `iter_texts`;
- `themes/`, `images/`, `imexspecs/` and the JSON files at the top (options, properties,
  references): known, not modelled.

Any other folder is an unknown design part. Text files are decoded by BOM, then UTF-8, then
cp1252, with CRLF or LF line ends.
"""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from toolkit.datamacros import local
from toolkit.schema import SchemaError, parse_xsd
from toolkit.sources import SourceError, decode_text
from toolkit.sources.access import AccessDesign

KNOWN = (
    "forms",
    "reports",
    "modules",
    "queries",
    "macros",
    "tbldefs",
    "tdmacros",
    "relations",
    "tables",
    "themes",
    "images",
    "imexspecs",
)
REL_ENFORCE_OFF = 0x2
REL_CASCADE_UPDATE = 0x100
REL_CASCADE_DELETE = 0x1000


def read_text(path: Path) -> tuple[str, str, bool]:
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")) or (
        len(data) >= 4 and data[1] == 0 and data[3] == 0
    ):
        return decode_text(data)
    try:
        return data.decode("utf-8"), "utf-8", False
    except UnicodeDecodeError:
        return decode_text(data, "cp1252")


class VcsExportReader:
    kind = "vcs-export"

    def __init__(self, path: Path):
        self.root = Path(path)
        if not self.root.is_dir():
            raise SourceError("export folder missing")

    def _files(self, folder: str, *exts: str) -> list[Path]:
        d = self.root / folder
        if not d.is_dir():
            return []
        return sorted(
            p for p in d.iterdir() if p.is_file() and p.suffix.lower() in exts
        )

    # ------------------------------------------------------------ data access

    def tables(self) -> list[str]:
        return sorted(p.stem for p in self._files("tbldefs", ".xml", ".json"))

    def iter_rows(self, table: str):
        """Rows of tables/<table>.txt (tab-separated, header line) or .xml. Data plane only."""
        txt = self.root / "tables" / f"{table}.txt"
        xml = self.root / "tables" / f"{table}.xml"
        if txt.is_file():
            text, _e, _l = read_text(txt)
            reader = csv.reader(io.StringIO(text), delimiter="\t")
            header = next(reader, None)
            if header is None:
                return
            for row in reader:
                yield dict(zip(header, row))
        elif xml.is_file():
            root = ET.fromstring(xml.read_bytes())
            for row in root:
                if len(row):
                    yield {local(c.tag): c.text for c in row}

    def iter_texts(self):
        for table in sorted({p.stem for p in self._files("tables", ".txt", ".xml")}):
            for row in self.iter_rows(table):
                for value in row.values():
                    if value:
                        yield "data", table, value
        for folder, kind in (
            ("modules", "code"),
            ("forms", "design"),
            ("reports", "design"),
            ("queries", "design"),
            ("macros", "design"),
        ):
            for p in self._files(folder, ".bas", ".cls", ".sql", ".xml"):
                yield kind, p.stem, read_text(p)[0]

    # ------------------------------------------------------------ design

    def design(self) -> AccessDesign:
        d = AccessDesign(source_kind="vcs-export")
        for p in sorted(self.root.iterdir()):
            if p.is_dir():
                n = sum(1 for f in p.rglob("*") if f.is_file())
                d.parts[p.name] = n
                if p.name not in KNOWN:
                    d.unknown_parts[p.name] = n
                    d.error("unknown-part-folder", f"folder:{p.name}", count=n)
            elif p.suffix.lower() != ".json":
                d.unknown_parts["(top)"] = d.unknown_parts.get("(top)", 0) + 1
        if "(top)" in d.unknown_parts:
            d.error("unknown-part-file", "folder:(top)", count=d.unknown_parts["(top)"])

        for p in self._files("tbldefs", ".xml"):
            try:
                tables, warnings = parse_xsd(p.read_bytes())
            except ET.ParseError:
                d.error("table-xsd-xml", f"table:{p.stem}")
                continue
            except SchemaError as exc:
                d.error(f"table-{exc.code}", f"table:{p.stem}", detail=exc.detail)
                continue
            d.tables.extend(tables)
            for w in warnings:
                d.warn(w["code"], w["object"])
        for p in self._files("tbldefs", ".json"):
            try:
                doc = json.loads(read_text(p)[0])
            except ValueError:
                d.error("linked-table-json", f"table:{p.stem}")
                continue
            items = doc.get("Items", doc) if isinstance(doc, dict) else {}
            low = {str(k).lower(): v for k, v in items.items()}
            d.linked_tables.append(
                {
                    "name": low.get("name") or p.stem,
                    "connect": low.get("connect"),
                    "source_table": low.get("sourcetablename"),
                }
            )
        for p in self._files("relations", ".json"):
            rel = self._relation(d, p)
            if rel:
                d.relationships.append(rel)

        self._code_objects(d, "forms", d.forms)
        self._code_objects(d, "reports", d.reports)
        bas = {p.stem for p in self._files("queries", ".bas")}
        for p in self._files("queries", ".bas"):
            d.queries.append((p.stem, self._read(d, "queries", p)))
        for p in self._files("queries", ".sql"):
            if p.stem not in bas:
                d.sql_queries.append((p.stem, self._read(d, "queries", p)))
        for p in self._files("modules", ".bas", ".cls"):
            kind = "class" if p.suffix.lower() == ".cls" else "standard"
            d.modules.append((p.stem, self._read(d, "modules", p), kind))
        for p in self._files("macros", ".bas"):
            d.macros.append((p.stem, self._read(d, "macros", p)))
        for p in self._files("macros", ".xml"):
            d.macros.append((p.stem, p.read_bytes()))
        for p in self._files("tdmacros", ".xml"):
            d.data_macros.append((p.stem, p.read_bytes()))
        return d

    def _read(self, d: AccessDesign, folder: str, p: Path) -> str:
        text, enc, lossy = read_text(p)
        key = f"{folder}:{enc}"
        d.encodings[key] = d.encodings.get(key, 0) + 1
        if lossy:
            d.warn("cp1252-unmapped-byte", f"{folder}:{p.stem}")
        return text

    def _code_objects(self, d: AccessDesign, folder: str, sink: list) -> None:
        marker = "CodeBehindForm"
        for p in self._files(folder, ".bas"):
            text = self._read(d, folder, p)
            cls = p.with_suffix(".cls")
            if cls.is_file() and marker not in text and "CodeBehindReport" not in text:
                code = self._read(d, folder, cls)
                text = text.rstrip("\r\n") + "\n" + marker + "\n" + code
            sink.append((p.stem, text))

    def _relation(self, d: AccessDesign, p: Path) -> dict | None:
        try:
            doc = json.loads(read_text(p)[0])
        except ValueError:
            d.error("relation-json", f"relationship:{p.stem}")
            return None
        items = doc.get("Items", doc) if isinstance(doc, dict) else {}
        attrs = int(items.get("Attributes") or 0)
        fields = items.get("Fields") or []
        return {
            "name": items.get("Name") or p.stem,
            "from_table": items.get("ForeignTable"),
            "from_columns": [f.get("ForeignName") for f in fields],
            "to_table": items.get("Table"),
            "to_columns": [f.get("Name") for f in fields],
            "enforce": not (attrs & REL_ENFORCE_OFF),
            "cascade_update": bool(attrs & REL_CASCADE_UPDATE),
            "cascade_delete": bool(attrs & REL_CASCADE_DELETE),
            "one_to_one": bool(attrs & 0x1),
            "join": "inner",
        }
