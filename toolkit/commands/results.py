"""Summarize the S6 JUnit files into the committed equivalence/results.json.

`results summarize <eng> --junit-dir DIR [--junit-dir DIR ...] --out FILE`
(docs/contracts.md#harness, #pass-rate). It reads <private>/results/ (data zone), so it runs only
as `mp-data results summarize`, whose jail lets it write only --out. The required cells come from
the engagement (database.primary, database.verify_also) and goldens/manifest.json (seeds). Exit 1
when a required JUnit file is in no directory, 2 when one is in two directories or malformed.
The output and the summary hold counts and unit ids only, never a failure message.
"""

from __future__ import annotations

from pathlib import Path

from toolkit import quiet
from toolkit import results as res
from toolkit.engagement import load

COMMAND = "results summarize"


def add_arguments(parser) -> None:
    sub = parser.add_subparsers(
        dest="_results_action", metavar="<action>", parser_class=type(parser)
    )
    sub.required = True
    p = sub.add_parser("summarize", help="JUnit files into results.json")
    p.add_argument("engagement", help="<records>/engagement.json")
    p.add_argument(
        "--junit-dir",
        action="append",
        required=True,
        help="directory holding junit-<suite>-<dataset>-<db>.xml (repeatable)",
    )
    p.add_argument("--out", required=True, help="<target>/equivalence/results.json")
    p.set_defaults(_command=COMMAND, _run=run_summarize)


def run(args) -> int:
    return args._run(args)


def run_summarize(args) -> int:
    eng = load(args.engagement)
    try:
        s = res.summarize(eng, args.junit_dir)
    except res.ResultsError as exc:
        quiet.progress(f"{COMMAND}: {exc}")
        return quiet.summary(
            None, COMMAND, exc.exit_code, engagement=eng["id"], messages=[str(exc)]
        )
    out = Path(args.out)
    res.write(out, res.dumps(s.doc))
    eqc = [c for c in s.cells if c.suite == "equivalence"]
    gate = s.doc["gate"]
    counts = {
        "cells": len(s.cells),
        "equivalence_cells": len(eqc),
        "passed": sum(c.passed for c in eqc),
        "tests": sum(c.total for c in eqc),
        "not_passed": sum(c.total - c.passed for c in eqc),
    }
    for c in s.cells:
        quiet.progress(
            f"{c.suite:<12}{c.dataset:<12}{c.db:<12}{c.passed}/{c.total}  {c.rate:.4f}"
        )
    return quiet.summary(
        None,
        COMMAND,
        0,
        engagement=eng["id"],
        counts=counts,
        rates={"min_cell": gate["min_cell_rate"], "pooled": gate["pooled_rate"]},
        cells=[c.entry(False) for c in s.cells],
        paths=[str(out.resolve())],
        messages=s.messages,
    )
