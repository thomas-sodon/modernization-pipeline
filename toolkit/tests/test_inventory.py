"""inventory: counts, objects, edges and facts from synthetic model fixtures, and the three
--expect branches (docs/contracts.md#inventory)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from toolkit import cli
from toolkit import inventory as inv_mod
from toolkit import model as model_mod
from toolkit.engagement import load

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "inventory"
SHA = "a" * 64

ACCESS_COUNTS = {
    "tables": 3,
    "queries": 3,
    "queries_design_grid": 2,
    "queries_sql_text": 1,
    "forms": 1,
    "reports": 1,
    "modules": 2,
    "class_modules": 1,
    "macros": 1,
    "embedded_macros": 1,
    "data_macro_files": 1,
    "data_macros": 2,
    "objects_with_code_behind": 1,
    "vba_nonblank_lines": 30,
}

XLSM_COUNTS = {
    "worksheets": 3,
    "hidden_worksheets": 1,
    "chartsheets": 1,
    "tables": 3,
    "table_columns": {"Glossary": 2, "LineTable": 3, "QuoteTable": 4},
    "calculated_columns": 1,
    "defined_names": 3,
    "defined_names_broken": 1,
    "data_validations": 1,
    "formula_cells": 5,
    "formula_cells_by_sheet": {"Calc": 2, "Quote": 3},
    "formula_cells_without_cached_value": 1,
    "array_formula_cells": 1,
    "charts": 2,
    "form_buttons": 1,
    "macro_assignments": 2,
    "activex_controls": 1,
    "cell_comments": 2,
    "vba_components": 5,
    "vba_standard_modules": 1,
    "vba_class_modules": 1,
    "vba_document_modules": 2,
    "vba_userforms": 1,
    "vba_nonblank_lines": 38,
    "vba_procedures": 5,
    "userform_controls": 3,
    "vba_references": 3,
    "sample_outputs": {"msg": 1, "pdf": 2, "xlsx": 0},
}


def model_copy(tmp_path: Path, name: str, edit=None) -> Path:
    dest = tmp_path / f"model-{name}"
    shutil.copytree(FIXTURES / name, dest)
    if edit:
        objects = json.loads((dest / "objects.json").read_text())
        schema = json.loads((dest / "schema.json").read_text())
        edit(objects, schema)
        (dest / "objects.json").write_text(json.dumps(objects))
        (dest / "schema.json").write_text(json.dumps(schema))
    return dest


def engagement(make_engagement, tmp_path, kind="accdt", expected=None, **source):
    src = {
        "kind": kind,
        "path": "{{p.private}}/sources/app." + ("xlsm" if kind == "xlsm" else kind),
        "bytes": 4096,
        "sha256": SHA,
        **source,
    }
    return make_engagement(
        tmp_path,
        source=src,
        expected_inventory=expected or {},
        app={
            "slug": "widgets",
            "project_name": "Widget Shop",
            "timezone": "UTC",
            "use_tz": False,
        },
    )


def run_cli(args, capfd) -> tuple[int, dict]:
    code = cli.main([str(a) for a in args])
    out, _err = capfd.readouterr()
    lines = out.splitlines()
    assert len(lines) == 1, out
    return code, json.loads(lines[0])


# ------------------------------------------------------------------ fixtures are valid models


def test_access_fixture_is_a_valid_model():
    docs = {
        stem: json.loads((FIXTURES / "access" / f"{stem}.json").read_text())
        for stem in model_mod.FILES
    }
    assert model_mod.validate(docs) == []


# ------------------------------------------------------------------ counts


def test_access_counts(make_engagement, tmp_path):
    eng = load(engagement(make_engagement, tmp_path))
    inv, missing = inv_mod.build(eng, FIXTURES / "access")
    assert missing == []
    assert inv["counts"] == ACCESS_COUNTS


def test_properties_axl_are_not_data_macros(make_engagement, tmp_path):
    eng = load(engagement(make_engagement, tmp_path))
    objects = json.loads((FIXTURES / "access" / "objects.json").read_text())
    assert objects["facts"]["parts"]["properties/*.axl"] == 3
    inv, _ = inv_mod.build(eng, FIXTURES / "access")
    assert inv["counts"]["data_macro_files"] == 1
    assert inv["counts"]["data_macros"] == 2


def test_vcs_export_uses_access_keys(make_engagement, tmp_path):
    eng = load(engagement(make_engagement, tmp_path, kind="vcs-export"))
    inv, _ = inv_mod.build(eng, FIXTURES / "access")
    assert set(inv["counts"]) == set(ACCESS_COUNTS)


@pytest.mark.parametrize("kind", ["accdb", "mdb"])
def test_accdb_and_mdb_count_tables_and_queries_only(make_engagement, tmp_path, kind):
    eng = load(engagement(make_engagement, tmp_path, kind=kind))
    inv, _ = inv_mod.build(eng, FIXTURES / "access")
    assert inv["counts"] == {"tables": 3, "queries": 3}
    assert {o["type"] for o in inv["objects"]} == {"table", "query"}


def test_xlsm_counts_every_key(make_engagement, tmp_path):
    eng = load(engagement(make_engagement, tmp_path, kind="xlsm"))
    inv, missing = inv_mod.build(eng, FIXTURES / "xlsm")
    assert missing == []
    assert inv["counts"] == XLSM_COUNTS
    assert list(XLSM_COUNTS) == list(inv_mod.XLSM_KEYS)


def test_xlsm_key_the_model_lacks_is_left_out(make_engagement, tmp_path):
    def drop(objects, schema):
        del objects["facts"]["array_formula_cells"]

    eng = load(engagement(make_engagement, tmp_path, kind="xlsm"))
    inv, missing = inv_mod.build(eng, model_copy(tmp_path, "xlsm", drop))
    assert "array_formula_cells" not in inv["counts"]
    assert missing == ["array_formula_cells"]


# ------------------------------------------------------------------ objects and edges


def _objects(inv):
    return {(o["type"], o["name"]): o["metrics"] for o in inv["objects"]}


def _edges(inv):
    return {(e["from"], e["to"]) for e in inv["edges"]}


def test_access_objects_and_edges(make_engagement, tmp_path):
    eng = load(engagement(make_engagement, tmp_path))
    inv, _ = inv_mod.build(eng, FIXTURES / "access")
    objs = _objects(inv)
    for name in ("tblCustomers", "tblWidgetOrders", "tblWidgets"):
        assert ("table", name) in objs
    assert objs[("table", "tblWidgetOrders")] == {"columns": 4}
    assert objs[("query", "qryOpenWidgetOrders")] == {
        "joins": 1,
        "outer_joins": 1,
        "groups": 0,
        "parameters": 1,
    }
    assert objs[("form", "frmWidgetOrder")] == {
        "controls": 2,
        "events": 3,
        "lines": 10,
        "procedures": 2,
    }
    assert ("macro", "AutoExec") in objs
    assert ("macro", "frmWidgetOrder.cmdShip.Click") in objs
    assert ("module", "modWidgetMath") in objs and ("module", "clsWidgetCart") in objs
    assert ("module", "Form_frmWidgetOrder") not in objs
    assert objs[("procedure", "modWidgetMath.AddTax")] == {"lines": 6}
    assert ("data_macro", "tblWidgetOrders.BeforeChange") in objs
    assert all(isinstance(v, int) for m in objs.values() for v in m.values())
    edges = _edges(inv)
    assert ("query:qryOpenWidgetOrders", "table:tblCustomers") in edges
    assert ("query:qryOrderCounts", "query:qryOpenWidgetOrders") in edges
    assert ("query:qryOpenWidgetOrders", "function:Nz") in edges
    assert ("query:qryAllContacts", "table:tblWidgets") in edges
    assert ("form:frmWidgetOrder", "query:qryOpenWidgetOrders") in edges
    assert ("report:rptWidgetInvoice", "table:tblWidgetOrders") in edges
    assert ("form:frmWidgetOrder", "procedure:Form_frmWidgetOrder.Form_Load") in edges
    assert ("procedure:modWidgetMath.AddTax", "form:frmWidgetOrder") in edges
    assert ("procedure:modWidgetMath.ShipOrder", "table:tblWidgetOrders") in edges
    assert inv["rule_bearing_kinds"] == [
        "procedure",
        "data_macro",
        "macro",
        "query",
        "form",
        "report",
    ]


def test_linked_tables_are_table_objects(make_engagement, tmp_path):
    def link(objects, schema):
        objects["linked_tables"] = [
            {
                "name": "tblRemote",
                "backend_kind": "access",
                "backend": 1,
                "backend_file": "be.accdb",
                "source_table": "tblRemote",
            },
            {
                "name": "tblSql",
                "backend_kind": "odbc",
                "backend": 2,
                "backend_file": None,
                "source_table": "dbo.x",
            },
        ]
        objects["facts"]["data_backends"] = 2

    eng = load(engagement(make_engagement, tmp_path, layout="split"))
    inv, _ = inv_mod.build(eng, model_copy(tmp_path, "access", link))
    objs = _objects(inv)
    assert ("table", "tblRemote") in objs and ("table", "tblSql") in objs
    assert inv["counts"]["tables"] == 3  # local tables only
    facts = inv["facts"]
    assert facts["linked_tables"] == {"access": 1, "odbc": 1}
    assert facts["external_links"] == {"odbc": 1}
    assert facts["data_backends"] == 2
    assert facts["data_copy_bytes"] is None


def test_xlsm_objects_and_name_forms(make_engagement, tmp_path):
    eng = load(engagement(make_engagement, tmp_path, kind="xlsm"))
    inv, _ = inv_mod.build(eng, FIXTURES / "xlsm")
    objs = _objects(inv)
    for name in ("QuoteTable", "LineTable", "Glossary"):
        assert ("table", name) in objs
    assert ("calculated_column", "QuoteTable[Total]") in objs
    assert ("formula_block", "Calc!B2:B3") in objs
    assert ("data_validation", "Quote!B2:B9") in objs
    assert ("chart", "Dash!Chart 1") in objs
    assert ("userform", "frmQuote") in objs
    assert ("worksheet", "Calc") in objs
    assert ("defined_name", "_xlnm.Print_Area") in objs
    assert ("procedure", "modQuote.MakeQuote") in objs
    edges = _edges(inv)
    assert ("formula_block:Calc!B2:B3", "table:QuoteTable") in edges
    assert ("formula_block:Calc!B2:B3", "defined_name:TaxRate") in edges
    assert ("button:Quote!Button 1", "procedure:modQuote.MakeQuote") in edges
    assert ("button:Quote!Shape 2", "procedure:modQuote.ShowForm") in edges
    assert inv["rule_bearing_kinds"][0] == "procedure"
    assert "calculated_column" in inv["rule_bearing_kinds"]
    assert inv["facts"]["vba_office_references"] == ["Outlook"]
    assert inv["facts"]["defined_names_broken"] == 1


def test_inventory_header_and_rate(make_engagement, tmp_path):
    path = engagement(make_engagement, tmp_path)
    data = json.loads(path.read_text())
    data["commercial"]["rate_usd_per_hour"] = 75
    path.write_text(json.dumps(data))
    eng = load(path)
    inv, _ = inv_mod.build(eng, FIXTURES / "access")
    assert inv["engagement"] == "demo-eng"
    assert inv["project_name"] == "Widget Shop"
    assert inv["source"] == {"kind": "accdt", "bytes": 4096, "sha256": SHA}
    assert inv["rate_usd_per_hour"] == 75
    assert inv["facts"]["relationships"] == 1
    assert inv["facts"]["data_backends"] == 1
    assert inv["facts"]["source_bytes"] == 4096


# ------------------------------------------------------------------ CLI and --expect


def test_expect_matches_expected_inventory(make_engagement, tmp_path, capfd, quiet):
    expected = {k: v for k, v in ACCESS_COUNTS.items() if k != "embedded_macros"}
    expected["_note"] = "ignored"
    eng = engagement(make_engagement, tmp_path, expected=expected)
    out = tmp_path / "out" / "deep" / "inventory.json"
    code, summary = run_cli(
        ["inventory", eng, "--model", FIXTURES / "access", "--out", out, "--expect"],
        capfd,
    )
    assert code == 0 and summary["status"] == "ok"
    assert any("embedded_macros" in m for m in summary["messages"])
    inv = json.loads(out.read_text())
    assert inv["expectation"] == "compared"
    assert not (eng.parent / "inventory.lock.json").exists()


def test_expect_difference_fails(make_engagement, tmp_path, capfd, quiet):
    expected = dict(ACCESS_COUNTS, forms=2, no_such_key=4)
    eng = engagement(make_engagement, tmp_path, expected=expected)
    code, summary = run_cli(
        [
            "inventory",
            eng,
            "--model",
            FIXTURES / "access",
            "--out",
            tmp_path / "i.json",
            "--expect",
        ],
        capfd,
    )
    assert code == 1 and summary["status"] == "failed"
    assert "forms: expected 2, counted 1" in summary["messages"]
    assert "no_such_key: expected 4, counted not counted" in summary["messages"]


def test_expect_nested_compared_key_by_key(make_engagement, tmp_path, capfd, quiet):
    expected = {"tables": 3, "table_columns": {"QuoteTable": 5, "LineTable": 3}}
    eng = engagement(make_engagement, tmp_path, kind="xlsm", expected=expected)
    code, summary = run_cli(
        [
            "inventory",
            eng,
            "--model",
            FIXTURES / "xlsm",
            "--out",
            tmp_path / "i.json",
            "--expect",
        ],
        capfd,
    )
    assert code == 1
    assert "table_columns.QuoteTable: expected 5, counted 4" in summary["messages"]
    assert not any(m.startswith("table_columns.LineTable") for m in summary["messages"])


def test_expect_records_then_compares_lock(make_engagement, tmp_path, capfd, quiet):
    eng = engagement(make_engagement, tmp_path)
    out = tmp_path / "inventory.json"
    args = ["inventory", eng, "--model", FIXTURES / "access", "--out", out, "--expect"]
    code, summary = run_cli(args, capfd)
    assert code == 0 and summary["status"] == "recorded"
    assert "recorded, not compared; approve at the gate" in summary["messages"]
    lock = json.loads((eng.parent / "inventory.lock.json").read_text())
    assert lock["format_version"] == 1
    assert lock["source_sha256"] == SHA
    assert lock["counts"] == ACCESS_COUNTS
    assert len(lock["recorded"]) == 10
    assert json.loads(out.read_text())["expectation"] == "recorded"
    # second run: compared with the lock
    code, summary = run_cli(args, capfd)
    assert code == 0 and summary["status"] == "ok"
    assert json.loads(out.read_text())["expectation"] == "compared"
    # a lock with a different count fails
    lock["counts"]["forms"] = 9
    (eng.parent / "inventory.lock.json").write_text(json.dumps(lock))
    code, summary = run_cli(args, capfd)
    assert code == 1
    assert "forms: expected 9, counted 1" in summary["messages"]


def test_lock_for_another_source_fails(make_engagement, tmp_path, capfd, quiet):
    eng = engagement(make_engagement, tmp_path)
    lock = {
        "format_version": 1,
        "recorded": "2026-01-01",
        "source_sha256": "b" * 64,
        "counts": ACCESS_COUNTS,
    }
    (eng.parent / "inventory.lock.json").write_text(json.dumps(lock))
    code, summary = run_cli(
        [
            "inventory",
            eng,
            "--model",
            FIXTURES / "access",
            "--out",
            tmp_path / "i.json",
            "--expect",
        ],
        capfd,
    )
    assert code == 1
    assert any("source_sha256" in m for m in summary["messages"])


def test_missing_model_exits_2(make_engagement, tmp_path, capfd, quiet):
    eng = engagement(make_engagement, tmp_path)
    code, summary = run_cli(
        [
            "inventory",
            eng,
            "--model",
            tmp_path / "nope",
            "--out",
            tmp_path / "i.json",
            "--expect",
        ],
        capfd,
    )
    assert code == 2 and summary["status"] == "error"
    assert not (tmp_path / "i.json").exists()


def test_output_is_deterministic(make_engagement, tmp_path, capfd, quiet):
    eng = engagement(make_engagement, tmp_path, expected=ACCESS_COUNTS)
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    for out in (a, b):
        code, _ = run_cli(
            [
                "inventory",
                eng,
                "--model",
                FIXTURES / "access",
                "--out",
                out,
                "--expect",
            ],
            capfd,
        )
        assert code == 0
    assert a.read_bytes() == b.read_bytes()
    text = a.read_text()
    assert (
        text
        == json.dumps(json.loads(text), indent=2, ensure_ascii=False, sort_keys=True)
        + "\n"
    )
    assert "recorded" not in json.loads(text)
