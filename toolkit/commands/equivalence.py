"""Compare an implementation with the goldens, cell by cell (dataset x database).

`equivalence <eng> --adapter reference|django --datasets synthetic|engagement|all|<list>
[--dbs postgresql|mssql|from-engagement] [-k EXPR] [--only-areas A[,B...]] [--collect-only]
[--allow-not-implemented] [--allow-empty] [--min-pass-per-cell F] [--junit-dir DIR]
[--matrix FILE]` (docs/contracts.md#harness, #pass-rate). Runs that include the engagement dataset
run only through mp-data. Exit 0 pass, 1 below the rate or a failed outcome, 2 usage or input
errors, 5 when every cell selected nothing (0 with --allow-empty, status no-data).
"""

from __future__ import annotations

from toolkit import equivalence as eq
from toolkit.engagement import load


def add_arguments(parser) -> None:
    parser.add_argument("engagement", help="<records>/engagement.json")
    parser.add_argument("--adapter", required=True, choices=("reference", "django"))
    parser.add_argument(
        "--datasets",
        required=True,
        help="synthetic, engagement, all, or a comma-separated list such as seed-1",
    )
    parser.add_argument(
        "--dbs",
        default=None,
        help="postgresql, mssql or from-engagement (django only)",
    )
    parser.add_argument(
        "-k", dest="keyword", default=None, help="pytest -k, passed to every cell"
    )
    parser.add_argument("--only-areas", default=None, help="comma-separated area ids")
    parser.add_argument(
        "--collect-only", action="store_true", help="print the test ids, run nothing"
    )
    parser.add_argument("--allow-not-implemented", action="store_true")
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument("--min-pass-per-cell", type=float, default=None)
    parser.add_argument("--junit-dir", default=None)
    parser.add_argument("--matrix", default=None)


def run(args) -> int:
    eng = load(args.engagement)
    opts = eq.Options(
        adapter=args.adapter,
        datasets=args.datasets,
        dbs=args.dbs,
        keyword=args.keyword,
        only_areas=args.only_areas,
        collect_only=args.collect_only,
        allow_not_implemented=args.allow_not_implemented,
        allow_empty=args.allow_empty,
        min_pass_per_cell=args.min_pass_per_cell,
        junit_dir=args.junit_dir,
        matrix=args.matrix,
    )
    return eq.run(eng, args.engagement, opts)
