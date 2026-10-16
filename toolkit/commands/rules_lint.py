"""Lint a rules file (analysis/rules.json or analysis/rules/<area>.json) against rules.schema.json
and the semantic rules of docs/contracts.md#rules-json. Each problem names its JSON pointer.
Exit 0 clean, 1 problems, 2 missing or unreadable file."""

from __future__ import annotations

import sys

from toolkit import quiet
from toolkit.rules import InputError, lint_rules, load_json, show

COMMAND = "rules-lint"


def add_arguments(parser) -> None:
    parser.add_argument(
        "file", help="rules.json (kind merged) or rules/<area>.json (kind area)"
    )


def run(args) -> int:
    try:
        doc = load_json(args.file, "rules file")
    except InputError as exc:
        print(f"{COMMAND}: {exc}", file=sys.stderr)
        return quiet.summary("error", COMMAND, 2, messages=[str(exc)])
    problems = [show(p) for p in lint_rules(doc)]
    for line in problems:
        print(line, file=sys.stderr)
    code = 1 if problems else 0
    counts = {"problems": len(problems)}
    if isinstance(doc, dict):
        for key in ("areas", "rules", "objects"):
            if isinstance(doc.get(key), list):
                counts[key] = len(doc[key])
    return quiet.summary(None, COMMAND, code, counts=counts, messages=problems)
