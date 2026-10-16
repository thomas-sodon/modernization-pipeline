"""Generate the seeded synthetic datasets from the design (S2.synth).

`synth <eng> --schema-from auto --seeds 1,2,3 --out <target>/goldens/synthetic [--check]` writes
each seed as a dataset directory at `<out>/seed-N/input/` (docs/contracts.md#dataset-directory):
the model schema's tables and columns, none of the engagement's values. It reads only the
design zone (`<private>/out/model/`) and `<target>/analysis/rules.json` (lookups), never the
engagement dataset, so it runs as `uv run toolkit synth` with full output.

`--schema-from auto` (and `model`) take `<private>/out/model/schema.json` for every source kind;
for a workbook that is one table per business ListObject.

Each seed directory is built in a temporary directory next to it, validated, and renamed over
`seed-N/input`; nothing else under `--out` is touched. `--check` builds every seed into a
temporary directory, compares it byte for byte with `seed-N/input` and exits 1 on any
difference, writing nothing. The bytes depend on the pinned Faker version.

Each `seed-N/input` holds a `.gitattributes` (`* -text`, synth.GITATTRIBUTES): the CSVs end
records in CRLF and some seed-2 fields hold CR LF, so git must store them byte-exact or a clone
fails `dataset validate` and `--check`. `--check` compares that file too.

The summary names the lookup columns whose codes were generated (copy_values true, but neither
the model nor rules.json lookups[].values holds codes) and those that took rules.json values. It also
names the key-value columns whose rows hold typed values because the code converts them per key.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from toolkit import dataset as ds
from toolkit import quiet, synth
from toolkit.engagement import data_zone, load

COMMAND = "synth"


def add_arguments(parser) -> None:
    parser.add_argument("engagement", help="<records>/engagement.json")
    parser.add_argument(
        "--schema-from",
        choices=("auto", "model"),
        default="auto",
        help="auto and model: <private>/out/model/schema.json",
    )
    parser.add_argument("--seeds", default="1,2,3", help="comma-separated seed numbers")
    parser.add_argument("--out", required=True, help="<target>/goldens/synthetic")
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate into a temp dir and compare byte for byte; write nothing",
    )


def parse_seeds(text: str) -> list[int]:
    seeds = []
    for part in text.split(","):
        part = part.strip()
        if not part.isdigit() or int(part) < 1:
            raise synth.SynthError("--seeds takes positive integers, comma-separated")
        if int(part) in seeds:
            raise synth.SynthError("--seeds lists a seed twice")
        seeds.append(int(part))
    if not seeds:
        raise synth.SynthError("--seeds is empty")
    return seeds


def _inside(path: Path, roots) -> bool:
    for root in roots:
        root = Path(root).resolve()
        if path == root or root in path.parents:
            return True
    return False


def _replace(new: Path, dest: Path) -> None:
    old = None
    if dest.exists() or dest.is_symlink():
        old = Path(tempfile.mkdtemp(prefix=f".{dest.name}.old-", dir=dest.parent))
        os.rename(dest, old / "input")
    os.rename(new, dest)
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)


def run(args) -> int:
    eng = load(args.engagement)
    eid = eng["id"]
    private = Path(eng["paths"]["private"])
    model = private / "out" / "model"
    rules = Path(eng["paths"]["target"]) / "analysis" / "rules.json"
    out = Path(args.out).absolute()

    def error(message: str) -> int:
        return quiet.summary("error", COMMAND, 2, engagement=eid, messages=[message])

    try:
        seeds = parse_seeds(args.seeds)
    except synth.SynthError as exc:
        return error(str(exc))
    if _inside(out.resolve(), data_zone(str(private))):
        return error("--out lies in the data zone; seeds are committed design files")
    if not (model / "schema.json").is_file():
        return error("model schema.json missing: run parse first")
    if not rules.is_file():
        return error("analysis/rules.json missing: S1 writes it")
    try:
        design = synth.load_design(model, rules)
        plan = synth.make_plan(design)
    except synth.SynthError as exc:
        return error(str(exc))

    counts = {
        "seeds": len(seeds),
        "tables": len(plan.order),
        "rows": 0,
        "skipped_rows": 0,
    }
    messages = [f"generated codes: {name}" for name in plan.generated]
    messages += [f"codes from rules.json values: {name}" for name in plan.from_rules]
    messages += [f"typed values per key: {name}" for name in plan.typed]

    if args.check:
        tmp = Path(tempfile.mkdtemp(prefix="synth-check-"))
        try:
            diffs = []
            for seed in seeds:
                built = tmp / f"seed-{seed}" / "input"
                result = synth.write_seed(plan, seed, built)
                problems = ds.validate(built, design.schema)["problems"]
                if problems:
                    return quiet.summary(
                        "failed",
                        COMMAND,
                        1,
                        engagement=eid,
                        counts=counts,
                        messages=[
                            f"seed-{seed}: generated dataset is invalid",
                            *problems,
                        ],
                    )
                counts["rows"] += sum(t["rows"] for t in result["doc"]["tables"])
                counts["skipped_rows"] += result["skipped"]
                for rel in ds.same_tree(built, out / f"seed-{seed}" / "input"):
                    diffs.append(f"seed-{seed}/input/{rel}")
            counts["differences"] = len(diffs)
            for d in diffs:
                quiet.progress(f"differs: {d}")
            if diffs:
                return quiet.summary(
                    "failed",
                    COMMAND,
                    1,
                    engagement=eid,
                    counts=counts,
                    messages=[f"{len(diffs)} files differ", *diffs, *messages],
                )
            return quiet.summary(
                "ok", COMMAND, 0, engagement=eid, counts=counts, messages=messages
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    out.mkdir(parents=True, exist_ok=True)
    for seed in seeds:
        parent = out / f"seed-{seed}"
        parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=".input.tmp-", dir=parent))
        try:
            built = tmp / "input"
            result = synth.write_seed(plan, seed, built)
            problems = ds.validate(built, design.schema)["problems"]
            if problems:
                return quiet.summary(
                    "failed",
                    COMMAND,
                    1,
                    engagement=eid,
                    counts=counts,
                    messages=[f"seed-{seed}: generated dataset is invalid", *problems],
                )
            _replace(built, parent / "input")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        counts["rows"] += sum(t["rows"] for t in result["doc"]["tables"])
        counts["skipped_rows"] += result["skipped"]
        for t in result["doc"]["tables"]:
            quiet.progress(
                f"seed-{seed} {t['name']}: rows {t['rows']} sha256 {t['sha256']}"
            )
    return quiet.summary(
        "ok", COMMAND, 0, engagement=eid, counts=counts, messages=messages
    )
