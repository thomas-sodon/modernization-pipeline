"""estimate and healthcheck on synthetic inventories (docs/contracts.md#records, #cli-registry)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from toolkit import cli
from toolkit import estimate as est_mod
from toolkit import healthcheck as hc
from toolkit import inventory as inv_mod
from toolkit.engagement import load, pipeline_json
from toolkit.tests.conftest import git

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "inventory"
SHA = "c" * 64


def run_cli(args, capfd) -> tuple[int, dict]:
    code = cli.main([str(a) for a in args])
    out, _err = capfd.readouterr()
    lines = out.splitlines()
    assert len(lines) == 1, out
    return code, json.loads(lines[0])


def make_inventory(
    make_engagement, tmp_path, kind="accdt", edit=None, rate=None, **source
):
    model = FIXTURES / ("xlsm" if kind == "xlsm" else "access")
    if edit:
        dest = tmp_path / "model"
        shutil.copytree(model, dest)
        objects = json.loads((dest / "objects.json").read_text())
        edit(objects)
        (dest / "objects.json").write_text(json.dumps(objects))
        model = dest
    eng_path = make_engagement(
        tmp_path,
        source={
            "kind": kind,
            "path": "{{p.private}}/sources/app." + ("xlsm" if kind == "xlsm" else kind),
            "bytes": 4096,
            "sha256": SHA,
            **source,
        },
        app={
            "slug": "widgets",
            "project_name": "Widget Shop",
            "timezone": "UTC",
            "use_tz": False,
        },
        commercial={
            "tier": "demo",
            "quote_is_simulated": True,
            "milestones": [],
            "rate_usd_per_hour": rate,
        },
    )
    inv, _ = inv_mod.build(load(eng_path), model)
    path = tmp_path / "out" / "inventory.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(inv_mod.dumps(inv), encoding="utf-8")
    return path, inv


def two_backends(objects):
    objects["linked_tables"] = [
        {
            "name": "tblA",
            "backend_kind": "access",
            "backend": 1,
            "backend_file": "a.accdb",
            "source_table": "tblA",
        },
        {
            "name": "tblB",
            "backend_kind": "access",
            "backend": 2,
            "backend_file": "b.accdb",
            "source_table": "tblB",
        },
    ]
    objects["facts"]["data_backends"] = 2
    objects["facts"]["linked_tables"] = {"access": 2}


# ------------------------------------------------------------------ pricing rule


def test_price_rule_rounds_up_to_round_to():
    assert est_mod.price(Decimal("160.6"), 130, 100) == 20900
    assert est_mod.price(Decimal(10), 130, 100) == 1300  # exact multiple stays
    assert est_mod.price(Decimal("10.01"), 130, 100) == 1400
    assert est_mod.price(Decimal(29), 130, 500) == 4000


def test_tiers():
    p = pipeline_json()
    assert est_mod.pick_tier(Decimal(30), 1, p) == "stage2-small"
    assert est_mod.pick_tier(Decimal(90), 1, p) == "stage2-small"
    assert est_mod.pick_tier(Decimal("95.5"), 1, p) == est_mod.BETWEEN
    assert est_mod.pick_tier(Decimal(110), 1, p) == "stage2-standard"
    assert est_mod.pick_tier(Decimal(200), 0, p) == "stage2-standard"
    assert est_mod.pick_tier(Decimal("200.1"), 1, p) == est_mod.ABOVE
    assert est_mod.pick_tier(Decimal(150), 2, p) == est_mod.CUSTOM


def test_coefficients_file_starts_with_source():
    first = est_mod.COEFFICIENTS.read_text().splitlines()[0]
    assert first.startswith('_source = "starting guesses, not measured;')
    coef, _ = est_mod.load_coefficients()
    assert coef["access"]["base"] == 24 and coef["stage1"]["base"] == 10
    assert coef["bands"] == {"low": 0.8, "high": 1.25}


# ------------------------------------------------------------------ estimate


def test_access_estimate_clamps_point_and_has_stage1(
    make_engagement, tmp_path, capfd, quiet
):
    inv_path, _ = make_inventory(make_engagement, tmp_path)
    out = tmp_path / "e" / "estimate.json"
    code, summary = run_cli(["estimate", inv_path, "--out", out], capfd)
    assert code == 0, summary
    est = json.loads(out.read_text())
    # 24 + 1 form + 0.75 report + 3 x 0.25 queries + 3 x 0.25 tables + 2 x 0.25 macros
    # + 2 x 0.5 data macros + 30 lines x 0.0075 = 28.975 -> 29
    assert est["hours"] == {"low": 23.2, "point": 29, "high": 36.3}
    assert est["tier"] == "stage2-small"
    assert est["rate_usd_per_hour"] == 130
    # low 23.2 x 130 = 3016 -> 3100; point 3770 -> 3800, clamped to 8000; high 4719 -> 4800
    assert est["price_usd"] == {"low": 3100, "point": 8000, "high": 4800}
    assert (
        "stage2-small point raised from 3800 to the tier minimum 8000 USD"
        in est["price_note"]
    )
    # stage1: 10 + 3 x 0.15 + 1 x 0.05 + 2 x 0.25 = 11
    assert est["stage1"]["hours"] == {"low": 8.8, "point": 11, "high": 13.8}
    assert est["stage1"]["price_usd"] == {"low": 1200, "point": 2000, "high": 1800}
    assert (
        "stage1 point raised from 1500 to the tier minimum 2000 USD"
        in est["price_note"]
    )
    assert est["inventory_sha256"] == hashlib.sha256(inv_path.read_bytes()).hexdigest()
    assert (
        est["coefficients_sha256"]
        == hashlib.sha256(est_mod.COEFFICIENTS.read_bytes()).hexdigest()
    )
    assert (
        est["rules_sha256"] is None and est["by_area"] is None and est["delta"] is None
    )
    assert sorted(est) == sorted(
        [
            "format_version",
            "inventory_sha256",
            "rules_sha256",
            "hours",
            "tier",
            "rate_usd_per_hour",
            "price_usd",
            "price_note",
            "stage1",
            "by_area",
            "delta",
            "coefficients_sha256",
        ]
    )


def test_two_runs_identical_bytes(make_engagement, tmp_path, capfd, quiet):
    inv_path, _ = make_inventory(make_engagement, tmp_path)
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    for out in (a, b):
        assert run_cli(["estimate", inv_path, "--out", out], capfd)[0] == 0
    assert a.read_bytes() == b.read_bytes()


def test_engagement_rate_wins(make_engagement, tmp_path):
    _, inv = make_inventory(make_engagement, tmp_path, rate=80)
    coef, csha = est_mod.load_coefficients()
    est = est_mod.compute(inv, "x", pipeline_json(), coef, csha)
    assert est["rate_usd_per_hour"] == 80
    assert est["price_usd"]["low"] == est_mod.price(Decimal("23.2"), 80, 100)


def test_unclamped_tiers_say_why(make_engagement, tmp_path):
    _, inv = make_inventory(make_engagement, tmp_path)
    coef, csha = est_mod.load_coefficients()
    coef = json.loads(json.dumps(coef))
    coef["access"]["base"] = 90  # point 94.975 -> between rows
    est = est_mod.compute(inv, "x", pipeline_json(), coef, csha)
    assert est["tier"] == est_mod.BETWEEN
    assert est["price_usd"]["point"] == est_mod.price(Decimal(95), 130, 100)
    assert est["price_note"].startswith("not clamped: between stage2-small")


def test_custom_quote_for_two_back_ends(make_engagement, tmp_path):
    _, inv = make_inventory(
        make_engagement, tmp_path, edit=two_backends, layout="split"
    )
    coef, csha = est_mod.load_coefficients()
    est = est_mod.compute(inv, "x", pipeline_json(), coef, csha)
    assert est["tier"] == est_mod.CUSTOM
    assert "not clamped: custom quote: more than one data back end" in est["price_note"]
    # stage1 counts the two linked tables: 10 + 0.45 + 0.05 + 2 x 0.25 + 0.5 = 11.5
    assert est["stage1"]["hours"]["point"] == 11.5


def test_xlsm_estimate_has_no_stage1(make_engagement, tmp_path):
    _, inv = make_inventory(make_engagement, tmp_path, kind="xlsm")
    coef, csha = est_mod.load_coefficients()
    est = est_mod.compute(inv, "x", pipeline_json(), coef, csha)
    assert est["stage1"] is None
    # 24 + 1.5 userform + 0.5 block + 0.25 calc + 2 x 0.5 charts + 0.1 validation
    # + 2 x 0.5 business tables + 2 x 2.0 sample kinds + 38 x 0.0075 = 32.635 -> 32.6
    assert est["hours"]["point"] == 32.6


# ------------------------------------------------------------------ --rules and --delta


def rules_doc(rules, objects):
    return {
        "format_version": 1,
        "kind": "merged",
        "engagement": "demo-eng",
        "rules": rules,
        "objects": objects,
    }


OBJECTS = [
    {"type": "form", "name": "frmWidgetOrder", "decision": "keep", "area": "orders"},
    {
        "type": "query",
        "name": "qryOpenWidgetOrders",
        "decision": "keep",
        "area": "orders",
    },
    {
        "type": "procedure",
        "name": "modWidgetMath.AddTax",
        "decision": "keep",
        "area": "orders",
    },
    {"type": "table", "name": "tblWidgets", "decision": "keep", "area": "stock"},
    {
        "type": "report",
        "name": "rptWidgetInvoice",
        "decision": "drop",
        "area": "orders",
    },
]
R1 = {
    "id": "R-orders-001",
    "area": "orders",
    "sources": [{"type": "form", "ref": "frmWidgetOrder"}],
}
R2 = {
    "id": "R-orders-002",
    "area": "orders",
    "sources": [{"type": "query", "ref": "qryOpenWidgetOrders"}],
}
R3 = {
    "id": "R-stock-001",
    "area": "stock",
    "sources": [{"type": "table", "ref": "tblWidgets"}],
}


def test_rules_by_area(make_engagement, tmp_path, capfd, quiet):
    inv_path, _ = make_inventory(make_engagement, tmp_path)
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps(rules_doc([R1], OBJECTS)))
    out = tmp_path / "est.json"
    code, _ = run_cli(["estimate", inv_path, "--rules", rules, "--out", out], capfd)
    assert code == 0
    est = json.loads(out.read_text())
    # orders: form 1.0 + query 0.25 + procedure 6 lines x 0.0075 = 1.295 -> 1.3; stock: 0.25 -> 0.3
    assert est["by_area"] == {"orders": 1.3, "stock": 0.3}
    assert est["rules_sha256"] == hashlib.sha256(rules.read_bytes()).hexdigest()
    assert est["delta"] is None


def test_delta_against_committed_version(
    make_engagement, make_git_repo, tmp_path, capfd, quiet
):
    inv_path, _ = make_inventory(make_engagement, tmp_path)
    repo = make_git_repo(tmp_path, "rules-repo")
    rules = repo / "analysis" / "rules.json"
    rules.parent.mkdir()
    v1 = json.dumps(rules_doc([R1, R2], OBJECTS), indent=2) + "\n"
    rules.write_text(v1)
    git("-C", str(repo), "add", "analysis/rules.json")
    git("-C", str(repo), "commit", "-q", "-m", "v1")
    base_sha = hashlib.sha256(v1.encode()).hexdigest()
    r1_changed = dict(
        R1, sources=R1["sources"] + [{"type": "report", "ref": "rptWidgetInvoice"}]
    )
    rules.write_text(json.dumps(rules_doc([r1_changed, R3], OBJECTS), indent=2) + "\n")
    git("-C", str(repo), "commit", "-q", "-am", "v2")
    out = tmp_path / "co.json"
    code, summary = run_cli(
        ["estimate", inv_path, "--rules", rules, "--delta", base_sha, "--out", out],
        capfd,
    )
    assert code == 0, summary
    d = json.loads(out.read_text())["delta"]
    assert d["base_rules_sha256"] == base_sha
    assert d["added"] == ["R-stock-001"]
    assert d["removed"] == ["R-orders-002"]
    assert d["changed"] == ["R-orders-001"]
    # changed R-orders-001: form 1.0 + report 0.75; added R-stock-001: table 0.25 -> 0.3
    assert d["by_area"] == {"orders": 1.8, "stock": 0.3}
    # 2.1 h x 130 = 273 -> 300, unclamped (far below every tier floor)
    assert d["price_usd"] == 300


def test_delta_unknown_sha_exits_2(
    make_engagement, make_git_repo, tmp_path, capfd, quiet
):
    inv_path, _ = make_inventory(make_engagement, tmp_path)
    repo = make_git_repo(tmp_path, "rules-repo")
    rules = repo / "rules.json"
    rules.write_text(json.dumps(rules_doc([R1], OBJECTS)))
    git("-C", str(repo), "add", "rules.json")
    git("-C", str(repo), "commit", "-q", "-m", "v1")
    out = tmp_path / "co.json"
    code, summary = run_cli(
        ["estimate", inv_path, "--rules", rules, "--delta", "d" * 64, "--out", out],
        capfd,
    )
    assert code == 2 and summary["status"] == "error"
    assert not out.exists()


# ------------------------------------------------------------------ render --into


QUOTE = "# Quote\n\nIntro text.\n\n<!-- estimate:begin -->\nold block\n<!-- estimate:end -->\n\nTerms.\n"


def test_render_replaces_only_the_block(make_engagement, tmp_path, capfd, quiet):
    inv_path, _ = make_inventory(make_engagement, tmp_path)
    est_path = tmp_path / "est.json"
    assert run_cli(["estimate", inv_path, "--out", est_path], capfd)[0] == 0
    quote = tmp_path / "quote.md"
    quote.write_text(QUOTE)
    code, _ = run_cli(["estimate", "render", est_path, "--into", quote], capfd)
    assert code == 0
    text = quote.read_text()
    assert text.startswith("# Quote\n\nIntro text.\n\n<!-- estimate:begin -->\n")
    assert text.endswith("\n<!-- estimate:end -->\n\nTerms.\n")
    assert "old block" not in text
    assert (
        "| Stage 2 (stage2-small) | 23.2 / 29 / 36.3 | 3,100 / 8,000 / 4,800 |" in text
    )
    assert "Stage 1 alone" in text
    # --check: same bytes pass and nothing is written
    before = quote.read_bytes()
    code, summary = run_cli(
        ["estimate", "render", est_path, "--into", quote, "--check"], capfd
    )
    assert code == 0 and summary["command"] == "estimate render"
    assert quote.read_bytes() == before
    # a difference fails --check without writing
    quote.write_text(text.replace("Stage 1 alone", "Stage one"))
    changed = quote.read_bytes()
    code, _ = run_cli(
        ["estimate", "render", est_path, "--into", quote, "--check"], capfd
    )
    assert code == 1
    assert quote.read_bytes() == changed


def test_render_missing_markers(make_engagement, tmp_path, capfd, quiet):
    inv_path, _ = make_inventory(make_engagement, tmp_path)
    est_path = tmp_path / "est.json"
    assert run_cli(["estimate", inv_path, "--out", est_path], capfd)[0] == 0
    quote = tmp_path / "quote.md"
    quote.write_text("# Quote\n\n<!-- estimate:begin -->\nno end marker\n")
    code, _ = run_cli(["estimate", "render", est_path, "--into", quote], capfd)
    assert code == 2
    code, _ = run_cli(
        ["estimate", "render", est_path, "--into", quote, "--check"], capfd
    )
    assert code == 1
    assert quote.read_text() == "# Quote\n\n<!-- estimate:begin -->\nno end marker\n"


# ------------------------------------------------------------------ health check


SECTIONS = [
    "## Size",
    "## Risk flags",
    "## Complexity",
    "## Effort and price band",
    "## Next step",
]


def test_healthcheck_render_and_check(make_engagement, tmp_path, capfd, quiet):
    inv_path, _ = make_inventory(make_engagement, tmp_path)
    out = tmp_path / "docs" / "health-check.md"
    code, _ = run_cli(["healthcheck", inv_path, "--out", out], capfd)
    assert code == 0
    text = out.read_text()
    assert text.startswith("# Health check: Widget Shop\n")
    positions = [text.index(s) for s in SECTIONS]
    assert positions == sorted(positions)
    assert "| Forms | 1 |" in text
    assert "**VBA present**" in text
    assert "**Low**" in text
    assert (
        "| Form_frmWidgetOrder" not in text and "| frmWidgetOrder | form | 10 |" in text
    )
    assert "stage2-small" in text and "Stage 1 alone" in text
    assert "credited toward the migration" in text
    assert str(tmp_path) not in text
    assert not re.search(r"\b20\d\d-\d\d-\d\d\b", text)
    code, _ = run_cli(["healthcheck", inv_path, "--out", out, "--check"], capfd)
    assert code == 0
    out.write_text(text + "extra\n")
    code, summary = run_cli(["healthcheck", inv_path, "--out", out, "--check"], capfd)
    assert code == 1 and summary["status"] == "failed"
    assert out.read_text().endswith("extra\n")
    missing = tmp_path / "none.md"
    code, _ = run_cli(["healthcheck", inv_path, "--out", missing, "--check"], capfd)
    assert code == 1 and not missing.exists()


def test_healthcheck_two_back_ends_is_custom_quote(make_engagement, tmp_path):
    _, inv = make_inventory(
        make_engagement, tmp_path, edit=two_backends, layout="split"
    )
    coef, csha = est_mod.load_coefficients()
    text = hc.render(inv, pipeline_json(), coef, csha, hc.TEMPLATE.read_text())
    flags = dict(hc.risk_flags(inv))
    assert "More than one data back end" in flags
    assert "custom quote" in flags["More than one data back end"]
    assert "Split front end and back end" in flags
    assert (
        flags["Back end not received"]
        == "back end not received: the counts cover the front end only"
    )
    assert "Price band: **custom quote**: more than one data back end." in text


def test_healthcheck_xlsm_flags(make_engagement, tmp_path):
    _, inv = make_inventory(make_engagement, tmp_path, kind="xlsm")
    flags = dict(hc.risk_flags(inv))
    assert "Outlook" in flags["Outlook or Word automation"]
    assert flags["Broken defined names"].startswith("1 defined names")
    level, why = hc.complexity_level(inv)
    assert level == "Low" and "userforms plus formula blocks" in why
    coef, csha = est_mod.load_coefficients()
    text = hc.render(inv, pipeline_json(), coef, csha, hc.TEMPLATE.read_text())
    assert "Stage 1" not in text.split("## Next step")[1]
    for s in SECTIONS:
        assert s in text


@pytest.mark.parametrize(
    ("lines", "forms", "level"),
    [
        (6000, 1, "High"),
        (10, 41, "High"),
        (1001, 1, "Medium"),
        (10, 21, "Medium"),
        (1000, 20, "Low"),
    ],
)
def test_complexity_thresholds(lines, forms, level):
    inv = {
        "source": {"kind": "accdt"},
        "counts": {"vba_nonblank_lines": lines, "forms": forms, "reports": 0},
        "objects": [],
    }
    assert hc.complexity_level(inv)[0] == level


def test_large_file_flag():
    inv = {
        "source": {"kind": "accdt"},
        "counts": {},
        "objects": [],
        "facts": {
            "source_bytes": 3 * 1024**3,
            "data_copy_bytes": None,
            "compiled_only": True,
        },
    }
    flags = dict(hc.risk_flags(inv))
    assert "near the 2 GB Access file limit" in flags["Large source file"]
    assert "Compiled-only source" in flags
