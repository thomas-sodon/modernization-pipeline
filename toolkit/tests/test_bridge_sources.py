"""S0's readers of accdb and mdb files and of source.data_copy, proved through the bridge.

S0.parse (toolkit/sources/accdb.py) and S0.dataset (toolkit/dataset.py) read Access files only
through toolkit.reference.bridge, which did not exist when their tests ran. This test creates the
files it needs in tmp_path: an .accdb (newDatabaseVersion V2010) and an .mdb (V2003) with the
fixture's two related tables (Access DDL through UCanAccess, rows read with toolkit.dataset) and
two saved queries (a SELECT with an inner join and a UNION) written into MSysObjects and
MSysQueries, plus an .accdt assembled from the fixture's text parts. Fixtures:
toolkit/tests/fixtures/reference/bridge_sources/ (hand-written, synthetic). No client file, demo
source or engagements/<id>/ record is read.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from toolkit import cli
from toolkit.reference import bridge, qbe
from toolkit.reference import build as bld

FIX = Path(__file__).parent / "fixtures" / "reference" / "bridge_sources"
OBJ = "template/database/objects/"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
META = "http://schemas.microsoft.com/office/access/2005/04/template/object-metadata"

SELECT_DESIGN = {
    "name": "qVendorShipments",
    "params": [],
    "design": {
        "operation": 1,
        "option": 0,
        "input_tables": [
            {"name": "Vendors", "alias": None},
            {"name": "Shipments", "alias": None},
        ],
        "output_columns": [
            {"expression": "Vendors.VendorName", "alias": None},
            {"expression": "Shipments.ShipmentID", "alias": None},
            {"expression": "Shipments.Freight", "alias": None},
        ],
        "joins": [
            {
                "left": "Vendors",
                "right": "Shipments",
                "expression": "Vendors.VendorID = Shipments.VendorID",
                "flag": 1,
            }
        ],
        "where": None,
        "having": None,
        "group_by": [],
        "order_by": [{"expression": "Shipments.ShipmentID", "flag": 0}],
    },
}
EXPECTED_QUERIES = {
    "qVendorShipments": (
        "SELECT Vendors.VendorName, Shipments.ShipmentID, Shipments.Freight\n"
        "FROM Vendors INNER JOIN Shipments ON Vendors.VendorID = Shipments.VendorID\n"
        "ORDER BY Shipments.ShipmentID;"
    ),
    "qAllNames": "SELECT Vendors.VendorName FROM Vendors\nUNION SELECT Shipments.Note FROM Shipments;",
}


def make_access_file(path: Path, version: str) -> Path:
    """Create an Access file with the fixture tables, rows and two saved queries."""
    schema = json.loads((FIX / "schema.json").read_text(encoding="utf-8"))
    conn = bridge.connect(path, read_only=False, new_version=version)
    try:
        bld.create_tables(conn, schema["tables"])
    finally:
        conn.close()
    assert bld.load_rows(path, FIX / "dataset") == (2, 7)
    db = bridge.open_database(path, read_only=False)
    try:
        flag, rows = qbe.design_rows(SELECT_DESIGN)
        assert flag == qbe.SELECT_OBJECT_FLAG
        qbe.write_saved_query(db, "qVendorShipments", rows, flag)
        union = qbe.union_rows(
            "SELECT Vendors.VendorName FROM Vendors",
            "SELECT Shipments.Note FROM Shipments",
        )
        qbe.write_saved_query(db, "qAllNames", union, qbe.UNION_OBJECT_FLAG)
    finally:
        db.close()
    return path


def build_accdt(path: Path, change_type: tuple[str, str, str] | None = None) -> Path:
    """Assemble an .accdt from the fixture's text parts. change_type=(table, from, to) rewrites
    one od:jetType in that table's part."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="x"/>')
        z.writestr(
            "_rels/.rels", f'<?xml version="1.0"?><Relationships xmlns="{REL}"/>'
        )
        z.writestr("template/template.xml", '<?xml version="1.0"?><Template/>')
        for table in ("Vendors", "Shipments"):
            text = (FIX / "accdt" / f"{table}.xsd").read_text(encoding="utf-8")
            if change_type and change_type[0] == table:
                old, new = change_type[1], change_type[2]
                assert old in text
                text = text.replace(old, new, 1)
            z.writestr(f"{OBJ}{table}.xsd", text.encode("utf-8"))
            z.writestr(
                f"{OBJ}properties/{table}.xml",
                f'<?xml version="1.0"?><AccessObject xmlns="urn:synthetic"><Type>Table</Type><Name>{table}</Name></AccessObject>',
            )
            z.writestr(
                f"{OBJ}_rels/{table}.xsd.rels",
                f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{REL}">'
                f'<Relationship Id="rId1" Type="{META}" Target="properties/{table}.xml"/></Relationships>',
            )
        z.writestr(
            "template/database/relationships.xml",
            (FIX / "accdt" / "relationships.xml").read_bytes(),
        )
    return path


def run_cli(capfd, *argv) -> tuple[int, dict]:
    capfd.readouterr()
    code = cli.main([str(a) for a in argv])
    out, _err = capfd.readouterr()
    lines = out.strip().splitlines()
    assert len(lines) == 1, out
    return code, json.loads(lines[0])


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setenv("MP_QUIET", "1")
    monkeypatch.delenv("MP_SANDBOX", raising=False)


@pytest.mark.parametrize(
    "kind,version,file_format",
    [("accdb", "V2010", "V2010"), ("mdb", "V2003", "V2003")],
)
def test_parse_reads_created_accdb_and_mdb(
    tmp_path, make_engagement, capfd, kind, version, file_format
):
    path = make_access_file(tmp_path / f"app.{kind}", version)
    db = bridge.open_database(path)
    try:
        assert str(db.getFileFormat().name()) == file_format
        assert {q.name: q.sql for q in bridge.queries(db)} == EXPECTED_QUERIES
        assert {q.name: q.type for q in bridge.queries(db)} == {
            "qVendorShipments": "select",
            "qAllNames": "union",
        }
    finally:
        db.close()
    eng = make_engagement(
        tmp_path,
        id=f"bs-{kind}",
        source={
            "kind": kind,
            "path": str(path),
            "layout": "single-file",
            "saved_with_data": True,
        },
    )
    out = tmp_path / "home" / "engagements" / f"bs-{kind}" / "private" / "out" / "model"
    code, summary = run_cli(capfd, "parse", eng, "--strict", "--out", out)
    assert code == 0, summary
    assert summary["counts"]["tables"] == 2 and summary["counts"]["queries"] == 2
    objects = json.loads((out / "objects.json").read_text(encoding="utf-8"))
    assert {q["name"]: q["sql"] for q in objects["queries"]} == EXPECTED_QUERIES
    schema = json.loads((out / "schema.json").read_text(encoding="utf-8"))
    types = {
        t["name"]: [(c["name"], c["type"]) for c in t["columns"]]
        for t in schema["tables"]
    }
    assert types["Shipments"] == [
        ("ShipmentID", "autonumber"),
        ("VendorID", "longinteger"),
        ("ShippedOn", "datetime"),
        ("Freight", "currency"),
        ("Express", "yesno"),
        ("Note", "text"),
    ]


def _accdt_engagement(
    tmp_path, make_engagement, accdt: Path, data_copy: Path, ident: str
):
    return make_engagement(
        tmp_path,
        id=ident,
        source={
            "kind": "accdt",
            "path": str(accdt),
            "data_copy": str(data_copy),
            "saved_with_data": False,
        },
    )


def test_accdt_with_data_copy_parses_and_detects_a_type_change(
    tmp_path, make_engagement, capfd
):
    copy = make_access_file(tmp_path / "data.accdb", "V2010")
    good = build_accdt(tmp_path / "good.accdt")
    eng = _accdt_engagement(tmp_path, make_engagement, good, copy, "bs-accdt")
    out = tmp_path / "home" / "engagements" / "bs-accdt" / "private" / "out" / "model"
    code, summary = run_cli(capfd, "parse", eng, "--strict", "--out", out)
    assert code == 0, summary
    facts = json.loads((out / "objects.json").read_text(encoding="utf-8"))["facts"]
    assert {
        t["table"]: (t["design_columns"], t["copy_columns"], t["matching_columns"])
        for t in facts["data_copy"]
    } == {
        "Vendors": (4, 4, 4),
        "Shipments": (6, 6, 6),
    }
    assert not any("data-copy-differs" in m for m in summary["messages"])
    changed = build_accdt(
        tmp_path / "changed.accdt",
        ("Vendors", 'od:jetType="integer"', 'od:jetType="longinteger"'),
    )
    eng2 = _accdt_engagement(tmp_path, make_engagement, changed, copy, "bs-accdt2")
    out2 = tmp_path / "home" / "engagements" / "bs-accdt2" / "private" / "out" / "model"
    code, summary = run_cli(capfd, "parse", eng2, "--strict", "--out", out2)
    assert code == 1
    assert "error data-copy-differs: 1" in summary["messages"]


def test_dataset_export_reads_the_data_copy(tmp_path, make_engagement, capfd):
    copy = make_access_file(tmp_path / "data.accdb", "V2010")
    accdt = build_accdt(tmp_path / "app.accdt")
    eng = _accdt_engagement(tmp_path, make_engagement, accdt, copy, "bs-export")
    private = tmp_path / "home" / "engagements" / "bs-export" / "private"
    assert (
        run_cli(capfd, "parse", eng, "--strict", "--out", private / "out" / "model")[0]
        == 0
    )
    target = private / "datasets" / "engagement"
    code, summary = run_cli(capfd, "dataset", "export", eng, "--out", target)
    assert code == 0, summary
    written = json.loads((target / "dataset.json").read_text(encoding="utf-8"))
    expected = json.loads(
        (FIX / "dataset" / "dataset.json").read_text(encoding="utf-8")
    )
    assert written["load_order"] == expected["load_order"]
    assert {
        t["name"]: (t["rows"], t["sha256"], t["columns"]) for t in written["tables"]
    } == {t["name"]: (t["rows"], t["sha256"], t["columns"]) for t in expected["tables"]}


def test_unreadable_access_files_exit_2_now_that_the_bridge_exists(
    tmp_path, make_engagement, capfd
):
    """With the bridge in place S0 no longer answers BridgeNeeded: an unreadable data copy,
    accdb source or back-end file is a SourceError ('the database cannot be opened', exit 2).
    These are the post-bridge forms of S0's three bridge-missing tests in test_dataset.py."""
    from toolkit import dataset as tds
    from toolkit.sources import SourceError

    junk = tmp_path / "junk.accdb"
    junk.write_bytes(b"\x00" * 64)

    accdt = build_accdt(tmp_path / "app.accdt")
    eng = _accdt_engagement(tmp_path, make_engagement, accdt, junk, "bs-junk")
    private = tmp_path / "home" / "engagements" / "bs-junk" / "private"
    model = private / "out" / "model"
    model.mkdir(parents=True, exist_ok=True)
    (model / "schema.json").write_bytes((FIX / "schema.json").read_bytes())
    code, summary = run_cli(
        capfd, "dataset", "export", eng, "--out", private / "datasets" / "engagement"
    )
    assert code == 2
    assert summary["messages"] == ["the database cannot be opened"]

    eng2 = make_engagement(
        tmp_path,
        id="bs-junk2",
        source={"kind": "accdb", "path": str(junk), "saved_with_data": True},
    )
    private2 = tmp_path / "home" / "engagements" / "bs-junk2" / "private"
    model2 = private2 / "out" / "model"
    model2.mkdir(parents=True, exist_ok=True)
    (model2 / "schema.json").write_bytes((FIX / "schema.json").read_bytes())
    code, summary = run_cli(
        capfd, "dataset", "export", eng2, "--out", private2 / "datasets" / "engagement"
    )
    assert code == 2
    assert summary["messages"] == ["the database cannot be opened"]

    with pytest.raises(SourceError) as exc:
        tds.open_backend(junk)
    assert not isinstance(exc.value, tds.BridgeNeeded)
    assert str(exc.value) == "the database cannot be opened"
    with pytest.raises(SourceError):
        tds.open_backend(tmp_path / "missing.accdb")
