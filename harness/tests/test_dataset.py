"""harness/dataset.py: value text form, CSV round trip, load order and validation.

Synthetic values only (a made-up widget shop)."""

from __future__ import annotations

import ast
import csv
import datetime as dt
import hashlib
import importlib.util
import json
import math
import shutil
import struct
from decimal import Decimal
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
MODULE = HERE.parent / "dataset.py"
_spec = importlib.util.spec_from_file_location("harness_dataset_under_test", MODULE)
ds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ds)


def col(name, ctype, **extra):
    return {
        "name": name,
        "type": ctype,
        "size": None,
        "precision": None,
        "scale": None,
        "required": False,
        **extra,
    }


def schema_of(tables, relationships=()):
    return {
        "format_version": 1,
        "source_kind": "accdt",
        "tables": tables,
        "relationships": list(relationships),
    }


def table(name, columns, role="data"):
    return {
        "name": name,
        "role": role,
        "columns": columns,
        "primary_key": [],
        "indexes": [],
    }


def rel(child, ccol, parent, pcol, name=None):
    return {
        "name": name or f"{parent}{child}",
        "from_table": child,
        "from_columns": [ccol],
        "to_table": parent,
        "to_columns": [pcol],
        "enforce": True,
        "cascade_update": False,
        "cascade_delete": False,
    }


ALL_TYPES = [
    ("t_text", "text"),
    ("t_memo", "memo"),
    ("t_link", "hyperlink"),
    ("t_byte", "byte"),
    ("t_int", "integer"),
    ("t_long", "longinteger"),
    ("t_auto", "autonumber"),
    ("t_single", "single"),
    ("t_double", "double"),
    ("t_cur", "currency"),
    ("t_dec", "decimal"),
    ("t_dt", "datetime"),
    ("t_yn", "yesno"),
    ("t_guid", "guid"),
]


# ------------------------------------------------------------------ format_value


@pytest.mark.parametrize(
    "ctype,value,text",
    [
        ("text", "plain", "plain"),
        ("text", "", ""),
        ("text", "\\N", "\\\\N"),
        ("text", "\\\\N", "\\\\\\N"),
        ("text", "N", "N"),
        ("memo", 'a,b\r\nc "q"', 'a,b\r\nc "q"'),
        ("hyperlink", "Site#http://example.invalid#", "Site#http://example.invalid#"),
        ("byte", 255, "255"),
        ("integer", -32768, "-32768"),
        ("longinteger", "42", "42"),
        ("autonumber", 7.0, "7"),
        ("currency", Decimal("12.5000"), "12.5"),
        ("currency", "-0.0000", "0"),
        ("currency", "1E+3", "1000"),
        ("decimal", 1e-7, "0.0000001"),
        ("decimal", "1234567.890", "1234567.89"),
        ("single", 0.1, "0.1"),
        ("single", 1 / 3, "0.33333334"),
        ("single", "-0", "-0.0"),
        ("single", 16777217, "16777216.0"),
        ("double", 0.1 + 0.2, "0.30000000000000004"),
        ("double", "1E-07", "1e-07"),
        ("double", 1, "1.0"),
        ("datetime", dt.datetime(2024, 3, 1, 9, 30, 5), "2024-03-01T09:30:05"),
        ("datetime", dt.date(2024, 2, 29), "2024-02-29T00:00:00"),
        ("datetime", "2024-02-29", "2024-02-29T00:00:00"),
        ("datetime", "2024-02-29 23:59:59", "2024-02-29T23:59:59"),
        ("datetime", dt.datetime(2024, 1, 1, 0, 0, 0, 600000), "2024-01-01T00:00:01"),
        ("yesno", True, "1"),
        ("yesno", -1, "1"),
        ("yesno", "false", "0"),
        (
            "guid",
            "{6F9619FF-8B86-D011-B42D-00C04FC964FF}",
            "6f9619ff-8b86-d011-b42d-00c04fc964ff",
        ),
    ],
)
def test_format_value(ctype, value, text):
    assert ds.format_value(ctype, value) == text


@pytest.mark.parametrize("ctype", [t for _n, t in ALL_TYPES])
def test_null_is_backslash_n_for_every_type(ctype):
    assert ds.format_value(ctype, None) == "\\N"
    assert ds.parse_value(ctype, "\\N") is None


@pytest.mark.parametrize(
    "ctype,value",
    [
        ("double", float("nan")),
        ("double", float("inf")),
        ("single", float("-inf")),
        ("single", 1e39),
        ("currency", "NaN"),
        ("decimal", Decimal("Infinity")),
        ("byte", 256),
        ("integer", 40000),
        ("longinteger", 2**31),
        ("longinteger", 1.5),
        ("yesno", "maybe"),
        ("guid", "not-a-guid"),
        ("datetime", "01/02/2024"),
        ("datetime", dt.datetime(2024, 1, 1, tzinfo=dt.UTC)),
        ("complex", "x"),
        ("oleobject", b"x"),
    ],
)
def test_format_value_errors(ctype, value):
    with pytest.raises(ValueError):
        ds.format_value(ctype, value)


def test_error_messages_never_hold_the_value():
    with pytest.raises(ValueError) as exc:
        ds.format_value("integer", "WIDGET-SENTINEL-99")
    assert "WIDGET-SENTINEL" not in str(exc.value)


def test_single_is_shortest_binary32_round_trip():
    for x in (0.1, 0.2, 1 / 3, 3.14159, 1e-6, 123456.789, 0.05, 2.0**-20):
        text = ds.format_value("single", x)
        packed = struct.pack("<f", x)
        assert struct.pack("<f", float(text)) == packed
        digits = len(text.replace("-", "").replace(".", "").split("e")[0].lstrip("0"))
        for shorter in range(1, digits):
            assert (
                struct.pack(
                    "<f", float(format(struct.unpack("<f", packed)[0], f".{shorter}g"))
                )
                != packed
            )


@pytest.mark.parametrize(
    "ctype,text",
    [
        ("integer", "01"),
        ("integer", "+1"),
        ("currency", "1.50"),
        ("currency", "-0"),
        ("currency", "1e3"),
        ("double", "0.1000"),
        ("single", "0.10000000149011612"),
        ("datetime", "2024-02-29"),
        ("datetime", "2024-02-30T00:00:00"),
        ("yesno", "-1"),
        ("guid", "6F9619FF-8B86-D011-B42D-00C04FC964FF"),
        ("text", "\\N\\"),
    ],
)
def test_parse_value_rejects_non_canonical(ctype, text):
    if ctype == "text":
        assert (
            ds.parse_value(ctype, text) == text
        )  # only exactly backslashes+N is special
        return
    with pytest.raises(ValueError):
        ds.parse_value(ctype, text)


def test_parse_value_types():
    assert ds.parse_value("text", "\\\\N") == "\\N"
    assert ds.parse_value("text", "") == ""
    assert ds.parse_value("currency", "12.5") == Decimal("12.5")
    assert ds.parse_value("datetime", "2024-03-01T00:00:00") == dt.datetime(2024, 3, 1)
    assert ds.parse_value("yesno", "1") is True
    assert ds.parse_value("single", "0.1") == 0.1
    assert math.copysign(1, ds.parse_value("double", "-0.0")) == -1
    assert ds.parse_value("memo", "\\\\\\N") == "\\\\N"


# ------------------------------------------------------------------ CSV writer and reader


VALUES = [
    {
        "t_text": 'Widget, "large"',
        "t_memo": "line one\r\nline two\nthree\rfour",
        "t_link": "",
        "t_byte": 0,
        "t_int": -5,
        "t_long": 70000,
        "t_auto": 1,
        "t_single": 0.1,
        "t_double": 0.1 + 0.2,
        "t_cur": Decimal("-4.25"),
        "t_dec": Decimal("0.001"),
        "t_dt": dt.datetime(2024, 3, 1, 23, 59, 59),
        "t_yn": True,
        "t_guid": "6f9619ff-8b86-d011-b42d-00c04fc964ff",
    },
    {name: None for name, _t in ALL_TYPES},
    {
        "t_text": "\\N",
        "t_memo": "",
        "t_link": "\\\\N",
        "t_byte": 255,
        "t_int": 0,
        "t_long": -1,
        "t_auto": 2,
        "t_single": -0.0,
        "t_double": 1e-300,
        "t_cur": Decimal(0),
        "t_dec": Decimal("123456789.123456789"),
        "t_dt": dt.datetime(1899, 12, 30),
        "t_yn": False,
        "t_guid": None,
    },
]


def write_all_types(tmp_path):
    columns = [{"name": n, "type": t} for n, t in ALL_TYPES]
    sch = schema_of(
        [
            table(
                "Every Type",
                [col(n, t) for n, t in ALL_TYPES]
                + [col("Pic", "oleobject"), col("Att", "complex")],
            )
        ]
    )
    meta = {"name": "seed-7", "kind": "synthetic", "seed": 7, "schema_sha256": "0" * 64}
    doc = ds.write_dataset(
        tmp_path / "d",
        meta,
        [
            {
                "name": "Every Type",
                "columns": columns,
                "rows": VALUES,
                "omitted_columns": [
                    {"name": "Pic", "type": "oleobject", "reason": "oleobject"},
                    {"name": "Att", "type": "complex", "reason": "complex"},
                ],
            }
        ],
    )
    return doc, sch


def test_round_trip_every_type(tmp_path):
    doc, sch = write_all_types(tmp_path)
    meta, tables = ds.read_dataset(tmp_path / "d")
    assert meta == doc
    rows = list(tables["Every Type"])
    assert len(rows) == 3
    for got, want in zip(rows, VALUES):
        for name, ctype in ALL_TYPES:
            w = want[name]
            if ctype == "single" and w is not None:
                assert struct.pack("<f", got[name]) == struct.pack("<f", w)
            elif ctype in ("currency", "decimal") and w is not None:
                assert got[name] == w
            else:
                assert got[name] == w, name
    # NULL and the empty string stay apart
    assert rows[1]["t_text"] is None and rows[2]["t_memo"] == ""
    result = ds.validate(tmp_path / "d", sch)
    assert result["problems"] == []


def test_csv_bytes_follow_rfc4180_crlf(tmp_path):
    doc, _sch = write_all_types(tmp_path)
    data = (tmp_path / "d" / doc["tables"][0]["file"]).read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    assert data.endswith(b"\r\n")
    assert hashlib.sha256(data).hexdigest() == doc["tables"][0]["sha256"]
    lines = data.split(b"\r\n")
    assert lines[0] == b",".join(n.encode() for n, _t in ALL_TYPES)
    # NULL unquoted, empty string quoted, quotes doubled
    assert b"\r\n" + b",".join([b"\\N"] * len(ALL_TYPES)) + b"\r\n" in data
    assert b'"Widget, ""large"""' in data
    assert b',"",' in data


def test_format_value_matches_the_csv_writer(tmp_path):
    doc, _sch = write_all_types(tmp_path)
    path = tmp_path / "d" / doc["tables"][0]["file"]
    with open(path, encoding="utf-8", newline="") as fh:
        records = list(csv.reader(fh))
    assert records[0] == [n for n, _t in ALL_TYPES]
    for rec, row in zip(records[1:], VALUES):
        assert rec == [ds.format_value(t, row[n]) for n, t in ALL_TYPES]


def test_rows_as_sequences_and_single_column_empty_string(tmp_path):
    meta = {"name": "seed-2", "kind": "synthetic", "seed": 2, "schema_sha256": "1" * 64}
    doc = ds.write_dataset(
        tmp_path / "d",
        meta,
        [
            {
                "name": "Notes",
                "columns": [{"name": "Note", "type": "text"}],
                "rows": [[""], [None], ["x"]],
            }
        ],
    )
    data = (tmp_path / "d" / doc["tables"][0]["file"]).read_bytes()
    assert data == b'Note\r\n""\r\n\\N\r\nx\r\n'
    _m, tables = ds.read_dataset(tmp_path / "d")
    assert [r["Note"] for r in tables["Notes"]] == ["", None, "x"]


def test_write_refuses_complex_columns(tmp_path):
    meta = {"name": "seed-2", "kind": "synthetic", "seed": 2, "schema_sha256": "1" * 64}
    with pytest.raises(ValueError):
        ds.write_dataset(
            tmp_path / "d",
            meta,
            [{"name": "T", "columns": [{"name": "A", "type": "complex"}], "rows": []}],
        )


def test_file_safe_contract_examples():
    assert ds.file_safe("qryOrderTotals") == "qryOrderTotals"
    assert ds.file_safe("Order Details") == "Order_Details-fe3ac0d4"
    assert (
        ds.file_safe("InvoiceTable[Net Amount]") == "InvoiceTable_Net_Amount_-f34d32b9"
    )


def test_unknown_format_version_exits_2(tmp_path):
    (tmp_path / "dataset.json").write_text(json.dumps({"format_version": 2}))
    with pytest.raises(ds.DatasetError) as exc:
        ds.read_meta(tmp_path)
    assert exc.value.exit_code == 2
    with pytest.raises(ds.DatasetError):
        ds.read_meta(tmp_path / "missing")


# ------------------------------------------------------------------ load order


def test_load_order_parents_first():
    sch = schema_of(
        [table(n, []) for n in ("Lines", "Orders", "Customers", "Regions", "Staff")],
        [
            rel("Lines", "OrderID", "Orders", "OrderID"),
            rel("Orders", "CustomerID", "Customers", "CustomerID"),
            rel("Customers", "RegionID", "Regions", "RegionID"),
            rel("Staff", "BossID", "Staff", "StaffID"),
        ],
    )
    order, broken = ds.load_order(
        sch, ["Lines", "Orders", "Customers", "Regions", "Staff"]
    )
    assert order == ["Regions", "Customers", "Orders", "Lines", "Staff"]
    assert broken == []
    assert ds.self_references(sch, order) == ["Staff"]
    # tables not in the dataset are ignored
    assert ds.load_order(sch, ["Lines", "Customers"])[0] == ["Customers", "Lines"]


def test_load_order_cycle_broken_by_name():
    sch = schema_of(
        [table(n, []) for n in ("B", "A", "C", "D")],
        [
            rel("A", "x", "B", "x"),
            rel("B", "x", "A", "x"),
            rel("C", "x", "A", "x"),
            rel("D", "x", "C", "x"),
        ],
    )
    order, broken = ds.load_order(sch, ["A", "B", "C", "D"])
    assert broken == ["A"]
    assert order == ["A", "B", "C", "D"]


# ------------------------------------------------------------------ validate


def make_valid(tmp_path):
    sch = schema_of(
        [
            table(
                "Customers",
                [
                    col("CustomerID", "autonumber"),
                    col("Name", "text"),
                    col("Photo", "oleobject"),
                ],
            ),
            table(
                "Orders",
                [
                    col("OrderID", "autonumber"),
                    col("CustomerID", "longinteger"),
                    col("Qty", "integer"),
                    col("Total", "decimal", computed=True, formula="=[@Qty]*2"),
                ],
            ),
            table("Glossary", [col("Term", "text")], role="documentation"),
        ],
        [rel("Orders", "CustomerID", "Customers", "CustomerID")],
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    sch_path = tmp_path / "schema.json"
    sch_path.write_text(json.dumps(sch))
    meta = {
        "name": "seed-3",
        "kind": "synthetic",
        "seed": 3,
        "schema_sha256": hashlib.sha256(sch_path.read_bytes()).hexdigest(),
        "load_order": ["Customers", "Orders"],
    }
    ds.write_dataset(
        tmp_path / "d",
        meta,
        [
            {
                "name": "Customers",
                "columns": [
                    {"name": "CustomerID", "type": "autonumber"},
                    {"name": "Name", "type": "text"},
                ],
                "omitted_columns": [
                    {"name": "Photo", "type": "oleobject", "reason": "oleobject"}
                ],
                "rows": [[1, "Acme Widgets"], [2, None]],
            },
            {
                "name": "Orders",
                "columns": [
                    {"name": "OrderID", "type": "autonumber"},
                    {"name": "CustomerID", "type": "longinteger"},
                    {"name": "Qty", "type": "integer"},
                ],
                "omitted_columns": [
                    {"name": "Total", "type": "decimal", "reason": "computed"}
                ],
                "rows": [[1, 1, 3], [2, None, 4], [3, 2, 5]],
            },
        ],
    )
    return tmp_path / "d", sch_path


def edit_meta(directory, fn):
    meta = json.loads((directory / "dataset.json").read_text())
    fn(meta)
    (directory / "dataset.json").write_text(json.dumps(meta))


def test_validate_accepts_computed_omission_and_absent_documentation(tmp_path):
    d, sch = make_valid(tmp_path)
    result = ds.validate(d, sch)
    assert result["problems"] == []
    assert result["schema_sha256_matches"]
    assert result["counts"] == {"tables": 2, "rows": 5, "problems": 0, "fk_orphans": 0}


def test_validate_rejects_omitted_ordinary_column(tmp_path):
    d, sch = make_valid(tmp_path)

    def omit_qty(meta):
        t = meta["tables"][1]
        t["columns"] = t["columns"][:2]
        t["omitted_columns"].append(
            {"name": "Qty", "type": "integer", "reason": "computed"}
        )

    edit_meta(d, omit_qty)
    problems = ds.validate(d, sch)["problems"]
    assert any("Orders.Qty: omitted as computed" in p for p in problems)


def test_validate_rejects_missing_column_and_table(tmp_path):
    d, sch = make_valid(tmp_path)
    edit_meta(d, lambda m: m["tables"][0]["columns"].pop())
    problems = ds.validate(d, sch)["problems"]
    assert any("Customers.Name: column missing" in p for p in problems)
    d2, sch2 = make_valid(tmp_path / "two")

    def drop(meta):
        meta["tables"].pop(0)
        meta["load_order"] = ["Orders"]

    edit_meta(d2, drop)
    problems = ds.validate(d2, sch2)["problems"]
    assert "Customers: table missing" in problems
    assert ds.validate(d2, sch2, allow_absent={"Customers"})["problems"] == []


def test_validate_rejects_wrong_type_rows_and_sha(tmp_path):
    d, sch = make_valid(tmp_path)

    def wrong(meta):
        meta["tables"][1]["columns"][2]["type"] = "byte"
        meta["tables"][0]["rows"] = 9
        meta["tables"][0]["sha256"] = "f" * 64

    edit_meta(d, wrong)
    problems = ds.validate(d, sch)["problems"]
    assert "Orders.Qty: type differs from the schema" in problems
    assert "Customers: row count differs" in problems
    assert "Customers: sha256 differs" in problems


def test_validate_rejects_fk_orphan(tmp_path):
    d, sch = make_valid(tmp_path)
    path = d / "tables" / "Orders.csv"
    path.write_bytes(path.read_bytes().replace(b"3,2,5", b"3,9,5"))
    edit_meta(
        d,
        lambda m: m["tables"][1].update(
            sha256=hashlib.sha256(path.read_bytes()).hexdigest()
        ),
    )
    result = ds.validate(d, sch)
    assert result["counts"]["fk_orphans"] == 1
    assert any("foreign-key orphans" in p for p in result["problems"])


@pytest.mark.parametrize(
    "old,new,what",
    [
        (b"\r\n", b"\n", "quoting or record ends"),
        (b"1,Acme Widgets", b'1,"Acme Widgets"', "quoting or record ends"),
        (b"2,\\N", b"2,", "quoting or record ends"),
        (b"CustomerID,Name", b"\xef\xbb\xbfCustomerID,Name", "BOM"),
    ],
)
def test_validate_rejects_csv_rule_breaks(tmp_path, old, new, what):
    d, sch = make_valid(tmp_path)
    path = d / "tables" / "Customers.csv"
    path.write_bytes(path.read_bytes().replace(old, new))
    edit_meta(
        d,
        lambda m: m["tables"][0].update(
            sha256=hashlib.sha256(path.read_bytes()).hexdigest()
        ),
    )
    problems = ds.validate(d, sch)["problems"]
    assert any(what in p for p in problems), problems


def test_validate_rejects_non_canonical_value_without_printing_it(tmp_path):
    d, sch = make_valid(tmp_path)
    path = d / "tables" / "Orders.csv"
    path.write_bytes(path.read_bytes().replace(b"1,1,3", b"1,1,03"))
    problems = ds.validate(d, sch)["problems"]
    assert any("row 1 column Qty" in p for p in problems)
    assert not any("03" in p for p in problems)


def test_validate_load_order_and_documentation_table(tmp_path):
    d, sch = make_valid(tmp_path)
    edit_meta(d, lambda m: m.update(load_order=["Orders", "Customers"]))
    assert any("load_order" in p for p in ds.validate(d, sch)["problems"])
    d2, sch2 = make_valid(tmp_path / "two")
    shutil.copy(d2 / "tables" / "Orders.csv", d2 / "tables" / "Glossary.csv")
    edit_meta(
        d2,
        lambda m: m["tables"].append(
            {
                "name": "Glossary",
                "file": "tables/Glossary.csv",
                "rows": 0,
                "sha256": "0" * 64,
                "columns": [{"name": "Term", "type": "text"}],
                "omitted_columns": [],
            }
        ),
    )
    problems = ds.validate(d2, sch2)["problems"]
    assert "Glossary: documentation tables are not in datasets" in problems


def test_validate_missing_dataset_json_is_exit_2(tmp_path):
    _d, sch = make_valid(tmp_path)
    with pytest.raises(ds.DatasetError) as exc:
        ds.validate(tmp_path / "nothing", sch)
    assert exc.value.exit_code == 2


def test_same_tree(tmp_path):
    d, _sch = make_valid(tmp_path)
    shutil.copytree(d, tmp_path / "copy")
    assert ds.same_tree(d, tmp_path / "copy") == []
    (tmp_path / "copy" / "tables" / "Orders.csv").write_bytes(b"x")
    (tmp_path / "copy" / "extra.txt").write_bytes(b"x")
    assert ds.same_tree(d, tmp_path / "copy") == ["extra.txt", "tables/Orders.csv"]


def test_stdlib_only_and_no_toolkit_import():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            imported.add(node.module.split(".")[0])
    import sys

    assert "toolkit" not in imported and "harness" not in imported
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}
