"""Render the equivalence report the client receives.

`report <eng> --results <target>/equivalence/results.json --out <target>/docs/equivalence-report.md
[--check]` (docs/contracts.md#harness, #pass-rate). Reads only committed files and the design-zone
inventory (never the data zone or the reference catalog) and renders
templates/docs/equivalence-report.md into 8 sections. Exit 1, listing the reasons, when the gate
or one of the report's checks fails; the file is still written so the gap is visible. --check
renders in memory, compares with --out byte for byte, exits 1 on any difference and writes nothing.
"""

from __future__ import annotations

from pathlib import Path

from toolkit import quiet
from toolkit import report as rp
from toolkit.engagement import load

COMMAND = "report"


def add_arguments(parser) -> None:
    parser.add_argument("engagement", help="<records>/engagement.json")
    parser.add_argument(
        "--results", required=True, help="<target>/equivalence/results.json"
    )
    parser.add_argument(
        "--out", required=True, help="<target>/docs/equivalence-report.md"
    )
    parser.add_argument(
        "--check", action="store_true", help="compare with --out, write nothing"
    )


def run(args) -> int:
    eng = load(args.engagement)
    try:
        inp = rp.load_inputs(eng, Path(args.results))
        text, a = rp.render(inp)
    except rp.ReportError as exc:
        quiet.progress(f"{COMMAND}: {exc}")
        return quiet.summary(
            None, COMMAND, 2, engagement=eng["id"], messages=[str(exc)]
        )
    out = Path(args.out)
    messages = list(a.reasons)
    counts = {
        "cells": len(a.eq_cells),
        "kept_units": a.q,
        "passed": a.passed,
        "tests": a.tests,
        "reasons": len(a.reasons),
        "lines": len(text.splitlines()),
    }
    rates = {
        "min_cell": a.min_cell,
        "pooled": a.pooled,
        "coverage_adjusted": min(1.0, a.coverage_adjusted),
        "characterization": inp.status["rate"],
        "empty_share": inp.status["empty_share"],
    }
    paths = None
    if args.check:
        current = out.read_bytes() if out.is_file() else None
        if current != text.encode("utf-8"):
            messages.insert(
                0,
                f"{out.name} "
                + ("missing" if current is None else "differs from a fresh render"),
            )
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(text.encode("utf-8"))
        paths = [str(out.resolve())]
    for m in messages:
        quiet.progress(f"{COMMAND}: {m}")
    code = 1 if messages else 0
    return quiet.summary(
        None,
        COMMAND,
        code,
        engagement=eng["id"],
        counts=counts,
        rates=rates,
        paths=paths,
        messages=messages,
    )
