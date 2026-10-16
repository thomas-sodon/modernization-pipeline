"""Estimate effort and price from inventory.json, or render an estimate into a quote.

`estimate <inventory.json> [--rules <rules.json>] [--delta <sha256>] --out <file.json>`
`estimate render <estimate.json> --into <quote.md> [--check]`

The estimate is byte-deterministic (sorted keys, no timestamps): hours {low, point, high} from
toolkit/estimate.toml, the pricing_tiers row, price_usd by the pipeline.json pricing rule with
point clamped to the tier row (price_note names a clamp or why there is none), the Stage 1 band
for Access kinds, by_area with --rules, and with --delta the change since the committed rules.json
version whose sha256 is given (exit 2 when none matches). `render` replaces only the block
between <!-- estimate:begin --> and <!-- estimate:end -->: missing markers exit 2, or 1 with
--check, which writes nothing and exits 1 on any difference.
"""

from __future__ import annotations

import json
from pathlib import Path

from toolkit import estimate as est_mod
from toolkit import quiet
from toolkit.cli import UsageError
from toolkit.engagement import pipeline_json

COMMAND = "estimate"


def add_arguments(parser) -> None:
    parser.add_argument(
        "inputs",
        nargs="+",
        metavar="<inventory.json> | render <estimate.json>",
        help="inventory.json, or 'render' and an estimate.json",
    )
    parser.add_argument("--rules", help="rules.json (by_area from the kept objects)")
    parser.add_argument("--delta", metavar="SHA256", help="base rules.json sha256")
    parser.add_argument("--out", help="estimate.json to write")
    parser.add_argument("--into", help="render: quote.md with the estimate markers")
    parser.add_argument(
        "--check", action="store_true", help="render: compare, write nothing"
    )


def _read_json(path: Path, what: str) -> dict:
    if not path.is_file():
        raise est_mod.EstimateError(f"{what} missing")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise est_mod.EstimateError(f"{what} is not JSON") from exc


def _render(args) -> int:
    command = "estimate render"
    if len(args.inputs) != 2 or not args.into or args.out or args.rules or args.delta:
        raise UsageError(
            "usage: estimate render <estimate.json> --into <quote.md> [--check]"
        )
    est = _read_json(Path(args.inputs[1]), "estimate.json")
    if est.get("format_version") != 1:
        return quiet.summary(
            "error", command, 2, messages=["estimate.json: unknown format_version"]
        )
    into = Path(args.into)
    if not into.is_file():
        return quiet.summary("error", command, 2, messages=["quote file missing"])
    text = into.read_text(encoding="utf-8")
    new = est_mod.render_into(text, est)
    if new is None:
        return quiet.summary(
            None,
            command,
            1 if args.check else 2,
            messages=["estimate markers missing"],
        )
    if args.check:
        if new != text:
            return quiet.summary(
                None,
                command,
                1,
                messages=["estimate block differs from a fresh render"],
            )
        return quiet.summary(None, command, 0)
    into.write_text(new, encoding="utf-8")
    return quiet.summary(None, command, 0, paths=[str(into.resolve())])


def run(args) -> int:
    if args.inputs[0] == "render":
        try:
            return _render(args)
        except est_mod.EstimateError as exc:
            return quiet.summary("error", "estimate render", 2, messages=[str(exc)])
    if len(args.inputs) != 1 or not args.out or args.into or args.check:
        raise UsageError(
            "usage: estimate <inventory.json> [--rules <rules.json>] [--delta <sha256>] --out <file.json>"
        )
    if args.delta and not args.rules:
        raise UsageError("--delta needs --rules")
    try:
        inv_path = Path(args.inputs[0])
        if not inv_path.is_file():
            raise est_mod.EstimateError("inventory.json missing")
        inv_bytes = inv_path.read_bytes()
        inv = json.loads(inv_bytes.decode("utf-8"))
        rules = rules_sha = base = None
        if args.rules:
            rules_path = Path(args.rules)
            if not rules_path.is_file():
                raise est_mod.EstimateError("rules.json missing")
            rules_bytes = rules_path.read_bytes()
            rules = json.loads(rules_bytes.decode("utf-8"))
            rules_sha = est_mod.sha256_bytes(rules_bytes)
            if args.delta:
                found = est_mod.find_committed(rules_path, args.delta)
                if found is None:
                    raise est_mod.EstimateError(
                        "no committed version of the rules file has that sha256"
                    )
                base = json.loads(found.decode("utf-8"))
        coef, coef_sha = est_mod.load_coefficients()
        est = est_mod.compute(
            inv,
            est_mod.sha256_bytes(inv_bytes),
            pipeline_json(),
            coef,
            coef_sha,
            rules=rules,
            rules_sha256=rules_sha,
            base_rules=base,
            base_sha256=args.delta,
        )
    except est_mod.EstimateError as exc:
        return quiet.summary("error", COMMAND, 2, messages=[str(exc)])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(est_mod.dumps(est), encoding="utf-8")
    counts = {
        "hours_point": round(est["hours"]["point"]),
        "price_usd_point": est["price_usd"]["point"],
    }
    if est["stage1"]:
        counts["stage1_price_usd_point"] = est["stage1"]["price_usd"]["point"]
    messages = [f"tier: {est['tier']}"]
    if est["price_note"]:
        messages.append(f"price note: {est['price_note']}")
    return quiet.summary(
        None,
        COMMAND,
        0,
        engagement=inv.get("engagement"),
        counts=counts,
        sha256s={"estimate.json": est_mod.sha256_bytes(out.read_bytes())},
        paths=[str(out.resolve())],
        messages=messages,
    )
