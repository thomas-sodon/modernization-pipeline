"""`toolkit dataset export|validate` and the toolkit.dataset adapters.

Sources are assembled at test time from the hand-written synthetic fixtures in
toolkit/tests/fixtures/dataset/ (a made-up widget shop): an .accdt zip holding sampleData parts,
a vcs-export folder, an xlsm workbook built with openpyxl, and CSV dataset directories. No
container file is committed."""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import jsonschema
import pytest

from toolkit import cli
from toolkit import dataset as tds
from toolkit.engagement import load

FIX = Path(__file__).parent / "fixtures" / "dataset"
SAMPLE = FIX / "sample"
SCHEMA = FIX / "schema.json"
SAMPLE_DATA = "template/database/objects/sampleData/"


LAST_STDERR: list[str] = [""]


def run(capfd, *argv):
    code = cli.main(list(argv))
    out, err = capfd.readouterr()
    LAST_STDERR[0] = err
    lines = out.splitlines()
    assert len(lines) == 1, out
    return code, json.loads(lines[0])


def build_accdt(path: Path, orders: str = "Orders.xml") -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="x"/>')
        z.writestr("template/template.xml", '<?xml version="1.0"?><Template/>')
        z.writestr(
            SAMPLE_DATA + "Widget Customers.xml",
            (FIX / "accdt" / "Widget_Customers.xml").read_bytes(),
        )
        z.writestr(
            SAMPLE_DATA + "Orders.xml", (FIX / "accdt" / orders).read_bytes()
        )
    return path


def setup(
    tmp_path,
    make_engagement,
    source: dict,
    objects: dict | None = None,
    schema: Path = SCHEMA,
):
    eng_path = make_engagement(tmp_path, source=source)
    eng = load(eng_path)
    model = Path(eng["paths"]["private"]) / "out" / "model"
    model.mkdir(parents=True, exist_ok=True)
    shutil.copy(schema, model / "schema.json")
    if objects is not None:
        (model / "objects.json").write_text(json.dumps(objects), encoding="utf-8")
    return eng_path, eng


def accdt_engagement(tmp_path, make_engagement, **source):
    eng_path, eng = setup(
        tmp_path,
        make_engagement,
        {"kind": "accdt", "path": "{{p.private}}/sources/widgets.accdt", **source},
    )
    src = Path(eng["source"]["path"])
    src.parent.mkdir(parents=True, exist_ok=True)
    build_accdt(src)
    return eng_path, eng


def out_dir(eng) -> Path:
    return Path(eng["paths"]["private"]) / "datasets" / "engagement"


# ------------------------------------------------------------------ accdt


def test_accdt_export_matches_the_hand_written_sample(
    tmp_path, make_engagement, capfd, quiet
):
    eng_path, eng = accdt_engagement(tmp_path, make_engagement, saved_with_data=True)
    out = out_dir(eng)
    code, summary = run(capfd, "dataset", "export", str(eng_path), "--out", str(out))
    assert code == 0, summary
    assert summary["status"] == "ok"
    assert summary["command"] == "dataset export"
    assert summary["counts"]["tables"] == 2
    assert summary["counts"]["rows"] == 8
    assert summary["counts"]["omitted_columns"] == 2
    assert summary["counts"]["unknown_columns"] == 0
    # per-table detail goes to stderr (the data-zone log under mp-data), not the summary
    assert "table Orders: rows 4" in LAST_STDERR[0]
    assert "columns [OrderID, CustomerID, Weight, Ratio, Notes, Token]" in LAST_STDERR[0]
    # the CSV bytes equal the hand-written sample dataset (same schema, same rows)
    for name in ("Orders.csv", "Widget_Customers-225f06c7.csv"):
        assert (out / "tables" / name).read_bytes() == (
            SAMPLE / "tables" / name
        ).read_bytes()
    meta = json.loads((out / "dataset.json").read_text())
    sample = json.loads((SAMPLE / "dataset.json").read_text())
    assert meta["tables"] == sample["tables"]
    assert meta["load_order"] == ["Widget Customers", "Orders"]
    assert (meta["name"], meta["kind"], meta["seed"]) == (
        "engagement",
        "engagement",
        None,
    )
    assert f"sha256 {meta['tables'][1]['sha256']}" in LAST_STDERR[0]
    # the summary holds aggregate counts only: no values, and no table or column names,
    # so mp-data's corpus filter (substring match) can never withhold it
    line = json.dumps(summary)
    for value in ("Sprocket", "Gear, Cog", "rush", "6f9619ff", "12.5"):
        assert value not in line
    for name in ("Orders", "Widget Customers", "CustomerID", "Notes", "Token"):
        assert name not in line
    assert set(summary) <= {
        "status", "command", "exit_code", "engagement", "counts", "messages"
    }
    # export --check: identical, and it writes nothing
    before = sorted(p.name for p in out.parent.iterdir())
    code, summary = run(
        capfd, "dataset", "export", str(eng_path), "--out", str(out), "--check"
    )
    assert code == 0, summary
    assert summary["counts"]["differences"] == 0
    assert sorted(p.name for p in out.parent.iterdir()) == before



def test_accdt_export_reads_the_embedded_schema_layout(
    tmp_path, make_engagement, capfd, quiet
):
    """A sampleData part whose rows follow an embedded xsd:schema inside a wrapper element
    exports the same rows as the bare-dataroot layout: the schema is never read as a row."""
    eng_path, eng = setup(
        tmp_path,
        make_engagement,
        {
            "kind": "accdt",
            "path": "{{p.private}}/sources/widgets.accdt",
            "saved_with_data": True,
        },
    )
    src = Path(eng["source"]["path"])
    src.parent.mkdir(parents=True, exist_ok=True)
    build_accdt(src, orders="Orders.embedded.xml")
    out = out_dir(eng)
    code, summary = run(capfd, "dataset", "export", str(eng_path), "--out", str(out))
    assert code == 0, summary
    assert summary["counts"]["rows"] == 8
    assert "table Orders: rows 4" in LAST_STDERR[0]
    assert summary["counts"]["unknown_columns"] == 0
    assert (out / "tables" / "Orders.csv").read_bytes() == (
        SAMPLE / "tables" / "Orders.csv"
    ).read_bytes()

def test_export_check_reports_a_difference(tmp_path, make_engagement, capfd, quiet):
    eng_path, eng = accdt_engagement(tmp_path, make_engagement, saved_with_data=True)
    out = out_dir(eng)
    assert run(capfd, "dataset", "export", str(eng_path), "--out", str(out))[0] == 0
    target = out / "tables" / "Orders.csv"
    target.write_bytes(target.read_bytes() + b"99,\\N,\\N,\\N,\\N,\\N\r\n")
    code, summary = run(
        capfd, "dataset", "export", str(eng_path), "--out", str(out), "--check"
    )
    assert code == 1
    assert summary["status"] == "failed"
    assert "1 files differ" in summary["messages"]
    assert "Orders" not in json.dumps(summary)
    assert "differs: tables/Orders.csv" in LAST_STDERR[0]
    # --check leaves the directory as it was
    assert target.read_bytes().endswith(b"99,\\N,\\N,\\N,\\N,\\N\r\n")
    # a missing directory is a difference too
    shutil.rmtree(out)
    code, _summary = run(
        capfd, "dataset", "export", str(eng_path), "--out", str(out), "--check"
    )
    assert code == 1
    assert not out.exists()


def test_export_replaces_the_directory_atomically(
    tmp_path, make_engagement, capfd, quiet
):
    eng_path, eng = accdt_engagement(tmp_path, make_engagement, saved_with_data=True)
    out = out_dir(eng)
    out.mkdir(parents=True)
    (out / "stale.csv").write_text("old")
    assert run(capfd, "dataset", "export", str(eng_path), "--out", str(out))[0] == 0
    assert not (out / "stale.csv").exists()
    assert sorted(p.name for p in out.parent.iterdir()) == ["engagement"]


def test_accdt_without_data_writes_an_empty_dataset(
    tmp_path, make_engagement, capfd, quiet
):
    eng_path, eng = accdt_engagement(tmp_path, make_engagement, saved_with_data=False)
    out = out_dir(eng)
    code, summary = run(capfd, "dataset", "export", str(eng_path), "--out", str(out))
    assert code == 0
    assert summary["status"] == "ok"
    assert summary["counts"]["tables"] == 0
    assert summary["messages"] == ["no data in the source"]
    meta = json.loads((out / "dataset.json").read_text())
    assert meta["tables"] == [] and meta["load_order"] == []
    assert not (out / "tables").exists()
    code, _s = run(
        capfd, "dataset", "export", str(eng_path), "--out", str(out), "--check"
    )
    assert code == 0


def test_reader_that_returns_non_rows_fails(
    tmp_path, make_engagement, capfd, quiet, monkeypatch
):
    eng_path, eng = accdt_engagement(tmp_path, make_engagement, saved_with_data=True)

    class Wrong:
        def iter_rows(self, table):
            yield {"element": None, "annotation": None}

    monkeypatch.setattr(tds, "source_reader", lambda eng: Wrong())
    code, summary = run(
        capfd, "dataset", "export", str(eng_path), "--out", str(out_dir(eng))
    )
    assert code == 1
    assert "no field matches a column" in summary["messages"][0]
    assert not out_dir(eng).exists()


def test_value_that_does_not_fit_fails_without_printing_it(
    tmp_path, make_engagement, capfd, quiet, monkeypatch
):
    eng_path, eng = accdt_engagement(tmp_path, make_engagement, saved_with_data=True)

    class Bad:
        def iter_rows(self, table):
            yield {"OrderID": "SENTINEL-NOT-A-NUMBER", "CustomerID": "1"}

    monkeypatch.setattr(tds, "source_reader", lambda eng: Bad())
    code, summary = run(
        capfd, "dataset", "export", str(eng_path), "--out", str(out_dir(eng))
    )
    assert code == 1
    assert "SENTINEL" not in json.dumps(summary)
    assert "column OrderID" in summary["messages"][0]


# ------------------------------------------------------------------ vcs-export


def test_vcs_export_table_data(tmp_path, make_engagement, capfd, quiet):
    eng_path, eng = setup(
        tmp_path,
        make_engagement,
        {
            "kind": "vcs-export",
            "path": "{{p.private}}/sources/export",
            "saved_with_data": True,
        },
    )
    tables = Path(eng["source"]["path"]) / "tables"
    tables.mkdir(parents=True)
    shutil.copy(FIX / "vcs" / "Orders.txt", tables / "Orders.txt")
    shutil.copy(FIX / "vcs" / "Widget_Customers.txt", tables / "Widget Customers.txt")
    out = out_dir(eng)
    code, summary = run(capfd, "dataset", "export", str(eng_path), "--out", str(out))
    assert code == 0, summary
    _meta, rows = tds.read_dataset(out)
    orders = list(rows["Orders"])
    assert orders[1] == {
        "OrderID": 21,
        "CustomerID": None,
        "Weight": None,
        "Ratio": None,
        "Notes": "",
        "Token": None,
    }
    customers = list(rows["Widget Customers"])
    assert customers[0]["Active"] is True
    assert str(customers[0]["Balance"]) == "3.1"
    assert customers[0]["Created"].isoformat() == "2024-05-06T07:08:09"


# ------------------------------------------------------------------ data copy, bridge, back ends


def test_unreadable_data_copy(tmp_path, make_engagement, capfd, quiet):
    eng_path, eng = accdt_engagement(
        tmp_path,
        make_engagement,
        saved_with_data=False,
        data_copy="{{p.private}}/sources/copy.accdb",
    )
    Path(eng["source"]["data_copy"]).write_bytes(b"\x00" * 64)
    code, summary = run(
        capfd, "dataset", "export", str(eng_path), "--out", str(out_dir(eng))
    )
    assert code == 2
    assert summary["messages"] == ["the database cannot be opened"]


def test_unreadable_accdb_source(tmp_path, make_engagement, capfd, quiet):
    eng_path, eng = setup(
        tmp_path,
        make_engagement,
        {
            "kind": "accdb",
            "path": "{{p.private}}/sources/app.accdb",
            "saved_with_data": True,
        },
    )
    src = Path(eng["source"]["path"])
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"\x00" * 64)
    code, summary = run(
        capfd, "dataset", "export", str(eng_path), "--out", str(out_dir(eng))
    )
    assert code == 2
    assert summary["messages"] == ["the database cannot be opened"]


def test_open_backend(tmp_path):
    with pytest.raises(tds.SourceError) as exc:
        f = tmp_path / "back.accdb"
        f.write_bytes(b"\x00" * 64)
        tds.open_backend(f, json.loads(SCHEMA.read_text()))
    assert str(exc.value) == "the database cannot be opened"
    with pytest.raises(tds.SourceError):
        tds.open_backend(tmp_path / "missing.accdb")
    reader = tds.open_backend(SAMPLE)
    assert reader.tables() == ["Orders", "Widget Customers"]
    assert len(list(reader.iter_rows("Orders"))) == 4


def test_data_copy_as_csv_dataset_directory(tmp_path, make_engagement, capfd, quiet):
    eng_path, eng = accdt_engagement(
        tmp_path,
        make_engagement,
        saved_with_data=False,
        data_copy="{{p.private}}/sources/copy",
    )
    shutil.copytree(SAMPLE, eng["source"]["data_copy"])
    out = out_dir(eng)
    code, summary = run(capfd, "dataset", "export", str(eng_path), "--out", str(out))
    assert code == 0, summary
    for name in ("Orders.csv", "Widget_Customers-225f06c7.csv"):
        assert (out / "tables" / name).read_bytes() == (
            SAMPLE / "tables" / name
        ).read_bytes()


def linked(name, backend, kind="access", backend_file="back.accdb"):
    return {
        "name": name,
        "backend_kind": kind,
        "backend": backend,
        "backend_file": backend_file,
        "source_table": None,
    }


def test_more_than_one_back_end(tmp_path, make_engagement, capfd, quiet):
    objects = {
        "linked_tables": [
            linked("Orders", 1),
            linked("Widget Customers", 2, backend_file="other.accdb"),
        ]
    }
    eng_path, eng = accdt_engagement(tmp_path, make_engagement, saved_with_data=True)
    model = Path(eng["paths"]["private"]) / "out" / "model"
    (model / "objects.json").write_text(json.dumps(objects))
    code, summary = run(
        capfd, "dataset", "export", str(eng_path), "--out", str(out_dir(eng))
    )
    assert code == 2
    assert summary["messages"] == ["more than one data back end: custom quote"]


def test_back_end_not_received_skips_its_tables(
    tmp_path, make_engagement, capfd, quiet
):
    objects = {"linked_tables": [linked("Orders", 1)]}
    eng_path, eng = accdt_engagement(tmp_path, make_engagement, saved_with_data=True)
    model = Path(eng["paths"]["private"]) / "out" / "model"
    (model / "objects.json").write_text(json.dumps(objects))
    out = out_dir(eng)
    code, summary = run(capfd, "dataset", "export", str(eng_path), "--out", str(out))
    assert code == 0, summary
    assert summary["status"] == "no-data"
    assert summary["counts"]["skipped_tables"] == 1
    assert "1 tables skipped: their back end was not received" in summary["messages"]
    meta = json.loads((out / "dataset.json").read_text())
    assert [t["name"] for t in meta["tables"]] == ["Widget Customers"]
    assert meta["load_order"] == ["Widget Customers"]


def test_received_back_end_is_read_through_open_backend(
    tmp_path, make_engagement, capfd, quiet
):
    objects = {"linked_tables": [linked("Orders", 1, backend_file="back")]}
    eng_path, eng = accdt_engagement(
        tmp_path,
        make_engagement,
        saved_with_data=True,
        backends=[
            {"path": "{{p.private}}/sources/back", "sha256": None, "password_ref": None}
        ],
    )
    (Path(eng["paths"]["private"]) / "out" / "model" / "objects.json").write_text(
        json.dumps(objects)
    )
    shutil.copytree(SAMPLE, Path(eng["paths"]["private"]) / "sources" / "back")
    code, summary = run(
        capfd, "dataset", "export", str(eng_path), "--out", str(out_dir(eng))
    )
    assert code == 0, summary
    assert summary["status"] == "ok"
    assert summary["counts"]["rows"] == 8


# ------------------------------------------------------------------ xlsm


XLSM_SCHEMA = {
    "format_version": 1,
    "source_kind": "xlsm",
    "tables": [
        {
            "name": "PartsTable",
            "role": "data",
            "sheet": "Parts",
            "ref": "A1:D3",
            "columns": [
                {
                    "name": "Part",
                    "type": "text",
                    "size": None,
                    "precision": None,
                    "scale": None,
                    "required": False,
                },
                {
                    "name": "Qty",
                    "type": "longinteger",
                    "size": None,
                    "precision": None,
                    "scale": None,
                    "required": False,
                },
                {
                    "name": "Price",
                    "type": "decimal",
                    "size": None,
                    "precision": None,
                    "scale": None,
                    "required": False,
                },
                {
                    "name": "Line Total",
                    "type": "decimal",
                    "size": None,
                    "precision": None,
                    "scale": None,
                    "required": False,
                    "computed": True,
                    "formula": "=[@Qty]*[@Price]",
                },
            ],
            "primary_key": ["Part"],
            "indexes": [],
        },
        {
            "name": "HelpTable",
            "role": "documentation",
            "sheet": "Help",
            "ref": "A1:A2",
            "columns": [
                {
                    "name": "Topic",
                    "type": "text",
                    "size": None,
                    "precision": None,
                    "scale": None,
                    "required": False,
                }
            ],
            "primary_key": [],
            "indexes": [],
        },
    ],
    "relationships": [],
}


def build_xlsm(path: Path) -> Path:
    import datetime as dt

    import openpyxl
    from openpyxl.worksheet.table import Table

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Parts"
    ws.append(["Part", "Qty", "Price", "Line Total"])
    ws.append(["Sprocket", 3, 2.5, "=B2*C2"])
    ws.append(["Cog", None, 0.1, "=B3*C3"])
    ws.add_table(Table(displayName="PartsTable", ref="A1:D3"))
    help_ws = wb.create_sheet("Help")
    help_ws.append(["Topic"])
    help_ws.append(["How to order widgets"])
    help_ws.add_table(Table(displayName="HelpTable", ref="A1:A2"))
    wb.properties.created = dt.datetime(2024, 1, 1)
    wb.save(path)
    return path


def test_xlsm_list_objects(tmp_path, make_engagement, capfd, quiet):
    schema_path = tmp_path / "xlsm-schema.json"
    schema_path.write_text(json.dumps(XLSM_SCHEMA))
    eng_path, eng = setup(
        tmp_path,
        make_engagement,
        {
            "kind": "xlsm",
            "path": "{{p.private}}/sources/widgets.xlsm",
            "saved_with_data": True,
        },
        schema=schema_path,
    )
    src = Path(eng["source"]["path"])
    src.parent.mkdir(parents=True, exist_ok=True)
    build_xlsm(src)
    out = out_dir(eng)
    code, summary = run(capfd, "dataset", "export", str(eng_path), "--out", str(out))
    assert code == 0, summary
    meta = json.loads((out / "dataset.json").read_text())
    assert [t["name"] for t in meta["tables"]] == ["PartsTable"]
    assert meta["tables"][0]["omitted_columns"] == [
        {"name": "Line Total", "type": "decimal", "reason": "computed"}
    ]
    assert (out / "tables" / "PartsTable.csv").read_bytes() == (
        b"Part,Qty,Price\r\nSprocket,3,2.5\r\nCog,\\N,0.1\r\n"
    )
    # the seed-shaped dataset validates: computed column omitted, documentation table absent
    code, summary = run(
        capfd, "dataset", "validate", str(out), "--schema", str(schema_path)
    )
    assert code == 0, summary


# ------------------------------------------------------------------ validate command


def test_validate_command_on_the_sample(capfd, quiet):
    code, summary = run(
        capfd, "dataset", "validate", str(SAMPLE), "--schema", str(SCHEMA)
    )
    assert code == 0, summary
    assert summary["command"] == "dataset validate"
    assert summary["counts"] == {"tables": 2, "rows": 8, "problems": 0, "fk_orphans": 0}


def test_validate_command_failures(tmp_path, capfd, quiet):
    d = tmp_path / "d"
    shutil.copytree(SAMPLE, d)
    meta = json.loads((d / "dataset.json").read_text())
    meta["tables"][1][
        "columns"
    ].pop()  # Token omitted as if computed: an ordinary column
    meta["tables"][1]["omitted_columns"].append(
        {"name": "Token", "type": "guid", "reason": "computed"}
    )
    meta["extra"] = True
    (d / "dataset.json").write_text(json.dumps(meta))
    code, summary = run(capfd, "dataset", "validate", str(d), "--schema", str(SCHEMA))
    assert code == 1
    assert summary["status"] == "failed"
    text = "\n".join(summary["messages"])
    assert "Orders.Token: omitted as computed but not computed in the schema" in text
    assert "dataset.json: (root): additionalProperties check failed" in text
    code, summary = run(
        capfd, "dataset", "validate", str(tmp_path / "none"), "--schema", str(SCHEMA)
    )
    assert code == 2
    code, summary = run(
        capfd,
        "dataset",
        "validate",
        str(SAMPLE),
        "--schema",
        str(tmp_path / "none.json"),
    )
    assert code == 2


def test_dataset_schema_accepts_the_sample_and_the_contract_example():
    schema = json.loads(
        (Path(tds.__file__).parent / "schemas" / "dataset.schema.json").read_text()
    )
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(json.loads((SAMPLE / "dataset.json").read_text()), schema)
    bad = json.loads((SAMPLE / "dataset.json").read_text())
    bad["kind"] = "engagement"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_toolkit_reexports_the_harness_formatter():
    import harness.dataset as h

    assert tds.format_value is h.format_value
    assert tds.write_dataset is h.write_dataset
    assert tds.file_safe("Order Details") == "Order_Details-fe3ac0d4"
    from toolkit.model import file_safe

    for name in ("Order Details", "InvoiceTable[Net Amount]", "plain", "ü"):
        assert tds.file_safe(name) == file_safe(name)
