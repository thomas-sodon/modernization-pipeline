"""toolkit fetch (docs/contracts.md#source-lock). Fixture bytes are served from a local
http.server and a local bare git repo; the real sources are never downloaded."""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import threading
from pathlib import Path

import pytest

from toolkit import fetch
from toolkit.cli import main
from toolkit.engagement import load

from .conftest import git

PAYLOAD = b"synthetic source bytes for fetch tests\n" * 100
SHA = hashlib.sha256(PAYLOAD).hexdigest()
MD5_HEX = hashlib.md5(PAYLOAD).hexdigest()
JAR = b"PK synthetic jar bytes"
JAR_SHA = hashlib.sha256(JAR).hexdigest()
HEADERS = {
    "Last-Modified": "Wed, 27 Nov 2024 23:34:51 GMT",
    "ETag": '"0xSYNTHETIC1"',
    "Content-MD5": base64.b64encode(bytes.fromhex(MD5_HEX)).decode(),
}


class Server:
    def __init__(self):
        self.files = {}
        self.headers = dict(HEADERS)
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, body: bool):
                outer.requests.append((self.command, self.path))
                data = outer.files.get(self.path)
                if data is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                for k, v in outer.headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if body:
                    self.wfile.write(data)

            def do_HEAD(self):
                self._send(False)

            def do_GET(self):
                self._send(True)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def gets(self):
        return [r for r in self.requests if r[0] == "GET"]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(monkeypatch, tmp_path):
    srv = Server()
    srv.files["/maven/org/example/demo/1.0/demo-1.0.jar"] = JAR
    srv.files["/src/app.accdt"] = PAYLOAD
    monkeypatch.setattr(
        fetch,
        "jar_specs",
        lambda stack=None: [{"coord": "org.example:demo:1.0", "sha256": JAR_SHA}],
    )
    monkeypatch.setattr(
        fetch,
        "mirrors",
        lambda stack=None: [srv.base + "/missing-mirror", srv.base + "/maven"],
    )
    vendor = tmp_path / "vendor"
    monkeypatch.setattr(fetch, "default_vendor_dir", lambda: vendor)
    yield srv
    srv.close()


def url_engagement(make_engagement, tmp_path, server, **source):
    src = {
        "kind": "accdt",
        "path": "{{p.private}}/sources/app.accdt",
        "url": server.base + "/src/app.accdt",
        "bytes": len(PAYLOAD),
        "sha256": SHA,
        "last_modified": HEADERS["Last-Modified"],
        "etag": "0xSYNTHETIC1",
        "content_md5": MD5_HEX,
    }
    src.update(source)
    return make_engagement(tmp_path, source=src)


def test_url_source_downloads_and_verifies(tmp_path, make_engagement, server):
    eng = url_engagement(make_engagement, tmp_path, server)
    result = fetch.verify(load(eng), record_if_unset=False)
    assert result.code == 0, result.messages
    assert result.sha256s["source"] == SHA
    assert ("HEAD", "/src/app.accdt") in server.requests
    assert len([r for r in server.gets() if r[1] == "/src/app.accdt"]) == 1
    assert (tmp_path / "vendor" / "demo-1.0.jar").read_bytes() == JAR
    # present file: only hashed, no network
    server.requests.clear()
    result = fetch.verify(load(eng), record_if_unset=False)
    assert result.code == 0
    assert [r for r in server.requests if r[1].startswith("/src/")] == []
    assert result.counts["jars_present"] == 1


def test_drifted_etag_exits_1_without_download(
    tmp_path, make_engagement, server, quiet, capfd
):
    server.headers["ETag"] = '"0xDRIFTED"'
    eng = url_engagement(make_engagement, tmp_path, server)
    code = main(["fetch", str(eng), "--verify"])
    out = capfd.readouterr().out
    assert code == 1
    summary = json.loads(out)
    assert summary["status"] == "failed"
    assert any("ETag" in m for m in summary["messages"])
    assert server.gets() == []
    assert not (
        tmp_path
        / "home"
        / "engagements"
        / "demo-eng"
        / "private"
        / "sources"
        / "app.accdt"
    ).exists()


@pytest.mark.parametrize(
    "header,value",
    [
        ("Last-Modified", "Thu, 28 Nov 2024 00:00:00 GMT"),
        ("Content-MD5", base64.b64encode(b"0" * 16).decode()),
    ],
)
def test_other_header_drift(tmp_path, make_engagement, server, header, value):
    server.headers[header] = value
    eng = url_engagement(make_engagement, tmp_path, server)
    result = fetch.verify(load(eng), record_if_unset=False)
    assert result.code == 1
    assert server.gets() == []


def test_download_mismatch_exits_1_and_keeps_nothing(tmp_path, make_engagement, server):
    eng = url_engagement(make_engagement, tmp_path, server, sha256="0" * 64)
    result = fetch.verify(load(eng), record_if_unset=False)
    assert result.code == 1
    src = Path(load(eng)["source"]["path"])
    assert not src.exists()
    assert list(src.parent.iterdir()) == []


def test_present_file_mismatch_never_rewrites_pins(
    tmp_path, make_engagement, server, quiet, capfd
):
    eng = url_engagement(make_engagement, tmp_path, server)
    src = Path(load(eng)["source"]["path"])
    src.parent.mkdir(parents=True)
    src.write_bytes(PAYLOAD + b"changed")
    before = eng.read_bytes()
    code = main(["fetch", str(eng), "--verify", "--record-if-unset"])
    assert code == 1
    assert eng.read_bytes() == before
    assert not (eng.parent / "source.lock.json").exists()
    assert server.requests == [] or all(
        not r[1].startswith("/src/") for r in server.requests
    )


def local_engagement(make_engagement, tmp_path, **source):
    eng = make_engagement(
        tmp_path,
        source={
            "kind": "accdb",
            "path": "{{p.private}}/sources/front.accdb",
            "data_copy": "{{p.private}}/sources/data/back.accdb",
            "backends": [
                {
                    "path": "{{p.private}}/sources/data/back.accdb",
                    "sha256": None,
                    "password_ref": None,
                }
            ],
            **source,
        },
    )
    private = tmp_path / "home" / "engagements" / "demo-eng" / "private"
    (private / "sources" / "data").mkdir(parents=True, exist_ok=True)
    (private / "sources" / "front.accdb").write_bytes(b"front end")
    (private / "sources" / "data" / "back.accdb").write_bytes(b"back end")
    return eng


def test_local_source_without_pins_exits_2(
    tmp_path, make_engagement, server, quiet, capfd
):
    eng = local_engagement(make_engagement, tmp_path)
    assert main(["fetch", str(eng), "--verify"]) == 2
    summary = json.loads(capfd.readouterr().out)
    assert summary["status"] == "error"


def test_record_if_unset_writes_lock_then_verifies(
    tmp_path, make_engagement, server, quiet, capfd
):
    eng = local_engagement(make_engagement, tmp_path)
    assert main(["fetch", str(eng), "--verify", "--record-if-unset"]) == 0
    summary = json.loads(capfd.readouterr().out)
    assert summary["status"] == "recorded"
    assert "recorded; approve at the S0 gate" in summary["messages"]
    lock = json.loads((eng.parent / "source.lock.json").read_text())
    assert lock["format_version"] == 1
    assert lock["source"]["path"] == "sources/front.accdb"
    assert lock["source"]["sha256"] == hashlib.sha256(b"front end").hexdigest()
    assert lock["source"]["git"] is None
    assert lock["data_copy"]["path"] == "sources/data/back.accdb"
    assert lock["backends"][0]["sha256"] == hashlib.sha256(b"back end").hexdigest()
    loaded = load(eng)
    assert loaded["source"]["data_copy_sha256"] == lock["data_copy"]["sha256"]
    # a second run compares with the lock (status ok), and a change fails without rewriting it
    assert main(["fetch", str(eng), "--verify", "--record-if-unset"]) == 0
    assert json.loads(capfd.readouterr().out)["status"] == "ok"
    before = (eng.parent / "source.lock.json").read_bytes()
    (
        tmp_path
        / "home"
        / "engagements"
        / "demo-eng"
        / "private"
        / "sources"
        / "data"
        / "back.accdb"
    ).write_bytes(b"other")
    assert main(["fetch", str(eng), "--verify", "--record-if-unset"]) == 1
    assert (eng.parent / "source.lock.json").read_bytes() == before


def test_git_source(tmp_path, make_engagement, server):
    work = tmp_path / "work"
    git("init", "-q", "-b", "master", str(work))
    (work / "Book.xlsm").write_bytes(b"synthetic workbook")
    git("-C", str(work), "add", ".")
    git("-C", str(work), "commit", "-q", "-m", "one")
    commit = git("-C", str(work), "rev-parse", "HEAD").stdout.strip()
    (work / "Book.xlsm").write_bytes(b"later change")
    git("-C", str(work), "commit", "-q", "-am", "two")
    bare = tmp_path / "remote.git"
    git("clone", "-q", "--bare", str(work), str(bare))
    eng = make_engagement(
        tmp_path,
        source={
            "kind": "xlsm",
            "git": str(bare),
            "branch": "master",
            "commit": commit,
            "clone_dir": "{{p.private}}/sources/Book",
            "path": "{{p.private}}/sources/Book/Book.xlsm",
            "bytes": len(b"synthetic workbook"),
            "sha256": hashlib.sha256(b"synthetic workbook").hexdigest(),
        },
    )
    result = fetch.verify(load(eng), record_if_unset=False)
    assert result.code == 0, result.messages
    # wrong commit pin
    eng2 = make_engagement(
        tmp_path / "b",
        source={**json.loads(eng.read_text())["source"], "commit": "0" * 40},
    )
    result = fetch.verify(load(eng2), record_if_unset=False)
    assert result.code == 1


def test_fetch_jars_only(tmp_path, server, quiet, capfd):
    assert main(["fetch", "--jars"]) == 0
    summary = json.loads(capfd.readouterr().out)
    assert summary["sha256s"]["demo-1.0.jar"] == JAR_SHA
    assert (tmp_path / "vendor" / "demo-1.0.jar").exists()
    # a wrong jar on disk is replaced
    (tmp_path / "vendor" / "demo-1.0.jar").write_bytes(b"tampered")
    assert main(["fetch", "--jars"]) == 0
    assert (tmp_path / "vendor" / "demo-1.0.jar").read_bytes() == JAR


def test_jar_with_wrong_hash_fails(tmp_path, server, monkeypatch):
    monkeypatch.setattr(
        fetch,
        "jar_specs",
        lambda stack=None: [{"coord": "org.example:demo:1.0", "sha256": "1" * 64}],
    )
    result = fetch.ensure_jars()
    assert result.code == 1
    assert not (tmp_path / "vendor" / "demo-1.0.jar").exists()


def test_usage_errors(quiet, capfd):
    assert main(["fetch"]) == 2
    capfd.readouterr()
    assert main(["fetch", "--jars", "--verify"]) == 2


def test_real_jar_specs_are_pinned():
    specs = fetch.jar_specs()
    assert {s["coord"].split(":")[1] for s in specs} == {
        "ucanaccess",
        "jackcess",
        "hsqldb",
    }
    assert all(len(s["sha256"]) == 64 for s in specs)
    assert fetch.mirrors()[0].startswith(
        "https://maven-central.storage-download.googleapis.com"
    )
    assert fetch.mirrors()[1] == "https://repo1.maven.org/maven2"
    assert fetch.jar_path("io.github.spannm:ucanaccess:5.1.8") == (
        "io/github/spannm/ucanaccess/5.1.8/ucanaccess-5.1.8.jar",
        "ucanaccess-5.1.8.jar",
    )
