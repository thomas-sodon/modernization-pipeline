"""VBA: encodings, line ends, line counts, the procedure scanner and references."""

from __future__ import annotations

from pathlib import Path

from toolkit import vba
from toolkit.sources import decode_text

FIX = Path(__file__).parent / "fixtures" / "parse" / "access"
KNOWN = {
    "tables": {"tblWidgetOrders", "tblCustomers"},
    "queries": {"qryOpenWidgetOrders"},
    "forms": {"frmWidgetOrder"},
    "reports": {"rptWidgetInvoice"},
}


def module_text(name: str, crlf: bool = True) -> str:
    """The fixture as Access stores it (cp1252, CRLF), decoded the way parse decodes it."""
    text = (FIX / name).read_text(encoding="utf-8")
    if crlf:
        text = text.replace("\n", "\r\n")
    decoded, enc, lossy = decode_text(text.encode("cp1252"), "cp1252")
    assert enc == "cp1252" and not lossy
    return decoded


def test_cp1252_decoding_keeps_accents():
    text = module_text("modWidgetMath.bas.txt")
    assert "café" in text and "€" in text


def test_nonblank_lines_rule():
    code = (
        'Attribute VB_Name = "modX"\r\n'
        "Option Explicit\r\n"
        "\r\n"
        "' comments count\r\n"
        "   \t \r\n"
        "Sub A()\r\n"
        "    Attribute = 1\r\n"
        "End Sub\r\n"
    )
    # 'Attribute VB_Name' is excluded; an indented assignment to a variable called Attribute
    # starts with spaces, so after strip it starts with 'Attribute ' and is excluded too
    assert vba.nonblank_lines(code) == 4
    assert vba.nonblank_lines(code.replace("\r\n", "\n")) == 4


def test_crlf_and_lf_give_the_same_scan():
    a = vba.scan(module_text("modWidgetMath.bas.txt", crlf=True), KNOWN)
    b = vba.scan(module_text("modWidgetMath.bas.txt", crlf=False), KNOWN)
    assert a == b
    assert vba.normalize("a\r\nb\rc") == "a\nb\nc\n"


def test_procedure_scanner():
    text = module_text("modWidgetMath.bas.txt")
    procs = {p["name"]: p for p in vba.scan(text, KNOWN)}
    assert list(procs) == ["LineTotal", "NextTicket", "RepriceOpenOrders", "ShowPrices"]
    assert "GetTickCount" not in procs  # Declare statements are not procedures
    lt = procs["LineTotal"]
    assert (lt["kind"], lt["scope"], lt["static"]) == ("Function", "Public", False)
    lines = vba.normalize(text).split("\n")
    assert lines[lt["start"] - 1].startswith("Public Function LineTotal")
    assert lines[lt["start"]].strip().startswith("Optional")  # continued declaration
    assert lines[lt["end"] - 1] == "End Function"
    nt = procs["NextTicket"]
    assert (nt["kind"], nt["scope"], nt["static"]) == ("Function", "Private", True)
    assert procs["RepriceOpenOrders"]["scope"] == "Friend"
    assert procs["ShowPrices"]["scope"] == "Public"
    assert vba.nonblank_lines(text) == 30


def test_references_per_procedure():
    procs = {
        p["name"]: p for p in vba.scan(module_text("modWidgetMath.bas.txt"), KNOWN)
    }
    refs = procs["RepriceOpenOrders"]["refs"]
    assert refs == {
        "tables": ["tblWidgetOrders"],
        "queries": ["qryOpenWidgetOrders"],
        "forms": ["frmWidgetOrder"],
        "reports": [],
        "docmd": ["OpenForm", "OpenQuery"],
        "dao": True,
    }
    assert procs["LineTotal"]["refs"]["dao"] is False
    assert procs["ShowPrices"]["refs"]["tables"] == []


def test_class_module_kind_and_properties():
    text = module_text("clsWidgetCart.cls.txt", crlf=False)
    assert vba.module_kind(text) == "class"
    assert vba.module_kind(module_text("modWidgetMath.bas.txt")) == "standard"
    assert vba.module_name(text, "x") == "clsWidgetCart"
    kinds = [(p["name"], p["kind"]) for p in vba.scan(text)]
    assert kinds == [
        ("Class_Initialize", "Sub"),
        ("Count", "Property Get"),
        ("Label", "Property Let"),
        ("Owner", "Property Set"),
    ]


def test_logical_lines_join_continuations():
    code = "Public Sub A(ByVal x As Long, _\n             ByVal y As Long)\nEnd Sub\n"
    lines = list(vba.logical_lines(code))
    assert lines[0][0:2] == (1, 2)
    assert "ByVal y As Long" in lines[0][2]


def test_comment_mentioning_a_table_is_not_a_reference():
    code = "Sub A()\n    ' tblCustomers is not used here\n    x = 1\nEnd Sub\n"
    (p,) = vba.scan(code, KNOWN)
    assert p["refs"]["tables"] == []


def test_unterminated_procedure_is_flagged():
    (p,) = vba.scan("Sub A()\n    x = 1\n", KNOWN)
    assert p.get("unterminated") is True
