"""harness/canonical.py: canonical values per type, CSV, row order, documents and digests
(docs/contracts.md#canonical-output). Synthetic values only (a made-up widget shop)."""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import hmac
import importlib.util
import struct
import unicodedata
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
MODULE = HERE.parent / "canonical.py"
_spec = importlib.util.spec_from_file_location("harness_canonical_under_test", MODULE)
cn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cn)

f = cn.format_value


def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def test_stdlib_only():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0 or node.module is None
            if node.module:
                names.add(node.module.split(".")[0])
    import sys

    assert names <= set(sys.stdlib_module_names) | {"__future__"}, names
    assert cn.CANONICAL_VERSION == 1


def test_null_and_unknown_type():
    for t in cn.TYPES:
        assert f(t, None) == "\\N"
    with pytest.raises(cn.CanonicalError):
        f("oleobject", b"\x00")
    with pytest.raises(cn.CanonicalError):
        f("complex", "x")


def test_integers():
    for t in cn.INT_TYPES:
        assert f(t, 42) == "42"
    assert f("longinteger", -7) == "-7"
    assert f("longinteger", 3.0) == "3"
    assert f("longinteger", Decimal("5.000")) == "5"
    assert f("longinteger", "007") == "7"
    assert f("longinteger", "-0") == "0"
    assert f("integer", -0.0) == "0"
    with pytest.raises(cn.CanonicalError):
        f("longinteger", 2.5)
    with pytest.raises(cn.CanonicalError):
        f("longinteger", True)
    with pytest.raises(cn.CanonicalError):
        f("longinteger", "twelve")


def test_currency_and_decimal_four_places_half_even():
    assert f("currency", 12) == "12.0000"
    assert f("currency", Decimal("12.5")) == "12.5000"
    assert f("decimal", Decimal("1.00005")) == "1.0000"
    assert f("decimal", Decimal("1.00015")) == "1.0002"
    assert f("decimal", Decimal("-1.00025")) == "-1.0002"
    assert f("currency", 0.1) == "0.1000"
    assert f("currency", Decimal("-0.00001")) == "0.0000"
    assert f("currency", "118") == "118.0000"
    assert f("decimal", Decimal("1E+3")) == "1000.0000"
    with pytest.raises(cn.CanonicalError):
        f("currency", float("nan"))
    with pytest.raises(cn.CanonicalError):
        f("currency", Decimal("Infinity"))
    with pytest.raises(cn.CanonicalError):
        f("currency", False)


def test_single_six_and_double_twelve_significant_digits():
    assert f("single", f32(0.1)) == "0.1"
    assert f("single", f32(1234567.0)) == "1234570"
    assert f("single", f32(0.000123456789)) == "0.000123457"
    assert f("single", 2.5) == "2.5"
    assert f("single", 0.0) == "0"
    assert f("single", -0.0) == "0"
    assert f("single", 1e20) == "100000000000000000000"
    assert f("double", 0.1 + 0.2) == "0.3"
    assert f("double", 1 / 3) == "0.333333333333"
    assert f("double", 123456789012345.0) == "123456789012000"
    assert f("double", 2.0) == "2"
    assert f("double", -1.5e-7) == "-0.00000015"
    assert f("double", Decimal("0.1250")) == "0.125"
    # half-even at the last kept digit
    assert f("single", Decimal("1.234565")) == "1.23456"
    assert f("single", Decimal("1.234575")) == "1.23458"
    with pytest.raises(cn.CanonicalError):
        f("double", float("inf"))


def test_datetime():
    assert f("datetime", dt.datetime(2024, 3, 1)) == "2024-03-01T00:00:00"
    assert f("datetime", dt.date(2024, 3, 2)) == "2024-03-02T00:00:00"
    assert (
        f("datetime", dt.datetime(2024, 3, 1, 9, 30, 5, 600000))
        == "2024-03-01T09:30:06"
    )
    assert (
        f("datetime", dt.time(14, 5)) == "1899-12-30T14:05:00"
    )  # Access time-only value
    assert f("datetime", "2024-03-01 09:30") == "2024-03-01T09:30:00"
    with pytest.raises(cn.CanonicalError):
        f("datetime", dt.datetime(2024, 3, 1, tzinfo=dt.UTC))
    with pytest.raises(cn.CanonicalError):
        f("datetime", "March first")


def test_yesno():
    assert f("yesno", True) == "1"
    assert f("yesno", False) == "0"
    assert f("yesno", -1) == "1"  # Access True
    assert f("yesno", 1) == "1"
    assert f("yesno", 0) == "0"
    assert f("yesno", "true") == "1"
    with pytest.raises(cn.CanonicalError):
        f("yesno", 2)


def test_text_nfc_and_backslash_rule():
    decomposed = "Café"
    assert f("text", decomposed) == "Café"
    assert unicodedata.is_normalized("NFC", f("memo", decomposed))
    assert f("text", "") == ""
    assert f("text", " widget, large ") == " widget, large "
    assert f("text", "\\N") == "\\\\N"
    assert f("hyperlink", "\\\\N") == "\\\\\\N"
    assert cn.unescape("\\\\N") == "\\N" and cn.unescape("\\N") is None
    with pytest.raises(cn.CanonicalError):
        f("text", 12)


def test_guid():
    g = uuid.UUID("12345678-9abc-def0-1234-56789abcdef0")
    assert f("guid", g) == "12345678-9abc-def0-1234-56789abcdef0"
    assert f("guid", "{12345678-9ABC-DEF0-1234-56789ABCDEF0}") == str(g)
    with pytest.raises(cn.CanonicalError):
        f("guid", "not-a-guid")


COLS = [
    {"name": "ItemID", "type": "longinteger"},
    {"name": "Label", "type": "text"},
    {"name": "Price", "type": "currency"},
]


def test_csv_quoting_crlf_and_header():
    data = cn.canonical_csv(
        COLS, [(1, 'Bolt, "hex"', Decimal("0.5")), (2, "", None), (3, "two\nlines", 1)]
    )
    assert data == (
        b"ItemID,Label,Price\r\n"
        b'1,"Bolt, ""hex""",0.5000\r\n'
        b'2,"",\\N\r\n'
        b'3,"two\nlines",1.0000\r\n'
    )
    assert cn.canonical_csv(COLS, []) == b"ItemID,Label,Price\r\n"
    with pytest.raises(cn.CanonicalError):
        cn.canonical_csv(COLS, [(1, "x")])


def test_order_without_order_by_is_the_row_tuple_in_byte_order():
    rows = [(9, "b", 1), (10, "a", 1), (2, "é", None), (2, "z", None)]
    got = cn.canonical_rows(COLS, rows)
    assert [r[0] for r in got] == ["10", "2", "2", "9"]  # field strings, not numbers
    assert got[1][1] == "z" and got[2][1] == "é"  # 'z' (0x7a) before U+00E9
    assert cn.canonical_rows(COLS, list(reversed(rows))) == got


def test_order_by_typed_compare_desc_nulls_and_ties():
    rows = [(9, "b", 1), (10, "a", 1), (2, "c", None), (3, "d", Decimal("0.5"))]
    asc = cn.canonical_rows(COLS, rows, [{"column": "Price", "desc": False}])
    assert [r[0] for r in asc] == [
        "2",
        "3",
        "10",
        "9",
    ]  # NULL first; tie 1.0000 broken by row tuple
    desc = cn.canonical_rows(COLS, rows, [{"column": "Price", "desc": True}])
    assert [r[0] for r in desc] == ["10", "9", "3", "2"]  # NULL last when descending
    num = cn.canonical_rows(COLS, rows, [{"column": "ItemID", "desc": False}])
    assert [r[0] for r in num] == ["2", "3", "9", "10"]  # numerically
    two = cn.canonical_rows(
        COLS,
        rows,
        [{"column": "Price", "desc": True}, {"column": "Label", "desc": True}],
    )
    assert [r[1] for r in two] == ["b", "a", "d", "c"]
    with pytest.raises(cn.CanonicalError):
        cn.canonical_rows(COLS, rows, [{"column": "Missing", "desc": False}])


def test_order_by_datetime_and_nfc_text():
    cols = [{"name": "At", "type": "datetime"}, {"name": "Who", "type": "text"}]
    rows = [
        (dt.datetime(2024, 1, 2), "Café"),
        (dt.datetime(2023, 12, 31, 23), "Cafa"),
        (None, "Cafz"),
    ]
    got = cn.canonical_rows(cols, rows, [{"column": "At", "desc": False}])
    assert [r[0] for r in got] == ["\\N", "2023-12-31T23:00:00", "2024-01-02T00:00:00"]
    by_text = cn.canonical_rows(cols, rows, [{"column": "Who", "desc": False}])
    assert [r[1] for r in by_text] == [
        "Cafa",
        "Cafz",
        "Café",
    ]  # code point of the NFC form


def test_document_canonical_json():
    doc = {
        "15002": {"total": "1.0000", "customer": "Café"},
        "15001": {"lines": [{"amount": "2.0000"}], "net": "2.0000"},
    }
    data = cn.canonical_document(doc)
    assert (
        data
        == (
            '{"15001":{"lines":[{"amount":"2.0000"}],"net":"2.0000"},'
            '"15002":{"customer":"Café","total":"1.0000"}}\n'
        ).encode()
    )
    with pytest.raises(cn.CanonicalError):
        cn.canonical_document({"1": {"total": 1.0}})
    with pytest.raises(cn.CanonicalError):
        cn.canonical_document({"1": "flat"})


def test_digest_sha256_and_hmac(tmp_path):
    data = b"ItemID\r\n1\r\n"
    assert cn.digest(data) == hashlib.sha256(data).hexdigest()
    assert cn.digest(data.decode()) == hashlib.sha256(data).hexdigest()
    src = tmp_path / "engagement.bin"
    src.write_bytes(b"synthetic data file" * 1000)
    key = cn.key_from_file(src)
    assert (
        key == hmac.new(b"modpipe-golden-v1", src.read_bytes(), hashlib.sha256).digest()
    )
    keyed = cn.digest(data, key)
    assert keyed == hmac.new(key, data, hashlib.sha256).hexdigest()
    assert keyed != cn.digest(data)
    assert len(keyed) == 64


def test_file_safe():
    assert cn.file_safe("qryOrderTotals") == "qryOrderTotals"
    assert cn.file_safe("Order Details") == "Order_Details-fe3ac0d4"
    assert (
        cn.file_safe("InvoiceTable[Net Amount]") == "InvoiceTable_Net_Amount_-f34d32b9"
    )
