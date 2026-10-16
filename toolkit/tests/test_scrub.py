"""Scrub tokens, the token map and `toolkit scrub --check` (decision D8). Synthetic strings only."""

from __future__ import annotations

import json
import os

import pytest

from toolkit import cli
from toolkit import scrub as scrub_mod
from toolkit.scrub import Scrubber, check_dir, findings, locked_map


@pytest.mark.parametrize(
    ("text", "kind", "kept"),
    [
        ("mail ops.team@example.invalid today", "EMAIL", "mail "),
        ("call +1 555 010 0199 now", "PHONE", "call "),
        ("call (555) 010-0199 now", "PHONE", "call "),
        ("ODBC;DSN=w;UID=app_user;PWD=Sup3rS3cret;", "SECRET", "ODBC;DSN=w;UID="),
        (
            "Provider=x;Password=letmein99;Data Source=y",
            "SECRET",
            "Provider=x;Password=",
        ),
        ("https://deploy:hunter2@git.example.invalid/repo.git", "SECRET", "https://"),
        ("https://api.example.invalid/v1?token=abcd1234efgh", "URL", ""),
        ("key AKIAABCDEFGHIJKLMNOP in config", "SECRET", "key "),
        ('Const API_KEY = "zz-910-qq-77"', "SECRET", "Const API_KEY = "),
        ('strPassword = "opensesame"', "SECRET", "strPassword = "),
    ],
)
def test_scrub_kinds(text, kind, kept):
    s = Scrubber()
    out = s.text(text)
    assert f"<{kind}-1>" in out
    assert out.startswith(kept)
    assert findings(out) == []
    assert s.hits.get(kind, 0) >= 1


def test_private_key_block():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu\n-----END RSA PRIVATE KEY-----"
    out = Scrubber().text("x " + pem + " y")
    assert out == "x <SECRET-1> y"


@pytest.mark.parametrize(
    "text",
    [
        "Me.txtPassword = Me.txtConfirm",
        'If strPassword = "" Then',
        "x = 2024-11-27 and version 16.0.17928.20114",
        'Range("A1:B20").Value = 1234567',
        "lngTotal = 100 + 200 + 300",
        "IP 192.168.100.20 is local",
        "<EMAIL-3> and <PHONE-2> stay as they are",
    ],
)
def test_no_false_positives(text):
    assert Scrubber().text(text) == text
    assert findings(text) == []


def test_same_original_same_token_and_numbering():
    s = Scrubber()
    a = s.text("a@example.invalid, b@example.invalid, a@example.invalid")
    assert a == "<EMAIL-1>, <EMAIL-2>, <EMAIL-1>"
    s2 = Scrubber(s.mapping())
    assert s2.text("b@example.invalid c@example.invalid") == "<EMAIL-2> <EMAIL-3>"


def test_obj_scrubs_values_and_keys():
    s = Scrubber()
    out = s.obj({"z": ["x@example.invalid"], "y@example.invalid": 1, "n": 5})
    # keys are visited in sorted order, each key before its value
    assert out == {"<EMAIL-1>": 1, "n": 5, "z": ["<EMAIL-2>"]}


def test_check_dir_names_file_and_path_never_value(tmp_path):
    (tmp_path / "code").mkdir()
    (tmp_path / "objects.json").write_text(
        json.dumps(
            {"forms": [{"caption": "write to boss@example.invalid"}], "ok": "fine"}
        )
    )
    (tmp_path / "code" / "m.txt").write_text("a\nPWD=abc123xyz;\n")
    hits = check_dir(tmp_path)
    assert hits == [
        ("code/m.txt", "line 2", "SECRET"),
        ("objects.json", "$.forms[0].caption", "EMAIL"),
    ]


def test_scrub_check_command(tmp_path, capfd, quiet):
    (tmp_path / "schema.json").write_text(json.dumps({"note": "ring 555-010-0199 x"}))
    code = cli.main(["scrub", "--check", str(tmp_path)])
    out, _err = capfd.readouterr()
    summary = json.loads(out)
    assert code == 1 and summary["status"] == "failed"
    assert summary["messages"] == ["schema.json: $.note: PHONE"]
    assert "555" not in out
    (tmp_path / "schema.json").write_text(json.dumps({"note": "ring <PHONE-1> x"}))
    assert cli.main(["scrub", "--check", str(tmp_path)]) == 0
    capfd.readouterr()
    assert cli.main(["scrub", "--check", str(tmp_path / "missing")]) == 2
    capfd.readouterr()
    assert cli.main(["scrub", str(tmp_path)]) == 2
    capfd.readouterr()


def test_locked_map_empty_file_is_no_map(tmp_path):
    path = tmp_path / "scrub-map.json"
    path.write_bytes(b"")
    with locked_map(path) as (existing, save):
        assert existing == {}
        save({"<EMAIL-1>": "q@example.invalid"})
    doc = json.loads(path.read_text())
    assert doc == {"format_version": 1, "tokens": {"<EMAIL-1>": "q@example.invalid"}}
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    with locked_map(path) as (existing, _save):
        assert existing == {"<EMAIL-1>": "q@example.invalid"}


def test_atomic_write_falls_back_to_in_place(tmp_path, monkeypatch):
    path = tmp_path / "scrub-map.json"
    path.write_text("old")
    inode = path.stat().st_ino

    def refuse(*_a, **_k):
        raise OSError(16, "Device or resource busy")

    monkeypatch.setattr(scrub_mod.os, "replace", refuse)
    scrub_mod.atomic_write(path, b"new")
    assert path.read_bytes() == b"new"
    assert path.stat().st_ino == inode  # rewritten in place, as on a bind mount
    assert sorted(os.listdir(tmp_path)) == ["scrub-map.json"]


def test_atomic_write_replaces(tmp_path):
    path = tmp_path / "workbook" / "cells.json"
    scrub_mod.atomic_write(path, b"{}\n")
    assert path.read_bytes() == b"{}\n"
    assert sorted(os.listdir(path.parent)) == ["cells.json"]
