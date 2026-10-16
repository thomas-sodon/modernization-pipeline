"""Vendor the equivalence harness into the target repo.

`vendor-harness --target <target> [--check]` (docs/contracts.md#harness): copies the pipeline's
`harness/` (canonical.py and dataset.py included) to `<target>/equivalence/`, removing files the
harness no longer has, and leaves `adapter.toml` and `results.json` untouched. `--check` compares
and exits 1 on any difference, writing nothing.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from toolkit import quiet

SOURCE = Path(__file__).resolve().parents[2] / "harness"
KEEP = ("adapter.toml", "results.json")
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache"}
SKIP_SUFFIXES = (".pyc", ".pyo")


def add_arguments(parser) -> None:
    parser.add_argument(
        "--target", required=True, help="the engagement root in the target repo"
    )
    parser.add_argument(
        "--check", action="store_true", help="compare only; exit 1 on drift"
    )


def harness_files(root: Path) -> dict[str, Path]:
    """Relative path -> file, without caches and without adapter.toml / results.json."""
    out: dict[str, Path] = {}
    if not root.is_dir():
        return out
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        for f in sorted(files):
            if f.endswith(SKIP_SUFFIXES):
                continue
            path = Path(d) / f
            rel = path.relative_to(root).as_posix()
            if rel in KEEP:
                continue
            out[rel] = path
    return out


def differences(source: Path, dest: Path) -> tuple[list[str], list[str], list[str]]:
    """(missing in dest, changed, extra in dest)."""
    src, dst = harness_files(source), harness_files(dest)
    missing = sorted(set(src) - set(dst))
    extra = sorted(set(dst) - set(src))
    changed = sorted(
        r for r in set(src) & set(dst) if src[r].read_bytes() != dst[r].read_bytes()
    )
    return missing, changed, extra


def vendor(source: Path, dest: Path) -> tuple[int, int]:
    missing, changed, extra = differences(source, dest)
    for rel in missing + changed:
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / rel, out)
        shutil.copymode(source / rel, out)
    for rel in extra:
        (dest / rel).unlink()
    for d, dirs, files in os.walk(dest, topdown=False):
        p = Path(d)
        if p != dest and not any(p.iterdir()):
            p.rmdir()
    return len(missing) + len(changed), len(extra)


def run(args) -> int:
    command = "vendor-harness"
    target = Path(args.target)
    if not target.is_dir():
        return quiet.summary(
            "error", command, 2, messages=[f"--target {target} is not a directory"]
        )
    if not (SOURCE / "plugin.py").is_file():
        return quiet.summary("error", command, 2, messages=[f"{SOURCE} has no harness"])
    dest = target / "equivalence"
    missing, changed, extra = differences(SOURCE, dest)
    counts = {"files": len(harness_files(SOURCE))}
    if args.check:
        msgs = (
            [f"missing: {r}" for r in missing]
            + [f"differs: {r}" for r in changed]
            + [f"not in harness: {r}" for r in extra]
        )
        counts["differences"] = len(msgs)
        for m in msgs:
            quiet.progress(m)
        return quiet.summary(
            None,
            command,
            1 if msgs else 0,
            counts=counts,
            paths=[str(dest)],
            messages=msgs or ["the vendored harness matches harness/"],
        )
    dest.mkdir(parents=True, exist_ok=True)
    copied, removed = vendor(SOURCE, dest)
    counts.update({"copied": copied, "removed": removed})
    return quiet.summary(
        None,
        command,
        0,
        counts=counts,
        paths=[str(dest)],
        messages=[f"vendored {counts['files']} files into {dest}"],
    )
