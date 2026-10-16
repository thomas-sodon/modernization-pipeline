"""msaccess-vcs-addin export folders, and the back-end --fail-on conditions.

The synthetic export lives in toolkit/tests/fixtures/parse/vcs/. Files whose real extension the
repos forbid (.bas, .cls) are stored with a .txt suffix and restored in a temp copy here."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from toolkit import cli
from toolkit import model as model_mod
from toolkit.sources.vcs_export import VcsExportReader

FIX = Path(__file__).parent / "fixtures" / "parse" / "vcs"


def materialize(dest: Path) -> Path:
    for src in FIX.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(FIX).as_posix()
        for ext in (".bas.txt", ".cls.txt"):
            if rel.endswith(ext):
                rel = rel[: -len(".txt")]
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    return dest


@pytest.fixture
def vcs_eng(make_engagement, tmp_path):
    def make(**source):
        eng = make_engagement(
            tmp_path,
            source={
                "kind": "vcs-export",
                "path": "{{p.private}}/sources/export",
                **source,
            },
        )
        paths = json.loads((eng.parent / "paths.local.json").read_text())
        private = Path(paths["private"])
        materialize(private / "sources" / "export")
        return eng, private

    return make


def run_parse(capfd, eng, out, *flags):
    code = cli.main(["parse", str(eng), "--out", str(out), *flags])
    stdout, _err = capfd.readouterr()
    summary = json.loads(stdout.strip())
    assert summary["exit_code"] == code
    return code, summary


def test_reader_design(tmp_path):
    reader = VcsExportReader(materialize(tmp_path / "export"))
    d = reader.design()
    assert d.errors == [] and d.unknown_parts == {}
    assert sorted(t["name"] for t in d.tables) == ["tblCustomers", "tblOrders"]
    assert [n for n, _ in d.queries] == ["qryCustomers"]
    assert [n for n, _ in d.sql_queries] == ["qryUnionNames"]
    (form,) = d.forms
    assert "CodeBehindForm" in form[1]  # the split .cls is joined back
    assert sorted((n, k) for n, _t, k in d.modules) == [
        ("clsCounter", "class"),
        ("modUtil", "standard"),
    ]
    assert reader.tables() == ["tblCustomers", "tblOrders", "tblPriceList"]


def test_rows_only_through_iter_rows(tmp_path):
    reader = VcsExportReader(materialize(tmp_path / "export"))
    rows = list(reader.iter_rows("tblCustomers"))
    assert rows[0] == {"CustomerID": "1", "CustomerName": "SENTINEL-VCS-ROW-4410"}
    assert ("data", "tblCustomers", "SENTINEL-VCS-ROW-4410") in list(
        reader.iter_texts()
    )


def test_parse_vcs_export(vcs_eng, capfd, quiet):
    eng, private = vcs_eng()
    out = private / "out" / "model"
    code, summary = run_parse(capfd, eng, out, "--strict")
    assert code == 0, summary
    c = summary["counts"]
    expected = {
        "tables": 2,
        "linked_tables": 1,
        "relationships": 1,
        "queries": 2,
        "queries_design_grid": 1,
        "queries_sql_text": 1,
        "forms": 1,
        "reports": 1,
        "modules": 2,
        "class_modules": 1,
        "macros": 1,
        "data_macros": 1,
        "objects_with_code_behind": 1,
        "data_backends": 1,
        "missing_backends": 1,
    }
    assert {k: c[k] for k in expected} == expected
    docs = {s: json.loads((out / f"{s}.json").read_text()) for s in model_mod.FILES}
    assert model_mod.validate(docs) == []
    objects = docs["objects"]
    assert objects["linked_tables"] == [
        {
            "name": "tblPriceList",
            "backend_kind": "access",
            "backend": 1,
            "backend_file": "widgets_be.accdb",
            "source_table": "tblPriceList",
        }
    ]
    queries = {q["name"]: q for q in objects["queries"]}
    assert queries["qryUnionNames"]["query_type"] == "union"
    assert queries["qryCustomers"]["storage"] == "design"
    (rel,) = docs["schema"]["relationships"]
    assert (rel["from_table"], rel["to_table"], rel["cascade_delete"]) == (
        "tblOrders",
        "tblCustomers",
        True,
    )
    procs = {m["name"]: m for m in docs["procedures"]["modules"]}
    assert procs["Form_frmCustomer"]["kind"] == "form"
    assert procs["modUtil"]["procedures"][0]["refs"]["tables"] == ["tblOrders"]
    (macro,) = objects["macros"]
    assert macro["actions"] == [
        {"action": "OpenForm", "args": {"FormName": "frmCustomer"}, "depth": 0}
    ]
    for p in out.rglob("*"):
        if p.is_file():
            assert "SENTINEL-VCS-ROW-4410" not in p.read_text(encoding="utf-8")


def test_two_data_back_ends_exit_2(vcs_eng, capfd, quiet):
    eng, private = vcs_eng()
    (private / "sources" / "export" / "tbldefs" / "tblArchive.json").write_text(
        json.dumps(
            {
                "Items": {
                    "Name": "tblArchive",
                    "Connect": ";DATABASE=D:\\old\\archive_be.accdb",
                }
            }
        )
    )
    out = private / "out" / "model"
    code, summary = run_parse(capfd, eng, out, "--strict")
    assert code == 0 and summary["counts"]["data_backends"] == 2
    code, summary = run_parse(capfd, eng, out, "--strict", "--fail-on", "multi-backend")
    assert code == 2
    assert summary["status"] == "error"
    assert summary["messages"][0] == "more than one data back end: custom quote"
    assert (out / "objects.json").is_file()  # checked after the model is written


def test_listed_but_missing_back_end(vcs_eng, tmp_path, capfd, quiet):
    missing = (
        tmp_path
        / "home"
        / "engagements"
        / "demo-eng"
        / "private"
        / "sources"
        / "widgets_be.accdb"
    )
    eng, private = vcs_eng(
        backends=[{"path": str(missing), "sha256": None, "password_ref": None}]
    )
    out = private / "out" / "model"
    code, summary = run_parse(capfd, eng, out, "--strict")
    assert code == 0
    code, summary = run_parse(
        capfd, eng, out, "--strict", "--fail-on", "missing-backend"
    )
    assert code == 1
    assert "missing-backend: a linked back end was not received" in summary["messages"]


def test_received_back_end_is_not_missing(vcs_eng, tmp_path, capfd, quiet, monkeypatch):
    received = (
        tmp_path
        / "home"
        / "engagements"
        / "demo-eng"
        / "private"
        / "sources"
        / "widgets_be.accdb"
    )
    eng, private = vcs_eng(
        backends=[{"path": str(received), "sha256": None, "password_ref": None}]
    )
    received.write_bytes(b"\x00\x01\x00\x00Standard ACE DB synthetic")
    monkeypatch.setattr("toolkit.sources.accdb.probe_encrypted", lambda path: False)
    code, summary = run_parse(
        capfd,
        eng,
        private / "out" / "model",
        "--strict",
        "--fail-on",
        "missing-backend",
    )
    assert code == 0
    assert summary["counts"]["missing_backends"] == 0


def test_unresolved_link(vcs_eng, capfd, quiet):
    eng, private = vcs_eng()
    (private / "sources" / "export" / "tbldefs" / "tblOdd.json").write_text(
        json.dumps({"Name": "tblOdd", "Connect": "MYSTERY;thing=1"})
    )
    out = private / "out" / "model"
    code, summary = run_parse(
        capfd, eng, out, "--strict", "--fail-on", "unresolved-link"
    )
    assert code == 1
    assert (
        "unresolved-link: a connect string cannot be classified" in summary["messages"]
    )
    assert "warning linked-table-unresolved: 1" in summary["messages"]


def test_unknown_folder_and_odbc_link(vcs_eng, capfd, quiet):
    eng, private = vcs_eng()
    export = private / "sources" / "export"
    (export / "strange").mkdir()
    (export / "strange" / "a.txt").write_text("x")
    (export / "tbldefs" / "tblRemote.json").write_text(
        json.dumps(
            {"Name": "tblRemote", "Connect": "ODBC;DSN=widgets;UID=app;PWD=hunter22;"}
        )
    )
    out = private / "out" / "model"
    code, summary = run_parse(capfd, eng, out, "--strict")
    assert code == 1
    assert "unknown part folder strange: 1" in summary["messages"]
    objects = json.loads((out / "objects.json").read_text())
    kinds = {t["name"]: t["backend_kind"] for t in objects["linked_tables"]}
    assert kinds["tblRemote"] == "odbc"
    assert "hunter22" not in (out / "objects.json").read_text()
