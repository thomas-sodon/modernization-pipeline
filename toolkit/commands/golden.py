"""Goldens: record unit outputs in the canonical form and report the characterization rate.

`golden record <eng> [--dataset engagement|seeds|all] [--private-only] [--check]`
(docs/contracts.md#goldens-manifest). Seeds read only committed seeds and the design zone, so
`--dataset seeds` runs as `uv run toolkit golden record`; engagement and all run only through
mp-data. `--check` re-records in memory and exits 1 on any difference, writing nothing.
`--private-only` records only the engagement dataset into <private>/goldens/ and exits 1 when an
engagement status, row count or digest differs from the committed manifest.

`golden status <goldens dir> --rules <rules.json> --dataset engagement --min-ok 0.9
--max-empty 0.25` (docs/contracts.md#pass-rate) reads only committed files.

Summaries carry counts, unit ids, statuses and reasons; never values.
"""

from __future__ import annotations

import re
from pathlib import Path

from toolkit import golden as gl
from toolkit import quiet
from toolkit.engagement import load


def add_arguments(parser) -> None:
    sub = parser.add_subparsers(
        dest="_golden_action", metavar="<action>", parser_class=type(parser)
    )
    sub.required = True
    p = sub.add_parser("record", help="record goldens and the manifest")
    p.add_argument("engagement", help="<records>/engagement.json")
    p.add_argument(
        "--dataset",
        default=None,
        help="engagement, seeds, seed-N or all (default: all; engagement with --private-only)",
    )
    p.add_argument(
        "--private-only",
        action="store_true",
        help="record only the engagement dataset into <private>/goldens/",
    )
    p.add_argument(
        "--check", action="store_true", help="re-record and compare; write nothing"
    )
    p.set_defaults(_command="golden record", _run=run_record)
    p = sub.add_parser("status", help="characterization rate of a dataset")
    p.add_argument("goldens", help="<target>/goldens")
    p.add_argument("--rules", required=True, help="<target>/analysis/rules.json")
    p.add_argument("--dataset", default="engagement")
    p.add_argument("--min-ok", type=float, default=0.9)
    p.add_argument("--max-empty", type=float, default=0.25)
    p.set_defaults(_command="golden status", _run=run_status)


def run(args) -> int:
    return args._run(args)


# ------------------------------------------------------------------ record


def _datasets(eng: dict, which: str) -> list[tuple[str, Path]]:
    private, target = Path(eng["paths"]["private"]), Path(eng["paths"]["target"])
    seeds = gl.seed_dirs(target)
    out: list[tuple[str, Path]] = []
    if which in ("engagement", "all"):
        out.append(("engagement", private / "datasets" / "engagement"))
    if which in ("seeds", "all"):
        out += seeds
    elif re.fullmatch(r"seed-\d+", which):
        out += [s for s in seeds if s[0] == which]
    return out


def _error(eng: dict | None, message: str, code: int = 2) -> int:
    """A one-message failure; a command to run goes on a line (message) of its own."""
    quiet.progress(message)
    return quiet.summary(
        None,
        "golden record",
        code,
        engagement=eng["id"] if eng else None,
        messages=message.split("\n"),
    )


def run_record(args) -> int:
    command = "golden record"
    eng = load(args.engagement)
    eid = eng["id"]
    private, target = Path(eng["paths"]["private"]), Path(eng["paths"]["target"])
    which = args.dataset or ("engagement" if args.private_only else "all")
    if which not in ("engagement", "seeds", "all") and not re.fullmatch(
        r"seed-\d+", which
    ):
        return _error(eng, "--dataset must be engagement, seeds, seed-N or all")
    if args.private_only and which != "engagement":
        return _error(eng, "--private-only records only the engagement dataset")
    if which in ("engagement", "all") and not gl.in_data_plane():
        return _error(
            eng,
            f"the engagement dataset records only through mp-data, run:\nmp-data golden record {args.engagement} --dataset {which}",
        )
    datasets = _datasets(eng, which)
    edir = private / "datasets" / "engagement"
    if which in ("engagement", "all") and not (edir / "dataset.json").is_file():
        return _error(
            eng,
            f"engagement dataset missing, run:\nmp-data dataset export {args.engagement} --out {edir}",
        )
    if which != "engagement" and not any(n != "engagement" for n, _d in datasets):
        return _error(eng, f"no seed dataset for {which} under goldens/synthetic")
    try:
        rec = gl.record_datasets(
            eng, args.engagement, datasets, all_seeds=which in ("seeds", "all")
        )
    except gl.GoldenError as exc:
        return _error(eng, str(exc), exc.exit_code)

    counts: dict[str, int] = {"units": len(rec.manifest["units"])}
    units = []
    for uid in sorted(rec.outcomes):
        for name, o in rec.outcomes[uid].items():
            k = f"{name}.{o.status}"
            counts[k] = counts.get(k, 0) + 1
            if o.status != "ok":
                units.append(
                    {"id": uid, "status": o.status, "reason": o.reason, "dataset": name}
                )
    messages: list[str] = []
    for name, _d in datasets:
        line = f"{name}: " + ", ".join(
            f"{counts.get(f'{name}.{s}', 0)} {s}" for s in gl.STATUSES
        )
        messages.append(line)
        quiet.progress(line)
    for u in units:
        quiet.progress(f"  {u['dataset']} {u['id']}: {u['status']} ({u['reason']})")
    if rec.failures:
        counts["failures"] = len(rec.failures)
        for f in rec.failures:
            quiet.progress(f"failed: {f}")
        units += [
            {"id": f.split(" on ", 1)[0], "status": "failed", "reason": None}
            for f in rec.failures
        ]
        return quiet.summary(
            None,
            command,
            1,
            engagement=eid,
            counts=counts,
            units=units,
            messages=[f"failed: {f}" for f in rec.failures] + messages,
        )

    manifest_path = target / "goldens" / "manifest.json"
    if args.private_only:
        counts["private_files_written"] = gl.write_private(rec, private)
        try:
            diffs = gl.compare_engagement(rec, manifest_path)
        except gl.GoldenError as exc:
            return _error(eng, str(exc), exc.exit_code)
        counts["differences"] = len(diffs)
        msgs = [
            f"engagement differs from the manifest: {u} ({what})" for u, what in diffs
        ]
        for m in msgs:
            quiet.progress(m)
        return quiet.summary(
            None,
            command,
            1 if diffs else 0,
            engagement=eid,
            counts=counts,
            units=units,
            messages=msgs + messages,
        )

    if args.check:
        diffs = gl.check_public(rec, target)
        if "engagement" in [n for n, _d in datasets]:
            diffs += gl.check_private(rec, private)
        counts["differences"] = len(diffs)
        for d in diffs:
            quiet.progress(d)
        return quiet.summary(
            None,
            command,
            1 if diffs else 0,
            engagement=eid,
            counts=counts,
            units=units,
            messages=(diffs or ["re-record matches the committed goldens"]) + messages,
        )

    written = gl.write_public(rec, target)
    if "engagement" in [n for n, _d in datasets]:
        counts["private_files_written"] = gl.write_private(rec, private)
    counts["files_written"] = len(written)
    goldens = target / "goldens"
    return quiet.summary(
        None,
        command,
        0,
        engagement=eid,
        counts=counts,
        units=units,
        paths=[str(goldens / "manifest.json"), str(goldens / "params.json")],
        messages=messages,
    )


# ------------------------------------------------------------------ status


def run_status(args) -> int:
    command = "golden status"
    goldens = Path(args.goldens)
    try:
        rep = gl.status_report(goldens, Path(args.rules), args.dataset)
    except gl.GoldenError as exc:
        return quiet.summary(
            "error", command, exc.exit_code, messages=str(exc).split("\n")
        )
    messages = list(rep["problems"])
    if rep["rate"] < args.min_ok:
        messages.append(f"characterization rate {rep['rate']} is below {args.min_ok}")
    if rep["empty_share"] > args.max_empty:
        messages.append(f"empty share {rep['empty_share']} is above {args.max_empty}")
    code = 1 if messages else 0
    counts = {"units": rep["q"]}
    for s, n in rep["by_status"].items():
        counts[s] = n
    for area, per in sorted(rep["by_area"].items()):
        for s, n in sorted(per.items()):
            counts[f"{area}.{s}"] = n
    quiet.progress(
        f"{args.dataset}: {rep['q']} kept units, rate {rep['rate']}, empty share {rep['empty_share']}"
    )
    for s, n in rep["by_status"].items():
        quiet.progress(f"  {s}: {n}")
    for area, per in sorted(rep["by_area"].items()):
        quiet.progress(
            f"  area {area}: " + ", ".join(f"{n} {s}" for s, n in sorted(per.items()))
        )
    for u in rep["not_ok"]:
        quiet.progress(f"  not ok: {u['id']} {u['status']} ({u['reason']})")
    for m in messages:
        quiet.progress(m)
    return quiet.summary(
        None,
        command,
        code,
        counts=counts,
        rates={"characterization": rep["rate"], "empty_share": rep["empty_share"]},
        units=rep["not_ok"],
        paths=[str(goldens.resolve() / "manifest.json")],
        messages=messages,
    )
