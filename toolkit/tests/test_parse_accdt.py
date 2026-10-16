"""accdt reader and `toolkit parse` end to end on a template assembled at test time.

The .accdt (an OPC zip) is built here from the hand-written parts in
toolkit/tests/fixtures/parse/: SaveAsText parts as UTF-16LE with a BOM and CRLF, modules as
cp1252, metadata and relationship parts generated. No container file is committed."""

from __future__ import annotations

import codecs
import json
import zipfile
from pathlib import Path

import pytest

from toolkit import cli
from toolkit import model as model_mod
from toolkit.sources import EncryptedSource
from toolkit.sources.accdt import AccdtReader

FIX = Path(__file__).parent / "fixtures" / "parse"
OBJ = "template/database/objects/"
NS = "http://example.invalid/synthetic-accdt"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
REL_TYPES = {
    "metadata": "http://schemas.microsoft.com/office/access/2005/04/template/object-metadata",
    "datamacros": "http://schemas.microsoft.com/office/2007/relationships/DataMacros",
    "properties": "http://schemas.microsoft.com/office/2007/relationships/ObjectProperties",
    "sampledata": "http://schemas.microsoft.com/office/access/2005/04/template/table-data",
}
OBJECTS = [
    # (object name, metadata type, fixture, extension, encoding)
    ("tblWidgetOrders", "Table", "access/tblWidgetOrders.xsd", "xsd", "utf-8"),
    ("tblCustomers", "Table", "access/tblCustomers.xsd", "xsd", "utf-8"),
    ("frmWidgetOrder", "Form", "access/frmWidgetOrder.txt", "txt", "utf-16"),
    ("frmWidgetHost", "Form", "macros/frmWidgetHost.txt", "txt", "utf-16"),
    ("rptWidgetInvoice", "Report", "access/rptWidgetInvoice.txt", "txt", "utf-16"),
    ("qryOpenWidgetOrders", "Query", "access/qryOpenWidgetOrders.txt", "txt", "utf-16"),
    ("qryAllContacts", "Query", "access/qryAllContacts.txt", "txt", "utf-16"),
    ("mcrStartup", "Macro", "macros/mcrStartup.txt", "txt", "utf-16"),
    ("modWidgetMath", "Module", "access/modWidgetMath.bas.txt", "txt", "cp1252"),
    ("clsWidgetCart", "Module", "access/clsWidgetCart.cls.txt", "txt", "cp1252-lf"),
]


def _encode(text: str, encoding: str) -> bytes:
    if encoding == "utf-16":
        return codecs.BOM_UTF16_LE + text.replace("\n", "\r\n").encode("utf-16-le")
    if encoding == "cp1252":
        return text.replace("\n", "\r\n").encode("cp1252")
    if encoding == "cp1252-lf":
        return text.encode("cp1252")
    return text.encode(encoding)


def _rels(entries: list[tuple[str, str]]) -> str:
    items = "".join(
        f'<Relationship Id="rId{i}" Type="{REL_TYPES[kind]}" Target="{target}"/>'
        for i, (kind, target) in enumerate(entries, 1)
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{REL}">{items}</Relationships>'


def build_accdt(path: Path, extra: dict[str, bytes] | None = None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="x"/>')
        z.writestr(
            "_rels/.rels", f'<?xml version="1.0"?><Relationships xmlns="{REL}"/>'
        )
        z.writestr("template/template.xml", '<?xml version="1.0"?><Template/>')
        for name, otype, fixture, ext, enc in OBJECTS:
            text = (FIX / fixture).read_text(encoding="utf-8")
            part = f"{name}.{ext}"
            z.writestr(OBJ + part, _encode(text, enc))
            z.writestr(
                f"{OBJ}properties/{name}.xml",
                f'<?xml version="1.0"?><AccessObject xmlns="{NS}"><Type>{otype}</Type>'
                f"<Name>{name}</Name><NameMap/></AccessObject>",
            )
            rels = [("metadata", f"properties/{name}.xml")]
            if name == "tblWidgetOrders":
                rels += [
                    ("datamacros", "dataMacros/tblWidgetOrders.axl"),
                    ("properties", "properties/tblWidgetOrders.axl"),
                    ("sampledata", "sampleData/tblWidgetOrders.xml"),
                ]
            z.writestr(f"{OBJ}_rels/{part}.rels", _rels(rels))
        axl = (FIX / "access/tblWidgetOrders.axl").read_text(encoding="utf-8")
        z.writestr(OBJ + "dataMacros/tblWidgetOrders.axl", axl.encode("utf-16"))
        props = (FIX / "access/tblWidgetOrders.properties.axl").read_text(
            encoding="utf-8"
        )
        z.writestr(OBJ + "properties/tblWidgetOrders.axl", props.encode("utf-16"))
        z.writestr(
            OBJ + "sampleData/tblWidgetOrders.xml",
            (FIX / "access/tblWidgetOrders.sample.xml").read_bytes(),
        )
        z.writestr(
            "template/database/relationships.xml",
            '<?xml version="1.0"?><dataroot><MSysRelationships><ccolumn>1</ccolumn>'
            "<grbit>4352</grbit><icolumn>0</icolumn><szColumn>CustomerID</szColumn>"
            "<szObject>tblWidgetOrders</szObject><szReferencedColumn>CustomerID"
            "</szReferencedColumn><szReferencedObject>tblCustomers</szReferencedObject>"
            "<szRelationship>tblCustomerstblWidgetOrders</szRelationship>"
            "</MSysRelationships></dataroot>",
        )
        z.writestr(
            "template/database/vbaReferences.xml",
            '<?xml version="1.0"?><VBAReferences><VBAReference><GUID>{0-0}</GUID>'
            "<MajorVer>1</MajorVer><MinorVer>0</MinorVer></VBAReference></VBAReferences>",
        )
        z.writestr(
            "template/database/databaseProperties.xml",
            '<?xml version="1.0"?><Application><Properties>'
            '<Property Name="AppTitle" Value="Synthetic widgets"/></Properties></Application>',
        )
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return path


@pytest.fixture
def accdt_eng(make_engagement, tmp_path):
    def make(extra=None, **source):
        eng = make_engagement(
            tmp_path,
            source={
                "kind": "accdt",
                "path": "{{p.private}}/sources/widgets.accdt",
                "compiled_only": False,
                **source,
            },
        )
        paths = json.loads((eng.parent / "paths.local.json").read_text())
        src = Path(paths["private"]) / "sources" / "widgets.accdt"
        src.parent.mkdir(parents=True, exist_ok=True)
        build_accdt(src, extra)
        return eng, Path(paths["private"])

    return make


def run_parse(capfd, eng, out, *flags):
    code = cli.main(["parse", str(eng), "--out", str(out), *flags])
    stdout, _err = capfd.readouterr()
    lines = stdout.strip().splitlines()
    assert len(lines) == 1, stdout
    summary = json.loads(lines[0])
    assert summary["exit_code"] == code
    return code, summary


def test_reader_classifies_parts(tmp_path):
    src = build_accdt(tmp_path / "w.accdt")
    reader = AccdtReader(src)
    d = reader.design()
    assert d.errors == []
    assert d.parts == {
        "_rels/*.rels": 10,
        "dataMacros/*.axl": 1,
        "objects/*.txt": 8,
        "objects/*.xsd": 2,
        "properties/*.axl": 1,
        "properties/*.xml": 10,
        "sampleData/*.xml": 1,
    }
    assert sorted(n for n, _ in d.forms) == ["frmWidgetHost", "frmWidgetOrder"]
    assert [n for n, _ in d.queries] == ["qryAllContacts", "qryOpenWidgetOrders"]
    assert d.encodings == {
        "form:utf-16le-bom": 2,
        "macro:utf-16le-bom": 1,
        "module:cp1252": 2,
        "query:utf-16le-bom": 2,
        "report:utf-16le-bom": 1,
    }
    assert reader.tables() == ["tblCustomers", "tblWidgetOrders"]


def test_rows_only_through_iter_rows(tmp_path):
    reader = AccdtReader(build_accdt(tmp_path / "w.accdt"))
    rows = list(reader.iter_rows("tblWidgetOrders"))
    assert len(rows) == 2
    assert rows[0]["Status"] == "SENTINEL-ROW-VALUE-7731"
    assert rows[0]["Order Notes"] == "synthetic note"
    texts = list(reader.iter_texts())
    assert ("data", "tblWidgetOrders", "SENTINEL-ROW-VALUE-7731") in texts
    assert any(kind == "code" and name == "modWidgetMath" for kind, name, _t in texts)


def test_parse_accdt_end_to_end(accdt_eng, tmp_path, capfd, quiet):
    eng, private = accdt_eng()
    out = private / "out" / "model"
    code, summary = run_parse(capfd, eng, out, "--strict")
    assert code == 0, summary
    assert summary["status"] == "ok"
    c = summary["counts"]
    expected = {
        "tables": 2,
        "relationships": 1,
        "queries": 2,
        "queries_design_grid": 1,
        "queries_sql_text": 1,
        "forms": 2,
        "reports": 1,
        "modules": 2,
        "class_modules": 1,
        "macros": 1,
        "embedded_macros": 1,
        "data_macro_files": 1,
        "data_macros": 3,
        "objects_with_code_behind": 2,
        "parse_errors": 0,
        "schema_problems": 0,
    }
    assert {k: c[k] for k in expected} == expected
    docs = {s: json.loads((out / f"{s}.json").read_text()) for s in model_mod.FILES}
    assert model_mod.validate(docs) == []
    for stem in model_mod.FILES:
        raw = (out / f"{stem}.json").read_bytes()
        assert raw.endswith(b"\n") and b"\r\n" not in raw
    code_files = sorted(p.name for p in (out / "code").iterdir())
    assert code_files == [
        "Form_frmWidgetOrder.txt",
        "Report_rptWidgetInvoice.txt",
        "clsWidgetCart.txt",
        "modWidgetMath.txt",
    ]
    mods = {m["name"]: m for m in docs["procedures"]["modules"]}
    assert mods["Form_frmWidgetOrder"]["kind"] == "form"
    assert mods["Report_rptWidgetInvoice"]["kind"] == "report"
    assert mods["clsWidgetCart"]["kind"] == "class"
    assert mods["modWidgetMath"]["code_file"] == "code/modWidgetMath.txt"
    assert c["vba_nonblank_lines"] == sum(m["nonblank_lines"] for m in mods.values())
    tables = {t["name"]: t for t in docs["schema"]["tables"]}
    cols = {col["name"]: col for col in tables["tblWidgetOrders"]["columns"]}
    assert [col["name"] for col in tables["tblWidgetOrders"]["columns"]][:3] == [
        "OrderNo",
        "CustomerID",
        "OrderDate",
    ]
    assert cols["OrderNo"]["type"] == "autonumber" and cols["OrderNo"]["required"]
    assert cols["Order Notes"]["type"] == "memo"
    assert (cols["Status"]["type"], cols["Status"]["size"]) == ("text", 20)
    assert (cols["Weight"]["precision"], cols["Weight"]["scale"]) == (10, 3)
    assert cols["Photo"]["type"] == "complex"
    assert cols["Rush"]["type"] == "yesno"
    assert cols["CustomerID"]["properties"]["Caption"] == "Customer"
    ccols = {col["name"]: col for col in tables["tblCustomers"]["columns"]}
    assert ccols["Website"]["type"] == "hyperlink"
    assert ccols["RowGuid"]["type"] == "guid"
    assert tables["tblWidgetOrders"]["primary_key"] == ["OrderNo"]
    idx = {i["name"]: i for i in tables["tblWidgetOrders"]["indexes"]}
    assert idx["ByDateNotes"]["columns"] == ["OrderDate", "Order Notes"]
    (rel,) = docs["schema"]["relationships"]
    assert rel == {
        "name": "tblCustomerstblWidgetOrders",
        "from_table": "tblWidgetOrders",
        "from_columns": ["CustomerID"],
        "to_table": "tblCustomers",
        "to_columns": ["CustomerID"],
        "enforce": True,
        "cascade_update": True,
        "cascade_delete": True,
        "one_to_one": False,
        "join": "inner",
    }
    objects = docs["objects"]
    assert objects["code_behind"] == ["form:frmWidgetOrder", "report:rptWidgetInvoice"]
    assert [m["name"] for m in objects["macros"]] == [
        "mcrStartup",
        "frmWidgetHost.cmdShowReport.Click",
    ]
    assert objects["database_properties"] == {"names": ["AppTitle"]}
    assert objects["facts"]["sample_data_tables"] == 1
    assert objects["diagnostics"]["errors"] == []


def test_no_rows_or_values_in_the_model(accdt_eng, capfd, quiet):
    eng, private = accdt_eng()
    out = private / "out" / "model"
    run_parse(capfd, eng, out, "--strict")
    for p in out.rglob("*"):
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            assert "SENTINEL-ROW-VALUE-7731" not in text
            assert "synthetic note" not in text


def test_two_runs_give_identical_bytes(accdt_eng, capfd, quiet):
    eng, private = accdt_eng()
    a, b = private / "out" / "model", private / "review" / "x" / "model"
    run_parse(capfd, eng, a, "--strict")
    run_parse(capfd, eng, b, "--strict")
    files_a = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
    assert files_a == files_b
    for rel in files_a:
        assert (a / rel).read_bytes() == (b / rel).read_bytes(), rel


def test_parse_scrubs_before_writing(accdt_eng, capfd, quiet):
    eng, private = accdt_eng()
    out = private / "out" / "model"
    code, summary = run_parse(capfd, eng, out, "--strict")
    assert code == 0
    assert summary["counts"]["scrub_tokens"] >= 3
    text = (out / "code" / "modWidgetMath.txt").read_text(encoding="utf-8")
    for original in (
        "widgets@example.invalid",
        "+1 555 010 0199",
        "s3cretPass",
        "widget_app",
    ):
        assert original not in text
        assert original not in json.dumps(summary)
    assert "<EMAIL-1>" in text and "<PHONE-1>" in text
    assert "PWD=<SECRET-" in text and "UID=<SECRET-" in text
    smap = json.loads((private / "scrub-map.json").read_text())
    assert smap["tokens"]["<EMAIL-1>"] == "widgets@example.invalid"
    # a second run reuses the map: same tokens, same bytes
    before = text
    run_parse(capfd, eng, out, "--strict")
    assert (out / "code" / "modWidgetMath.txt").read_text(encoding="utf-8") == before
    assert cli.main(["scrub", "--check", str(out)]) == 0
    capfd.readouterr()


def test_unknown_part_folder_fails_strict_only(accdt_eng, capfd, quiet):
    eng, private = accdt_eng(extra={OBJ + "mystery/thing.bin": b"\x00\x01"})
    out = private / "out" / "model"
    code, summary = run_parse(capfd, eng, out, "--strict")
    assert code == 1
    assert "unknown part folder mystery: 1" in summary["messages"]
    assert "error unknown-part-folder: 1" in summary["messages"]
    assert (out / "objects.json").is_file()  # the model is still written
    code, _summary = run_parse(capfd, eng, out)
    assert code == 0


@pytest.mark.parametrize(
    "fail_on",
    [
        None,
        "multi-backend",
        "compiled-only,missing-backend,multi-backend,unresolved-link",
    ],
)
def test_encrypted_source_exits_2_whatever_fail_on(
    accdt_eng, capfd, quiet, monkeypatch, fail_on
):
    eng, private = accdt_eng()

    def encrypted(self, path):
        raise EncryptedSource()

    monkeypatch.setattr(AccdtReader, "__init__", encrypted)
    flags = ["--strict"] + (["--fail-on", fail_on] if fail_on else [])
    code, summary = run_parse(capfd, eng, private / "out" / "model", *flags)
    assert code == 2
    assert summary["status"] == "error"
    assert summary["messages"] == ["encrypted source: custom quote"]
    assert not (private / "out" / "model" / "objects.json").exists()


def test_compiled_only_fail_on(accdt_eng, capfd, quiet):
    eng, private = accdt_eng(compiled_only=True)
    out = private / "out" / "model"
    code, summary = run_parse(capfd, eng, out, "--strict")
    assert code == 0
    code, summary = run_parse(capfd, eng, out, "--strict", "--fail-on", "compiled-only")
    assert code == 1
    assert "compiled-only: no VBA source" in summary["messages"]
    assert (
        json.loads((out / "objects.json").read_text())["facts"]["compiled_only"] is True
    )


def test_missing_source_and_bad_fail_on(accdt_eng, capfd, quiet):
    eng, private = accdt_eng()
    (private / "sources" / "widgets.accdt").unlink()
    code, summary = run_parse(capfd, eng, private / "out" / "model", "--strict")
    assert code == 2 and summary["messages"] == ["source missing"]
    code, summary = run_parse(
        capfd, eng, private / "out" / "model", "--fail-on", "everything"
    )
    assert code == 2 and summary["status"] == "error"
