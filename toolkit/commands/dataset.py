"""Export or validate a dataset directory (docs/contracts.md#dataset-directory).

`dataset export <eng> --out DIR [--check]`: reads the engagement's data through the source
readers and writes DIR atomically (a temp directory next to DIR, validated, then renamed over
DIR); exit 1 when what it wrote is invalid. --check regenerates into a temp directory, compares
it with DIR file by file and exits 1 on any difference, writing nothing. Runs only through
mp-data (it reads the data zone).

`dataset validate <dir> --schema <schema.json>`: checks dataset.json against
toolkit/schemas/dataset.schema.json and every table, column, CSV rule, row count, sha256 and
foreign key against the schema; exit 1 on a problem, 2 when dataset.json is missing.

Export summaries hold aggregate counts and fixed messages only; table and column names,
per-table row counts and sha256s go to stderr (the data-zone log under mp-data). Never values.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import jsonschema

from toolkit import dataset as ds
from toolkit import quiet
from toolkit.engagement import load
from toolkit.sources import SourceError

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "dataset.schema.json"


def add_arguments(parser) -> None:
    sub = parser.add_subparsers(
        dest="_dataset_action", metavar="<action>", parser_class=type(parser)
    )
    sub.required = True
    exp = sub.add_parser("export", help="export the engagement data (mp-data only)")
    exp.add_argument("engagement", help="<records>/engagement.json")
    exp.add_argument("--out", required=True, help="dataset directory (data zone)")
    exp.add_argument(
        "--check",
        action="store_true",
        help="regenerate into a temp dir and compare with --out; write nothing",
    )
    exp.set_defaults(_command="dataset export", _run=run_export)
    val = sub.add_parser("validate", help="validate a dataset directory")
    val.add_argument("directory", help="dataset directory")
    val.add_argument("--schema", required=True, help="model schema.json")
    val.set_defaults(_command="dataset validate", _run=run_validate)


def run(args) -> int:  # dispatch happens through _run set by the sub-parsers
    return args._run(args)


# ------------------------------------------------------------------ helpers


def schema_problems(meta: dict) -> list[str]:
    """dataset.json against dataset.schema.json (paths only, never values)."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    out = []
    for err in sorted(validator.iter_errors(meta), key=lambda e: list(e.path)):
        where = "/".join(str(p) for p in err.path) or "(root)"
        out.append(f"dataset.json: {where}: {err.validator} check failed")
    return out


def check_dir(directory: Path, schema, allow_absent=()) -> dict:
    meta = ds.read_meta(directory)
    problems = schema_problems(meta)
    result = ds.validate(directory, schema, allow_absent=allow_absent)
    result["problems"] = problems + result["problems"]
    result["counts"]["problems"] = len(result["problems"])
    return result


def _log_tables(doc: dict, broken: list[str]) -> None:
    """Per-table detail (names, row counts, sha256s, columns) goes to stderr only.

    Under mp-data stderr lands in the data-zone log. The summary line keeps aggregate counts
    and fixed messages: mp-data withholds a summary that contains any corpus string as a
    substring, and a long line of design names can contain a data string.
    """
    for t in doc["tables"]:
        cols = ", ".join(c["name"] for c in t["columns"])
        quiet.progress(f"table {t['name']}: rows {t['rows']} sha256 {t['sha256']} columns [{cols}]")
    for name in broken:
        quiet.progress(f"load order: cycle broken at {name}")


# ------------------------------------------------------------------ export


def run_export(args) -> int:
    command = "dataset export"
    eng = load(args.engagement)
    eid = eng["id"]
    out = Path(args.out).absolute()
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.check:
        tmp = Path(tempfile.mkdtemp(prefix="dataset-check-"))
    else:
        tmp = Path(tempfile.mkdtemp(prefix=f".{out.name}.tmp-", dir=out.parent))
    try:
        target = tmp / "dataset"
        try:
            result = ds.export(eng, target)
        except ds.RowError as exc:
            return quiet.summary(
                "failed", command, 1, engagement=eid, messages=[str(exc)]
            )
        except (SourceError, ds.DatasetError) as exc:
            return quiet.summary(
                "error", command, 2, engagement=eid, messages=[str(exc)]
            )
        doc = result["doc"]
        allow = set(result["skipped"])
        if not doc["tables"]:
            allow |= {t["name"] for t in result["schema"].get("tables") or []}
        check = check_dir(target, result["schema"], allow_absent=allow)
        _log_tables(doc, result["broken"])
        counts = {
            "tables": len(doc["tables"]),
            "rows": sum(t["rows"] for t in doc["tables"]),
            "omitted_columns": sum(len(t["omitted_columns"]) for t in doc["tables"]),
            "skipped_tables": len(result["skipped"]),
            "cycles_broken": len(result["broken"]),
            "self_references": len(result["self_references"]),
            "unknown_columns": result["unknown_columns"],
        }
        messages = [m for m in result["messages"] if not m.startswith("load order:")]
        if result["broken"]:
            messages.append(f"load order: {len(result['broken'])} cycles broken")
        if check["problems"]:
            counts["problems"] = len(check["problems"])
            return quiet.summary(
                "failed",
                command,
                1,
                engagement=eid,
                counts=counts,
                messages=["written dataset is invalid", *check["problems"]],
            )
        if args.check:
            diffs = ds.same_tree(target, out)
            counts["differences"] = len(diffs)
            if diffs:
                for d in diffs:
                    quiet.progress(f"differs: {d}")
                return quiet.summary(
                    "failed",
                    command,
                    1,
                    engagement=eid,
                    counts=counts,
                    messages=[f"{len(diffs)} files differ"] + messages,
                )
        else:
            _replace(target, out)
        status = "no-data" if result["status"] == "no-data" else "ok"
        return quiet.summary(
            status,
            command,
            0,
            engagement=eid,
            counts=counts,
            messages=messages,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _replace(new: Path, out: Path) -> None:
    """Rename `new` over `out` (the old directory, if any, is moved aside first, then removed)."""
    old = None
    if out.exists() or out.is_symlink():
        old = Path(tempfile.mkdtemp(prefix=f".{out.name}.old-", dir=out.parent))
        os.rename(out, old / "dataset")
    os.rename(new, out)
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)


# ------------------------------------------------------------------ validate


def run_validate(args) -> int:
    command = "dataset validate"
    directory = Path(args.directory)
    schema_path = Path(args.schema)
    if not schema_path.is_file():
        return quiet.summary("error", command, 2, messages=["--schema file missing"])
    try:
        result = check_dir(directory, schema_path)
    except ds.DatasetError as exc:
        return quiet.summary("error", command, exc.exit_code, messages=[str(exc)])
    messages = list(result["problems"])
    if not result["schema_sha256_matches"]:
        messages.append("schema_sha256 differs from --schema (not a failure)")
    code = 1 if result["problems"] else 0
    return quiet.summary(
        None, command, code, counts=result["counts"], messages=messages
    )
