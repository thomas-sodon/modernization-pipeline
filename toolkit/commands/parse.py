"""Parse the engagement source into the scrubbed object model (docs/contracts.md#object-model).

`parse <eng> --strict --out DIR [--fail-on compiled-only,missing-backend,multi-backend,unresolved-link]`

Reads the source through its reader (toolkit/sources), builds schema.json, objects.json,
procedures.json, datamacros.json and code/<module>.txt, scrubs every string (toolkit/scrub.py;
the token map goes to the private scrub-map.json), validates each file against
toolkit/schemas/model.schema.json and writes them to DIR. Exit codes:

- 1 under --strict on any parse error or unknown design part (warnings never fail); 1 when the
  model does not validate; 1 for a --fail-on condition compiled-only, missing-backend or
  unresolved-link; 2 for multi-backend ('more than one data back end: custom quote');
- 2 when the source is missing, encrypted or password-protected ('encrypted source: custom
  quote', whatever --fail-on says), or an accdb/mdb source or data copy needs the Jackcess bridge
  that is not built yet.

The --fail-on conditions are checked after the model is written. The summary holds counts per
object kind, warning and error codes with counts and the output paths; never source text.
"""

from __future__ import annotations

from pathlib import Path

from toolkit import model as model_mod
from toolkit import quiet
from toolkit.cli import UsageError
from toolkit.engagement import load
from toolkit.scrub import Scrubber, locked_map
from toolkit.sources import BridgeMissing, EncryptedSource, SourceError, open_reader

FAIL_ON = ("compiled-only", "missing-backend", "multi-backend", "unresolved-link")
COMMAND = "parse"


def add_arguments(parser) -> None:
    parser.add_argument("engagement", help="<records>/engagement.json")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 on any parse error or unknown design part",
    )
    parser.add_argument("--out", required=True, help="model directory (design zone)")
    parser.add_argument(
        "--fail-on",
        default="",
        help="comma-separated: " + ",".join(FAIL_ON),
    )


def _fail_on(value: str) -> list[str]:
    items = [v.strip() for v in value.split(",") if v.strip()]
    bad = [v for v in items if v not in FAIL_ON]
    if bad:
        raise UsageError(f"--fail-on: unknown condition(s) {', '.join(sorted(bad))}")
    return items


def _code_counts(items: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        out[it["code"]] = out.get(it["code"], 0) + 1
    return dict(sorted(out.items()))


def received_backends(eng: dict) -> list[Path]:
    src = eng["source"]
    files = []
    if src.get("data_copy"):
        files.append(Path(src["data_copy"]))
    for b in src.get("backends") or []:
        files.append(Path(b["path"]))
    return files


def backend_conditions(eng: dict, linked: list[dict]) -> dict:
    """missing / multi / unresolved back-end facts from objects.json linked_tables."""
    access = {}
    for lt in linked:
        if lt["backend_kind"] == "access":
            access.setdefault(lt["backend"], lt.get("backend_file"))
    received = [p for p in received_backends(eng) if p.is_file()]
    received_names = {p.name.lower() for p in received}
    missing = 0
    for _num, fname in sorted(access.items()):
        if fname and fname in received_names:
            continue
        if len(access) == 1 and received:
            continue
        missing += 1
    return {
        "data_backends": len(access),
        "missing_backends": missing,
        "unresolved_links": sum(
            1 for lt in linked if lt["backend_kind"] == "unresolved"
        ),
    }


def _check_backends_encrypted(eng: dict) -> None:
    for path in received_backends(eng):
        if path.is_file():
            from toolkit.sources.accdb import probe_encrypted

            if probe_encrypted(path):
                raise EncryptedSource("back end")


def run(args) -> int:
    fail_on = _fail_on(args.fail_on)
    eng = load(args.engagement)
    eid = eng["id"]
    src = eng["source"]
    out_dir = Path(args.out)
    private = Path(eng["paths"]["private"])
    if not Path(src["path"]).exists():
        return quiet.summary(
            "error", COMMAND, 2, engagement=eid, messages=["source missing"]
        )
    try:
        reader = open_reader(eng)
        _check_backends_encrypted(eng)
        if src["kind"] == "xlsm":
            model = reader.build_model()
        else:
            from toolkit.sources.access import build_model

            design = reader.design()
            path = Path(src["path"])
            design.compiled_only = bool(
                src.get("compiled_only")
            ) or path.suffix.lower() in (
                ".accde",
                ".mde",
            )
            model = build_model(design)
            model["errors"] = design.errors
            model["warnings"] = design.warnings
        data_copy = None
        if src.get("data_copy"):
            from toolkit.sources.accdb import compare_data_copy

            data_copy = compare_data_copy(Path(src["data_copy"]), model["schema"])
    except EncryptedSource as exc:
        return quiet.summary("error", COMMAND, 2, engagement=eid, messages=[str(exc)])
    except BridgeMissing as exc:
        return quiet.summary("error", COMMAND, 2, engagement=eid, messages=[str(exc)])
    except SourceError as exc:
        return quiet.summary("error", COMMAND, 2, engagement=eid, messages=[str(exc)])

    errors = list(model.get("errors", []))
    warnings = list(model.get("warnings", []))
    if data_copy is not None:
        model["objects"]["facts"]["data_copy"] = data_copy["tables"]
        for d in data_copy["differences"]:
            errors.append({"code": "data-copy-differs", "object": f"table:{d}"})
    model["objects"]["diagnostics"] = {
        "errors": sorted(errors, key=_diag_key),
        "warnings": sorted(warnings, key=_diag_key),
        "unknown_parts": model.get("unknown_parts", {}),
        "package_parts": model.get("package", {}),
    }

    with locked_map(private / "scrub-map.json") as (existing, save):
        scrubber = Scrubber(existing)
        for stem in model_mod.FILES:
            model[stem] = scrubber.obj(model[stem])
        model["code"] = {k: scrubber.text(v) for k, v in sorted(model["code"].items())}
        mapping = scrubber.mapping()
        if mapping != existing:
            save(mapping)

    docs, code_files = model_mod.finalize(model)
    problems = model_mod.validate(docs)
    written = model_mod.write(out_dir, docs, code_files)

    counts = dict(model["counts"])
    counts["parse_errors"] = len(errors)
    counts["parse_warnings"] = len(warnings)
    counts["schema_problems"] = len(problems)
    counts["code_files"] = len(code_files)
    counts["scrub_tokens"] = sum(scrubber.hits.values())
    messages = []
    for p in problems[:10]:
        messages.append(f"model invalid: {p}")
    for code, n in _code_counts(errors).items():
        messages.append(f"error {code}: {n}")
    for code, n in _code_counts(warnings).items():
        messages.append(f"warning {code}: {n}")
    for folder, n in sorted(model.get("unknown_parts", {}).items()):
        messages.append(f"unknown part folder {folder}: {n}")
    for kind, n in sorted(scrubber.hits.items()):
        messages.append(f"scrubbed {kind}: {n}")

    code = 0
    if problems:
        code = 1
    if args.strict and (errors or model.get("unknown_parts")):
        code = 1

    facts = model["objects"].get("facts", {})
    if src["kind"] != "xlsm":
        bc = backend_conditions(eng, model["objects"].get("linked_tables", []))
        counts.update(bc)
        if "compiled-only" in fail_on and facts.get("compiled_only"):
            messages.append("compiled-only: no VBA source")
            code = max(code, 1)
        if "missing-backend" in fail_on and bc["missing_backends"]:
            messages.append("missing-backend: a linked back end was not received")
            code = max(code, 1)
        if "unresolved-link" in fail_on and bc["unresolved_links"]:
            messages.append("unresolved-link: a connect string cannot be classified")
            code = max(code, 1)
        if "multi-backend" in fail_on and bc["data_backends"] > 1:
            messages.insert(0, "more than one data back end: custom quote")
            code = 2

    paths = [str(p) for p in written if p.suffix == ".json"]
    paths.append(str(out_dir / "code"))
    for line in messages:
        quiet.progress(line)
    return quiet.summary(
        None,
        COMMAND,
        code,
        engagement=eid,
        counts=counts,
        paths=paths,
        messages=messages,
    )


def _diag_key(d: dict):
    return (
        d.get("code", ""),
        d.get("object", ""),
        str(d.get("line", "")),
        str(d.get("detail", "")),
    )
