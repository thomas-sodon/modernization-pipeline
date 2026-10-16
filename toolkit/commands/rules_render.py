"""Render keep-drop.md from a rules file (docs/contracts.md#rules-json).

Writes the Keep, Drop and Out of scope tables, sorted by area, type and name, deterministically.
With --check it renders in memory, prints the first differing line numbers and exits 1 on any
difference (a missing file counts); it never writes. Exit 2 when the rules file is missing."""

from __future__ import annotations

import sys
from pathlib import Path

from toolkit import quiet
from toolkit.rules import (
    RULES_SCHEMA,
    InputError,
    differing_lines,
    load_json,
    render_keep_drop,
    schema_problems,
    show,
)

COMMAND = "rules-render"


def add_arguments(parser) -> None:
    parser.add_argument("rules", help="analysis/rules.json")
    parser.add_argument(
        "--keep-drop",
        required=True,
        metavar="MD",
        help="keep-drop.md to write or check",
    )
    parser.add_argument(
        "--check", action="store_true", help="compare only; never write"
    )


def run(args) -> int:
    try:
        doc = load_json(args.rules, "rules file")
    except InputError as exc:
        print(f"{COMMAND}: {exc}", file=sys.stderr)
        return quiet.summary("error", COMMAND, 2, messages=[str(exc)])
    problems = [show(p) for p in schema_problems(doc, RULES_SCHEMA)]
    if problems:
        for line in problems:
            print(line, file=sys.stderr)
        return quiet.summary(
            None,
            COMMAND,
            1,
            counts={"problems": len(problems)},
            messages=["rules file fails its schema; run rules-lint", *problems],
        )
    text = render_keep_drop(doc)
    target = Path(args.keep_drop)
    counts = {"objects": len(doc["objects"]), "lines": text.count("\n")}
    if not args.check:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
        return quiet.summary(
            None, COMMAND, 0, counts=counts, messages=[f"wrote {target.name}"]
        )
    if not target.is_file():
        msg = f"{target.name} missing"
        print(msg, file=sys.stderr)
        return quiet.summary(None, COMMAND, 1, counts=counts, messages=[msg])
    current = target.read_bytes().decode("utf-8", errors="replace")
    if current == text:
        return quiet.summary(
            None, COMMAND, 0, counts=counts, messages=[f"{target.name} is up to date"]
        )
    lines = differing_lines(text, current)
    shown = ", ".join(str(n) for n in lines[:10])
    msg = f"{target.name} differs from the render at line(s) {shown}" + (
        " ..." if len(lines) > 10 else ""
    )
    print(msg, file=sys.stderr)
    counts["differing_lines"] = len(lines)
    return quiet.summary(None, COMMAND, 1, counts=counts, messages=[msg])
