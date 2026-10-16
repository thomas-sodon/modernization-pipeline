"""SaveAsText parser: blocks, strings, hex lines, encodings, layouts, queries and macros.

Fixtures are hand-written (toolkit/tests/fixtures/parse/); nothing comes from a real database."""

from __future__ import annotations

import codecs
from pathlib import Path

import pytest

from toolkit import model as model_mod
from toolkit import saveastext as sat
from toolkit.sources import decode_text
from toolkit.sources.access import AccessDesign, build_model

FIX = Path(__file__).parent / "fixtures" / "parse"


def read(rel: str) -> str:
    return (FIX / rel).read_text(encoding="utf-8")


def layout(rel: str, name: str, owner: str = "form") -> dict:
    return sat.form_layout(sat.parse(read(rel)), name, owner)


def by_name(items):
    return {i["name"]: i for i in items}


# ---------------------------------------------------------------- the reader


def test_nested_blocks_and_code_behind():
    doc = sat.parse(read("access/frmWidgetOrder.txt"))
    form = doc.first("Form")
    assert form is not None
    assert [p.name for p in doc.props] == ["Version", "VersionRequired"]
    container = form.children[0]
    assert container.kind == ""
    kinds = [b.kind for b in container.children]
    assert kinds == ["Label", "TextBox", "FormHeader", "Section", "FormFooter"]
    detail = container.children[3]
    tab = next(b for b in detail.children[0].children if b.kind == "Tab")
    pages = [b for b in tab.children[0].children if b.kind == "Page"]
    assert [p.text("Name") for p in pages] == ["pgLines", "pgNotes"]
    assert doc.code_marker == "CodeBehindForm"
    assert doc.code.startswith("Attribute VB_GlobalNameSpace")
    assert "Private Sub cmdClose_Click()" in doc.code


def test_escaped_quotes_in_both_styles():
    doc = sat.parse(
        "Begin Form\n"
        '    Caption ="say ""hi"" twice"\n'
        '    RecordSource ="a \\"b\\" c \\\\ d"\n'
        '    Tag =""\n'
        "End\n"
    )
    form = doc.first("Form")
    assert form.text("Caption") == 'say "hi" twice'
    assert form.text("RecordSource") == 'a "b" c \\ d'
    assert form.text("Tag") == ""


def test_string_continuation_and_octal_escapes():
    doc = sat.parse(read("access/qryAllContacts.txt"))
    sql = doc.root.typed("SQL")
    assert sql.startswith('SELECT CustomerName AS ContactName, "customer" AS Kind')
    assert "tblCustomers\r\nUNION ALL SELECT" in sql
    assert sql.endswith(";\r\n")
    combo = layout("access/frmWidgetOrder.txt", "frmWidgetOrder")
    rs = by_name(combo["controls"])["cboCustomer"]["row_source"]
    assert (
        rs == "SELECT CustomerID, CustomerName FROM tblCustomers ORDER BY CustomerName;"
    )


def test_hex_continuation_lines():
    doc = sat.parse(read("access/frmWidgetOrder.txt"))
    prt = doc.first("Form").get("PrtMip")
    assert isinstance(prt, sat.Block)
    assert len(prt.hex) == 32 + 24
    assert prt.hex[:4] == bytes.fromhex("68010000")
    q = sat.parse(read("access/qryOpenWidgetOrders.txt"))
    guid = q.root.typed("GUID")
    assert isinstance(guid, sat.Block) and len(guid.hex) == 16


def test_typed_properties():
    q = sat.parse(read("access/qryOpenWidgetOrders.txt"))
    types = {p.name: p.type for p in q.root.props if p.type}
    assert types == {
        "ReturnsRecords": "dbBoolean",
        "ODBCTimeout": "dbInteger",
        "RecordsetType": "dbByte",
        "GUID": "dbBinary",
    }
    col = q.root.children[-1].children[0]
    assert col.typed("Name") == "tblWidgetOrders.OrderNo"
    assert col.typed("AggregateType") == "-1"
    assert next(p for p in col.props if p.name == "AggregateType").type == "dbLong"


def test_utf16le_bom_and_crlf_parse_the_same():
    text = read("access/frmWidgetOrder.txt")
    data = codecs.BOM_UTF16_LE + text.replace("\n", "\r\n").encode("utf-16-le")
    decoded, enc, lossy = decode_text(data)
    assert enc == "utf-16le-bom" and not lossy
    a = sat.form_layout(sat.parse(decoded), "frmWidgetOrder", "form")
    b = sat.form_layout(sat.parse(text), "frmWidgetOrder", "form")
    assert a == b


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Begin Form\nEnd\nEnd\n", "unbalanced-end"),
        ('Begin Form\n    Caption ="open\n', "unterminated-string"),
        ("Begin Form\n    %%% what\nEnd\n", "unrecognized-line"),
        ("Begin Form\n    Begin Section\nEnd\n", "unclosed-block"),
        ('Begin Form\n"orphan"\nEnd\n', "orphan-continuation"),
        ("Begin Form\n    X = Begin\n        0x123\n    End\nEnd\n", "odd-hex"),
    ],
)
def test_errors_have_codes_and_lines(text, code):
    with pytest.raises(sat.SaveAsTextError) as exc:
        sat.parse(text)
    assert exc.value.code == code
    assert exc.value.line >= 1


def test_error_detail_carries_no_source_text():
    with pytest.raises(sat.SaveAsTextError) as exc:
        sat.parse("Begin Form\n    ?? secret-looking value 12345\nEnd\n")
    assert "secret" not in str(exc.value) and "12345" not in str(exc.value)


# ---------------------------------------------------------------- layout facts


def test_form_layout_in_twips():
    f = layout("access/frmWidgetOrder.txt", "frmWidgetOrder")
    assert f["record_source"].endswith('WHERE Status="Open";')
    assert f["caption"] == "Widget order"
    assert f["has_code_behind"] is True
    assert f["width"] == 9360
    assert f["events"] == ["Load", "Current:expression"]
    assert f["sections"] == [
        {"name": "FormHeader", "kind": "form_header", "height": 720},
        {"name": "Detail", "kind": "detail", "height": 5040},
        {"name": "FormFooter", "kind": "form_footer", "height": 600},
    ]
    c = by_name(f["controls"])
    assert c["lblHeading"]["caption"] == 'Order "express" form'
    assert (c["lblHeading"]["left"], c["lblHeading"]["top"]) == (360, 120)
    # width and height come from the form's default TextBox and Label blocks
    assert {k: c["txtOrderNo"][k] for k in ("left", "top", "width", "height")} == {
        "left": 1800,
        "top": 240,
        "width": 2160,
        "height": 300,
    }
    assert c["txtOrderNo"]["events"] == ["AfterUpdate"]
    assert c["lblOrderNo"]["parent"] == "txtOrderNo"
    assert (c["lblOrderNo"]["width"], c["lblOrderNo"]["height"]) == (1440, 300)
    assert c["cboCustomer"]["events"] == ["NotInList"]
    assert c["cmdClose"]["section"] == "FormFooter"
    assert c["cmdClose"]["events"] == ["Click"]
    sub = c["sfrLines"]
    assert sub["type"] == "Subform"
    assert sub["source_object"] == "sfrWidgetOrderLines"
    assert sub["page"] == "pgLines"
    assert (sub["left"], sub["top"], sub["width"], sub["height"]) == (
        360,
        1680,
        8280,
        2880,
    )
    assert c["txtNotes"]["page"] == "pgNotes"
    assert c["tabDetail"]["page"] is None
    assert f["tab_pages"] == [
        {"tab_control": "tabDetail", "name": "pgLines", "caption": "Lines", "index": 0},
        {"tab_control": "tabDetail", "name": "pgNotes", "caption": "Notes", "index": 1},
    ]
    assert "Page" not in {x["type"] for x in f["controls"]}


def test_report_layout_sections_and_groups():
    r = layout("access/rptWidgetInvoice.txt", "rptWidgetInvoice", "report")
    assert [(s["name"], s["kind"]) for s in r["sections"]] == [
        ("PageHeaderSection", "page_header"),
        ("GroupHeader0", "group_header"),
        ("Detail", "detail"),
        ("PageFooterSection", "page_footer"),
    ]
    assert r["groups"] == [
        {
            "control_source": "OrderNo",
            "group_header": True,
            "group_footer": False,
            "descending": False,
        },
        {
            "control_source": "OrderDate",
            "group_header": False,
            "group_footer": False,
            "descending": True,
        },
    ]
    c = by_name(r["controls"])
    assert c["txtOrder"]["control_source"] == '="Order " & [OrderNo]'
    assert c["txtCustomerLabel"]["top"] == 0
    assert r["events"] == ["NoData"]


# ---------------------------------------------------------------- macros


def test_standalone_macro_actions():
    m = sat.macro_model(sat.parse(read("macros/mcrStartup.txt")), "mcrStartup")
    assert m["kind"] == "standalone" and m["owner"] is None
    assert m["actions"] == [
        {
            "action": "If",
            "args": {"Condition": '[TempVars]![StartMode]="admin"'},
            "depth": 0,
        },
        {"action": "OpenForm", "args": {"FormName": "frmWidgetAdmin"}, "depth": 1},
        {"action": "RunCode", "args": {"FunctionName": "InitWidgets()"}, "depth": 0},
    ]


def test_embedded_macro_and_event_mark():
    f = layout("macros/frmWidgetHost.txt", "frmWidgetHost")
    macros = f["_macros"]
    assert macros == [
        {
            "name": "frmWidgetHost.cmdShowReport.Click",
            "kind": "embedded",
            "owner": "form:frmWidgetHost.cmdShowReport.Click",
            "actions": [
                {
                    "action": "OpenReport",
                    "args": {"ReportName": "rptWidgetInvoice", "View": "Print Preview"},
                    "depth": 0,
                }
            ],
        }
    ]
    assert by_name(f["controls"])["cmdShowReport"]["events"] == ["Click:macro"]
    assert f["_warnings"] == []


def test_macros_go_to_objects_macros():
    design = AccessDesign(source_kind="accdt")
    design.forms.append(("frmWidgetHost", read("macros/frmWidgetHost.txt")))
    design.macros.append(("mcrStartup", read("macros/mcrStartup.txt")))
    model = build_model(design)
    assert design.errors == []
    macros = model["objects"]["macros"]
    assert [(m["name"], m["kind"], m["owner"]) for m in macros] == [
        ("mcrStartup", "standalone", None),
        (
            "frmWidgetHost.cmdShowReport.Click",
            "embedded",
            "form:frmWidgetHost.cmdShowReport.Click",
        ),
    ]
    assert model["counts"]["macros"] == 1
    assert model["counts"]["embedded_macros"] == 1
    model["objects"]["diagnostics"] = {
        "errors": [],
        "warnings": [],
        "unknown_parts": {},
        "package_parts": {},
    }
    docs, _code = model_mod.finalize(model)
    assert model_mod.validate(docs) == []


def test_form_event_macro_without_axl_uses_positions():
    text = (
        "Begin Form\n"
        '    OnOpen ="[Embedded Macro]"\n'
        "    OnOpenEmMacro = Begin\n"
        "        Version =196611\n"
        "        Begin\n"
        '            Action ="Beep"\n'
        "        End\n"
        "        Begin\n"
        '            Action ="OpenForm"\n'
        '            Argument ="frmOther"\n'
        "        End\n"
        "    End\n"
        "End\n"
    )
    f = sat.form_layout(sat.parse(text), "frmPlain", "form")
    assert f["events"] == ["Open:macro"]
    (m,) = f["_macros"]
    assert m["name"] == "frmPlain.Open" and m["owner"] == "form:frmPlain.Open"
    assert m["actions"] == [
        {"action": "Beep", "args": {}, "depth": 0},
        {"action": "OpenForm", "args": {"1": "frmOther"}, "depth": 0},
    ]
    assert {w["code"] for w in f["_warnings"]} == {"embedded-macro-positional-args"}


# ---------------------------------------------------------------- queries


def test_design_grid_query():
    q = sat.query_model(
        sat.parse(read("access/qryOpenWidgetOrders.txt")),
        "qryOpenWidgetOrders",
        {"tblWidgetOrders", "tblCustomers"},
    )
    assert q["storage"] == "design" and q["query_type"] == "select" and q["sql"] is None
    d = q["design"]
    assert [t["name"] for t in d["input_tables"]] == ["tblWidgetOrders", "tblCustomers"]
    assert d["output_columns"][0] == {
        "expression": "tblWidgetOrders.OrderNo",
        "alias": None,
    }
    assert d["output_columns"][1]["alias"] == "CustomerLabel"
    assert d["output_columns"][1]["expression"].startswith("UCase([CustomerName])")
    assert d["joins"] == [
        {
            "left": "tblCustomers",
            "right": "tblWidgetOrders",
            "expression": "tblCustomers.CustomerID = tblWidgetOrders.CustomerID",
            "flag": 1,
        }
    ]
    assert d["order_by"] == [{"expression": "tblWidgetOrders.OrderDate", "flag": 1}]
    assert q["params"] == ["[Forms]![frmWidgetOrder]![txtFrom]"]
    assert q["functions"] == ["Nz", "UCase"]
    assert q["form_refs"] == ["frmWidgetOrder!txtFrom"]


def test_sql_text_query():
    q = sat.query_model(sat.parse(read("access/qryAllContacts.txt")), "qryAllContacts")
    assert q["storage"] == "sql"
    assert q["query_type"] == "union"
    assert q["design"] == {}
    assert "UNION ALL" in q["sql"]


@pytest.mark.parametrize(
    ("sql", "kind"),
    [
        ("TRANSFORM Sum(x) SELECT a FROM t GROUP BY a PIVOT b;", "crosstab"),
        ("PARAMETERS [p] Long; UPDATE t SET a = [p];", "action"),
        ("SELECT a INTO t2 FROM t;", "action"),
        ("SELECT a FROM t UNION SELECT a FROM u;", "union"),
        ("SELECT a FROM t WHERE b = 'UNION';", "select"),
    ],
)
def test_classify_sql(sql, kind):
    assert sat.classify_sql(sql) == kind
