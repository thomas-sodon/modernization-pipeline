"""Check analysis/rules.json and rules.md against the signed spec (docs/contracts.md#records).

The approved state is gates.json S1 {rules_sha256, rules_md_sha256}, replaced by the
highest-numbered approved CO-NNN entry. Exit 0 when both files hash to it, or to the _after hashes
of the highest-numbered pending change order whose base is the approved rules hash ('pending
CO-NNN: approve at the gate'). Exit 1 otherwise ('spec not signed' without S1 hashes); exit 2 when
rules.json, rules.md or gates.json is missing. A missing change-orders directory is no error."""

from __future__ import annotations

import sys

from toolkit import quiet
from toolkit.rules import InputError, spec_check

COMMAND = "spec-check"


def add_arguments(parser) -> None:
    parser.add_argument(
        "analysis", help="the analysis/ directory holding rules.json and rules.md"
    )
    parser.add_argument("--gates", required=True, help="<records>/gates.json")
    parser.add_argument(
        "--change-orders", required=True, metavar="DIR", help="<records>/change-orders/"
    )


def run(args) -> int:
    try:
        res = spec_check(args.analysis, args.gates, args.change_orders)
    except InputError as exc:
        print(f"{COMMAND}: {exc}", file=sys.stderr)
        return quiet.summary("error", COMMAND, 2, messages=[str(exc)])
    sha = {"rules.json": res.rules_sha256, "rules.md": res.rules_md_sha256}
    if res.approved:
        state = res.approved["state"]
        for key, name in (
            ("rules_sha256", "rules.json"),
            ("rules_md_sha256", "rules.md"),
        ):
            value = res.approved.get(key)
            if isinstance(value, str) and len(value) == 64:
                sha[f"approved {state} {name}"] = value
    for key, value in sha.items():
        print(f"{key}: {value}", file=sys.stderr)
    for line in res.messages:
        print(line, file=sys.stderr)
    return quiet.summary(None, COMMAND, res.code, sha256s=sha, messages=res.messages)
