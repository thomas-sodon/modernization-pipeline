"""S6.report: `purge` (docs/contracts.md#databases, #records, cli-registry).

Everything runs in tmp_path with an injected fake database server per dialect: no real database
is touched. The private tree, the engagement and source.lock.json are synthetic.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import jsonschema
import pytest

from toolkit import cli
from toolkit import purge as pg

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "schemas" / "summary.schema.json").read_text(
        encoding="utf-8"
    )
)
PG_URL = "postgresql://fake:fake@127.0.0.1:1"
MSSQL_URL = "mssql://fake:fake@127.0.0.1:2"
OURS = [
    "mpdata_demo_eng_stage1",
    "mpdata_demo_eng_gate",
    "mpdata_test_demo_eng_s6_report",
    "demo_eng_dev",
    "demo_eng",
    "test_demo_eng_s1",
    "test_demo_eng_s5_feature_orders",
]
KEPT = [
    "postgres",
    "testxdemo_eng_s1",  # matches test_demo_eng_* only when _ is a wildcard
    "mpdataxdemo_eng_stage1",
    "test_demo_eng",  # no suffix: not a pattern
    "demo_eng_other",  # neither <db> nor <db>_dev
    "mpdata_demo_eng_two_stage1",  # belongs to engagement demo_eng_two
    "mpdata_test_demo_eng_two_s1",
    "test_demo_eng_two_s1",
    "other_app",
]
COPY = {
    "path": "C:\\mp\\demo-eng\\frontend-relinked.accdb",
    "what": "relinked front end",
}


class FakeServer:
    def __init__(self, names, answers=True):
        self.names = list(names)
        self.answers = answers
        self.dropped: list[str] = []

    def list_databases(self):
        if not self.answers:
            raise ConnectionRefusedError("synthetic: not answering")
        return list(self.names)

    def drop(self, name):
        self.dropped.append(name)
        self.names.remove(name)

    def close(self):
        pass


@pytest.fixture
def servers(monkeypatch):
    made = {
        "postgresql": FakeServer(OURS + KEPT),
        "mssql": FakeServer(["master", "mpdata_demo_eng_stage1", "test_demo_eng_s2"]),
    }
    seen = []

    def factory(dialect, url_text):
        seen.append((dialect, url_text))
        return made[dialect]

    monkeypatch.setattr(pg, "connect", factory)
    made["seen"] = seen
    return made


@pytest.fixture
def setup(tmp_path, make_engagement, monkeypatch, quiet):
    for name in ("PG_URL", "MSSQL_URL", "MP_DATA_PG_URL", "MP_DATA_MSSQL_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MP_DATA_PG_URL", PG_URL)
    monkeypatch.setenv("MP_DATA_MSSQL_URL", MSSQL_URL)
    monkeypatch.setenv("MP_OTHER_DB_NAMES", "demo_eng_two,unrelated")
    box = {
        "available": True,
        "host": None,
        "access_build": None,
        "client_data_allowed": True,
        "disk_encrypted": True,
        "copies": [COPY],
    }
    eng = make_engagement(tmp_path, environment={"windows_box": box})
    paths = json.loads((eng.parent / "paths.local.json").read_text(encoding="utf-8"))
    private = Path(paths["private"])
    files = {
        "sources/app.accdb": b"x" * 10,
        "datasets/engagement/tables/Orders.csv": b"1,2\r\n",
        "logs/tmp-abc/scratch": b"abc",
        "out/model/schema.json": b"{}",
        "synthetic/results/S6/junit.xml": b"<x/>",
        "scrub-map.json": b"{}",
    }
    for rel, data in files.items():
        p = private / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    lock = {
        "format_version": 1,
        "recorded": "2026-01-01",
        "source": {
            "path": "sources/app.accdb",
            "bytes": 10,
            "sha256": "ab" * 32,
            "git": None,
        },
        "data_copy": None,
        "backends": [],
    }
    (eng.parent / "source.lock.json").write_text(json.dumps(lock), encoding="utf-8")
    return eng, private, Path(paths["records"])


def run_cli(capfd, *argv) -> tuple[int, dict]:
    capfd.readouterr()
    code = cli.main([str(a) for a in argv])
    out, _err = capfd.readouterr()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1, out
    summary = json.loads(lines[0])
    jsonschema.validate(summary, SCHEMA)
    return code, summary


def today() -> str:
    return dt.datetime.now(dt.UTC).date().isoformat()


def test_dry_run_deletes_nothing(setup, servers, capfd):
    eng, private, records = setup
    code, s = run_cli(capfd, "purge", eng, "--dry-run")
    assert code == 0, s
    assert (private / "sources" / "app.accdb").is_file()
    assert servers["postgresql"].dropped == [] and servers["mssql"].dropped == []
    assert not list(records.glob("destruction-record-*.md"))
    assert s["counts"]["files"] == 6
    assert s["counts"]["bytes"] == 10 + 5 + 3 + 2 + 4 + 2
    assert s["counts"]["databases"] == len(OURS) + 2
    assert "paths" not in s
    for name in OURS:
        assert f"would drop postgresql {name}" in s["messages"]
    assert "would drop mssql test_demo_eng_s2" in s["messages"]
    assert f"delete by hand: {COPY['path']}" in s["messages"]
    # stage1's local: resolution: the MP_DATA_ URL of each dialect
    assert servers["seen"] == [("postgresql", PG_URL), ("mssql", MSSQL_URL)]


def test_real_run_deletes_drops_and_records(setup, servers, capfd):
    eng, private, records = setup
    code, s = run_cli(capfd, "purge", eng)
    assert code == 0, s
    assert not private.exists()
    assert sorted(servers["postgresql"].dropped) == sorted(OURS)
    assert sorted(servers["postgresql"].names) == sorted(KEPT)
    assert sorted(servers["mssql"].dropped) == [
        "mpdata_demo_eng_stage1",
        "test_demo_eng_s2",
    ]
    record = records / f"destruction-record-{today()}.md"
    assert s["paths"] == [str(record)]
    text = record.read_text(encoding="utf-8")
    assert text.startswith("# Destruction record: demo-eng\n")
    assert f"date: {today()}\n" in text
    for heading in (
        "## Deleted",
        "## Databases dropped",
        "## Servers not checked",
        "## Sources",
        "## Copies to delete by hand",
    ):
        assert f"\n{heading}\n" in text
    assert "| private/sources | 1 | 10 |" in text
    assert "| private/scrub-map.json | 1 | 2 |" in text
    assert "| postgresql | mpdata_demo_eng_gate |" in text
    assert "| mssql | test_demo_eng_s2 |" in text
    assert f"| sources/app.accdb | {'ab' * 32} |" in text
    assert f"| {COPY['path']} | {COPY['what']} |" in text
    assert "## Servers not checked\nnone\n" in text
    for kept in KEPT:
        assert f"| {kept} |" not in text


def test_mount_point_is_emptied_and_kept(setup, servers, capfd, monkeypatch):
    eng, private, _records = setup
    real = os.path.ismount
    monkeypatch.setattr(os.path, "ismount", lambda p: Path(p) == private or real(p))
    code, _s = run_cli(capfd, "purge", eng)
    assert code == 0
    assert private.is_dir()
    assert list(private.iterdir()) == []


def test_server_not_answering_is_named_and_purge_goes_on(setup, servers, capfd):
    eng, private, records = setup
    servers["mssql"].answers = False
    code, s = run_cli(capfd, "purge", eng)
    assert code == 0, s
    assert (
        "not checked mssql: server did not answer (ConnectionRefusedError)"
        in s["messages"]
    )
    assert sorted(servers["postgresql"].dropped) == sorted(OURS)
    assert not private.exists()
    text = (records / f"destruction-record-{today()}.md").read_text(encoding="utf-8")
    assert "| mssql | server did not answer (ConnectionRefusedError) |" in text


def test_dialect_without_url_is_named(setup, servers, capfd, monkeypatch):
    eng, _private, _records = setup
    monkeypatch.delenv("MP_DATA_MSSQL_URL")
    code, s = run_cli(capfd, "purge", eng, "--dry-run")
    assert code == 0
    assert any(
        m.startswith(
            "not checked mssql: no server URL (MP_DATA_MSSQL_URL and MSSQL_URL unset)"
        )
        for m in s["messages"]
    )
    assert s["counts"]["servers_not_checked"] == 1
    assert [d for d, _u in servers["seen"]] == ["postgresql"]


def test_other_engagement_extending_the_name_is_kept():
    others = ["demo_eng_two"]
    assert pg.owned("mpdata_demo_eng_stage1", "demo_eng", others)
    assert not pg.owned("mpdata_demo_eng_two_stage1", "demo_eng", others)
    assert not pg.owned("test_demo_eng_two_s1", "demo_eng", others)
    # without the other engagement the same name would match
    assert pg.owned("test_demo_eng_two_s1", "demo_eng", [])
    assert not pg.owned("testxdemo_eng_s1", "demo_eng", [])
    assert not pg.owned("mpdata_testxdemo_eng_s1", "demo_eng", [])


def test_unset_other_db_names_exits_2(setup, servers, capfd, monkeypatch):
    eng, private, _records = setup
    monkeypatch.delenv("MP_OTHER_DB_NAMES")
    code, s = run_cli(capfd, "purge", eng, "--dry-run")
    assert code == 2
    assert "MP_OTHER_DB_NAMES" in s["messages"][0]
    assert private.is_dir()


def test_empty_other_db_names_means_none(setup, servers, capfd, monkeypatch):
    eng, _private, _records = setup
    monkeypatch.setenv("MP_OTHER_DB_NAMES", "")
    code, s = run_cli(capfd, "purge", eng, "--dry-run")
    assert code == 0
    assert "would drop postgresql test_demo_eng_two_s1" in s["messages"]


def test_symlinked_private_is_refused(setup, servers, capfd, tmp_path):
    eng, private, _records = setup
    elsewhere = tmp_path / "elsewhere"
    private.rename(elsewhere)
    private.symlink_to(elsewhere)
    code, s = run_cli(capfd, "purge", eng)
    assert code == 2
    assert "symlink" in s["messages"][0]
    assert (elsewhere / "sources" / "app.accdb").is_file()
    assert servers["postgresql"].dropped == []


def test_wrongly_named_private_is_refused(
    tmp_path, make_engagement, servers, capfd, quiet, monkeypatch
):
    monkeypatch.setenv("MP_OTHER_DB_NAMES", "")
    wrong = tmp_path / "home" / "data" / "private"
    eng = make_engagement(tmp_path, paths={"private": str(wrong)})
    (wrong / "keep.txt").write_text("synthetic", encoding="utf-8")
    code, s = run_cli(capfd, "purge", eng)
    assert code == 2
    assert "/engagements/demo-eng/private" in s["messages"][0]
    assert (wrong / "keep.txt").is_file()


def test_record_lists_none_when_nothing_matches(tmp_path, make_engagement, monkeypatch):
    for name in ("PG_URL", "MSSQL_URL", "MP_DATA_PG_URL", "MP_DATA_MSSQL_URL"):
        monkeypatch.delenv(name, raising=False)
    from toolkit.engagement import load

    eng = load(make_engagement(tmp_path))
    code, _plan, record, problems = pg.run(
        eng, False, env={"MP_OTHER_DB_NAMES": ""}, factory=None, today="2027-03-01"
    )
    assert code == 0 and problems == []
    assert record.name == "destruction-record-2027-03-01.md"
    text = record.read_text(encoding="utf-8")
    assert "## Databases dropped\nnone\n" in text
    assert "## Copies to delete by hand\nnone\n" in text
    assert "| postgresql | no server URL (MP_DATA_PG_URL and PG_URL unset) |" in text
    assert "| sources/app.accdb | unknown |" in text
