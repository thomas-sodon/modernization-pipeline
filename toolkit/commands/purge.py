"""Delete an engagement's private data and databases when retention ends.

`purge <eng> [--dry-run]` (docs/contracts.md#databases, #records; cli-registry). Runs only as
`mp-data purge`, which gives it the superuser URLs, MP_OTHER_DB_NAMES and write access to
<private> and <records>. It empties <private>, drops this engagement's databases on every local
server that answers, and writes <records>/destruction-record-<YYYY-MM-DD>.md listing what went,
the servers not checked, the source hashes and the Windows-box copies to delete by hand.
--dry-run prints the same plan as counts, sizes and database names and changes nothing.
Exit 2 when MP_OTHER_DB_NAMES is unset or <private> is a symlink or wrongly named.
"""

from __future__ import annotations

from toolkit import purge as pg
from toolkit import quiet
from toolkit.engagement import load

COMMAND = "purge"


def add_arguments(parser) -> None:
    parser.add_argument("engagement", help="<records>/engagement.json")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the plan, delete nothing"
    )


def run(args) -> int:
    eng = load(args.engagement)
    try:
        code, plan, record, problems = pg.run(eng, args.dry_run)
    except pg.PurgeError as exc:
        quiet.progress(f"{COMMAND}: {exc}")
        return quiet.summary(
            None, COMMAND, 2, engagement=eng["id"], messages=[str(exc)]
        )
    messages = problems + pg.plan_messages(plan, args.dry_run)
    for m in messages:
        quiet.progress(f"{COMMAND}: {m}")
    counts = pg.counts(plan)
    if not args.dry_run:
        counts["databases"] = sum(len(s.dropped) for s in plan.servers)
    return quiet.summary(
        None,
        COMMAND,
        code,
        engagement=eng["id"],
        counts=counts,
        paths=[str(record)] if record else None,
        messages=messages,
    )
