"""Create the Access tables on PostgreSQL and SQL Server, load a dataset and prove nothing was lost.

`toolkit stage1 <eng> --target-db local:<name>|secret:<ref>|URL [--dataset engagement|DIR|fresh-copy]
[--schema-from auto|model|data-model] [--schema-only] [--replace] [--verify] [--require-rows]
[--dialects from-engagement] [--report FILE]` (docs/contracts.md#cli-registry; toolkit/stage1.py).

- `local:<name>` is database <name> on the local server of each dialect --dialects selects
  (required; from-engagement = database.primary plus database.verify_also). The server URL is
  MP_DATA_PG_URL / MP_DATA_MSSQL_URL when set, else PG_URL / MSSQL_URL.
- `secret:<ref>` is the client's database named by engagement secrets.<ref>; --dialects and
  --replace are refused, and the URL is never printed, logged or written.
- A literal postgresql:// or mssql:// URL names one database; only for --schema-only and
  synthetic datasets outside the private directory.
- Engagement data (--dataset engagement, fresh-copy or a DIR under the private directory) loads
  only into a local: mpdata_* database or a secret: target.

Default mode creates a missing database, then creates and loads an empty target, does nothing when
tables and row counts already match, and otherwise exits 1. --replace drops every table first.
--schema-only reads only the design (never a dataset). --verify alone is read-only.
Exit 0 pass, 1 a check failed, 2 usage or missing input.
"""

from __future__ import annotations

import sys

from toolkit import quiet, stage1
from toolkit.engagement import load

COMMAND = stage1.COMMAND


def add_arguments(parser) -> None:
    parser.add_argument("engagement", help="<records>/engagement.json")
    parser.add_argument(
        "--target-db",
        required=True,
        metavar="local:<name>|secret:<ref>|URL",
        help="the database to create, load or check",
    )
    parser.add_argument(
        "--dataset",
        default="engagement",
        metavar="engagement|DIR|fresh-copy",
        help="engagement (the default), a dataset directory, or the cutover's fresh copy",
    )
    parser.add_argument(
        "--schema-from",
        default="auto",
        choices=["auto", "model", "data-model"],
        help="auto or model read out/model/schema.json; data-model is added by S4.models",
    )
    parser.add_argument(
        "--schema-only", action="store_true", help="DDL only; read no dataset"
    )
    parser.add_argument("--replace", action="store_true", help="drop every table first")
    parser.add_argument("--verify", action="store_true", help="run the checks")
    parser.add_argument(
        "--require-rows",
        action="store_true",
        help="exit 1 when a target table has fewer rows than the dataset or the dataset is empty",
    )
    parser.add_argument(
        "--dialects",
        default=None,
        help="from-engagement (required with local:, refused otherwise)",
    )
    parser.add_argument("--report", metavar="FILE", help="Markdown report to write")


def run(args) -> int:
    eng = load(args.engagement)
    ident = eng.get("id")
    try:
        code, fields = stage1.run(eng, args)
    except stage1.Stage1Error as exc:
        message = str(exc)
        print(f"{COMMAND}: {message}", file=sys.stderr)
        status = "failed" if exc.exit_code == 1 else "error"
        return quiet.summary(
            status, COMMAND, exc.exit_code, engagement=ident, messages=[message]
        )
    for line in fields["messages"]:
        print(line, file=sys.stderr)
    return quiet.summary(None, COMMAND, code, engagement=ident, **fields)
