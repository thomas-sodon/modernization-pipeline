"""toolkit.engagement.load and `toolkit engagement-lint` (docs/contracts.md#engagement-loading)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from toolkit.cli import main
from toolkit.commands.engagement_lint import lint
from toolkit.engagement import EngagementError, load

PIPELINE = Path(__file__).resolve().parents[2]


def demo_copy(tmp_path: Path, name: str) -> Path:
    records = tmp_path / name
    records.mkdir()
    shutil.copy(
        PIPELINE / "engagements" / name / "engagement.json", records / "engagement.json"
    )
    paths = {
        "format_version": 1,
        "pipeline": str(tmp_path / "pipeline"),
        "target_repo": str(tmp_path / "target"),
        "target": str(tmp_path / "target" / "sub"),
        "private": str(tmp_path / "home" / "engagements" / name / "private"),
        "records": str(records),
        "env_file": str(tmp_path / "home" / ".config" / "modernization" / "env"),
    }
    (records / "paths.local.json").write_text(json.dumps(paths), encoding="utf-8")
    return records / "engagement.json"


def edit(eng_path: Path, fn) -> None:
    data = json.loads(eng_path.read_text())
    fn(data)
    eng_path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("name", ["northwind-demo", "invoicegen-demo"])
def test_demo_engagements_pass(tmp_path, name):
    eng = demo_copy(tmp_path, name)
    _ident, problems = lint(eng)
    assert problems == []
    loaded = load(eng)
    assert loaded["source"]["path"].startswith(
        str(tmp_path / "home" / "engagements" / name / "private")
    )
    assert loaded["db_name"] == name.replace("-", "_")


def test_template_fails_naming_fields(tmp_path):
    records = tmp_path / "tpl"
    records.mkdir()
    shutil.copy(
        PIPELINE / "engagements" / "_template" / "engagement.json",
        records / "engagement.json",
    )
    paths = {
        "format_version": 1,
        "pipeline": str(tmp_path / "pipeline"),
        "target_repo": str(tmp_path / "target"),
        "target": str(tmp_path / "target"),
        "private": str(tmp_path / "private"),
        "records": str(records),
        "env_file": str(tmp_path / "env"),
    }
    (records / "paths.local.json").write_text(json.dumps(paths), encoding="utf-8")
    eng = records / "engagement.json"
    _ident, problems = lint(eng)
    text = "\n".join(problems)
    for field in ("id", "source.kind", "app.slug"):
        assert f"engagement: {field}:" in text
    assert main(["engagement-lint", str(eng)]) == 1
    with pytest.raises(EngagementError):
        load(eng)


def test_unknown_key_fails_underscore_key_passes(tmp_path, make_engagement):
    eng = make_engagement(tmp_path)
    assert lint(eng)[1] == []
    edit(eng, lambda d: d["source"].update({"_note": {"anything": [1, "<x>"]}}))
    edit(eng, lambda d: d.update({"_help": "<marker inside help is fine>"}))
    assert lint(eng)[1] == []
    edit(eng, lambda d: d["source"].update({"surprise": 1}))
    problems = lint(eng)[1]
    assert any("source" in p and "surprise" in p for p in problems)
    edit(eng, lambda d: (d["source"].pop("surprise"), d.update({"unknown_top": True})))
    assert any("unknown_top" in p for p in lint(eng)[1])


def test_execution_enum(tmp_path, make_engagement):
    eng = make_engagement(tmp_path, data_policy={"execution": "anywhere"})
    problems = lint(eng)[1]
    assert any("data_policy.execution" in p for p in problems)


@pytest.mark.parametrize(
    "mutate,field",
    [
        (
            lambda d: d["secrets"].update(
                {"stage1_target_url_ref": "file:/etc/passwd"}
            ),
            "secrets.stage1_target_url_ref",
        ),
        (
            lambda d: d["secrets"].update({"github_push": {"ref": "env:lower_case"}}),
            "secrets.github_push.ref",
        ),
        (
            lambda d: d["source"].update(
                {
                    "backends": [
                        {"path": "/x.accdb", "sha256": None, "password_ref": "plain"}
                    ]
                }
            ),
            "source.backends[0].password_ref",
        ),
        (
            lambda d: d["source"].update(
                {"external_links": [{"name": "t", "type": "odbc-oracle"}]}
            ),
            "source.external_links[0]",
        ),
        (
            lambda d: d["environment"]["windows_box"].update(
                {"copies": [{"path": "C:/x"}]}
            ),
            "environment.windows_box.copies[0]",
        ),
        (
            lambda d: d["environment"]["windows_box"].update(
                {"copies": [{"path": "C:/x", "what": "copy", "extra": 1}]}
            ),
            "environment.windows_box.copies[0]",
        ),
    ],
    ids=[
        "ref-scheme",
        "ref-lowercase",
        "backend-ref",
        "link-type",
        "copies-missing-what",
        "copies-extra-key",
    ],
)
def test_bad_values_fail(tmp_path, make_engagement, mutate, field):
    eng = make_engagement(tmp_path)
    edit(eng, mutate)
    problems = lint(eng)[1]
    assert any(field in p for p in problems), problems


def test_good_refs_pass(tmp_path, make_engagement):
    eng = make_engagement(
        tmp_path,
        secrets={
            "github_push": {
                "ref": "env:GITHUB_PUSH_C2026001",
                "scope": "repo",
                "expires": "2027-01-01",
            },
            "stage1_target_url_ref": "pass:clients/c1/stage1",
        },
    )
    assert lint(eng)[1] == []


def test_records_must_match_paths(tmp_path, make_engagement):
    eng = make_engagement(tmp_path, records="/somewhere/else")
    problems = lint(eng)[1]
    assert any(p.startswith("engagement: records:") for p in problems)
    with pytest.raises(EngagementError) as exc:
        load(eng)
    assert "records" in str(exc.value)


def test_client_records_inside_pipeline_fails(tmp_path, make_engagement):
    records = str(tmp_path / "pipeline" / "engagements" / "demo-eng")
    eng = make_engagement(
        tmp_path, bootstrap=False, records=records, data_policy={"execution": "local"}
    )
    assert any("outside paths.pipeline" in p for p in lint(eng)[1])


def test_missing_paths_local_exits_2(tmp_path, make_engagement, quiet, capfd):
    eng = make_engagement(tmp_path)
    (eng.parent / "paths.local.json").unlink()
    assert main(["engagement-lint", str(eng)]) == 2
    out = capfd.readouterr().out
    assert json.loads(out)["exit_code"] == 2
    with pytest.raises(EngagementError):
        load(eng)


def test_unresolved_placeholder_and_marker(tmp_path, make_engagement):
    eng = make_engagement(tmp_path, source={"path": "{{p.data}}/x.accdb"})
    with pytest.raises(EngagementError) as exc:
        load(eng)
    assert "source.path: unresolved {{p.data}}" in str(exc.value)
    eng = make_engagement(tmp_path, app={"project_name": "<Display name>"})
    with pytest.raises(EngagementError) as exc:
        load(eng)
    assert "app.project_name" in str(exc.value)
    assert "Display name" not in str(exc.value)


def test_db_name_derived_and_checked(tmp_path, make_engagement):
    eng = make_engagement(tmp_path, id="c2026001")
    assert load(eng)["db_name"] == "c2026001"
    eng = make_engagement(tmp_path, id="demo-eng", db_name="Bad-Name")
    assert any("db_name" in p for p in lint(eng)[1])


def test_reserved_slug_and_compiled_only(tmp_path, make_engagement):
    eng = make_engagement(tmp_path, app={"slug": "django"})
    assert any("slug_reserved" in p for p in lint(eng)[1])
    eng = make_engagement(tmp_path, source={"compiled_only": True})
    assert any("compiled_only" in p for p in lint(eng)[1])


def test_pins_filled_from_lock(tmp_path, make_engagement):
    eng = make_engagement(
        tmp_path,
        source={
            "data_copy": "{{p.private}}/sources/data/be.accdb",
            "backends": [
                {
                    "path": "{{p.private}}/sources/data/be.accdb",
                    "sha256": None,
                    "password_ref": None,
                }
            ],
        },
    )
    lock = {
        "format_version": 1,
        "recorded": "2026-10-16",
        "source": {
            "path": "sources/app.accdb",
            "bytes": 10,
            "sha256": "a" * 64,
            "git": None,
        },
        "data_copy": {"path": "sources/data/be.accdb", "bytes": 20, "sha256": "b" * 64},
        "backends": [
            {"path": "sources/data/be.accdb", "bytes": 20, "sha256": "c" * 64}
        ],
    }
    (eng.parent / "source.lock.json").write_text(json.dumps(lock), encoding="utf-8")
    (eng.parent / "inventory.lock.json").write_text(
        json.dumps({"format_version": 1, "tables": 3}), encoding="utf-8"
    )
    loaded = load(eng)
    assert loaded["source"]["sha256"] == "a" * 64
    assert loaded["source"]["bytes"] == 10
    assert loaded["source"]["data_copy_sha256"] == "b" * 64
    assert loaded["source"]["backends"][0]["sha256"] == "c" * 64
    assert loaded["inventory_lock"]["tables"] == 3
    # engagement values win
    edit(eng, lambda d: d["source"].update({"sha256": "d" * 64}))
    assert load(eng)["source"]["sha256"] == "d" * 64


def test_underscore_keys_ignored_and_dropped(tmp_path, make_engagement):
    eng = make_engagement(tmp_path, _anything="{{p.unknown}} <marker>")
    loaded = load(eng)
    assert "_anything" not in loaded
