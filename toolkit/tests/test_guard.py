"""toolkit guard (S0.guard): forbidden-file hooks and copy detection against the corpus.

Everything runs on temporary git repos and a synthetic vcs-export engagement built from
toolkit/tests/fixtures/guard/ under tmp_path; never on a real source."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from toolkit import guard
from toolkit.engagement import load

from .conftest import PIPELINE, git

FIX = Path(__file__).resolve().parent / "fixtures" / "guard"
MODULE = FIX / "vcs" / "modules" / "modGreenhouse.bas.txt"


def hooks_yaml(pipeline_style: bool, source_hook: str | None = None) -> str:
    exclude = "\n        exclude: '^toolkit/tests/fixtures/'" if pipeline_style else ""
    maxkb = 1024 if pipeline_style else 500
    source = source_hook or (
        "      - id: forbid-source-files\n"
        "        name: forbid source files and private data\n"
        "        language: fail\n"
        '        entry: "Never commit client or Microsoft source files, exports or private data:"\n'
        "        files: '(?i)([.](accdt|accdb|accde|mdb|mde|laccdb|ldb|mdw|xlsm|xlsb|xls|xlam|bas"
        "|cls|frm|frx|msg|bak)$|^(private|data|vendor)/)'" + exclude + "\n"
    )
    return (
        "repos:\n"
        "  - repo: local\n"
        "    hooks:\n"
        + source
        + "      - id: forbid-sample-outputs\n"
        "        name: forbid rendered sample outputs\n"
        "        language: fail\n"
        '        entry: "Never commit rendered sample outputs (PDF, EML, XLSX):"\n'
        "        files: '(?i)[.](pdf|eml|xlsx)$'" + exclude + "\n"
        "  - repo: https://github.com/pre-commit/pre-commit-hooks\n"
        "    rev: v6.0.0\n"
        "    hooks:\n"
        "      - id: check-added-large-files\n"
        f"        args: ['--enforce-all', '--maxkb={maxkb}']\n"
    )


def make_repo(tmp_path: Path, name: str, pipeline_style: bool, config: str | None = None):
    repo = tmp_path / "repos" / name
    repo.mkdir(parents=True)
    git("init", "-q", "-b", "main", str(repo))
    (repo / ".pre-commit-config.yaml").write_text(
        config or hooks_yaml(pipeline_style), encoding="utf-8"
    )
    (repo / "README.md").write_text("synthetic repo for guard tests\n", encoding="utf-8")
    if pipeline_style:
        (repo / "pipeline.json").write_text('{"name": "synthetic"}\n', encoding="utf-8")
        (repo / "toolkit").mkdir()
        (repo / "toolkit" / "__init__.py").write_text("", encoding="utf-8")
    git("-C", str(repo), "add", "-A")
    git("-C", str(repo), "commit", "-q", "-m", "init")
    return repo


@pytest.fixture
def eng(tmp_path, make_engagement):
    path = make_engagement(
        tmp_path,
        id="guard-eng",
        source={"kind": "vcs-export", "path": "{{p.private}}/sources/app"},
    )
    loaded = load(path)
    src = Path(loaded["source"]["path"])
    for f in (FIX / "vcs").rglob("*"):
        if f.is_file():
            rel = f.relative_to(FIX / "vcs")
            name = rel.name[: -len(".txt")] if rel.name.count(".") >= 2 else rel.name
            dest = src / rel.parent / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dest)
    return loaded


@pytest.fixture
def target(tmp_path):
    return make_repo(tmp_path, "target", pipeline_style=False)


@pytest.fixture
def pipe(tmp_path):
    return make_repo(tmp_path, "pipe", pipeline_style=True)


def copied_lines(n: int = 10) -> str:
    lines = MODULE.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[14:14 + n]) + "\n"


def failures(result) -> list[str]:
    return [f for r in result.repos for f in r.failures]


def failing(result, path: str) -> list[str]:
    return [f for f in failures(result) if f.split(": ", 1)[0].endswith(":" + path)]


# ------------------------------------------------------------------ worktree checks


def test_clean_repo_passes_and_writes_corpus(eng, target):
    result = guard.run([str(target)], eng=eng)
    assert not result.failed, failures(result)
    assert result.repos[0].checked == 2
    corpus = Path(eng["paths"]["private"]) / "corpus"
    assert (corpus / "code" / "module-1.txt").is_file()
    assert (corpus / "code" / "codebehind-1.txt").is_file()
    assert (corpus / "code" / "expr-1.txt").is_file()
    assert (corpus / "code" / "datamacro-1.txt").is_file()
    assert (corpus / "data" / "1.txt").is_file()
    strings = (corpus / "strings.txt").read_text(encoding="utf-8").splitlines()
    assert "EAST-POTTING-SHED" in strings
    assert "12345678" not in strings and "2024-03-01" not in strings
    assert all(len(s) >= 8 for s in strings)
    assert not [p for p in corpus.iterdir() if p.name.startswith(".")]
    assert result.corpus["corpus_code_files"] >= 5


def test_corpus_is_rebuilt_every_run(eng, target):
    corpus = Path(eng["paths"]["private"]) / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    (corpus / "code").mkdir(exist_ok=True)
    (corpus / "code" / "module-99.txt").write_text("stale\n", encoding="utf-8")
    guard.run([str(target)], eng=eng)
    guard.run([str(target)], eng=eng)
    assert not (corpus / "code" / "module-99.txt").exists()
    assert (corpus / "code" / "module-1.txt").read_text(encoding="utf-8").startswith(
        "Attribute VB_Name"
    )


def test_untracked_accdt_fails(eng, target):
    (target / "planted.accdt").write_bytes(b"PK\x03\x04 synthetic zip header")
    result = guard.run([str(target)], eng=eng)
    assert result.failed
    assert failing(result, "planted.accdt") == ["target:planted.accdt: forbid-source-files"]


def test_ten_copied_lines_fail(eng, target):
    (target / "notes.md").write_text(copied_lines(10), encoding="utf-8")
    result = guard.run([str(target)], eng=eng)
    hits = failing(result, "notes.md")
    assert len(hits) == 1 and "similarity" in hits[0]
    assert hits[0].endswith("corpus code/module-1.txt")
    assert "rs.Edit" not in " ".join(failures(result))


def test_paraphrase_passes(eng, target):
    shutil.copyfile(FIX / "paraphrase.txt", target / "notes.md")
    result = guard.run([str(target)], eng=eng)
    assert not result.failed, failures(result)


def test_handwritten_saveastext_passes(eng, target):
    shutil.copyfile(FIX / "handwritten-form.txt", target / "frmPottingMix.txt")
    result = guard.run([str(target)], eng=eng)
    assert not result.failed, failures(result)


def test_pipeline_style_filename_rules(eng, pipe):
    seed = pipe / "goldens" / "synthetic" / "seed-1" / "input"
    seed.mkdir(parents=True)
    (seed / "Orders.csv").write_text("OrderID,Qty\r\n1,2\r\n", encoding="utf-8")
    fixture = pipe / "toolkit" / "tests" / "fixtures" / "parse"
    fixture.mkdir(parents=True)
    (fixture / "Sample.bas").write_text(copied_lines(12), encoding="utf-8")
    (pipe / "data").mkdir()
    (pipe / "data" / "x.csv").write_text("a,b\r\n", encoding="utf-8")
    (pipe / "x.bas").write_text("Attribute VB_Name = \"x\"\n", encoding="utf-8")
    (pipe / "x.frx").write_bytes(b"\x00\x01binary")
    (pipe / "x.pdf").write_bytes(b"%PDF-1.4 synthetic")
    result = guard.run([str(pipe)], eng=eng)
    assert failing(result, "goldens/synthetic/seed-1/input/Orders.csv") == []
    sample = failing(result, "toolkit/tests/fixtures/parse/Sample.bas")
    assert len(sample) == 1 and "similarity" in sample[0]  # copy-checked, not a hook hit
    assert failing(result, "data/x.csv") == ["pipe:data/x.csv: forbid-source-files"]
    assert failing(result, "x.bas") == ["pipe:x.bas: forbid-source-files"]
    assert failing(result, "x.frx") == ["pipe:x.frx: forbid-source-files"]
    assert failing(result, "x.pdf") == ["pipe:x.pdf: forbid-sample-outputs"]


def test_large_file_limits(tmp_path, pipe, target):
    big = '{"pad": "' + "a" * (530 * 1024) + '"}\n'
    (pipe / "pipeline.json").write_text(big, encoding="utf-8")
    (target / "big.json").write_text(big, encoding="utf-8")
    result = guard.run([str(pipe), str(target)], hooks_only=True)
    assert failing(result, "pipeline.json") == []
    assert failing(result, "big.json") == ["target:big.json: check-added-large-files"]
    assert result.repos[0].maxkb == 1024 and result.repos[1].maxkb == 500


def test_staged_checks_only_staged_files(eng, target):
    (target / "planted.accdt").write_bytes(b"PK synthetic")  # untracked, not staged
    (target / "copied.md").write_text(copied_lines(10), encoding="utf-8")
    git("-C", str(target), "add", "copied.md")
    # the worktree copy is clean now; the staged blob still holds the copied lines
    shutil.copyfile(FIX / "paraphrase.txt", target / "copied.md")
    result = guard.run([str(target)], mode="staged", eng=eng)
    assert result.repos[0].checked == 1
    assert failing(result, "planted.accdt") == []
    hits = failing(result, "copied.md")
    assert len(hits) == 1 and "similarity" in hits[0]


def test_staged_forbidden_name_fails(eng, target):
    (target / "x.xlsm").write_bytes(b"PK synthetic workbook")
    git("-C", str(target), "add", "x.xlsm")
    result = guard.run([str(target)], mode="staged", eng=eng)
    assert failing(result, "x.xlsm") == ["target:x.xlsm: forbid-source-files"]


# ------------------------------------------------------------------ history


def test_history_copied_file_committed_then_deleted_fails(eng, target):
    (target / "copied.md").write_text(copied_lines(10), encoding="utf-8")
    git("-C", str(target), "add", "copied.md")
    git("-C", str(target), "commit", "-q", "-m", "add")
    git("-C", str(target), "rm", "-q", "copied.md")
    git("-C", str(target), "commit", "-q", "-m", "remove")
    assert guard.run([str(target)], eng=eng).failed is False
    result = guard.run([str(target)], mode="history", eng=eng)
    hits = [f for f in failures(result) if ":copied.md@" in f]
    assert len(hits) == 1 and "similarity" in hits[0]


def test_history_forbidden_name_and_size(eng, target):
    (target / "old.mdb").write_bytes(b"synthetic")
    (target / "big.json").write_text("b" * (530 * 1024), encoding="utf-8")
    git("-C", str(target), "add", "-A")
    git("-C", str(target), "commit", "-q", "-m", "add")
    git("-C", str(target), "rm", "-q", "old.mdb", "big.json")
    git("-C", str(target), "commit", "-q", "-m", "remove")
    result = guard.run([str(target)], mode="history", eng=eng)
    text = "\n".join(failures(result))
    assert ":old.mdb@" in text and "forbid-source-files" in text
    assert ":big.json@" in text and "check-added-large-files" in text


def test_history_large_pipeline_json_passes_in_pipeline_repo(eng, pipe):
    (pipe / "pipeline.json").write_text(
        '{"pad": "' + "c" * (530 * 1024) + '"}\n', encoding="utf-8"
    )
    git("-C", str(pipe), "commit", "-q", "-am", "grow pipeline.json")
    result = guard.run([str(pipe)], mode="history", eng=eng)
    assert not result.failed, failures(result)


def test_history_lists_unmerged_branch(eng, target):
    git("-C", str(target), "checkout", "-q", "-b", "feature/side")
    (target / "side.md").write_text("side work\n", encoding="utf-8")
    git("-C", str(target), "add", "side.md")
    git("-C", str(target), "commit", "-q", "-m", "side")
    git("-C", str(target), "checkout", "-q", "main")
    git("-C", str(target), "tag", "merged-tag")
    result = guard.run([str(target)], mode="history", eng=eng)
    assert not result.failed
    assert result.repos[0].unmerged == ["refs/heads/feature/side"]


# ------------------------------------------------------------------ refusals


def test_missing_source_exits_2(eng, target):
    shutil.rmtree(eng["source"]["path"])
    with pytest.raises(guard.GuardError, match="source missing: run mp-data fetch"):
        guard.run([str(target)], eng=eng)


def test_empty_corpus_exits_2(eng, target):
    src = Path(eng["source"]["path"])
    shutil.rmtree(src)
    src.mkdir()
    with pytest.raises(guard.GuardError, match="corpus would be empty"):
        guard.run([str(target)], eng=eng)


def test_missing_repo_exits_2(eng, tmp_path):
    with pytest.raises(guard.GuardError, match="repo missing"):
        guard.run([str(tmp_path / "nope")], eng=eng)
    (tmp_path / "plain").mkdir()
    with pytest.raises(guard.GuardError, match="repo missing"):
        guard.run([str(tmp_path / "plain")], hooks_only=True)


def test_redefined_hook_exits_2_before_any_hook_runs(tmp_path):
    marker = tmp_path / "hook-ran"
    redefined = (
        "      - id: forbid-source-files\n"
        "        name: redefined\n"
        "        language: system\n"
        f"        entry: touch {marker}\n"
        "        pass_filenames: false\n"
        "        always_run: true\n"
    )
    repo = make_repo(
        tmp_path, "bad", pipeline_style=False, config=hooks_yaml(False, redefined)
    )
    with pytest.raises(guard.GuardError, match="forbid-source-files"):
        guard.run([str(repo)], hooks_only=True)
    assert not marker.exists()


@pytest.mark.parametrize(
    "edit",
    [
        ("--maxkb=500", "--maxkb=5000"),
        ("rev: v6.0.0", "rev: v5.0.0"),
        ("language: fail\n        entry: \"Never commit rendered", "language: fail\n        stages: [manual]\n        entry: \"Never commit rendered"),
        ("|bak)$", "|bak|zip)$"),
    ],
    ids=["maxkb", "rev", "stages", "pattern"],
)
def test_changed_hook_definitions_exit_2(tmp_path, edit):
    config = hooks_yaml(False).replace(*edit)
    assert config != hooks_yaml(False)
    repo = make_repo(tmp_path, "bad", pipeline_style=False, config=config)
    with pytest.raises(guard.GuardError):
        guard.run([str(repo)], hooks_only=True)


def test_target_config_in_pipeline_repo_exits_2(tmp_path):
    repo = make_repo(tmp_path, "mixed", pipeline_style=True, config=hooks_yaml(False))
    with pytest.raises(guard.GuardError, match="forbid-source-files"):
        guard.run([str(repo)], hooks_only=True)


def test_duplicate_alias_exits_2(tmp_path):
    extra = (
        "  - repo: local\n"
        "    hooks:\n"
        "      - id: something-else\n"
        "        alias: forbid-sample-outputs\n"
        "        name: alias\n"
        "        language: system\n"
        "        entry: 'true'\n"
    )
    repo = make_repo(tmp_path, "alias", pipeline_style=False, config=hooks_yaml(False) + extra)
    with pytest.raises(guard.GuardError, match="exactly once"):
        guard.run([str(repo)], hooks_only=True)


def test_real_pipeline_config_is_accepted():
    repo = guard.open_repo(str(PIPELINE))
    assert repo.pipeline_style
    guard.check_config(repo)
    assert repo.maxkb == 1024 and repo.exclude == "^toolkit/tests/fixtures/"


# ------------------------------------------------------------------ report


def test_no_html_report_without_flag(eng, target, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (target / "notes.md").write_text(copied_lines(10), encoding="utf-8")
    result = guard.run([str(target)], eng=eng)
    assert result.failed
    assert list(tmp_path.rglob("*.html")) == []


def test_report_written_outside_repos_only(eng, target, tmp_path):
    (target / "notes.md").write_text(copied_lines(10), encoding="utf-8")
    out = tmp_path / "home" / "engagements" / "guard-eng" / "private" / "logs" / "report"
    result = guard.run([str(target)], eng=eng, report=str(out))
    assert result.failed and (out / "report.html").is_file()
    with pytest.raises(guard.GuardError, match="outside the repos"):
        guard.run([str(target)], eng=eng, report=str(target / "report"))
    assert not (target / "report").exists()


# ------------------------------------------------------------------ CLI


def _cli(args, tmp_path):
    proc = subprocess.run(
        [sys.executable, "-c", "import sys; from toolkit.cli import main; sys.exit(main())", *args],
        cwd=tmp_path,
        env={**__import__("os").environ, "MP_QUIET": "1"},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    lines = proc.stdout.splitlines()
    assert len(lines) == 1, proc.stdout
    return proc.returncode, json.loads(lines[0])


def test_cli_hooks_only_and_failures(tmp_path, target):
    code, summary = _cli(["guard", "--hooks-only", "--repo", str(target)], tmp_path)
    assert code == 0 and summary["status"] == "ok"
    assert summary["counts"]["files"] == 2
    (target / "x.eml").write_text("synthetic\n", encoding="utf-8")
    code, summary = _cli(["guard", "--hooks-only", "--repo", str(target)], tmp_path)
    assert code == 1 and summary["status"] == "failed"
    assert "target:x.eml: forbid-sample-outputs" in summary["messages"]


def test_cli_missing_source_exits_2(tmp_path, eng, target):
    shutil.rmtree(eng["source"]["path"])
    records = Path(eng["paths"]["records"]) / "engagement.json"
    code, summary = _cli(["guard", str(records), "--repo", str(target)], tmp_path)
    assert code == 2 and summary["status"] == "error"
    assert "source missing: run mp-data fetch" in summary["messages"]


def test_cli_needs_engagement_unless_hooks_only(tmp_path, target):
    code, summary = _cli(["guard", "--repo", str(target)], tmp_path)
    assert code == 2 and summary["status"] == "error"


def test_cli_full_run_summary_has_no_text(tmp_path, eng, target):
    (target / "notes.md").write_text(copied_lines(10), encoding="utf-8")
    records = Path(eng["paths"]["records"]) / "engagement.json"
    code, summary = _cli(["guard", str(records), "--repo", str(target)], tmp_path)
    assert code == 1 and summary["engagement"] == "guard-eng"
    line = json.dumps(summary)
    assert "TrayYieldScore" not in line and "QUARANTINE" not in line
    assert any("notes.md: similarity" in m for m in summary["messages"])


def test_strings_leave_out_design_names(eng, target):
    guard.run([str(target)], eng=eng)
    corpus = Path(eng["paths"]["private"]) / "corpus"
    strings = (corpus / "strings.txt").read_text(encoding="utf-8").splitlines()
    assert "WEST-COLD-FRAME" in strings
    assert "frmTrayBench" not in strings  # a data value that equals a form name
    assert "TrayBench" not in strings  # a data value inside a longer form name


def test_strings_leave_out_engagement_text_and_summary_vocabulary(eng):
    names = {"tblTrays", "frmTrayBench"}
    assert guard._inside_design_text("omitted_columns", names, eng)
    assert guard._inside_design_text("guard-eng", names, eng)
    assert guard._inside_design_text("TrayBenc", names, eng)
    assert not guard._inside_design_text("WEST-COLD-FRAME", names, eng)


def test_dataset_export_summary_is_not_withheld_after_guard(tmp_path, eng, target):
    """The real `dataset export` summary, checked with mp-data's own substring rule against the
    strings.txt that guard rebuilds, is never withheld."""
    from toolkit import dataplane

    records = Path(eng["paths"]["records"]) / "engagement.json"
    private = Path(eng["paths"]["private"])
    code, _s = _cli(["parse", str(records), "--out", str(private / "out" / "model")], tmp_path)
    assert code == 0, _s
    out = private / "datasets" / "engagement"
    code, export = _cli(["dataset", "export", str(records), "--out", str(out)], tmp_path)
    assert code == 0, export
    code, check = _cli(["dataset", "export", str(records), "--out", str(out), "--check"], tmp_path)
    assert code == 0 and check["counts"]["differences"] == 0, check
    result = guard.run([str(target)], eng=eng)
    assert not failures(result)
    assert (private / "corpus" / "strings.txt").read_text(encoding="utf-8").strip()
    for summary in (export, check):
        assert not dataplane.corpus_hit(json.dumps(summary), private), summary


def test_paths_holding_corpus_strings_are_withheld(eng, target):
    (target / "EAST-POTTING-SHED.accdt").write_bytes(b"PK synthetic")
    result = guard.run([str(target)], eng=eng)
    assert failing(result, "EAST-POTTING-SHED.accdt")
    messages = result.summary_messages()
    assert not any("EAST-POTTING-SHED" in m for m in messages)
    assert any("<path withheld, sha256 " in m and "forbid-source-files" in m for m in messages)


FORMAT_ONLY = """Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Option Compare Database
Option Explicit

Private Sub Form_Open(Cancel As Integer)
    Me.Caption = "Compost bins"
End Sub
"""

AXL_FORMAT_ONLY = (
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
    '<DataMacros xmlns="http://schemas.microsoft.com/office/accessservices/2009/11/application">'
    '<DataMacro Event="BeforeChange"><Statements><ConditionalBlock><If><Condition>[Bins]=0'
    '</Condition><Statements><Action Name="RaiseError"><Argument Name="Number">3</Argument>'
    '<Argument Name="Description">No bins</Argument></Action></Statements></If>'
    "</ConditionalBlock></Statements></DataMacro></DataMacros>\n"
)


def test_format_boilerplate_alone_passes(eng, target):
    (target / "frmCompost.cls.txt").write_text(FORMAT_ONLY, encoding="utf-8")
    (target / "tblBins.axl").write_text(AXL_FORMAT_ONLY, encoding="utf-8")
    result = guard.run([str(target)], eng=eng)
    assert not result.failed, failures(result)


def test_copied_data_macro_still_fails(eng, target):
    shutil.copyfile(FIX / "vcs" / "tdmacros" / "tblTrays.xml", target / "copied.axl")
    result = guard.run([str(target)], eng=eng)
    hits = failing(result, "copied.axl")
    assert len(hits) == 1 and "corpus code/datamacro-1.txt" in hits[0]


def test_format_boilerplate_keeps_no_content():
    parts = guard.format_boilerplate("datamacro", AXL_FORMAT_ONLY)
    assert "No bins" not in parts[0] and "[Bins]=0" not in parts[0]
    code = guard.format_boilerplate("codebehind", FORMAT_ONLY)
    assert not any("Compost" in p for p in code)
    assert code[0].startswith("Attribute VB_GlobalNameSpace = False\n")
    assert code[0].endswith("Option Explicit\n\nPrivate Sub Form_Open(Cancel As Integer)\n")
    assert code[1] == "End Sub\n"


def test_scores_are_deterministic(eng, target):
    """copydetect runs with a fixed hash seed, so the same inputs always give the same score."""
    lines = MODULE.read_text(encoding="utf-8").splitlines()
    (target / "partial.md").write_text(
        "\n".join(lines[14:17]) + "\n" + FIX.joinpath("paraphrase.txt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    first = guard.run([str(target)], eng=eng).summary_messages()
    for _ in range(2):
        assert guard.run([str(target)], eng=eng).summary_messages() == first
