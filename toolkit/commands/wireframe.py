"""Draw SVG wireframes of the forms behind the kept workflows from the object model.

`toolkit wireframe <eng> --model DIR --workflows FILE --out DIR [--check]`
(docs/contracts.md#cli-registry). Reads only the design-zone model (`objects.json` forms with
their sections, controls and tab pages) and rules.json `workflows[].forms`. Writes one
`<form-slug>.svg` per form plus `index.md`, and deletes `.svg` files in --out that this run did
not produce. Output depends only on the inputs.

--check renders in memory, compares with --out byte for byte and exits 1 naming every missing,
extra or changed file; it writes nothing. Exit 1 when a form named in the rules file is missing
from the model. Exit 2 when the model or the rules file is missing, or the model records no form
layout. Under MP_QUIET=1 the summary carries counts and file names only."""

from __future__ import annotations

import sys

from toolkit import quiet, wireframe
from toolkit.engagement import load

COMMAND = "wireframe"


def add_arguments(parser) -> None:
    parser.add_argument("engagement", help="<records>/engagement.json")
    parser.add_argument("--model", required=True, metavar="DIR", help="out/model directory")
    parser.add_argument(
        "--workflows", required=True, metavar="FILE", help="analysis/rules.json"
    )
    parser.add_argument("--out", required=True, metavar="DIR", help="docs/img/before")
    parser.add_argument("--check", action="store_true", help="compare only; never write")


def _stop(code: int, message: str, ident, counts=None, messages=None) -> int:
    print(f"{COMMAND}: {message}", file=sys.stderr)
    return quiet.summary(
        None, COMMAND, code, engagement=ident, counts=counts, messages=messages or [message]
    )


def run(args) -> int:
    eng = load(args.engagement)
    ident = eng.get("id")
    try:
        files, counts = wireframe.render_all(args.model, args.workflows)
    except wireframe.InputError as exc:
        return _stop(2, str(exc), ident)
    except wireframe.MissingForms as exc:
        return _stop(1, str(exc), ident, counts={"missing_forms": len(exc.names)})
    except wireframe.NoLayout as exc:
        return _stop(2, str(exc), ident)

    counts["files"] = len(files)
    if args.check:
        problems = wireframe.compare(files, args.out)
        counts["differences"] = len(problems)
        if problems:
            for line in problems:
                print(f"{COMMAND}: {line}", file=sys.stderr)
            return quiet.summary(
                None, COMMAND, 1, engagement=ident, counts=counts, messages=problems
            )
        return quiet.summary(
            None, COMMAND, 0, engagement=ident, counts=counts, messages=["up to date"]
        )

    written, deleted = wireframe.write(files, args.out)
    counts["written"] = written
    counts["deleted"] = deleted
    return quiet.summary(None, COMMAND, 0, engagement=ident, counts=counts)
