"""Count the design from the object model and check the counts (docs/contracts.md#inventory).

`inventory <eng> --model <private>/out/model --out <private>/out/inventory.json [--expect]`

Reads only the model files (never the source) and writes inventory.json: counts under the source
kind's keys, objects with metrics, dependency edges, rule-bearing kinds and the health-check
facts (sorted, no timestamps). With --expect it compares the counts with the engagement's numeric
expected_inventory, else with the records' inventory.lock.json, else records that lock (exit 0,
status recorded, 'recorded, not compared; approve at the gate'). Exit 1 on any difference (key,
expected and counted are listed) or a lock for another source; exit 2 when the model is missing.
"""

from __future__ import annotations

from pathlib import Path

from toolkit import inventory as inv_mod
from toolkit import quiet
from toolkit.engagement import load

COMMAND = "inventory"


def add_arguments(parser) -> None:
    parser.add_argument("engagement", help="<records>/engagement.json")
    parser.add_argument("--model", required=True, help="model directory (design zone)")
    parser.add_argument("--out", required=True, help="inventory.json to write")
    parser.add_argument(
        "--expect",
        action="store_true",
        help="compare with expected_inventory or inventory.lock.json, or record the lock",
    )


def _flat_counts(counts: dict) -> dict:
    return {k: v for k, v in counts.items() if isinstance(v, int) and v >= 0}


def run(args) -> int:
    eng = load(args.engagement)
    try:
        inv, missing = inv_mod.build(eng, args.model)
    except inv_mod.ModelMissing as exc:
        return quiet.summary(
            "error", COMMAND, 2, engagement=eng["id"], messages=[str(exc)]
        )
    messages = [f"{key}: not in the model, left out" for key in missing]
    code, status = 0, None
    paths = []
    if args.expect:
        result = inv_mod.expect(eng, inv["counts"])
        inv["expectation"] = (
            "recorded" if result["status"] == "recorded" else "compared"
        )
        messages = result["messages"] + messages
        if result["status"] == "failed":
            code = 1
        elif result["status"] == "recorded":
            status = "recorded"
            paths.append(result["lock_written"])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(inv_mod.dumps(inv), encoding="utf-8")
    paths.insert(0, str(out.resolve()))
    for m in messages:
        quiet.progress(m)
    counts = _flat_counts(inv["counts"])
    counts["objects"] = len(inv["objects"])
    counts["edges"] = len(inv["edges"])
    return quiet.summary(
        status,
        COMMAND,
        code,
        engagement=eng["id"],
        counts=counts,
        paths=paths,
        messages=messages,
    )
