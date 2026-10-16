"""Lint analysis/data-model.json (xlsm) against data-model.schema.json, then check snake_case
unique names, neutral types, keys and relationship columns, mapping targets, and that no source
column is both mapped and computed (docs/contracts.md#rules-json). Exit 0 clean, 1 problems,
2 missing or unreadable file."""

from __future__ import annotations

import sys

from toolkit import quiet
from toolkit.rules import InputError, lint_data_model, load_json, show

COMMAND = "schema-lint"


def add_arguments(parser) -> None:
    parser.add_argument("file", help="analysis/data-model.json")


def run(args) -> int:
    try:
        doc = load_json(args.file, "data model")
    except InputError as exc:
        print(f"{COMMAND}: {exc}", file=sys.stderr)
        return quiet.summary("error", COMMAND, 2, messages=[str(exc)])
    problems = [show(p) for p in lint_data_model(doc)]
    for line in problems:
        print(line, file=sys.stderr)
    counts = {"problems": len(problems)}
    if isinstance(doc, dict) and isinstance(doc.get("tables"), list):
        counts["tables"] = len(doc["tables"])
    return quiet.summary(
        None, COMMAND, 1 if problems else 0, counts=counts, messages=problems
    )
