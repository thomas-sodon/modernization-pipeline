"""xlsm: structure into the model, values into the private workbook/ folder.

The workbook is built with openpyxl at test time; form-control, ActiveX and shape-macro parts
are added to its zip by hand. The VBA path uses the hand-written PROJECT stream and component
texts in toolkit/tests/fixtures/parse/xlsm/ (no binary vbaProject.bin is committed)."""

from __future__ import annotations

import json
import struct
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.comments import Comment
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.formula import ArrayFormula
from openpyxl.worksheet.table import Table, TableColumn, TableFormula

from toolkit import cli, inventory
from toolkit import model as model_mod
from toolkit.sources import xlsm as xl

FIX = Path(__file__).parent / "fixtures" / "parse" / "xlsm"
COMPONENTS = {
    "ThisWorkbook": "ThisWorkbook.txt",
    "shQuote": "shQuote.txt",
    "modQuote": "modQuote.bas.txt",
    "clsLine": "clsLine.cls.txt",
}
SITES = {
    "frmQuote": [
        {"name": "cmdOK", "caption": "OK", "ClsidCacheIndex": 17, "value": None},
        {
            "name": "txtCustomer",
            "caption": None,
            "ClsidCacheIndex": 23,
            "value": "typed",
        },
    ]
}
SENTINELS = ("SENTINEL-XLSM-CUSTOMER", "SENTINEL-COMMENT-TEXT", "Alpha Widgets")
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _table(ws, name, ref, headers, formulas=None):
    t = Table(displayName=name, ref=ref)
    t.tableColumns = [
        TableColumn(
            id=i + 1,
            name=h,
            calculatedColumnFormula=TableFormula(attr_text=(formulas or {})[h])
            if h in (formulas or {})
            else None,
        )
        for i, h in enumerate(headers)
    ]
    ws.add_table(t)


def build_workbook(path: Path, extras: bool = False) -> Path:
    wb = Workbook()
    q = wb.active
    q.title = "Quote"
    q["A1"] = "Widget quote"
    q.merge_cells("A1:C1")
    q["A3"] = "Customer"
    q["B3"] = "SENTINEL-XLSM-CUSTOMER"
    q["A4"] = "Quantity"
    q["B4"] = 3
    q["A5"] = "Price"
    q["B5"] = 9.5
    q["A6"] = "Tax"
    q["B6"] = 0.2
    q["D2"] = "Notes"
    q["C4"] = "=B4*B5"
    q["C5"] = "=C4*TaxRate"
    q["D4"] = "=SUM(C4:C5)"
    q["A8"] = "=UPPER(A3)&B3"
    q["F1"] = "Long text " + "x" * 90
    q.column_dimensions["B"].width = 24
    q.row_dimensions[1].height = 30
    q["A4"].comment = Comment("SENTINEL-COMMENT-TEXT", "tester")
    dv = DataValidation(type="list", formula1='"1,2,3"')
    q.add_data_validation(dv)
    dv.add("B4")

    d = wb.create_sheet("Data")
    d.append(["Item", "CustomerID", "Qty", "Total"])
    d.append(["bolt", 101, 2, "=[@Qty]*2"])
    d.append(["nut", 102, 5, "=[@Qty]*2"])
    _table(
        d,
        "SalesTable",
        "A1:D3",
        ["Item", "CustomerID", "Qty", "Total"],
        {"Total": "[@Qty]*2"},
    )
    dv2 = DataValidation(type="list", formula1="Customers!$A$2:$A$3")
    d.add_data_validation(dv2)
    dv2.add("B2:B3")

    c = wb.create_sheet("Customers")
    c.append(["CustomerID", "Name"])
    c.append([101, "Alpha Widgets"])
    c.append([102, "Beta Widgets"])
    _table(c, "CustomerTable", "A1:B3", ["CustomerID", "Name"])

    g = wb.create_sheet("Glossary")
    g.append(["Term", "Meaning"])
    g.append(["Qty", "How many"])
    _table(g, "HelpGlossary", "A1:B2", ["Term", "Meaning"])

    dash = wb.create_sheet("Dashboard")
    chart = BarChart()
    chart.title = "Qty by item"
    chart.add_data(Reference(d, min_col=3, min_row=1, max_row=3), titles_from_data=True)
    dash.add_chart(chart, "B2")

    wb.defined_names["TaxRate"] = DefinedName("TaxRate", attr_text="Quote!$B$6")
    wb.defined_names["OldRange"] = DefinedName("OldRange", attr_text="#REF!$A$1")
    if extras:
        # an array (CSE) formula and a chart sheet, for the persisted facts
        q["F8"] = ArrayFormula("F8", "=SUM(B4:B5*2)")
        sheet_chart = BarChart()
        sheet_chart.add_data(
            Reference(d, min_col=3, min_row=1, max_row=3), titles_from_data=True
        )
        wb.create_chartsheet("QtyChart").add_chart(sheet_chart)
    wb.save(path)
    _add_controls(path)
    return path


def _add_controls(path: Path) -> None:
    """Add a form-control button (ctrlProp + VML), an ActiveX control and a shape macro."""
    with zipfile.ZipFile(path) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    rels = parts["xl/worksheets/_rels/sheet1.xml.rels"].decode()
    rels = rels.replace(
        "</Relationships>",
        f'<Relationship Id="rIdBtnV" Type="{R}/vmlDrawing" Target="../drawings/vmlDrawingBtn.vml"/>'
        f'<Relationship Id="rIdBtnC" Type="{R}/ctrlProp" Target="../ctrlProps/ctrlProp1.xml"/>'
        f'<Relationship Id="rIdAx" Type="{R}/control" Target="../activeX/activeX1.xml"/>'
        "</Relationships>",
    )
    parts["xl/worksheets/_rels/sheet1.xml.rels"] = rels.encode()
    sheet = parts["xl/worksheets/sheet1.xml"].decode()
    sheet = sheet.replace(
        "</worksheet>",
        f'<controls xmlns:r="{R}"><control shapeId="1025" r:id="rIdBtnC" name="Button 1"/>'
        '<control shapeId="1026" r:id="rIdAx" name="CommandButton1"/></controls></worksheet>',
    )
    parts["xl/worksheets/sheet1.xml"] = sheet.encode()
    parts["xl/ctrlProps/ctrlProp1.xml"] = (
        b'<formControlPr xmlns="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"'
        b' objectType="Button" lockText="1"/>'
    )
    parts["xl/activeX/activeX1.xml"] = (
        b'<ax:ocx xmlns:ax="http://schemas.microsoft.com/office/2006/activeX"'
        b' ax:classid="{D7053240-CE69-11CD-A777-00DD01143C57}" ax:persistence="persistStreamInit"/>'
    )
    parts["xl/drawings/vmlDrawingBtn.vml"] = (
        b'<xml xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office"'
        b' xmlns:x="urn:schemas-microsoft-com:office:excel">'
        b'<v:shape id="_x0000_s1025" type="#_x0000_t201"><v:textbox><div><font>Price it</font></div>'
        b'</v:textbox><x:ClientData ObjectType="Button"><x:Anchor>4, 0, 9, 0, 6, 0, 11, 0</x:Anchor>'
        b"<x:FmlaMacro>[0]!modQuote.PriceQuote</x:FmlaMacro></x:ClientData></v:shape></xml>"
    )
    drawing = parts["xl/drawings/drawing1.xml"].decode()
    cut = drawing.rindex("</")
    anchor = (
        '<xdr:twoCellAnchor xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"'
        ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<xdr:from><xdr:col>8</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>1</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>"
        "<xdr:to><xdr:col>10</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>3</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>"
        '<xdr:sp macro="modQuote.PriceQuote" textlink=""><xdr:nvSpPr><xdr:cNvPr id="9" name="Rounded Rectangle 9"/>'
        "<xdr:cNvSpPr/></xdr:nvSpPr><xdr:txBody><a:p><a:r><a:t>Run pricing</a:t></a:r></a:p></xdr:txBody></xdr:sp>"
        "<xdr:clientData/></xdr:twoCellAnchor>"
    )
    parts["xl/drawings/drawing1.xml"] = (
        drawing[:cut] + anchor + drawing[cut:]
    ).encode()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data)


def fixture_modules() -> dict[str, str]:
    return {n: (FIX / f).read_text(encoding="utf-8") for n, f in COMPONENTS.items()}


def export_folder(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for src, name in (
        ("modQuote.bas.txt", "modQuote.bas"),
        ("clsLine.cls.txt", "clsLine.cls"),
        ("frmQuote.frm.txt", "frmQuote.frm"),
    ):
        (dest / name).write_text(
            (FIX / src).read_text(encoding="utf-8"), encoding="cp1252"
        )
    return dest


def frm_code() -> str:
    text = (FIX / "frmQuote.frm.txt").read_text(encoding="utf-8")
    return text[text.index("Attribute VB_Name") :]


@pytest.fixture
def xlsm_eng(make_engagement, tmp_path, monkeypatch):
    eng = make_engagement(
        tmp_path,
        source={
            "kind": "xlsm",
            "clone_dir": "{{p.private}}/sources/QuoteBook",
            "path": "{{p.private}}/sources/QuoteBook/quote.xlsm",
            "vba_export": "VBA Code",
            "sample_outputs": {"pdf_dir": "PDF Out", "msg_dir": "Mail Out"},
        },
    )
    private = Path(json.loads((eng.parent / "paths.local.json").read_text())["private"])
    clone = private / "sources" / "QuoteBook"
    clone.mkdir(parents=True)
    build_workbook(clone / "quote.xlsm")
    export_folder(clone / "VBA Code")
    (clone / "PDF Out").mkdir()
    for n in ("q1.pdf", "q2.pdf"):
        (clone / "PDF Out" / n).write_bytes(b"%synthetic")
    modules = fixture_modules()
    modules["frmQuote"] = frm_code()
    monkeypatch.setattr(
        xl,
        "read_vba",
        lambda path: {
            "project": (FIX / "PROJECT.txt").read_text(encoding="utf-8"),
            "modules": modules,
            "references": ["stdole", "Office"],
            "sites": SITES,
        },
    )
    return eng, private


def run_parse(capfd, eng, out, *flags):
    code = cli.main(["parse", str(eng), "--out", str(out), *flags])
    stdout, _err = capfd.readouterr()
    summary = json.loads(stdout.strip())
    return code, summary


@pytest.fixture
def parsed(xlsm_eng, capfd, quiet):
    eng, private = xlsm_eng
    out = private / "out" / "model"
    code, summary = run_parse(capfd, eng, out, "--strict")
    docs = {s: json.loads((out / f"{s}.json").read_text()) for s in model_mod.FILES}
    return code, summary, docs, out, private


def test_parse_exit_and_counts(parsed):
    code, summary, docs, _out, _private = parsed
    assert code == 0, summary  # a broken #REF! name is a warning, not an error
    assert "warning defined-name-broken-ref: 1" in summary["messages"]
    assert model_mod.validate(docs) == []
    c = summary["counts"]
    expected = {
        "worksheets": 5,
        "hidden_worksheets": 0,
        "chartsheets": 0,
        "tables": 3,
        "calculated_columns": 1,
        "defined_names": 2,
        "defined_names_broken": 1,
        "data_validations": 2,
        "formula_cells": 6,
        "formula_cells_without_cached_value": 6,
        "array_formula_cells": 0,
        "charts": 1,
        "form_buttons": 1,
        "macro_assignments": 1,
        "activex_controls": 1,
        "cell_comments": 1,
        "vba_components": 5,
        "vba_standard_modules": 1,
        "vba_class_modules": 1,
        "vba_document_modules": 2,
        "vba_userforms": 1,
        "vba_references": 2,
        "userform_controls": 2,
    }
    assert {k: c[k] for k in expected} == expected


def test_tables_types_keys_and_names(parsed):
    _code, _summary, docs, _out, _private = parsed
    tables = {t["name"]: t for t in docs["schema"]["tables"]}
    sales = tables["SalesTable"]
    assert (sales["sheet"], sales["ref"], sales["role"]) == ("Data", "A1:D3", "data")
    cols = {c["name"]: c for c in sales["columns"]}
    assert [c["name"] for c in sales["columns"]] == [
        "Item",
        "CustomerID",
        "Qty",
        "Total",
    ]
    assert (cols["Item"]["type"], cols["CustomerID"]["type"]) == ("text", "longinteger")
    assert cols["Total"]["computed"] is True and cols["Total"]["formula"] == "[@Qty]*2"
    assert cols["CustomerID"]["choices_from"] == "Customers!$A$2:$A$3"
    assert tables["HelpGlossary"]["role"] == "documentation"
    assert tables["CustomerTable"]["primary_key"] == ["CustomerID"]
    assert tables["CustomerTable"]["key_inferred"] is True
    (rel,) = docs["schema"]["relationships"]
    assert (
        rel["from_table"],
        rel["from_columns"],
        rel["to_table"],
        rel["inferred"],
    ) == (
        "SalesTable",
        ["CustomerID"],
        "CustomerTable",
        True,
    )
    objects = docs["objects"]
    assert [c["name"] for c in objects["calculated_columns"]] == ["SalesTable[Total]"]
    assert [v["name"] for v in objects["data_validations"]] == [
        "Data!B2:B3",
        "Quote!B4",
    ]


def test_formula_blocks_and_cache_flags(parsed):
    _code, _summary, docs, _out, _private = parsed
    blocks = {b["name"]: b for b in docs["objects"]["formula_blocks"]}
    assert sorted(blocks) == ["Quote!A8", "Quote!C4:D5"]
    cells = {c["address"]: c for c in blocks["Quote!C4:D5"]["cells"]}
    assert cells["C5"]["formula"] == "=C4*TaxRate"
    assert cells["D4"]["formula"] == "=SUM(C4:C5)"
    assert all(c["has_cached_value"] is False for c in cells.values())
    facts = docs["objects"]["facts"]
    assert facts["formula_cells_by_sheet"] == {"Data": 2, "Quote": 4}
    assert facts["table_columns"] == {
        "CustomerTable": 2,
        "HelpGlossary": 2,
        "SalesTable": 4,
    }
    assert facts["sample_outputs"] == {"msg": 0, "pdf": 2}
    assert facts["chartsheets"] == 0
    assert facts["formula_cells_without_cached_value"] == 6
    assert facts["array_formula_cells"] == 0
    names = {d["name"]: d for d in docs["objects"]["defined_names"]}
    assert names["OldRange"]["broken"] is True and names["TaxRate"]["broken"] is False


def test_array_formula_and_chartsheet_facts_persist(xlsm_eng, capfd, quiet):
    eng, private = xlsm_eng
    build_workbook(private / "sources" / "QuoteBook" / "quote.xlsm", extras=True)
    out = private / "out" / "model"
    code, summary = run_parse(capfd, eng, out, "--strict")
    assert code == 0, summary
    docs = {s: json.loads((out / f"{s}.json").read_text()) for s in model_mod.FILES}
    assert model_mod.validate(docs) == []
    facts = docs["objects"]["facts"]
    assert facts["chartsheets"] == summary["counts"]["chartsheets"] == 1
    assert facts["array_formula_cells"] == summary["counts"]["array_formula_cells"] == 1
    assert (
        facts["formula_cells_without_cached_value"]
        == summary["counts"]["formula_cells_without_cached_value"]
        == 7
    )
    # the inventory reads all three from the model; none is reported missing
    counts, missing = inventory.count("xlsm", docs)
    assert missing == []
    assert counts["chartsheets"] == 1
    assert counts["array_formula_cells"] == 1
    assert counts["formula_cells_without_cached_value"] == 7


def test_layout_labels_and_value_null_positions(parsed):
    _code, _summary, docs, _out, _private = parsed
    objects = docs["objects"]
    roles = {s["name"]: s["role"] for s in objects["sheets"]}
    assert roles == {
        "Quote": "input",
        "Data": "input",
        "Customers": "input",
        "Glossary": "documentation",
        "Dashboard": "dashboard",
    }
    q = objects["layout"]["Quote"]
    assert q["column_widths"] == {"B": 24}
    assert q["row_heights"] == {"1": 30}
    assert q["merged"] == ["A1:C1"]
    labels = {lab["cell"]: lab["text"] for lab in q["labels"]}
    # A3 and B3 are read by a formula and D2 by a VBA Range literal: positions only. A short
    # text constant that nothing reads would count as a label (the contract's label rule).
    assert labels == {
        "A1": "Widget quote",
        "A4": "Quantity",
        "A5": "Price",
        "A6": "Tax",
    }
    constants = [c["cell"] for c in q["constants"]]
    assert constants == ["F1", "D2", "A3", "B3", "B4", "B5", "B6"]
    assert all(c["value"] is None for c in q["constants"])
    data = objects["layout"]["Data"]
    assert data["list_objects"] == [
        {
            "name": "SalesTable",
            "ref": "A1:D3",
            "headers": ["Item", "CustomerID", "Qty", "Total"],
        }
    ]
    assert data["labels"] == [] and data["constants"] == []


def test_drawings_controls_and_comments(parsed):
    _code, _summary, docs, _out, _private = parsed
    objects = docs["objects"]
    (chart,) = objects["charts"]
    assert chart["name"].startswith("Dashboard!")
    assert (chart["type"], chart["title"]) == ("barChart", "Qty by item")
    assert chart["anchor"]["from"] == "B2"
    assert chart["series"][0]["val"].endswith("$C$2:$C$3")
    assert objects["buttons"] == [
        {
            "name": "Quote!Button 1",
            "sheet": "Quote",
            "control": "Button 1",
            "caption": "Price it",
            "macro": "[0]!modQuote.PriceQuote",
            "anchor": {"from": "E10", "to": "G12"},
        }
    ]
    (shape,) = objects["macro_assignments"]
    assert (shape["name"], shape["macro"], shape["caption"]) == (
        "Dashboard!Rounded Rectangle 9",
        "modQuote.PriceQuote",
        "Run pricing",
    )
    assert shape["anchor"] == {"from": "I2", "to": "K4"}
    (ax,) = objects["activex"]
    assert (ax["name"], ax["clsid"]) == (
        "Quote!CommandButton1",
        "{D7053240-CE69-11CD-A777-00DD01143C57}",
    )
    assert objects["comments"] == {"Quote": 1}


def test_vba_components_and_userforms(parsed):
    _code, _summary, docs, out, _private = parsed
    mods = {m["name"]: m for m in docs["procedures"]["modules"]}
    assert {n: m["kind"] for n, m in mods.items()} == {
        "ThisWorkbook": "document",
        "clsLine": "class",
        "frmQuote": "userform",
        "modQuote": "standard",
        "shQuote": "document",
    }
    assert mods["modQuote"]["nonblank_lines"] == 12
    assert [p["name"] for p in mods["modQuote"]["procedures"]] == [
        "PriceQuote",
        "QuoteTitle",
    ]
    assert mods["shQuote"]["handlers"] == ["btnPrice_Click"]
    assert (out / "code" / "modQuote.txt").is_file()
    (uf,) = docs["objects"]["userforms"]
    assert uf["caption"] == "New quote"
    controls = {c["name"]: c for c in uf["controls"]}
    assert controls["cmdOK"] == {
        "name": "cmdOK",
        "type": "CommandButton",
        "caption": "OK",
        "left": 3120.0,
        "top": 2520.0,
        "width": 1215.0,
        "height": 375.0,
    }
    assert controls["txtCustomer"]["type"] == "TextBox"
    assert docs["objects"]["vba_references"] == [{"name": "stdole"}, {"name": "Office"}]


def test_values_stay_in_the_private_workbook_folder(parsed):
    _code, summary, _docs, out, private = parsed
    model_text = "".join(
        p.read_text(encoding="utf-8") for p in out.rglob("*") if p.is_file()
    ) + json.dumps(summary)
    for s in SENTINELS:
        assert s not in model_text
    wb_dir = private / "workbook"
    assert sorted(p.name for p in wb_dir.iterdir()) == [
        "cached-values.json",
        "cells.json",
        "comments.json",
    ]
    cells = json.loads((wb_dir / "cells.json").read_text())
    assert cells["Quote"]["B3"] == "SENTINEL-XLSM-CUSTOMER"
    comments = json.loads((wb_dir / "comments.json").read_text())
    assert comments["Quote"]["A4"] == "SENTINEL-COMMENT-TEXT"


def test_reader_rows_and_tables(xlsm_eng):
    eng, private = xlsm_eng
    from toolkit.engagement import load

    reader = xl.XlsmReader(
        private / "sources" / "QuoteBook" / "quote.xlsm", load(str(eng))
    )
    assert reader.tables() == ["CustomerTable", "HelpGlossary", "SalesTable"]
    rows = list(reader.iter_rows("CustomerTable"))
    assert rows == [
        {"CustomerID": 101, "Name": "Alpha Widgets"},
        {"CustomerID": 102, "Name": "Beta Widgets"},
    ]


# ---------------------------------------------------------------- VBA project pieces


def test_project_components():
    kinds = xl.project_components((FIX / "PROJECT.txt").read_text(encoding="utf-8"))
    assert kinds == {
        "ThisWorkbook": "document",
        "shQuote": "document",
        "modQuote": "standard",
        "clsLine": "class",
        "frmQuote": "userform",
    }


def _record(rid: int, data: bytes) -> bytes:
    return struct.pack("<HI", rid, len(data)) + data


def test_reference_names_from_dir_stream():
    stream = (
        _record(0x0001, b"\x01\x00\x00\x00")
        + struct.pack("<HIIH", 0x0009, 4, 1, 2)  # PROJECTVERSION: 6 data bytes
        + _record(0x0016, b"stdole")
        + _record(0x003E, "stdole".encode("utf-16-le"))
        + _record(0x000D, b"\x00" * 10)
        + _record(0x0016, b"Office")
        + _record(0x003E, "Office".encode("utf-16-le"))
        + _record(0x000F, b"\x02\x00")
        + _record(0x0016, b"not-a-reference")
    )
    assert xl.reference_names(stream) == ["stdole", "Office"]


def test_vba_model_warnings_for_missing_components():
    project = (FIX / "PROJECT.txt").read_text(encoding="utf-8")
    _modules, userforms, warnings = xl.vba_model(
        project, {"modQuote": "Sub A()\nEnd Sub\n", "Extra": ""}, []
    )
    assert {w["code"] for w in warnings} == {
        "vba-component-not-in-project",
        "vba-component-without-code",
    }
    assert [u["name"] for u in userforms] == ["frmQuote"]


def test_parse_frm_and_cross_check(tmp_path):
    form = xl.parse_frm((FIX / "frmQuote.frm.txt").read_text(encoding="utf-8"))
    assert form["caption"] == "New quote"
    assert [(c["name"], c["type"]) for c in form["controls"]] == [
        ("cmdOK", "CommandButton"),
        ("txtCustomer", "TextBox"),
    ]
    project = (FIX / "PROJECT.txt").read_text(encoding="utf-8")
    mods = fixture_modules()
    mods["frmQuote"] = frm_code()
    mods["modQuote"] += "Public Sub Extra()\nEnd Sub\n"
    modules, userforms, _w = xl.vba_model(project, mods, [], SITES)
    warnings = []
    xl.cross_check(export_folder(tmp_path / "VBA Code"), modules, userforms, warnings)
    assert sorted(w["code"] for w in warnings) == [
        "vba-export-lines-differ",
        "vba-export-procedures-differ",
    ]
    assert userforms[0]["controls"][1]["left"] == 240.0


def test_vba_cell_literals():
    code = (FIX / "modQuote.bas.txt").read_text(encoding="utf-8")
    assert xl.vba_cell_literals(code) == [(2, 4, 2, 4), (4, 2, 4, 3), (2, 6, 2, 6)]


def test_encrypted_workbook_exits_2(make_engagement, tmp_path, capfd, quiet):
    eng = make_engagement(
        tmp_path, source={"kind": "xlsm", "path": "{{p.private}}/sources/locked.xlsm"}
    )
    private = Path(json.loads((eng.parent / "paths.local.json").read_text())["private"])
    (private / "sources").mkdir(parents=True)
    (private / "sources" / "locked.xlsm").write_bytes(xl._CFB_MAGIC + b"\x00" * 504)
    code, summary = run_parse(
        capfd, eng, private / "out" / "model", "--strict", "--fail-on", "multi-backend"
    )
    assert code == 2 and summary["messages"] == ["encrypted source: custom quote"]
