"""Data macros (dataMacros/*.axl) and object properties (properties/*.axl)."""

from __future__ import annotations

import json
from pathlib import Path

from toolkit import datamacros as dm
from toolkit.sources.access import AccessDesign, build_model

FIX = Path(__file__).parent / "fixtures" / "parse" / "access"


def axl(name: str) -> bytes:
    """AXL parts are UTF-16 with a BOM in an accdt."""
    return (FIX / name).read_text(encoding="utf-8").encode("utf-16")


def test_data_macros_flattened_in_order():
    macros = dm.parse_data_macros(axl("tblWidgetOrders.axl"), "tblWidgetOrders")
    assert [(m["table"], m["event"], m["name"]) for m in macros] == [
        ("tblWidgetOrders", "BeforeChange", None),
        ("tblWidgetOrders", "AfterInsert", None),
        ("tblWidgetOrders", "Named", "dmArchiveOrder"),
    ]
    before, after, named = macros
    assert before["actions"] == [
        {"action": "If", "args": {"Condition": "[Quantity]<=0"}, "depth": 0},
        {
            "action": "RaiseError",
            "args": {"Number": "101", "Description": "Quantity must be positive"},
            "depth": 1,
        },
        {"action": "Else", "args": {}, "depth": 0},
        {"action": "SetLocalVar", "args": {"Name": "ok", "Value": "True"}, "depth": 1},
    ]
    assert after["actions"] == [
        {
            "action": "LookUpRecord",
            "args": {
                "Alias": "C",
                "Reference": "tblCustomers",
                "WhereCondition": "[C].[CustomerID]=[tblWidgetOrders].[CustomerID]",
            },
            "depth": 0,
        },
        {"action": "EditRecord", "args": {"Alias": "C"}, "depth": 1},
        {
            "action": "SetField",
            "args": {"Field": "C.LastOrder", "Value": "[tblWidgetOrders].[OrderDate]"},
            "depth": 2,
        },
    ]
    assert named["params"] == ["prmOrderNo"]
    assert named["actions"] == [{"action": "StopMacro", "args": {}, "depth": 0}]


def test_comments_are_counted_not_copied():
    macros = dm.parse_data_macros(axl("tblWidgetOrders.axl"), "tblWidgetOrders")
    dumped = json.dumps(macros)
    assert "reject empty orders" not in dumped
    assert "Comment" not in {a["action"] for m in macros for a in m["actions"]}


def test_properties_axl_is_metadata_only():
    props = dm.parse_properties(
        axl("tblWidgetOrders.properties.axl"), "table:tblWidgetOrders"
    )
    assert props == {
        "name": "table:tblWidgetOrders",
        "root": "ObjectMetadata",
        "properties": ["Description", "DisplayViewsOnSharePointSite"],
        "tags": ["ObjectMetadata", "Properties", "Property"],
    }
    assert "Orders for widgets" not in json.dumps(props)


def test_properties_axl_never_counts_as_data_macros():
    design = AccessDesign(source_kind="accdt")
    design.tables.append(
        {
            "name": "tblWidgetOrders",
            "role": "data",
            "columns": [],
            "primary_key": [],
            "indexes": [],
        }
    )
    design.data_macros.append(("tblWidgetOrders", axl("tblWidgetOrders.axl")))
    design.properties.append(
        ("table:tblWidgetOrders", axl("tblWidgetOrders.properties.axl"))
    )
    model = build_model(design)
    assert model["counts"]["data_macro_files"] == 1
    assert model["counts"]["data_macros"] == 3
    assert model["datamacros"]["files"] == [
        {"name": "tblWidgetOrders", "table": "tblWidgetOrders", "macros": 3}
    ]
    assert [p["name"] for p in model["objects"]["object_properties"]] == [
        "table:tblWidgetOrders"
    ]
    assert design.errors == [] and design.warnings == []


def test_unreadable_data_macro_is_an_error():
    design = AccessDesign(source_kind="accdt")
    design.data_macros.append(("tblBroken", b"<DataMacros><DataMacro"))
    build_model(design)
    assert [e["code"] for e in design.errors] == ["datamacro-xml"]
