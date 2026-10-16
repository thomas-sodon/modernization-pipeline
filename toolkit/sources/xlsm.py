"""Excel macro-enabled workbooks (source.kind xlsm).

The model holds the workbook's structure only (docs/contracts.md#object-model):

- schema.json: one table per ListObject (name, sheet, ref, columns in header order) with neutral
  types inferred from cached values (datetime, decimal, longinteger, text), calculated columns
  (`computed`, `formula` as written), list validations (`choices_from`), `role: documentation`
  for tables without business data, and inferred keys and foreign keys (`inferred: true`);
- objects.json: sheets with a role and their layout, formula blocks (contiguous formula ranges
  outside tables, every cell with its formula as written and `has_cached_value`), defined names
  (`broken` for #REF!, a warning), data validations, charts, form-control buttons, macro
  assignments, ActiveX controls, comment counts, userforms and VBA references;
- procedures.json: the VBA components typed from the PROJECT stream (Module= standard, Class=
  class, Document= document, BaseClass= userform) with their procedures.

Cached values, constant cell values and comment texts never enter the model: parse writes them
to the private workbook/ folder (data zone) with atomic writes. openpyxl reads sheets, tables,
validations and formulas; the package XML is read directly for defined names, drawings, charts,
controls, VML and comments; oletools (olevba, oleform) and olefile read the VBA project.
"""

from __future__ import annotations

import datetime as dt
import json
import posixpath
import re
import struct
import warnings
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from toolkit import vba
from toolkit.datamacros import local
from toolkit.sources import EncryptedSource, SourceError

_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
FORM_TYPES = {
    7: "Frame",
    12: "Image",
    14: "Frame",
    15: "MorphData",
    16: "SpinButton",
    17: "CommandButton",
    18: "TabStrip",
    21: "Label",
    23: "TextBox",
    24: "ListBox",
    25: "ComboBox",
    26: "CheckBox",
    27: "OptionButton",
    28: "ToggleButton",
    47: "ScrollBar",
    57: "MultiPage",
}
ROLE_HINTS = (
    ("dashboard", ("dashboard", "summary", "chart")),
    ("template", ("template",)),
    ("calculation", ("calc",)),
    ("documentation", ("glossary", "readme", "help", "about", "instructions", "doc")),
    ("input", ("input", "entry", "data")),
)
DOC_TABLE_HINTS = ("glossary", "readme", "help", "documentation", "notes")
LABEL_MAX = 80
_A1 = re.compile(r"\$?([A-Z]{1,3})\$?(\d{1,7})")
_REF = re.compile(
    r"(?:(?:'((?:[^']|'')+)'|([A-Za-z_][A-Za-z0-9_.]*))!)?"
    r"(\$?[A-Z]{1,3}\$?\d{1,7}(?::\$?[A-Z]{1,3}\$?\d{1,7})?)"
)
_VBA_RANGE = re.compile(r'\bRange\s*\(\s*"([^"]+)"', re.IGNORECASE)
_VBA_CELLS = re.compile(
    r"\bCells\s*\(\s*(\d+)\s*,\s*(\d+|\"[A-Za-z]{1,3}\")\s*\)", re.IGNORECASE
)


# ---------------------------------------------------------------- A1 helpers


def col_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def col_letters(n: int) -> str:
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def parse_range(ref: str) -> tuple[int, int, int, int]:
    """(min_col, min_row, max_col, max_row) of an A1 cell or range."""
    parts = ref.replace("$", "").split(":")
    m1 = _A1.fullmatch(parts[0])
    m2 = _A1.fullmatch(parts[-1])
    if not m1 or not m2:
        raise ValueError("not an A1 range")
    c1, r1 = col_index(m1.group(1)), int(m1.group(2))
    c2, r2 = col_index(m2.group(1)), int(m2.group(2))
    return min(c1, c2), min(r1, r2), max(c1, c2), max(r1, r2)


def a1(col: int, row: int) -> str:
    return f"{col_letters(col)}{row}"


def range_a1(c1, r1, c2, r2) -> str:
    return a1(c1, r1) if (c1, r1) == (c2, r2) else f"{a1(c1, r1)}:{a1(c2, r2)}"


def in_range(col, row, rng) -> bool:
    c1, r1, c2, r2 = rng
    return c1 <= col <= c2 and r1 <= row <= r2


# ---------------------------------------------------------------- formula blocks


def formula_blocks(cells: dict[tuple[int, int], str]) -> list[list[tuple[int, int]]]:
    """Group formula cells into connected blocks (4-neighbourhood); each block sorted."""
    seen: set = set()
    blocks = []
    for start in sorted(cells, key=lambda c: (c[1], c[0])):
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        block = []
        while stack:
            c, r = stack.pop()
            block.append((c, r))
            for nb in ((c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)):
                if nb in cells and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        blocks.append(sorted(block, key=lambda x: (x[1], x[0])))
    return blocks


def referenced_cells(
    formula: str, own_sheet: str
) -> list[tuple[str, tuple[int, int, int, int]]]:
    """(sheet, range) for each A1 reference in a formula (structured references ignored)."""
    out = []
    text = re.sub(r'"[^"]*"', '""', formula)
    for m in _REF.finditer(text):
        sheet = (
            m.group(1).replace("''", "'") if m.group(1) else (m.group(2) or own_sheet)
        )
        if m.start() > 0 and (
            text[m.start() - 1].isalnum() or text[m.start() - 1] in "_["
        ):
            continue
        try:
            out.append((sheet, parse_range(m.group(3))))
        except ValueError:
            continue
    return out


def vba_cell_literals(code: str) -> list[tuple[int, int, int, int]]:
    out = []
    for m in _VBA_RANGE.finditer(code):
        for part in m.group(1).split(","):
            try:
                out.append(parse_range(part.strip()))
            except ValueError:
                continue
    for m in _VBA_CELLS.finditer(code):
        row = int(m.group(1))
        col_s = m.group(2).strip('"')
        col = int(col_s) if col_s.isdigit() else col_index(col_s)
        out.append((col, row, col, row))
    return out


# ---------------------------------------------------------------- VBA project


def project_components(project_text: str) -> dict[str, str]:
    """Component kinds from the PROJECT stream text."""
    kinds = {}
    for line in project_text.replace("\r\n", "\n").split("\n"):
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        name = value.strip().split("/", 1)[0].strip('"')
        if key == "Module":
            kinds[name] = "standard"
        elif key == "Class":
            kinds[name] = "class"
        elif key == "Document":
            kinds[name] = "document"
        elif key == "BaseClass":
            kinds[name] = "userform"
    return kinds


def reference_names(dir_stream: bytes, codec: str = "cp1252") -> list[str]:
    """REFERENCENAME records (0x0016) of a decompressed VBA dir stream, in order."""
    names = []
    pos = 0
    n = len(dir_stream)
    while pos + 6 <= n:
        rid, size = struct.unpack_from("<HI", dir_stream, pos)
        pos += 6
        if rid == 0x000F:  # PROJECTMODULES: references are done
            break
        if rid == 0x0009:  # PROJECTVERSION: size says 4, the record holds 6 bytes
            pos += 6
            continue
        if rid == 0x0016:
            names.append(dir_stream[pos : pos + size].decode(codec, errors="replace"))
        pos += size
    return names


def vba_model(
    project_text: str,
    modules: dict[str, str],
    references: list[str],
    form_sites: dict[str, list[dict]] | None = None,
    known: dict | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """(procedures.json modules, userforms, warnings) from the PROJECT text, the component
    code as olevba extracts it, the reference names and the userform sites."""
    kinds = project_components(project_text)
    warnings = []
    out = []
    for name in sorted(modules):
        code = vba.normalize(modules[name])
        kind = kinds.get(name)
        if kind is None:
            kind = vba.module_kind(code)
            warnings.append(
                {"code": "vba-component-not-in-project", "object": f"module:{name}"}
            )
        out.append(
            {
                "name": name,
                "kind": kind,
                "nonblank_lines": vba.nonblank_lines(code),
                "code_file": None,
                "procedures": vba.scan(code, known or {}),
            }
        )
    for name in sorted(set(kinds) - set(modules)):
        warnings.append(
            {"code": "vba-component-without-code", "object": f"module:{name}"}
        )
    userforms = []
    for name in sorted(k for k, v in kinds.items() if v == "userform"):
        controls = []
        for site in (form_sites or {}).get(name, []):
            controls.append(
                {
                    "name": site.get("name"),
                    "type": FORM_TYPES.get(site.get("ClsidCacheIndex"), "Unknown"),
                    "caption": site.get("caption"),
                    "left": None,
                    "top": None,
                    "width": None,
                    "height": None,
                }
            )
        userforms.append({"name": name, "caption": None, "controls": controls})
    return out, userforms, warnings


def _decompress(data: bytes) -> bytes:
    from oletools.olevba import decompress_stream

    return decompress_stream(bytearray(data))


def read_vba(path: Path) -> dict | None:
    """PROJECT text, component code, reference names and userform sites (None without VBA)."""
    from oletools.olevba import VBA_Parser

    parser = VBA_Parser(str(path))
    try:
        if not parser.detect_vba_macros():
            return None
        modules = {}
        for _f, _stream, vba_filename, code in parser.extract_all_macros():
            name = vba_filename.rsplit(".", 1)[0] if vba_filename else None
            if name:
                modules[name] = code if isinstance(code, str) else code.decode("cp1252")
        project_text, refs = "", []
        for sub in parser.ole_subfiles or [parser]:
            ole = sub.ole_file
            if ole is None:
                continue
            for entry in ole.listdir():
                if entry[-1] == "PROJECT":
                    project_text = (
                        ole.openstream(entry).read().decode("cp1252", errors="replace")
                    )
                elif entry[-1].lower() == "dir" and len(entry) >= 2:
                    refs = reference_names(_decompress(ole.openstream(entry).read()))
        sites: dict[str, list[dict]] = {}
        for _f, stream_path, var in parser.extract_form_strings_extended():
            form = stream_path.rstrip("/").split("/")[-1]
            sites.setdefault(form, []).append(var)
        return {
            "project": project_text,
            "modules": modules,
            "references": refs,
            "sites": sites,
        }
    finally:
        parser.close()


# ---------------------------------------------------------------- exported VBA (.frm)


def parse_frm(text: str) -> dict:
    """Caption and control positions from an exported .frm header (units as written)."""
    form = {"caption": None, "controls": []}
    depth = 0
    current = None
    for raw in text.replace("\r\n", "\n").split("\n"):
        s = raw.strip()
        if s.startswith("Attribute VB_Name"):
            break
        m = re.match(r"^Begin\s+(\S+)\s+(\S+)", s)
        if m:
            depth += 1
            if depth == 1:
                current = form
            else:
                ctype = m.group(1).split(".")
                current = {
                    "name": m.group(2),
                    "type": ctype[1] if len(ctype) > 2 else m.group(1),
                    "caption": None,
                    "left": None,
                    "top": None,
                    "width": None,
                    "height": None,
                }
                form["controls"].append(current)
            continue
        if s == "End":
            depth -= 1
            current = form if depth >= 1 else None
            continue
        m = re.match(r"^(\w+)\s*=\s*(.*)$", s)
        if m and current is not None:
            key, value = m.group(1), m.group(2).strip()
            if key in ("Caption",) and value.startswith('"'):
                current["caption"] = value[1:].split('"', 1)[0]
            elif key in (
                "Left",
                "Top",
                "Width",
                "Height",
                "ClientLeft",
                "ClientTop",
                "ClientWidth",
                "ClientHeight",
            ):
                k = key.replace("Client", "").lower()
                try:
                    current[k] = float(value.split("'")[0].strip())
                except ValueError:
                    pass
    return form


def exported_components(folder: Path) -> dict[str, dict]:
    """name -> {code, frm} for .bas, .cls and .frm files of a VBA export folder."""
    out = {}
    if not folder.is_dir():
        return out
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() not in (".bas", ".cls", ".frm"):
            continue
        text = p.read_bytes().decode("cp1252", errors="replace")
        name = vba.module_name(text, p.stem)
        lines = vba.normalize(text).split("\n")
        start = 0
        for i, line in enumerate(lines):
            if line.startswith("Attribute VB_Name"):
                start = i
                break
        code = "\n".join(lines[start:])
        out[name] = {
            "code": code,
            "frm": parse_frm(text) if p.suffix.lower() == ".frm" else None,
        }
    return out


def cross_check(folder: Path, modules, userforms, warnings) -> None:
    """Compare exported .bas/.cls/.frm files with the workbook's components (warnings only);
    take userform captions and control positions from the exported .frm text."""
    exported = exported_components(folder)
    by_name = {m["name"]: m for m in modules}
    for name, exp in sorted(exported.items()):
        m = by_name.get(name)
        if m is None:
            warnings.append(
                {"code": "vba-export-extra-component", "object": f"module:{name}"}
            )
            continue
        lines = vba.nonblank_lines(exp["code"])
        procs = len(vba.scan(exp["code"]))
        if lines != m["nonblank_lines"]:
            warnings.append(
                {"code": "vba-export-lines-differ", "object": f"module:{name}"}
            )
        if procs != len(m["procedures"]):
            warnings.append(
                {"code": "vba-export-procedures-differ", "object": f"module:{name}"}
            )
        if exp["frm"]:
            for uf in userforms:
                if uf["name"] != name:
                    continue
                uf["caption"] = exp["frm"]["caption"]
                pos = {c["name"]: c for c in exp["frm"]["controls"]}
                for c in uf["controls"]:
                    p = pos.get(c["name"])
                    if p:
                        for k in ("left", "top", "width", "height"):
                            c[k] = p[k]
                        if c.get("caption") is None:
                            c["caption"] = p["caption"]


# ---------------------------------------------------------------- package XML


class Package:
    def __init__(self, zf: zipfile.ZipFile):
        self.zf = zf
        self.names = set(zf.namelist())

    def xml(self, name: str):
        return ET.fromstring(self.zf.read(name)) if name in self.names else None

    def rels(self, part: str) -> dict[str, tuple[str, str]]:
        """rId -> (type suffix, resolved target) for a part."""
        folder, _, fname = part.rpartition("/")
        rels_name = f"{folder}/_rels/{fname}.rels" if folder else f"_rels/{fname}.rels"
        root = self.xml(rels_name)
        out = {}
        if root is None:
            return out
        for rel in root:
            target = rel.get("Target") or ""
            if rel.get("TargetMode") == "External":
                resolved = target
            elif target.startswith("/"):
                resolved = target[1:]
            else:
                resolved = posixpath.normpath(posixpath.join(folder, target))
            out[rel.get("Id")] = ((rel.get("Type") or "").rsplit("/", 1)[-1], resolved)
        return out

    def sheets(self) -> list[dict]:
        wb = self.xml("xl/workbook.xml")
        rels = self.rels("xl/workbook.xml")
        out = []
        for i, sh in enumerate(e for e in wb.iter() if local(e.tag) == "sheet"):
            rid = sh.get(f"{{{NS_R}}}id")
            out.append(
                {
                    "name": sh.get("name"),
                    "index": i,
                    "state": sh.get("state") or "visible",
                    "part": rels.get(rid, ("", ""))[1],
                    "chartsheet": rels.get(rid, ("", ""))[0] == "chartsheet",
                }
            )
        return out

    def defined_names(self) -> list[dict]:
        wb = self.xml("xl/workbook.xml")
        out = []
        for e in wb.iter():
            if local(e.tag) == "definedName":
                refers = (e.text or "").strip()
                out.append(
                    {
                        "name": e.get("name"),
                        "scope_sheet": int(e.get("localSheetId"))
                        if e.get("localSheetId")
                        else None,
                        "hidden": e.get("hidden") in ("1", "true"),
                        "refers_to": refers,
                        "broken": "#REF!" in refers,
                    }
                )
        return out


def _anchor(el) -> dict | None:
    """from/to cells of a drawing anchor element (twoCellAnchor, oneCellAnchor, controlPr anchor)."""
    out = {}
    for part in ("from", "to"):
        node = next((c for c in el if local(c.tag) == part), None)
        if node is None:
            continue
        vals = {local(c.tag): (c.text or "0") for c in node}
        try:
            out[part] = a1(int(vals.get("col", "0")) + 1, int(vals.get("row", "0")) + 1)
        except ValueError:
            continue
    return out or None


def _shape_text(el) -> str | None:
    texts = [t.text for t in el.iter() if local(t.tag) == "t" and t.text]
    return "".join(texts) if texts else None


def _chart_info(pkg: Package, part: str) -> dict:
    root = pkg.xml(part)
    info = {"type": None, "title": None, "series": []}
    if root is None:
        return info
    for e in root.iter():
        tag = local(e.tag)
        if tag == "plotArea":
            for c in e:
                t = local(c.tag)
                if t.endswith("Chart"):
                    info["type"] = t
                    for ser in c:
                        if local(ser.tag) != "ser":
                            continue
                        refs = {}
                        for sub in ser:
                            st = local(sub.tag)
                            if st in ("tx", "cat", "val", "xVal", "yVal"):
                                f = next(
                                    (x for x in sub.iter() if local(x.tag) == "f"), None
                                )
                                if f is not None and f.text:
                                    refs[st] = f.text
                        info["series"].append(refs)
                    break
        elif tag == "title" and info["title"] is None:
            info["title"] = _shape_text(e)
    return info


def _vml_shapes(pkg: Package, part: str) -> dict[str, dict]:
    """VML shapes by numeric shape id: object type, macro, caption and anchor."""
    out = {}
    if part not in pkg.names:
        return out
    raw = pkg.zf.read(part).decode("utf-8", errors="replace")
    raw = re.sub(r"<br\s*>", "<br/>", raw)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return out
    for shape in root.iter():
        if local(shape.tag) != "shape":
            continue
        sid = re.sub(r"\D", "", (shape.get("id") or "").rsplit("_s", 1)[-1])
        data = next((c for c in shape.iter() if local(c.tag) == "ClientData"), None)
        if data is None:
            continue
        entry = {"object_type": data.get("ObjectType"), "macro": None, "anchor": None}
        for c in data:
            t = local(c.tag)
            if t == "FmlaMacro":
                entry["macro"] = (c.text or "").strip()
            elif t == "Anchor" and c.text:
                nums = [int(x) for x in re.findall(r"\d+", c.text)]
                if len(nums) == 8:
                    entry["anchor"] = {
                        "from": a1(nums[0] + 1, nums[2] + 1),
                        "to": a1(nums[4] + 1, nums[6] + 1),
                    }
        box = next((c for c in shape.iter() if local(c.tag) == "textbox"), None)
        entry["caption"] = (
            "".join(box.itertext()).strip() or None if box is not None else None
        )
        out[sid] = entry
    return out


# ---------------------------------------------------------------- the reader


def _jsonable(v):
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return str(v)


def infer_type(values: list) -> str:
    vals = [v for v in values if v not in (None, "")]
    if not vals:
        return "text"
    if all(isinstance(v, (dt.datetime, dt.date)) for v in vals):
        return "datetime"
    if all(isinstance(v, bool) for v in vals):
        return "yesno"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        if all(float(v).is_integer() for v in vals):
            return "longinteger"
        return "decimal"
    return "text"


class XlsmReader:
    kind = "xlsm"

    def __init__(self, path: Path, eng: dict):
        self.path = Path(path)
        self.eng = eng
        if not self.path.is_file():
            raise SourceError("source file missing")
        with open(self.path, "rb") as fh:
            head = fh.read(8)
        if head == _CFB_MAGIC:
            raise EncryptedSource()
        try:
            self.zip = zipfile.ZipFile(self.path)
        except zipfile.BadZipFile:
            raise SourceError("source is not an xlsm (zip) file") from None
        self.pkg = Package(self.zip)
        self._wb = None
        self._wbv = None

    # openpyxl workbooks (formulas, and cached values)
    @property
    def wb(self):
        if self._wb is None:
            import openpyxl

            with warnings.catch_warnings():
                # shapes are read from the package XML; openpyxl warns that it drops them
                warnings.simplefilter("ignore", UserWarning)
                self._wb = openpyxl.load_workbook(
                    self.path, data_only=False, keep_links=False
                )
        return self._wb

    @property
    def wbv(self):
        if self._wbv is None:
            import openpyxl

            with warnings.catch_warnings():
                # shapes are read from the package XML; openpyxl warns that it drops them
                warnings.simplefilter("ignore", UserWarning)
                self._wbv = openpyxl.load_workbook(
                    self.path, data_only=True, keep_links=False
                )
        return self._wbv

    def _list_objects(self):
        for ws in self.wb.worksheets:
            for table in ws.tables.values():
                yield ws, table.displayName or table.name, table

    # ------------------------------------------------------------ data access

    def tables(self) -> list[str]:
        return sorted(name for _ws, name, _t in self._list_objects())

    def iter_rows(self, table: str):
        """Rows of a ListObject (cached values) as {header: value}. Data plane only."""
        for ws, name, t in self._list_objects():
            if name != table:
                continue
            c1, r1, c2, r2 = parse_range(t.ref)
            wsv = self.wbv[ws.title]
            headers = [str(wsv.cell(r1, c).value) for c in range(c1, c2 + 1)]
            last = r2 - (t.totalsRowCount or 0)
            for r in range(r1 + 1, last + 1):
                yield {h: wsv.cell(r, c1 + i).value for i, h in enumerate(headers)}

    def iter_texts(self):
        for ws in self.wbv.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str) and cell.value:
                        yield "data", ws.title, cell.value
        for ws in self.wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    if cell.comment is not None and cell.comment.text:
                        yield "data", ws.title, cell.comment.text
        vb = read_vba(self.path)
        if vb:
            for name, code in sorted(vb["modules"].items()):
                yield "code", name, code

    # ------------------------------------------------------------ model

    def build_model(self) -> dict:
        errors: list[dict] = []
        warnings: list[dict] = []
        src = self.eng["source"]
        private = Path(self.eng["paths"]["private"])
        sheets_meta = self.pkg.sheets()
        vb = read_vba(self.path)
        modules_code = vb["modules"] if vb else {}
        all_code = "\n".join(modules_code.values())
        vba_literals = vba_cell_literals(all_code)

        # tables (ListObjects)
        tables, calc_columns, lo_ranges = [], [], {}
        validations = []
        for ws in self.wb.worksheets:
            for dv in ws.data_validations.dataValidation:
                sqref = str(dv.sqref)
                validations.append(
                    {
                        "name": f"{ws.title}!{sqref}",
                        "sheet": ws.title,
                        "range": sqref,
                        "type": dv.type,
                        "operator": dv.operator,
                        "formula1": dv.formula1,
                        "formula2": dv.formula2,
                        "allow_blank": bool(dv.allow_blank),
                    }
                )
        for ws, name, t in self._list_objects():
            rng = parse_range(t.ref)
            lo_ranges.setdefault(ws.title, []).append(rng)
            c1, r1, _c2, r2 = rng
            wsv = self.wbv[ws.title]
            last = r2 - (t.totalsRowCount or 0)
            columns = []
            for i, tc in enumerate(t.tableColumns):
                col = c1 + i
                values = [wsv.cell(r, col).value for r in range(r1 + 1, last + 1)]
                entry = {
                    "name": tc.name,
                    "type": infer_type(values),
                    "size": None,
                    "precision": None,
                    "scale": None,
                    "required": False,
                    "type_inferred": True,
                }
                f = tc.calculatedColumnFormula
                if f is not None and (f.attr_text or "").strip():
                    entry["computed"] = True
                    entry["formula"] = f.attr_text
                    calc_columns.append(
                        {
                            "name": f"{name}[{tc.name}]",
                            "table": name,
                            "column": tc.name,
                            "formula": f.attr_text,
                        }
                    )
                for v in validations:
                    if v["sheet"] != ws.title or v["type"] != "list":
                        continue
                    for part in v["range"].split():
                        try:
                            vr = parse_range(part)
                        except ValueError:
                            continue
                        if vr[0] <= col <= vr[2] and vr[1] <= last and vr[3] > r1:
                            entry["choices_from"] = v["formula1"]
                columns.append(entry)
            low = name.lower()
            role = "documentation" if any(h in low for h in DOC_TABLE_HINTS) else "data"
            tables.append(
                {
                    "name": name,
                    "sheet": ws.title,
                    "ref": t.ref,
                    "role": role,
                    "columns": columns,
                    "primary_key": [],
                    "indexes": [],
                    "_values": {
                        c["name"]: [
                            wsv.cell(r, c1 + i).value for r in range(r1 + 1, last + 1)
                        ]
                        for i, c in enumerate(columns)
                    },
                }
            )
        relationships = self._infer_keys(tables)

        # formulas and cells
        sheets, blocks, layout_cells, cached = [], [], {}, {}
        formula_refs: dict[str, list] = {}
        formula_count = 0
        no_cache = 0
        array_cells = 0
        per_sheet_formulas: dict[str, dict] = {}
        for ws in self.wb.worksheets:
            wsv = self.wbv[ws.title]
            fcells = {}
            for row in ws.iter_rows():
                for cell in row:
                    v = cell.value
                    if cell.data_type == "f" or type(v).__name__ in (
                        "ArrayFormula",
                        "DataTableFormula",
                    ):
                        text = (
                            v
                            if isinstance(v, str)
                            else getattr(v, "text", None) or str(v)
                        )
                        if type(v).__name__ == "ArrayFormula":
                            array_cells += 1
                        fcells[(cell.column, cell.row)] = text
                        for sheet, rng in referenced_cells(text, ws.title):
                            formula_refs.setdefault(sheet, []).append(rng)
            per_sheet_formulas[ws.title] = fcells
            formula_count += len(fcells)
            for c, r in fcells:
                val = wsv.cell(r, c).value
                if val is None:
                    no_cache += 1
                cached.setdefault(ws.title, {})[a1(c, r)] = _jsonable(val)
        charts, buttons, assignments, activex, comment_counts = self._drawings(
            sheets_meta, warnings
        )
        chart_sheets = {c["sheet"] for c in charts}
        for meta in sheets_meta:
            if meta["chartsheet"]:
                continue
            ws = self.wb[meta["name"]]
            title = ws.title
            fcells = per_sheet_formulas.get(title, {})
            in_lo = lo_ranges.get(title, [])
            outside = {
                k: v
                for k, v in fcells.items()
                if not any(in_range(k[0], k[1], r) for r in in_lo)
            }
            for block in formula_blocks(outside):
                cs = [c for c, _ in block]
                rs = [r for _, r in block]
                rng = range_a1(min(cs), min(rs), max(cs), max(rs))
                blocks.append(
                    {
                        "name": f"{title}!{rng}",
                        "sheet": title,
                        "range": rng,
                        "cells": [
                            {
                                "address": a1(c, r),
                                "formula": outside[(c, r)],
                                "has_cached_value": cached[title][a1(c, r)] is not None,
                            }
                            for c, r in block
                        ],
                    }
                )
            role = self._role(title, tables, chart_sheets, fcells, ws)
            sheets.append(
                {
                    "name": title,
                    "index": meta["index"],
                    "state": meta["state"],
                    "role": role,
                    "role_inferred": True,
                    "formula_cells": len(fcells),
                }
            )
            layout_cells[title] = self._layout(
                ws, role, in_lo, formula_refs, vba_literals, fcells
            )

        # private: cached values, constants and comments (data zone)
        private_cells = {}
        comments_private = {}
        for ws in self.wb.worksheets:
            wsv = self.wbv[ws.title]
            consts = {}
            for row in ws.iter_rows():
                for cell in row:
                    if cell.value is not None and cell.data_type != "f":
                        consts[cell.coordinate] = _jsonable(wsv[cell.coordinate].value)
                    if cell.comment is not None:
                        comments_private.setdefault(ws.title, {})[cell.coordinate] = (
                            cell.comment.text
                        )
            private_cells[ws.title] = consts
        self._write_private(private, cached, private_cells, comments_private)

        # VBA
        known = {"tables": {t["name"] for t in tables}}
        if vb:
            modules, userforms, vw = vba_model(
                vb["project"], vb["modules"], vb["references"], vb["sites"], known
            )
            warnings.extend(vw)
            refs = vb["references"]
        else:
            modules, userforms, refs = [], [], []
        if src.get("vba_export") and src.get("clone_dir"):
            cross_check(
                Path(src["clone_dir"]) / src["vba_export"], modules, userforms, warnings
            )
        for module in modules:
            module["handlers"] = sorted(
                {p["name"] for p in module["procedures"] if "_" in p["name"]}
            )
        dnames = self.pkg.defined_names()
        for d in dnames:
            if d["broken"]:
                warnings.append(
                    {
                        "code": "defined-name-broken-ref",
                        "object": f"defined_name:{d['name']}",
                    }
                )
        sample_counts = self._sample_outputs(src)

        for t in tables:
            t.pop("_values", None)
        vba_lines = sum(m["nonblank_lines"] for m in modules)
        kinds = {}
        for m in modules:
            kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
        counts = {
            "worksheets": sum(1 for s in sheets_meta if not s["chartsheet"]),
            "hidden_worksheets": sum(
                1
                for s in sheets_meta
                if not s["chartsheet"] and s["state"] != "visible"
            ),
            "chartsheets": sum(1 for s in sheets_meta if s["chartsheet"]),
            "tables": len(tables),
            "calculated_columns": len(calc_columns),
            "defined_names": len(dnames),
            "defined_names_broken": sum(1 for d in dnames if d["broken"]),
            "data_validations": len(validations),
            "formula_cells": formula_count,
            "formula_cells_without_cached_value": no_cache,
            "array_formula_cells": array_cells,
            "formula_blocks": len(blocks),
            "charts": len(charts),
            "form_buttons": len(buttons),
            "macro_assignments": len(assignments),
            "activex_controls": len(activex),
            "cell_comments": sum(comment_counts.values()),
            "vba_components": len(modules),
            "vba_standard_modules": kinds.get("standard", 0),
            "vba_class_modules": kinds.get("class", 0),
            "vba_document_modules": kinds.get("document", 0),
            "vba_userforms": kinds.get("userform", 0),
            "vba_nonblank_lines": vba_lines,
            "vba_procedures": sum(len(m["procedures"]) for m in modules),
            "userform_controls": sum(len(u["controls"]) for u in userforms),
            "vba_references": len(refs),
        }
        schema = {
            "format_version": 1,
            "source_kind": "xlsm",
            "tables": sorted(tables, key=lambda t: t["name"]),
            "relationships": sorted(relationships, key=lambda r: r["name"]),
        }
        objects = {
            "format_version": 1,
            "source_kind": "xlsm",
            "queries": [],
            "forms": [],
            "reports": [],
            "macros": [],
            "linked_tables": [],
            "sheets": sorted(sheets, key=lambda s: s["index"]),
            "layout": dict(sorted(layout_cells.items())),
            "formula_blocks": sorted(blocks, key=lambda b: b["name"]),
            "calculated_columns": sorted(calc_columns, key=lambda c: c["name"]),
            "defined_names": sorted(
                dnames, key=lambda d: (d["name"], d["scope_sheet"] or -1)
            ),
            "data_validations": sorted(validations, key=lambda v: v["name"]),
            "charts": sorted(charts, key=lambda c: c["name"]),
            "buttons": sorted(buttons, key=lambda b: b["name"]),
            "macro_assignments": sorted(assignments, key=lambda a: a["name"]),
            "activex": sorted(activex, key=lambda a: a["name"]),
            "comments": dict(sorted(comment_counts.items())),
            "userforms": sorted(userforms, key=lambda u: u["name"]),
            "vba_references": [{"name": r} for r in refs],
            "facts": {
                "compiled_only": False,
                "vba_present": bool(modules),
                "sample_outputs": sample_counts,
                "formula_cells_by_sheet": {
                    s: len(v) for s, v in sorted(per_sheet_formulas.items()) if v
                },
                "table_columns": {t["name"]: len(t["columns"]) for t in tables},
            },
        }
        code = {m["name"]: vba.normalize(modules_code[m["name"]]) for m in modules}
        return {
            "schema": schema,
            "objects": objects,
            "procedures": {
                "format_version": 1,
                "modules": sorted(modules, key=lambda m: m["name"]),
            },
            "datamacros": {"format_version": 1, "datamacros": [], "files": []},
            "code": code,
            "counts": counts,
            "errors": errors,
            "warnings": warnings,
        }

    # ------------------------------------------------------------ helpers

    def _infer_keys(self, tables: list[dict]) -> list[dict]:
        keys = {}
        for t in tables:
            if t["role"] != "data":
                continue
            for c in t["columns"]:
                vals = t["_values"][c["name"]]
                if c.get("computed") or not vals:
                    continue
                if all(v not in (None, "") for v in vals) and len(
                    {str(v) for v in vals}
                ) == len(vals):
                    t["primary_key"] = [c["name"]]
                    t["key_inferred"] = True
                    keys[t["name"]] = (c["name"], {str(v) for v in vals}, c["type"])
                    break
        rels = []
        for t in tables:
            if t["role"] != "data":
                continue
            for c in t["columns"]:
                if t["primary_key"] == [c["name"]] or c.get("computed"):
                    continue
                vals = {str(v) for v in t["_values"][c["name"]] if v not in (None, "")}
                if not vals:
                    continue
                for other, (kcol, kvals, ktype) in sorted(keys.items()):
                    if other == t["name"] or ktype != c["type"]:
                        continue
                    if vals <= kvals:
                        rels.append(
                            {
                                "name": f"{t['name']}.{c['name']}->{other}.{kcol}",
                                "from_table": t["name"],
                                "from_columns": [c["name"]],
                                "to_table": other,
                                "to_columns": [kcol],
                                "enforce": False,
                                "cascade_update": False,
                                "cascade_delete": False,
                                "inferred": True,
                            }
                        )
        return rels

    def _role(self, title, tables, chart_sheets, fcells, ws) -> str:
        low = title.lower()
        for role, hints in ROLE_HINTS:
            if any(h in low for h in hints):
                return role
        if title in chart_sheets:
            return "dashboard"
        own = [t for t in tables if t["sheet"] == title]
        if own and all(t["role"] == "documentation" for t in own):
            return "documentation"
        consts = sum(
            1
            for row in ws.iter_rows()
            for c in row
            if c.value is not None and c.data_type != "f"
        )
        if fcells and len(fcells) >= consts:
            return "calculation"
        return "input"

    def _layout(self, ws, role, in_lo, formula_refs, vba_literals, fcells) -> dict:
        widths = {}
        for key, dim in sorted(ws.column_dimensions.items()):
            if dim.width is not None and dim.customWidth:
                widths[key] = dim.width
        heights = {}
        for key, dim in sorted(ws.row_dimensions.items()):
            if dim.height is not None and dim.customHeight:
                heights[str(key)] = dim.height
        read = formula_refs.get(ws.title, []) + vba_literals
        labels, constants = [], []
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is None or cell.data_type == "f":
                    continue
                c, r = cell.column, cell.row
                if any(in_range(c, r, rng) for rng in in_lo):
                    continue
                is_label = (
                    role in ("input", "template", "dashboard")
                    and isinstance(cell.value, str)
                    and len(cell.value) <= LABEL_MAX
                    and not any(in_range(c, r, rng) for rng in read)
                )
                if is_label:
                    labels.append({"cell": cell.coordinate, "text": cell.value})
                else:
                    constants.append({"cell": cell.coordinate, "value": None})
        list_objects = []
        for t in sorted(ws.tables.values(), key=lambda t: t.displayName or t.name):
            name = t.displayName or t.name
            _c1, _r1, _c2, _r2 = parse_range(t.ref)
            list_objects.append(
                {
                    "name": name,
                    "ref": t.ref,
                    "headers": [tc.name for tc in t.tableColumns],
                }
            )
        return {
            "default_column_width": ws.sheet_format.defaultColWidth
            or ws.sheet_format.baseColWidth,
            "default_row_height": ws.sheet_format.defaultRowHeight,
            "column_widths": widths,
            "row_heights": heights,
            "merged": sorted(str(m) for m in ws.merged_cells.ranges),
            "labels": labels,
            "constants": constants,
            "list_objects": list_objects,
        }

    def _drawings(self, sheets_meta, warnings):
        charts, buttons, assignments, activex = [], [], [], []
        comment_counts = {}
        for meta in sheets_meta:
            part = meta["part"]
            sheet = meta["name"]
            rels = self.pkg.rels(part)
            vml = {}
            for rtype, target in rels.values():
                if rtype == "vmlDrawing":
                    vml.update(_vml_shapes(self.pkg, target))
                elif rtype == "comments":
                    root = self.pkg.xml(target)
                    if root is not None:
                        n = sum(1 for e in root.iter() if local(e.tag) == "comment")
                        if n:
                            comment_counts[sheet] = comment_counts.get(sheet, 0) + n
            for _rid, (rtype, target) in sorted(rels.items()):
                if rtype != "drawing":
                    continue
                droot = self.pkg.xml(target)
                drels = self.pkg.rels(target)
                if droot is None:
                    continue
                for anchor in droot:
                    if not local(anchor.tag).endswith("Anchor"):
                        continue
                    anc = _anchor(anchor)
                    for el in anchor.iter():
                        tag = local(el.tag)
                        if tag == "graphicFrame":
                            cnv = next(
                                (x for x in el.iter() if local(x.tag) == "cNvPr"), None
                            )
                            chart_ref = next(
                                (x for x in el.iter() if local(x.tag) == "chart"), None
                            )
                            if chart_ref is None:
                                continue
                            rid = chart_ref.get(f"{{{NS_R}}}id")
                            cpart = drels.get(rid, ("", ""))[1]
                            info = _chart_info(self.pkg, cpart)
                            cname = cnv.get("name") if cnv is not None else cpart
                            charts.append(
                                {
                                    "name": f"{sheet}!{cname}",
                                    "sheet": sheet,
                                    "chart": cname,
                                    "anchor": anc,
                                    **info,
                                }
                            )
                        elif tag in ("sp", "pic", "grpSp", "cxnSp") and el.get("macro"):
                            cnv = next(
                                (x for x in el.iter() if local(x.tag) == "cNvPr"), None
                            )
                            sname = cnv.get("name") if cnv is not None else "shape"
                            assignments.append(
                                {
                                    "name": f"{sheet}!{sname}",
                                    "sheet": sheet,
                                    "shape": sname,
                                    "kind": tag,
                                    "macro": el.get("macro"),
                                    "caption": _shape_text(el),
                                    "anchor": anc,
                                }
                            )
            # form controls and ActiveX controls listed in the sheet XML
            sroot = self.pkg.xml(part)
            if sroot is None:
                continue
            for ctl in sroot.iter():
                if local(ctl.tag) != "control":
                    continue
                rid = ctl.get(f"{{{NS_R}}}id")
                rtype, target = rels.get(rid, ("", ""))
                cname = ctl.get("name")
                shape_id = ctl.get("shapeId") or ""
                pr = next((x for x in ctl if local(x.tag) == "controlPr"), None)
                anc = None
                if pr is not None:
                    a = next((x for x in pr if local(x.tag) == "anchor"), None)
                    anc = _anchor(a) if a is not None else None
                v = vml.get(shape_id, {})
                if rtype == "ctrlProp":
                    croot = self.pkg.xml(target)
                    otype = croot.get("objectType") if croot is not None else None
                    if otype == "Button":
                        buttons.append(
                            {
                                "name": f"{sheet}!{cname}",
                                "sheet": sheet,
                                "control": cname,
                                "caption": v.get("caption"),
                                "macro": (pr.get("macro") if pr is not None else None)
                                or v.get("macro"),
                                "anchor": anc or v.get("anchor"),
                            }
                        )
                elif rtype == "control":
                    croot = self.pkg.xml(target)
                    clsid = None
                    if croot is not None:
                        clsid = next(
                            (
                                val
                                for key, val in croot.attrib.items()
                                if local(key) == "classid"
                            ),
                            None,
                        )
                    activex.append(
                        {
                            "name": f"{sheet}!{cname}",
                            "sheet": sheet,
                            "control": cname,
                            "clsid": clsid,
                            "part": target.rsplit("/", 1)[-1],
                            "anchor": anc or v.get("anchor"),
                        }
                    )
        # ActiveX parts not referenced from a sheet still count (xl/activeX/*.xml)
        listed = {a["part"] for a in activex}
        for name in sorted(self.pkg.names):
            if (
                re.fullmatch(r"xl/activeX/activeX\d+\.xml", name)
                and name.rsplit("/", 1)[-1] not in listed
            ):
                warnings.append(
                    {
                        "code": "activex-without-sheet",
                        "object": f"part:{name.rsplit('/', 1)[-1]}",
                    }
                )
                activex.append(
                    {
                        "name": f"?!{name.rsplit('/', 1)[-1]}",
                        "sheet": None,
                        "control": None,
                        "clsid": None,
                        "part": name.rsplit("/", 1)[-1],
                        "anchor": None,
                    }
                )
        return charts, buttons, assignments, activex, comment_counts

    def _write_private(self, private: Path, cached, cells, comments) -> None:
        from toolkit.scrub import atomic_write

        folder = private / "workbook"
        for name, doc in (
            ("cached-values.json", cached),
            ("cells.json", cells),
            ("comments.json", comments),
        ):
            body = json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
            atomic_write(folder / name, body.encode("utf-8"))

    def _sample_outputs(self, src: dict) -> dict:
        out = {}
        folders = src.get("sample_outputs") or {}
        base = Path(src.get("clone_dir") or self.path.parent)
        for key, rel in sorted(folders.items()):
            d = base / rel
            label = key.removesuffix("_dir")
            out[label] = (
                sum(
                    1 for p in d.iterdir() if p.is_file() and not p.name.startswith(".")
                )
                if d.is_dir()
                else 0
            )
        return out
