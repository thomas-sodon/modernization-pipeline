"""The Access side of `toolkit reference sql|build|run` (toolkit/commands/reference.py dispatches
here for source kinds accdt, vcs-export, accdb and mdb).

Summaries carry counts, unit ids with statuses and reasons, and row counts; never values.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from toolkit import quiet
from toolkit.reference import build as bld
from toolkit.reference import catalog as cat
from toolkit.sources import SourceError

STATUSES = ("ok", "empty", "needs-params", "unsupported", "failed")


def _paths(eng: dict) -> tuple[Path, Path]:
    return Path(eng["paths"]["private"]), Path(eng["paths"]["target"])


def _sql_command(eng_path: str) -> str:
    return f"uv run toolkit reference sql {eng_path} --all"


def in_data_plane() -> bool:
    return os.environ.get("MP_SANDBOX") == "mp-data"


# ------------------------------------------------------------------ sql


def write_catalog(eng: dict) -> tuple[dict, list[str], list[Path]]:
    private, target = _paths(eng)
    catalog, sqls, messages = cat.build(private, target, eng["id"])
    paths = cat.write(private, catalog, sqls)
    return catalog, messages, paths


def sql(eng: dict, args) -> int:
    command = "reference sql"
    if in_data_plane():
        return quiet.summary(
            "error",
            command,
            2,
            engagement=eng["id"],
            messages=[
                f"reference sql reads only the design zone: run {_sql_command(args.engagement)}"
            ],
        )
    try:
        catalog, messages, paths = write_catalog(eng)
    except (cat.CatalogError, SourceError) as exc:
        return quiet.summary(
            "error", command, 2, engagement=eng["id"], messages=[str(exc)]
        )
    counts = {
        "units": len(catalog["units"]),
        "ok": 0,
        "needs-params": 0,
        "unsupported": 0,
    }
    units = []
    for u in catalog["units"]:
        counts[u["status"]] = counts.get(u["status"], 0) + 1
        units.append({"id": u["id"], "status": u["status"], "reason": u["reason"]})
        if u["status"] != "ok":
            line = f"{u['id']}: {u['status']} ({u['reason']})"
            quiet.progress(line)
            messages.append(line)
    counts["needs_access_confirmation"] = sum(
        1 for u in catalog["units"] if u["needs_access_confirmation"]
    )
    for k in ("ok", "needs-params", "unsupported"):
        quiet.progress(f"{k}: {counts[k]}")
    return quiet.summary(
        None,
        command,
        0,
        engagement=eng["id"],
        counts=counts,
        units=units,
        paths=[str(p) for p in paths],
        messages=messages,
    )


# ------------------------------------------------------------------ build


def _ensure_catalog(eng: dict, eng_path: str, may_write: bool) -> dict:
    private, target = _paths(eng)
    state = cat.catalog_state(private, target)
    if state is not None:
        if not may_write:
            raise bld.BuildError(
                f"out/reference/catalog.json is {state}: run {_sql_command(eng_path)}"
            )
        write_catalog(eng)
    return cat.load_catalog(private)


def build(eng: dict, args) -> int:
    command = "reference build"
    dataset = args.dataset
    eid = eng["id"]
    if dataset == "all":
        return quiet.summary(
            "error",
            command,
            2,
            engagement=eid,
            messages=[
                "--dataset all is two commands, in this order:",
                f"uv run toolkit reference build {args.engagement} --dataset seeds",
                f"mp-data reference build {args.engagement} --dataset engagement",
            ],
        )
    result = bld.Result()
    try:
        if dataset == "engagement":
            if not in_data_plane():
                raise bld.BuildError(
                    f"the engagement reference builds only through mp-data: mp-data reference build {args.engagement} --dataset engagement"
                )
            _build_engagement(eng, args.engagement, result)
        else:
            if in_data_plane():
                raise bld.BuildError(
                    f"seed references build in the design zone: uv run toolkit reference build {args.engagement} --dataset {dataset}"
                )
            _build_seeds(eng, args.engagement, dataset, result)
    except bld.BuildError as exc:
        return quiet.summary(
            None,
            command,
            exc.exit_code,
            engagement=eid,
            counts=_build_counts(result),
            messages=[str(exc)] + result.messages,
        )
    except SourceError as exc:
        return quiet.summary("error", command, 2, engagement=eid, messages=[str(exc)])
    code = 0
    messages = list(result.messages)
    for uid, cls in sorted(result.query_failures.items()):
        messages.append(f"query {uid}: {cls}")
        code = 1
    for line in messages:
        quiet.progress(line)
    return quiet.summary(
        None,
        command,
        code,
        engagement=eid,
        counts=_build_counts(result),
        messages=messages,
    )


def _build_counts(result: bld.Result) -> dict:
    return {
        "databases_built": result.built,
        "databases_skipped": result.skipped,
        "tables": result.tables,
        "rows": result.rows,
        "queries": result.queries,
        "query_failures": len(result.query_failures),
    }


def _build_engagement(eng: dict, eng_path: str, result: bld.Result) -> None:
    from toolkit.dataset import has_no_data

    private, _target = _paths(eng)
    src = eng["source"]
    if has_no_data(eng):
        raise bld.BuildError("no engagement data: set source.data_copy")
    catalog = _ensure_catalog(eng, eng_path, may_write=False)
    target = private / "reference" / "engagement.accdb"
    copy_from = dataset_dir = None
    data_copy = src.get("data_copy")
    if (
        data_copy
        and Path(data_copy).is_dir()
        and (Path(data_copy) / "dataset.json").is_file()
    ):
        dataset_dir = Path(data_copy)
    elif data_copy:
        copy_from = Path(data_copy)
    elif (
        src["kind"] in ("accdb", "mdb")
        and (src.get("layout") or "single-file") == "single-file"
    ):
        copy_from = Path(src["path"])
    else:
        dataset_dir = private / "datasets" / "engagement"
    if copy_from is not None and not copy_from.is_file():
        raise bld.BuildError(
            "engagement data file missing: run mp-data fetch <eng> --verify"
        )
    if dataset_dir is not None and not (dataset_dir / "dataset.json").is_file():
        raise bld.BuildError(
            f"engagement dataset missing: run mp-data dataset export {eng_path} --out {private}/datasets/engagement"
        )
    bld.build_one(
        "engagement",
        target,
        bld.load_schema(private),
        dataset_dir,
        copy_from,
        cat.catalog_path(private),
        private,
        catalog,
        result,
        require_rows=True,
    )


def _build_seeds(eng: dict, eng_path: str, dataset: str, result: bld.Result) -> None:
    private, target_root = _paths(eng)
    seeds = bld.seed_dirs(target_root)
    if dataset != "seeds":
        if not dataset.startswith("seed-"):
            raise bld.BuildError(
                f"--dataset must be engagement, seeds, seed-N or all, not {dataset}"
            )
        seeds = [s for s in seeds if s[0] == dataset]
        if not seeds:
            raise bld.BuildError(
                f"{dataset}: goldens/synthetic/{dataset}/input/dataset.json missing"
            )
    catalog = _ensure_catalog(eng, eng_path, may_write=True)
    if not seeds:
        result.messages.append("no seed datasets under goldens/synthetic")
        return
    schema = bld.load_schema(private)
    for name, ddir in seeds:
        target = private / "synthetic" / "reference" / f"{name}.accdb"
        bld.build_one(
            name,
            target,
            schema,
            ddir,
            None,
            cat.catalog_path(private),
            private,
            catalog,
            result,
            require_rows=False,
        )


# ------------------------------------------------------------------ run


def params_values(eng: dict, dataset: str) -> dict[str, dict]:
    """{unit id: {param name: canonical string}} for `dataset` from the params file."""
    private, target = _paths(eng)
    path = (
        private / "goldens" / "params.json"
        if dataset == "engagement"
        else target / "goldens" / "params.json"
    )
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    out = {}
    for uid, entry in (doc.get("units") or {}).items():
        values = ((entry or {}).get("values") or {}).get(dataset)
        if values:
            out[uid] = values
    return out


def run(eng: dict, args) -> int:
    from toolkit.reference import engine as eng_mod

    command = "reference run"
    eid = eng["id"]
    private, _target = _paths(eng)
    dataset = args.dataset
    if dataset == "engagement" and not in_data_plane():
        return quiet.summary(
            "error",
            command,
            2,
            engagement=eid,
            messages=[
                f"the engagement dataset runs only through mp-data: mp-data reference run {args.engagement} --dataset engagement --all --summary"
            ],
        )
    try:
        db = eng_mod.database_for(private, dataset)
    except ValueError:
        return quiet.summary(
            "error",
            command,
            2,
            engagement=eid,
            messages=["--dataset must be engagement or seed-N"],
        )
    try:
        catalog = cat.load_catalog(private)
    except cat.CatalogError:
        return quiet.summary(
            "error",
            command,
            2,
            engagement=eid,
            messages=[f"catalog missing: run {_sql_command(args.engagement)}"],
        )
    if not db.is_file():
        return quiet.summary(
            "error",
            command,
            2,
            engagement=eid,
            messages=[
                f"reference database for {dataset} missing: run {eng_mod.build_command(dataset).replace('<eng>', args.engagement)}"
            ],
        )
    wanted = None if args.all else set(args.unit or [])
    values = params_values(eng, dataset)
    counts = {s: 0 for s in STATUSES}
    units, messages = [], []
    try:
        ref = eng_mod.AccessReference(private, db, catalog).open()
    except SourceError as exc:
        return quiet.summary("error", command, 2, engagement=eid, messages=[str(exc)])
    try:
        for u in catalog["units"]:
            uid = u["id"]
            if wanted is not None and uid not in wanted:
                continue
            entry = {
                "id": uid,
                "status": u["status"],
                "reason": u["reason"],
                "dataset": dataset,
            }
            if uid in ref.view_errors and u["status"] == "ok":
                entry["status"], entry["reason"] = "failed", None
                messages.append(f"{uid}: view {ref.view_errors[uid]}")
            elif ref.runnable(uid, values.get(uid)):
                try:
                    total = ref.count(uid, values.get(uid))
                    entry["status"] = "ok" if total else "empty"
                    entry["reason"] = None if total else "empty"
                    entry["total"] = total
                except eng_mod.ParamError as exc:
                    entry["status"], entry["reason"] = "needs-params", "other"
                    messages.append(f"{uid}: {exc}")
                except eng_mod.UnitError as exc:
                    entry["status"], entry["reason"] = "failed", None
                    messages.append(f"{uid}: {exc}")
            counts[entry["status"]] = counts.get(entry["status"], 0) + 1
            units.append(entry)
            line = f"{uid}: {entry['status']}" + (
                f" ({entry['reason']})" if entry["reason"] else ""
            )
            if "total" in entry:
                line += f", {entry['total']} rows"
            quiet.progress(line)
    finally:
        ref.close()
    counts["units"] = len(units)
    code = 1 if counts["failed"] else 0
    return quiet.summary(
        None,
        command,
        code,
        engagement=eid,
        counts=counts,
        units=units,
        messages=messages,
    )
