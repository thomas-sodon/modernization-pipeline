"""Coverage of the inventory by a rules file (docs/contracts.md#rules-json, Coverage).

The set is every inventory object whose type is in rule_bearing_kinds, plus every table. Prints
covered/total, the percentage with one decimal and the uncovered names. Exit 0 when the percentage
is at least --require and nothing hard-fails (an objects[] entry or a sources ref that is not in
the inventory, or a duplicate entry); 1 otherwise; 2 on a missing or unreadable input."""

from __future__ import annotations

import sys

from toolkit import quiet
from toolkit.rules import (
    RULES_SCHEMA,
    InputError,
    coverage,
    load_json,
    schema_problems,
    show,
)

COMMAND = "rules-coverage"


def add_arguments(parser) -> None:
    parser.add_argument("rules", help="analysis/rules.json")
    parser.add_argument("inventory", help="out/inventory.json")
    parser.add_argument(
        "--require", type=float, required=True, metavar="PCT", help="minimum percentage"
    )


def run(args) -> int:
    try:
        doc = load_json(args.rules, "rules file")
        inventory = load_json(args.inventory, "inventory")
        if not isinstance(inventory, dict):
            raise InputError("inventory is not an object")
        problems = schema_problems(doc, RULES_SCHEMA)
        if problems:
            lines = [show(p) for p in problems]
            for line in lines:
                print(line, file=sys.stderr)
            return quiet.summary(
                None,
                COMMAND,
                1,
                counts={"problems": len(lines)},
                messages=["rules file fails its schema; run rules-lint", *lines],
            )
        cov = coverage(doc, inventory)
    except InputError as exc:
        print(f"{COMMAND}: {exc}", file=sys.stderr)
        return quiet.summary("error", COMMAND, 2, messages=[str(exc)])

    hard = [show(p) for p in cov.hard]
    head = f"coverage {cov.covered}/{cov.total} = {cov.percent:.1f}% (require {args.require:g}%)"
    messages = [head, *hard]
    if cov.uncovered:
        messages.append("uncovered: " + ", ".join(cov.uncovered))
    print(head, file=sys.stderr)
    for line in hard:
        print(line, file=sys.stderr)
    for name in cov.uncovered:
        print(f"uncovered: {name}", file=sys.stderr)
    code = 0 if cov.meets(args.require) else 1
    return quiet.summary(
        None,
        COMMAND,
        code,
        counts={
            "covered": cov.covered,
            "total": cov.total,
            "uncovered": len(cov.uncovered),
            "hard_failures": len(hard),
        },
        messages=messages,
    )
