"""Render the one-page health check from inventory.json (templates/docs/health-check.md).

`healthcheck <private>/out/inventory.json --out <target>/docs/health-check.md [--check]`

Sections: Size, Risk flags (each with its trigger; more than one data back end is a custom
quote, and a back end not received is named), Complexity, Effort and price band, Next step.
Object names and counts only, no data values, no private paths and no date. --check renders in
memory and exits 1 when the file differs or is missing; it writes nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

from toolkit import estimate as est_mod
from toolkit import healthcheck as hc
from toolkit import quiet
from toolkit.engagement import pipeline_json

COMMAND = "healthcheck"


def add_arguments(parser) -> None:
    parser.add_argument("inventory", help="inventory.json (design zone)")
    parser.add_argument("--out", required=True, help="health-check.md to write")
    parser.add_argument(
        "--check", action="store_true", help="compare with --out, write nothing"
    )


def run(args) -> int:
    inv_path = Path(args.inventory)
    if not inv_path.is_file():
        return quiet.summary("error", COMMAND, 2, messages=["inventory.json missing"])
    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    if inv.get("format_version") != 1:
        return quiet.summary(
            "error", COMMAND, 2, messages=["inventory.json: unknown format_version"]
        )
    coef, coef_sha = est_mod.load_coefficients()
    text = hc.render(
        inv, pipeline_json(), coef, coef_sha, hc.TEMPLATE.read_text(encoding="utf-8")
    )
    out = Path(args.out)
    flags = len(hc.risk_flags(inv))
    counts = {"risk_flags": flags, "lines": len(text.splitlines())}
    if args.check:
        current = out.read_text(encoding="utf-8") if out.is_file() else None
        if current != text:
            why = "missing" if current is None else "differs from a fresh render"
            return quiet.summary(
                None,
                COMMAND,
                1,
                engagement=inv.get("engagement"),
                counts=counts,
                messages=[f"health-check.md {why}"],
            )
        return quiet.summary(
            None, COMMAND, 0, engagement=inv.get("engagement"), counts=counts
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return quiet.summary(
        None,
        COMMAND,
        0,
        engagement=inv.get("engagement"),
        counts=counts,
        paths=[str(out.resolve())],
    )
