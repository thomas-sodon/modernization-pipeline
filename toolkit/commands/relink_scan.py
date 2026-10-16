"""Scan the front end for ODBC relink problems and write docs/stage1-frontend-changes.md.

`toolkit relink-scan <eng> --model DIR --dialects from-engagement --out FILE
[--fail-on unsupported-type] [--check]` (docs/contracts.md#cli-registry). Reads only the
design-zone model, the engagement and, when present, {{p.target}}/analysis/rules.json. The
Markdown has 12 fixed sections, names, line numbers and counts only, and no run-dependent text,
so the same inputs give the same bytes.

--check renders in memory and compares with --out: exit 0 when equal, 1 when the file differs or
is missing (naming the differing sections); it never writes. --fail-on unsupported-type exits 1
listing every column whose type has no mapping in a selected dialect. Exit 2 when the model
directory is missing or the engagement does not load."""

from __future__ import annotations

import sys
from pathlib import Path

from toolkit import quiet, relink
from toolkit.engagement import load

COMMAND = "relink-scan"


def add_arguments(parser) -> None:
    parser.add_argument("engagement", help="<records>/engagement.json")
    parser.add_argument("--model", required=True, metavar="DIR", help="out/model directory")
    parser.add_argument(
        "--dialects",
        default="from-engagement",
        help="from-engagement (database.primary plus verify_also) or a comma list of "
        "postgresql, mssql",
    )
    parser.add_argument("--out", required=True, metavar="FILE", help="Markdown to write or check")
    parser.add_argument(
        "--fail-on",
        action="append",
        default=[],
        choices=["unsupported-type"],
        help="exit 1 when a column type has no mapping",
    )
    parser.add_argument("--check", action="store_true", help="compare only; never write")


def _error(message: str, engagement: str | None = None) -> int:
    print(f"{COMMAND}: {message}", file=sys.stderr)
    return quiet.summary("error", COMMAND, 2, engagement=engagement, messages=[message])


def run(args) -> int:
    eng = load(args.engagement)
    ident = eng.get("id")
    try:
        model = relink.load_model(args.model)
        dialects = relink.resolve_dialects(args.dialects, eng)
        rules = relink.load_rules(Path(eng["paths"]["target"]) / "analysis" / "rules.json")
    except relink.ModelError as exc:
        return _error(str(exc), ident)

    findings = relink.scan(eng, model, dialects, rules)
    text = relink.render(eng, findings)
    counts = relink.counts(findings)
    messages: list[str] = []
    code = 0

    target = Path(args.out)
    if args.check:
        if not target.is_file():
            messages.append(f"{target.name} missing")
            code = 1
        else:
            current = target.read_bytes().decode("utf-8", errors="replace")
            if current != text:
                diff = relink.differing_sections(text, current)
                messages.append(f"{target.name} differs in: {', '.join(diff)}")
                code = 1
            else:
                messages.append(f"{target.name} is up to date")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
        messages.append(f"wrote {target.name}")

    if "unsupported-type" in args.fail_on and findings.unsupported:
        unsupported = relink.unsupported_messages(findings)
        messages.extend(unsupported)
        code = 1
    for line in messages:
        print(line, file=sys.stderr)
    return quiet.summary(None, COMMAND, code, engagement=ident, counts=counts, messages=messages)
