"""Reference engine: Access SQL of saved queries, reference databases and runs (docs/contracts.md#reference-catalog).

`reference sql <eng> --all`: writes <private>/out/reference/catalog.json and out/reference/sql/
from the scrubbed model (design zone only).

`reference build <eng> --dataset engagement|seeds|seed-N`: seeds run as `uv run toolkit` and write
<private>/synthetic/reference/seed-N.accdb (running `reference sql` first when the catalog is
missing or older than the model); engagement runs only as `mp-data reference build` and writes
only <private>/reference/engagement.accdb (exit 2 naming `reference sql` when the catalog is
missing or stale). `--dataset all` exits 2 and names the two commands.

`reference run <eng> --dataset <name> --all --summary`: opens the dataset's reference database
read-only, runs every ok unit (and needs-params units that have values in the params file) and
prints counts per status and rows per unit; writes nothing. Exit 1 when a unit fails at run time
(Java exception class only).

`reference selfcheck <eng> --min-match F`: xlsm only (toolkit.reference_xlsm).

Dispatch: sql, build, run and selfcheck on engagement `source.kind` (accdt, vcs-export, accdb, mdb:
toolkit.reference; xlsm: toolkit.reference_xlsm), `ReferenceAdapter` on the catalog `engine`
(ucanaccess-*: toolkit.reference.engine.AccessAdapter; formualizer-*: toolkit.reference_xlsm's
`Adapter`). Both packages are imported lazily, so a missing one exits 2 naming it.
toolkit.reference_xlsm provides `sql(eng, args)`, `build(eng, args)`, `run(eng, args)`,
`selfcheck(eng, args)` (each returning the exit code through toolkit.quiet) and
`Adapter(private)`.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

from toolkit import quiet
from toolkit.engagement import load

ACCESS_KINDS = ("accdt", "vcs-export", "accdb", "mdb")
PACKAGES = {"access": "toolkit.reference.commands", "xlsm": "toolkit.reference_xlsm"}


def add_arguments(parser) -> None:
    sub = parser.add_subparsers(
        dest="_reference_action", metavar="<action>", parser_class=type(parser)
    )
    sub.required = True
    p = sub.add_parser("sql", help="write the reference catalog and SQL (design zone)")
    p.add_argument("engagement", help="<records>/engagement.json")
    p.add_argument(
        "--all",
        action="store_true",
        required=True,
        help="every saved query of the model",
    )
    p.set_defaults(_command="reference sql", _run=run_sql)
    p = sub.add_parser("build", help="build reference databases")
    p.add_argument("engagement", help="<records>/engagement.json")
    p.add_argument(
        "--dataset", required=True, help="engagement, seeds, seed-N (all exits 2)"
    )
    p.set_defaults(_command="reference build", _run=run_build)
    p = sub.add_parser(
        "run", help="run catalog units on a reference database (read-only)"
    )
    p.add_argument("engagement", help="<records>/engagement.json")
    p.add_argument("--dataset", required=True, help="engagement or seed-N")
    p.add_argument("--all", action="store_true", help="every unit of the catalog")
    p.add_argument("--unit", action="append", help="one unit id (repeatable)")
    p.add_argument(
        "--summary", action="store_true", help="counts per status and rows per unit"
    )
    p.set_defaults(_command="reference run", _run=run_run)
    p = sub.add_parser(
        "selfcheck", help="xlsm: compare the reference with cached values"
    )
    p.add_argument("engagement", help="<records>/engagement.json")
    p.add_argument("--min-match", type=float, default=1.0)
    p.set_defaults(_command="reference selfcheck", _run=run_selfcheck)


def run(args) -> int:  # dispatch happens through _run set by the sub-parsers
    return args._run(args)


def _family(eng: dict) -> str | None:
    kind = eng["source"]["kind"]
    if kind in ACCESS_KINDS:
        return "access"
    if kind == "xlsm":
        return "xlsm"
    return None


def _package(command: str, eng: dict):
    """(module, None) or (None, exit code after writing the error summary)."""
    family = _family(eng)
    if family is None:
        return None, quiet.summary(
            "error",
            command,
            2,
            engagement=eng["id"],
            messages=[f"source.kind {eng['source']['kind']} has no reference engine"],
        )
    name = PACKAGES[family]
    try:
        return importlib.import_module(name), None
    except ImportError as exc:
        missing = getattr(exc, "name", None) or name
        if not str(missing).startswith("toolkit"):
            raise
        return None, quiet.summary(
            "error",
            command,
            2,
            engagement=eng["id"],
            messages=[f"{name} is not built yet ({missing} missing)"],
        )


def _dispatch(args, action: str) -> int:
    command = f"reference {action}"
    eng = load(args.engagement)
    mod, code = _package(command, eng)
    if mod is None:
        return code
    return getattr(mod, action)(eng, args)


def run_sql(args) -> int:
    return _dispatch(args, "sql")


def run_build(args) -> int:
    return _dispatch(args, "build")


def run_run(args) -> int:
    if not args.all and not args.unit:
        return quiet.summary(
            "error", "reference run", 2, messages=["give --all or --unit ID"]
        )
    return _dispatch(args, "run")


def run_selfcheck(args) -> int:
    eng = load(args.engagement)
    if _family(eng) != "xlsm":
        return quiet.summary(
            "error",
            "reference selfcheck",
            2,
            engagement=eng["id"],
            messages=["reference selfcheck exists only for xlsm sources"],
        )
    return _dispatch(args, "selfcheck")


# ------------------------------------------------------------------ adapter


class ReferenceUnavailable(RuntimeError):
    """The reference adapter cannot be set up (catalog, engine package or private dir missing)."""


class ReferenceAdapter:
    """The pipeline-only reference adapter (docs/contracts.md#harness), duck-typed to the
    protocol: load_dataset(dir), run_saved_query(unit_id, params), render_document(unit_id, key,
    params) and describe(unit_id) for golden record. Created with no arguments by the harness
    plugin, it finds the private directory in $EQUIVALENCE_PRIVATE (golden record may pass
    private=) and dispatches on the catalog's engine."""

    def __init__(self, private=None):
        private = private or os.environ.get("EQUIVALENCE_PRIVATE")
        if not private:
            raise ReferenceUnavailable("EQUIVALENCE_PRIVATE is not set")
        self.private = Path(private)
        path = self.private / "out" / "reference" / "catalog.json"
        if not path.is_file():
            raise ReferenceUnavailable(
                "out/reference/catalog.json missing: run uv run toolkit reference sql <eng> --all"
            )
        engine = str(json.loads(path.read_text(encoding="utf-8")).get("engine") or "")
        if engine.startswith("ucanaccess-"):
            module, cls = "toolkit.reference.engine", "AccessAdapter"
        elif engine.startswith("formualizer-"):
            module, cls = "toolkit.reference_xlsm", "Adapter"
        else:
            raise ReferenceUnavailable(
                f"catalog engine {engine or '(none)'} is unknown"
            )
        try:
            impl = getattr(importlib.import_module(module), cls)
        except (ImportError, AttributeError):
            raise ReferenceUnavailable(f"{module} is not built yet") from None
        self._impl = impl(self.private)

    def load_dataset(self, directory):
        return self._impl.load_dataset(directory)

    def run_saved_query(self, unit_id, params=None):
        return self._impl.run_saved_query(unit_id, params or {})

    def render_document(self, unit_id, key=None, params=None):
        return self._impl.render_document(unit_id, key, params or {})

    def describe(self, unit_id):
        return self._impl.describe(unit_id)
