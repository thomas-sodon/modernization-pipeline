"""Effort and price estimate from inventory.json (docs/contracts.md#records, estimate.json).

`compute(...)` turns the inventory counts into Stage 2 hours with the coefficients of
estimate.toml, picks the pricing_tiers row, prices low, point and high with the pipeline.json
pricing rule (point clamped to the tier row), adds the Stage 1 band for Access kinds, and with a
rules.json the hours of the kept objects per area and, with a base version, the change-order delta.
Every number is computed with Decimal so two runs give the same bytes.

`render_into(text, estimate)` replaces only the block between the estimate markers of a quote.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from pathlib import Path

FORMAT_VERSION = 1
COEFFICIENTS = Path(__file__).resolve().parent / "estimate.toml"
BEGIN = "<!-- estimate:begin -->"
END = "<!-- estimate:end -->"
ACCESS_KINDS = ("accdt", "vcs-export", "accdb", "mdb")
TENTH = Decimal("0.1")

BETWEEN = "between stage2-small and stage2-standard: decide at S1"
ABOVE = "above stage2-standard: split into phases"
CUSTOM = "custom quote: more than one data back end"


class EstimateError(Exception):
    """Bad input (missing file, unknown version, no matching base rules): exit 2."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def load_coefficients(path: Path = COEFFICIENTS) -> tuple[dict, str]:
    data = Path(path).read_bytes()
    return tomllib.loads(data.decode("utf-8")), sha256_bytes(data)


def _d(value) -> Decimal:
    return Decimal(str(value))


def _tenth(value: Decimal) -> Decimal:
    return value.quantize(TENTH, rounding=ROUND_HALF_UP)


def _num(value: Decimal):
    """A Decimal as a JSON number: int when whole, else float with one decimal."""
    value = _tenth(value)
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def band(point: Decimal, coef: dict) -> dict[str, Decimal]:
    point = _tenth(point)
    bands = coef.get("bands", {})
    return {
        "low": _tenth(point * _d(bands.get("low", 0.8))),
        "point": point,
        "high": _tenth(point * _d(bands.get("high", 1.25))),
    }


def price(hours: Decimal, rate, round_to) -> int:
    """ceil(hours x rate / round_to) x round_to."""
    step = _d(round_to)
    units = (_d(hours) * _d(rate) / step).to_integral_value(rounding=ROUND_CEILING)
    return int(units * step)


def _family(kind: str) -> str:
    return "xlsm" if kind == "xlsm" else "access"


# ----------------------------------------------------------------------------- hours


def stage2_hours(inv: dict, coef: dict) -> Decimal:
    c = inv["counts"]
    kind = inv["source"]["kind"]
    if kind == "xlsm":
        k = coef["xlsm"]
        facts = inv.get("facts") or {}
        business = facts.get("business_tables", c.get("tables", 0))
        sample_kinds = sum(1 for v in (c.get("sample_outputs") or {}).values() if v)
        formula_blocks = sum(1 for o in inv["objects"] if o["type"] == "formula_block")
        return (
            _d(k["base"])
            + _d(k["userform"]) * c.get("vba_userforms", 0)
            + _d(k["formula_block"]) * formula_blocks
            + _d(k["calculated_column"]) * c.get("calculated_columns", 0)
            + _d(k["chart"]) * c.get("charts", 0)
            + _d(k["data_validation"]) * c.get("data_validations", 0)
            + _d(k["business_table"]) * business
            + _d(k["sample_output_kind"]) * sample_kinds
            + _d(k["vba_per_100_lines"]) * _d(c.get("vba_nonblank_lines", 0)) / 100
        )
    k = coef["access"]
    return (
        _d(k["base"])
        + _d(k["form"]) * c.get("forms", 0)
        + _d(k["report"]) * c.get("reports", 0)
        + _d(k["query"]) * c.get("queries", 0)
        + _d(k["table"]) * c.get("tables", 0)
        + _d(k["macro"]) * (c.get("macros", 0) + c.get("embedded_macros", 0))
        + _d(k["data_macro"]) * c.get("data_macros", 0)
        + _d(k["vba_per_100_lines"]) * _d(c.get("vba_nonblank_lines", 0)) / 100
    )


def stage1_hours(inv: dict, coef: dict) -> Decimal:
    c = inv["counts"]
    facts = inv.get("facts") or {}
    k = coef["stage1"]
    linked = facts.get("linked_tables") or {}
    external = facts.get("external_links") or {}
    # external_links already counts the linked tables of external kinds (odbc, sharepoint,
    # excel, text); count each link once.
    declared = sum(external.values()) - sum(
        v for k, v in linked.items() if k in external
    )
    links = sum(linked.values()) + max(0, declared)
    return (
        _d(k["base"])
        + _d(k["table"]) * c.get("tables", 0)
        + _d(k["relationship"]) * facts.get("relationships", 0)
        + _d(k["link"]) * links
        + _d(k["data_macro"]) * c.get("data_macros", 0)
    )


def object_hours(kind: str, typ: str, metrics: dict, coef: dict) -> Decimal:
    """Hours one inventory object adds (by_area and the change-order delta)."""
    fam = _family(kind)
    k = coef[fam]
    if typ == "procedure":
        return _d(k["vba_per_100_lines"]) * _d(metrics.get("lines", 0)) / 100
    if fam == "xlsm":
        key = {"table": "business_table"}.get(typ, typ)
    else:
        key = typ
    if key in ("base", "vba_per_100_lines") or key not in k:
        return Decimal(0)
    return _d(k[key])


# ----------------------------------------------------------------------------- tiers


def _tiers(pipeline: dict) -> dict:
    return {row["tier"]: row for row in pipeline.get("pricing_tiers") or []}


def pick_tier(point: Decimal, backends: int, pipeline: dict) -> str:
    tiers = _tiers(pipeline)
    small, standard = tiers["stage2-small"], tiers["stage2-standard"]
    if backends > 1:
        return CUSTOM
    if point <= _d(small["hours"]["max"]):
        return "stage2-small"
    if point < _d(standard["hours"]["min"]):
        return BETWEEN
    if point <= _d(standard["hours"]["max"]):
        return "stage2-standard"
    return ABOVE


def priced(
    hours: dict, rate, pipeline: dict, tier: str | None
) -> tuple[dict, str | None]:
    """price_usd {low, point, high} and the clamp note of point (None when unclamped)."""
    round_to = pipeline["pricing"]["round_to_usd"]
    prices = {k: price(v, rate, round_to) for k, v in hours.items()}
    row = _tiers(pipeline).get(tier) if tier else None
    if row is None:
        return prices, (f"not clamped: {tier}" if tier else None)
    lo, hi = row["price_usd"]["min"], row["price_usd"]["max"]
    point = prices["point"]
    if point < lo:
        prices["point"] = lo
        return prices, f"{tier} point raised from {point} to the tier minimum {lo} USD"
    if point > hi:
        prices["point"] = hi
        return prices, f"{tier} point lowered from {point} to the tier maximum {hi} USD"
    return prices, None


# ----------------------------------------------------------------------------- rules


def _inventory_index(inv: dict) -> dict:
    return {(o["type"], o["name"]): o.get("metrics") or {} for o in inv["objects"]}


def by_area(inv: dict, rules: dict, coef: dict) -> dict:
    index = _inventory_index(inv)
    kind = inv["source"]["kind"]
    totals: dict[str, Decimal] = {}
    for obj in rules.get("objects") or []:
        if obj.get("decision") != "keep" or not obj.get("area"):
            continue
        metrics = index.get((obj["type"], obj["name"]), {})
        hours = object_hours(kind, obj["type"], metrics, coef)
        totals[obj["area"]] = totals.get(obj["area"], Decimal(0)) + hours
    return {a: _num(h) for a, h in sorted(totals.items())}


def rule_hours(rule: dict, inv: dict, coef: dict) -> Decimal:
    index = _inventory_index(inv)
    kind = inv["source"]["kind"]
    seen = set()
    total = Decimal(0)
    for src in rule.get("sources") or []:
        key = (src.get("type"), src.get("ref"))
        if key in seen:
            continue
        seen.add(key)
        total += object_hours(kind, key[0], index.get(key, {}), coef)
    return total


def find_committed(rules_path: Path, sha: str) -> bytes | None:
    """The committed version of rules_path whose sha256 is sha, or None."""
    rules_path = Path(rules_path).resolve()
    git = ["git", "-C", str(rules_path.parent)]
    top = subprocess.run(
        [*git, "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if top.returncode != 0:
        return None
    root = Path(top.stdout.strip()).resolve()
    rel = rules_path.relative_to(root).as_posix()
    git = ["git", "-C", str(root)]  # pathspecs are relative to the working directory
    log = subprocess.run(
        [*git, "log", "--format=%H", "--", rel],
        capture_output=True,
        text=True,
        check=False,
    )
    if log.returncode != 0:
        return None
    for commit in log.stdout.split():
        shown = subprocess.run(
            [*git, "show", f"{commit}:{rel}"], capture_output=True, check=False
        )
        if shown.returncode == 0 and sha256_bytes(shown.stdout) == sha:
            return shown.stdout
    return None


def delta(base: dict, new: dict, base_sha: str, inv: dict, coef: dict, rate, pipeline):
    old_rules = {r["id"]: r for r in base.get("rules") or []}
    new_rules = {r["id"]: r for r in new.get("rules") or []}
    added = sorted(set(new_rules) - set(old_rules))
    removed = sorted(set(old_rules) - set(new_rules))
    changed = sorted(
        rid
        for rid in set(new_rules) & set(old_rules)
        if json.dumps(new_rules[rid], sort_keys=True)
        != json.dumps(old_rules[rid], sort_keys=True)
    )
    areas: dict[str, Decimal] = {}
    for rid in added + changed:
        rule = new_rules[rid]
        areas[rule["area"]] = areas.get(rule["area"], Decimal(0)) + rule_hours(
            rule, inv, coef
        )
    total = sum((_tenth(v) for v in areas.values()), Decimal(0))
    return {
        "base_rules_sha256": base_sha,
        "by_area": {a: _num(h) for a, h in sorted(areas.items())},
        "price_usd": price(total, rate, pipeline["pricing"]["round_to_usd"]),
        "added": added,
        "removed": removed,
        "changed": changed,
    }


# ----------------------------------------------------------------------------- compute


def compute(
    inv: dict,
    inventory_sha256: str,
    pipeline: dict,
    coef: dict,
    coefficients_sha256: str,
    rules: dict | None = None,
    rules_sha256: str | None = None,
    base_rules: dict | None = None,
    base_sha256: str | None = None,
) -> dict:
    if inv.get("format_version") != 1:
        raise EstimateError("inventory.json: unknown format_version")
    kind = inv["source"]["kind"]
    facts = inv.get("facts") or {}
    rate = inv.get("rate_usd_per_hour")
    if rate is None:
        rate = pipeline["pricing"]["rate_usd_per_hour"]
    hours = band(stage2_hours(inv, coef), coef)
    tier = pick_tier(hours["point"], int(facts.get("data_backends") or 0), pipeline)
    prices, note = priced(hours, rate, pipeline, tier)
    notes = [note] if note else []
    stage1 = None
    if kind in ACCESS_KINDS:
        s1_hours = band(stage1_hours(inv, coef), coef)
        s1_prices, s1_note = priced(s1_hours, rate, pipeline, "stage1")
        if s1_note:
            notes.append(s1_note)
        stage1 = {
            "hours": {k: _num(v) for k, v in s1_hours.items()},
            "price_usd": s1_prices,
        }
    out = {
        "format_version": FORMAT_VERSION,
        "inventory_sha256": inventory_sha256,
        "rules_sha256": rules_sha256 if rules is not None else None,
        "coefficients_sha256": coefficients_sha256,
        "hours": {k: _num(v) for k, v in hours.items()},
        "tier": tier,
        "rate_usd_per_hour": rate,
        "price_usd": prices,
        "price_note": "; ".join(notes) if notes else None,
        "stage1": stage1,
        "by_area": by_area(inv, rules, coef) if rules is not None else None,
        "delta": None,
    }
    if base_rules is not None and rules is not None:
        out["delta"] = delta(base_rules, rules, base_sha256, inv, coef, rate, pipeline)
    return out


# ----------------------------------------------------------------------------- render


def _usd(n) -> str:
    return f"{int(n):,}"


def _h(n) -> str:
    return f"{n:g}" if isinstance(n, float) else str(n)


def render_block(est: dict) -> str:
    h, p = est["hours"], est["price_usd"]
    lines = [
        "| Offer | Hours (low / point / high) | Price USD (low / point / high) |",
        "|---|---|---|",
        (
            f"| Stage 2 ({est['tier']}) | {_h(h['low'])} / {_h(h['point'])} / {_h(h['high'])} "
            f"| {_usd(p['low'])} / {_usd(p['point'])} / {_usd(p['high'])} |"
        ),
    ]
    if est.get("stage1"):
        sh, sp = est["stage1"]["hours"], est["stage1"]["price_usd"]
        lines.append(
            f"| Stage 1 alone | {_h(sh['low'])} / {_h(sh['point'])} / {_h(sh['high'])} "
            f"| {_usd(sp['low'])} / {_usd(sp['point'])} / {_usd(sp['high'])} |"
        )
    lines += [
        "",
        (
            f"Rate: {est['rate_usd_per_hour']} USD per hour. Quoted price: "
            f"{_usd(p['point'])} USD (the point estimate)."
        ),
    ]
    lines.append(f"Price note: {est['price_note'] or 'none'}.")
    if est.get("by_area"):
        lines += ["", "| Area | Hours |", "|---|---|"]
        lines += [f"| {a} | {_h(v)} |" for a, v in est["by_area"].items()]
    d = est.get("delta")
    if d:
        lines += [
            "",
            (
                f"Change since rules version {d['base_rules_sha256'][:12]}: "
                f"{len(d['added'])} added, {len(d['changed'])} changed, "
                f"{len(d['removed'])} removed; {_usd(d['price_usd'])} USD."
            ),
        ]
        for label in ("added", "changed", "removed"):
            if d[label]:
                lines.append(f"- {label}: {', '.join(d[label])}")
        if d["by_area"]:
            lines += ["", "| Area | Hours added |", "|---|---|"]
            lines += [f"| {a} | {_h(v)} |" for a, v in d["by_area"].items()]
    return "\n".join(lines)


def render_into(text: str, est: dict) -> str | None:
    """text with the marked block replaced, or None when a marker is missing."""
    start = text.find(BEGIN)
    end = text.find(END, start + len(BEGIN)) if start >= 0 else -1
    if start < 0 or end < 0:
        return None
    head = text[: start + len(BEGIN)]
    tail = text[end:]
    return f"{head}\n{render_block(est)}\n{tail}"
