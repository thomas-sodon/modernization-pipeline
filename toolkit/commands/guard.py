"""Copy guard: forbidden files and verbatim source text in the repos (S0.guard).

`guard <eng> --repo DIR [--repo DIR ...] [--staged | --history] [--report DIR]` rebuilds the
corpus in <private>/corpus/ from the engagement source, runs each repo's forbidden-file hooks
over its candidate set and copydetect over every UTF-8 text candidate. Runs through mp-data (it
reads the data zone).

`guard --hooks-only --repo DIR [--repo DIR ...]` runs only the hooks and needs no engagement
(pipeline CI).

Candidates: what `git add -A` would add; --staged the staged files; --history every blob
reachable from any ref (and lists refs not merged into main). Exit 0 pass, 1 a check failed,
2 usage, a refused hook config, a missing source or repo. The summary names repos, paths, hooks,
scores and corpus files, never matched text.
"""

from __future__ import annotations

from toolkit import guard, quiet
from toolkit.engagement import load, pipeline_json


def add_arguments(parser) -> None:
    parser.add_argument(
        "engagement", nargs="?", help="<records>/engagement.json (not with --hooks-only)"
    )
    parser.add_argument(
        "--repo", action="append", default=[], required=True, help="repo to check (repeatable)"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="check only staged files")
    mode.add_argument(
        "--history", action="store_true", help="check every blob reachable from any ref"
    )
    parser.add_argument(
        "--report", metavar="DIR", help="write copydetect's HTML report into DIR (data zone)"
    )
    parser.add_argument(
        "--hooks-only",
        action="store_true",
        help="run only the forbidden-file hooks; no engagement, no corpus",
    )


def run(args) -> int:
    command = "guard"
    mode = "staged" if args.staged else "history" if args.history else "worktree"
    eng = None
    eid = None
    if not args.hooks_only:
        if not args.engagement:
            return quiet.summary(
                "error", command, 2, messages=["usage: <eng> is required unless --hooks-only"]
            )
        eng = load(args.engagement)
        eid = eng["id"]
    params = guard.Params.from_stack(pipeline_json().get("stack"))
    try:
        result = guard.run(
            args.repo,
            mode=mode,
            eng=eng,
            params=params,
            report=args.report,
            hooks_only=args.hooks_only,
        )
    except guard.GuardError as exc:
        return quiet.summary("error", command, 2, engagement=eid, messages=[str(exc)])
    counts = {
        "repos": len(result.repos),
        "files": sum(r.checked for r in result.repos),
        "failures": sum(len(r.hits) for r in result.repos),
        **{f"files:{r.name}": r.checked for r in result.repos},
        **result.corpus,
    }
    if mode == "history":
        counts["unmerged_refs"] = sum(len(r.unmerged) for r in result.repos)
    messages = result.summary_messages()
    for r in result.repos:
        quiet.progress(f"{r.name}: {r.checked} files checked, {len(r.hits)} failing")
    code = 1 if result.failed else 0
    return quiet.summary(None, command, code, engagement=eid, counts=counts, messages=messages)
