"""Fetch and verify the engagement source against its pins; restore the vendor jars."""

from __future__ import annotations

from toolkit import quiet
from toolkit.cli import UsageError
from toolkit.engagement import load
from toolkit.fetch import FetchError, ensure_jars, verify


def add_arguments(parser) -> None:
    parser.add_argument("engagement", nargs="?", help="<records>/engagement.json")
    parser.add_argument(
        "--verify", action="store_true", help="fetch when missing, then check the pins"
    )
    parser.add_argument(
        "--record-if-unset",
        action="store_true",
        help="write source.lock.json when the engagement has no pin and there is no lock",
    )
    parser.add_argument(
        "--jars",
        action="store_true",
        help="only restore the reference-engine jars into vendor/",
    )


def run(args) -> int:
    if args.jars:
        if args.engagement or args.verify or args.record_if_unset:
            raise UsageError("fetch --jars takes no engagement and no other flag")
        try:
            result = ensure_jars()
        except FetchError as exc:
            return quiet.summary("error", "fetch", 2, messages=[str(exc)])
        return quiet.summary(
            None,
            "fetch",
            result.code,
            counts=result.counts,
            sha256s=result.sha256s,
            messages=result.messages,
        )
    if not args.engagement or not args.verify:
        raise UsageError(
            "usage: fetch <eng> --verify [--record-if-unset] | fetch --jars"
        )
    eng = load(args.engagement)
    try:
        result = verify(eng, args.record_if_unset)
    except FetchError as exc:
        return quiet.summary(
            "error", "fetch", 2, engagement=eng["id"], messages=[str(exc)]
        )
    for m in result.messages:
        quiet.progress(m)
    return quiet.summary(
        quiet.status_for(result.code, recorded=result.recorded),
        "fetch",
        result.code,
        engagement=eng["id"],
        counts=result.counts,
        sha256s=result.sha256s,
        messages=result.messages,
    )
