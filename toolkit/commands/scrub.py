"""Check that a model directory holds nothing a scrub would replace (decision D8).

`scrub --check DIR` reads every .json file (every string value and key) and every .txt, .md and
.csv file (line by line) under DIR and exits 1 when a credential, email address, phone number,
credential URL or secret-like string remains, naming the file and the JSON path or line and the
kind of hit, never the value. It reads only the design zone, so it runs as `uv run toolkit`.
"""

from __future__ import annotations

from pathlib import Path

from toolkit import quiet
from toolkit.cli import UsageError
from toolkit.scrub import check_dir

COMMAND = "scrub"


def add_arguments(parser) -> None:
    parser.add_argument("directory", nargs="?", help="model directory")
    parser.add_argument("--check", metavar="DIR", help="model directory to check")


def run(args) -> int:
    target = args.check or args.directory
    if not args.check or (args.directory and args.directory != args.check):
        raise UsageError("usage: scrub --check DIR")
    path = Path(target)
    if not path.is_dir():
        return quiet.summary("error", COMMAND, 2, messages=["directory missing"])
    hits = check_dir(path)
    files = sum(1 for p in path.rglob("*") if p.is_file())
    messages = [f"{rel}: {where}: {kind}" for rel, where, kind in hits]
    for m in messages:
        quiet.progress(m)
    by_kind: dict[str, int] = {}
    for _rel, _where, kind in hits:
        by_kind[kind] = by_kind.get(kind, 0) + 1
    counts = {"files": files, "hits": len(hits)}
    counts.update({f"hits_{k.lower()}": v for k, v in sorted(by_kind.items())})
    return quiet.summary(
        None,
        COMMAND,
        1 if hits else 0,
        counts=counts,
        messages=messages,
    )
