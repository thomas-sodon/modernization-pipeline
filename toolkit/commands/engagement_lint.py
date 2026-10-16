"""Check an engagement.json against engagement.schema.json and the driver's rules."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema

from toolkit import quiet
from toolkit.engagement import ID_RE, _load, pipeline_json

SCHEMA = Path(__file__).resolve().parent.parent / "schemas" / "engagement.schema.json"


def add_arguments(parser) -> None:
    parser.add_argument("engagement", help="<records>/engagement.json")


def _field(error) -> str:
    parts = []
    for p in error.absolute_path:
        if isinstance(p, int):
            parts.append(f"[{p}]")
        else:
            parts.append(("." if parts else "") + str(p))
    return "".join(parts) or "(top level)"


def schema_problems(raw: dict) -> list[str]:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    out = []
    for err in sorted(
        validator.iter_errors(raw), key=lambda e: [str(p) for p in e.absolute_path]
    ):
        if err.validator == "additionalProperties":
            extra = sorted(set(err.instance) - set(err.schema.get("properties", {})))
            extra = [k for k in extra if not str(k).startswith("_")]
            out.append(f"schema: {_field(err)}: unknown key(s) {', '.join(extra)}")
        elif err.validator == "required":
            out.append(
                f"schema: {_field(err)}: {err.message.split(' is a required')[0]} is required"
            )
        else:
            out.append(f"schema: {_field(err)}: fails {err.validator}")
    return out


def lint(path) -> tuple[str | None, list[str]]:
    """Return (engagement id, problems). Raises EngagementError for exit-2 conditions."""
    eng, raw, problems = _load(path)
    problems = list(problems)
    problems += schema_problems(raw)
    source = eng.get("source") if isinstance(eng.get("source"), dict) else {}
    app = eng.get("app") if isinstance(eng.get("app"), dict) else {}
    reserved = pipeline_json().get("slug_reserved", [])
    if app.get("slug") in reserved:
        problems.append("engagement: app.slug: reserved in pipeline.json slug_reserved")
    if source.get("compiled_only") is True:
        problems.append(
            "engagement: source.compiled_only: true; ask the client for the source .accdb"
        )
    records = eng.get("records")
    paths = eng["paths"]
    if records is not None and eng.get("bootstrap") is not True:
        pipeline = paths["pipeline"].rstrip("/") + "/"
        if (records.rstrip("/") + "/").startswith(pipeline):
            problems.append(
                "engagement: records: must lie outside paths.pipeline for bootstrap false"
            )
    ident = eng.get("id") if isinstance(eng.get("id"), str) else None
    # de-duplicate, keep order
    seen, unique = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return ident, unique


def run(args) -> int:
    ident, problems = lint(args.engagement)
    for p in problems:
        print(p, file=sys.stderr)

    eng_id = ident if ident and ID_RE.match(ident) and len(ident) <= 40 else None
    code = 1 if problems else 0
    return quiet.summary(
        None,
        "engagement-lint",
        code,
        engagement=eng_id,
        counts={"problems": len(problems)},
        messages=problems,
    )
