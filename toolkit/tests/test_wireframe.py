"""toolkit wireframe on a hand-written synthetic model (fixtures/wireframe/; no client content)."""

from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from toolkit import cli, wireframe

FIX = Path(__file__).parent / "fixtures" / "wireframe"
MODEL = FIX / "model"
RULES = FIX / "rules.json"
CANARY = "CANARY-1"
SVG_NS = "{http://www.w3.org/2000/svg}"


def _run(eng, out, *extra, model=MODEL, rules=RULES, capfd=None):
    code = cli.main(
        [
            "wireframe",
            str(eng),
            "--model",
            str(model),
            "--workflows",
            str(rules),
            "--out",
            str(out),
            *extra,
        ]
    )
    summary = None
    if capfd is not None:
        lines = [x for x in capfd.readouterr().out.splitlines() if x.strip()]
        assert len(lines) == 1, lines
        summary = json.loads(lines[0])
    return code, summary


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir()) if p.is_file()}


def _controls(svg: Path) -> dict[str, list[str]]:
    root = ET.fromstring(svg.read_bytes())
    by_type: dict[str, list[str]] = {}
    for g in root.iter(f"{SVG_NS}g"):
        if g.get("class") == "ctl":
            by_type.setdefault(g.get("data-type"), []).append(g.get("data-name"))
    return by_type


def _texts(svg: Path) -> list[str]:
    root = ET.fromstring(svg.read_bytes())
    return [t.text or "" for t in root.iter(f"{SVG_NS}text")]


@pytest.fixture
def eng(make_engagement, tmp_path):
    return make_engagement(tmp_path, source={"kind": "accdt"})


@pytest.fixture
def out(eng, tmp_path):
    target = tmp_path / "before"
    code, _ = _run(eng, target)
    assert code == 0
    return target


def test_one_svg_per_form_and_an_index(out):
    assert sorted(p.name for p in out.iterdir()) == [
        "frmwidget.svg",
        "index.md",
        "sfrmwidgetparts.svg",
    ]


def test_control_counts_per_svg(out):
    widget = _controls(out / "frmwidget.svg")
    assert {k: len(v) for k, v in widget.items()} == {
        "Label": 4,  # lblHelp sits on the second tab page and is not drawn
        "CommandButton": 1,
        "TextBox": 1,
        "ComboBox": 1,
        "Tab": 1,
        "Subform": 1,
    }
    parts = _controls(out / "sfrmwidgetparts.svg")
    assert {k: len(v) for k, v in parts.items()} == {
        "Label": 2,
        "TextBox": 1,
        "ComboBox": 1,
        "Image": 1,
    }


def test_sections_captions_names_and_kinds(out):
    svg = out / "frmwidget.svg"
    root = ET.fromstring(svg.read_bytes())
    kinds = [g.get("data-kind") for g in root.iter(f"{SVG_NS}g") if g.get("class") == "section"]
    assert kinds == ["form_header", "detail", "form_footer"]
    texts = _texts(svg)
    assert wireframe.CAPTION in texts
    for caption in ("Widgets", "Widget code", "Colour", "Ready", "Save widget"):
        assert caption in texts
    assert "txtCode" in texts and "cboColour" in texts  # text and combo boxes show their names
    assert "subform: sfrmWidgetParts" in texts
    assert "Parts" in texts and "Notes & help" in texts  # tab page captions, access keys removed
    assert not any("Widget card" in t for t in texts)  # the form caption is never drawn
    assert "image" in _texts(out / "sfrmwidgetparts.svg")
    assert wireframe.CAPTION in _texts(out / "sfrmwidgetparts.svg")


def test_scrubbed_token_kept_as_is():
    form = json.loads((MODEL / "objects.json").read_text(encoding="utf-8"))["forms"][0]
    for ctl in form["controls"]:
        ctl["page"] = None  # draw every control, the one on the second page included
    text, _count = wireframe.render_form(form)
    root = ET.fromstring(text.encode("utf-8"))
    assert "Ask <EMAIL-1>" in [t.text for t in root.iter(f"{SVG_NS}text")]


def test_canary_never_appears(out):
    for name, data in _snapshot(out).items():
        assert CANARY.encode() not in data, name


def test_index_lists_workflows_forms_pictures_and_caption(out):
    text = (out / "index.md").read_text(encoding="utf-8")
    assert "## widget_upkeep: Widget upkeep" in text
    assert "![frmWidget](frmwidget.svg)" in text
    assert "![sfrmWidgetParts](sfrmwidgetparts.svg)" in text
    assert text.count(wireframe.CAPTION) == 2


def test_two_runs_identical_bytes(eng, out, tmp_path):
    second = tmp_path / "second"
    assert _run(eng, second)[0] == 0
    assert _snapshot(out) == _snapshot(second)
    assert _run(eng, out)[0] == 0
    assert _snapshot(out) == _snapshot(second)


def test_run_deletes_stale_svg(eng, out):
    stale = out / "frmgone.svg"
    stale.write_text("<svg/>", encoding="utf-8")
    keep = out / "notes.txt"
    keep.write_text("kept\n", encoding="utf-8")
    assert _run(eng, out)[0] == 0
    assert not stale.exists()
    assert keep.exists()


def test_check_passes_then_fails_and_writes_nothing(eng, out, quiet, capfd):
    code, summary = _run(eng, out, "--check", capfd=capfd)
    assert code == 0 and summary["status"] == "ok"

    svg = out / "frmwidget.svg"
    data = bytearray(svg.read_bytes())
    data[-2] = ord(" ") if data[-2] != ord(" ") else ord("x")  # a one-byte change
    svg.write_bytes(bytes(data))
    before = _snapshot(out)
    code, summary = _run(eng, out, "--check", capfd=capfd)
    assert code == 1 and summary["status"] == "failed"
    assert "changed: frmwidget.svg" in summary["messages"]
    assert _snapshot(out) == before

    assert _run(eng, out)[0] == 0
    capfd.readouterr()
    (out / "frmold.svg").write_text("<svg/>", encoding="utf-8")
    (out / "index.md").unlink()
    before = _snapshot(out)
    code, summary = _run(eng, out, "--check", capfd=capfd)
    assert code == 1
    assert "extra: frmold.svg" in summary["messages"]
    assert "missing: index.md" in summary["messages"]
    assert _snapshot(out) == before


def test_check_into_missing_directory_writes_nothing(eng, tmp_path, quiet, capfd):
    target = tmp_path / "absent"
    code, summary = _run(eng, target, "--check", capfd=capfd)
    assert code == 1
    assert "missing: index.md" in summary["messages"]
    assert not target.exists()


def test_missing_form_exits_1_naming_it(eng, tmp_path, quiet, capfd):
    rules = tmp_path / "rules.json"
    doc = json.loads(RULES.read_text(encoding="utf-8"))
    doc["workflows"][0]["forms"].append("frmNotThere")
    rules.write_text(json.dumps(doc), encoding="utf-8")
    target = tmp_path / "x"
    code, summary = _run(eng, target, rules=rules, capfd=capfd)
    assert code == 1 and summary["status"] == "failed"
    assert any("frmNotThere" in m for m in summary["messages"])
    assert not target.exists()


def test_model_without_layout_exits_2(eng, tmp_path, quiet, capfd):
    model = tmp_path / "model"
    shutil.copytree(MODEL, model)
    doc = json.loads((model / "objects.json").read_text(encoding="utf-8"))
    for form in doc["forms"]:
        for ctl in form["controls"]:
            for key in ("left", "top"):
                ctl.pop(key)
    (model / "objects.json").write_text(json.dumps(doc), encoding="utf-8")
    code, summary = _run(eng, tmp_path / "x", model=model, capfd=capfd)
    assert code == 2
    assert any(wireframe.LAYOUT_MESSAGE in m for m in summary["messages"])
    assert any("forms[].controls[].left" in m for m in summary["messages"])


@pytest.mark.parametrize("which", ["model", "rules"])
def test_missing_inputs_exit_2(eng, tmp_path, quiet, capfd, which):
    kwargs = {"model": tmp_path / "nope"} if which == "model" else {"rules": tmp_path / "no.json"}
    code, summary = _run(eng, tmp_path / "x", capfd=capfd, **kwargs)
    assert code == 2 and summary["status"] == "error"
    assert not (tmp_path / "x").exists()


def test_quiet_summary_has_counts_only(eng, tmp_path, quiet, capfd):
    code, summary = _run(eng, tmp_path / "q", capfd=capfd)
    assert code == 0
    assert summary["counts"] == {
        "workflows": 1,
        "forms": 2,
        "controls": 14,
        "files": 3,
        "written": 3,
        "deleted": 0,
    }
    assert summary["engagement"] == "demo-eng"


def test_number_format_and_slugs():
    assert wireframe.num(8.0) == "8"
    assert wireframe.num(1 / 3) == "0.33"
    assert wireframe.num(-0.001) == "0"
    names = wireframe.slugs(["frm_A", "frmA", "frm-a", "frmB"])
    assert names["frmB"] == "frmb"
    assert len(set(names.values())) == 4
