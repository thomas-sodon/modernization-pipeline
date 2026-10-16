"""`toolkit synth` and toolkit.synth: seeded synthetic datasets from the design only.

The fixture in toolkit/tests/fixtures/synth/ is a hand-written model of a made-up widget shop:
schema.json (a lookup table, a self reference, a composite unique index, a computed and a complex
column, a documentation table), objects.json (query criteria, a form value list), a VBA module
with enums and comparisons, a data macro condition, and a rules.json with lookups."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from toolkit import cli, synth
from toolkit import dataset as ds
from toolkit.engagement import data_zone, load

FIX = Path(__file__).parent / "fixtures" / "synth"
MODEL = FIX / "model"
RULES = FIX / "rules.json"
SCHEMA = json.loads((MODEL / "schema.json").read_text(encoding="utf-8"))
SEEDS = (1, 2, 3)


def run(capfd, *argv):
    code = cli.main(list(argv))
    out, _err = capfd.readouterr()
    lines = out.splitlines()
    assert len(lines) == 1, out
    return code, json.loads(lines[0])


def setup_engagement(tmp_path, make_engagement):
    eng_path = make_engagement(
        tmp_path,
        source={"kind": "accdt", "path": "{{p.private}}/sources/widgets.accdt"},
    )
    eng = load(eng_path)
    private = Path(eng["paths"]["private"])
    shutil.copytree(MODEL, private / "out" / "model")
    rules = Path(eng["paths"]["target"]) / "analysis" / "rules.json"
    rules.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(RULES, rules)
    out = Path(eng["paths"]["target"]) / "goldens" / "synthetic"
    return str(eng_path), eng, out


@pytest.fixture(scope="module")
def plan():
    return synth.make_plan(synth.load_design(MODEL, RULES))


@pytest.fixture(scope="module")
def seeds(plan):
    return {
        s: {t["name"]: t["rows"] for t in synth.generate(plan, s).tables} for s in SEEDS
    }


def schema_table(name):
    return next(t for t in SCHEMA["tables"] if t["name"] == name)


def column(table, name):
    return next(c for c in schema_table(table)["columns"] if c["name"] == name)


def values(rows, col):
    return [r[col] for r in rows]


def tree_bytes(root: Path) -> dict:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ------------------------------------------------------------------ command


def test_writes_valid_seeds_and_check(tmp_path, make_engagement, quiet, capfd):
    eng, _e, out = setup_engagement(tmp_path, make_engagement)
    (out / "seed-1" / "expected").mkdir(parents=True)
    (out / "seed-1" / "expected" / "keep.csv").write_bytes(b"x\r\n")
    code, summary = run(
        capfd,
        "synth",
        eng,
        "--schema-from",
        "auto",
        "--seeds",
        "1,2,3",
        "--out",
        str(out),
    )
    assert code == 0, summary
    assert summary["status"] == "ok"
    assert summary["counts"]["seeds"] == 3
    sha = hashlib.sha256((MODEL / "schema.json").read_bytes()).hexdigest()
    for seed in SEEDS:
        d = out / f"seed-{seed}" / "input"
        result = ds.validate(d, MODEL / "schema.json")
        assert result["problems"] == [], result["problems"]
        assert result["schema_sha256_matches"]
        meta = ds.read_meta(d)
        assert (meta["format_version"], meta["name"], meta["kind"], meta["seed"]) == (
            1,
            f"seed-{seed}",
            "synthetic",
            seed,
        )
        assert meta["schema_sha256"] == sha
        assert meta["load_order"] == ds.load_order(SCHEMA, meta["load_order"])[0]
    # only seed-N/input is written
    assert (out / "seed-1" / "expected" / "keep.csv").read_bytes() == b"x\r\n"
    assert sorted(p.name for p in out.iterdir()) == ["seed-1", "seed-2", "seed-3"]
    assert sorted(p.name for p in (out / "seed-2").iterdir()) == ["input"]

    before = tree_bytes(out)
    code, summary = run(
        capfd,
        "synth",
        eng,
        "--schema-from",
        "auto",
        "--seeds",
        "1,2,3",
        "--out",
        str(out),
        "--check",
    )
    assert code == 0, summary
    assert summary["counts"]["differences"] == 0
    # a changed byte fails the check, and the check writes nothing
    target = out / "seed-2" / "input" / "tables" / "Orders.csv"
    target.write_bytes(target.read_bytes() + b"9,\r\n")
    changed = tree_bytes(out)
    code, summary = run(
        capfd,
        "synth",
        eng,
        "--schema-from",
        "auto",
        "--seeds",
        "1,2,3",
        "--out",
        str(out),
        "--check",
    )
    assert code == 1
    assert summary["status"] == "failed"
    assert "seed-2/input/tables/Orders.csv" in summary["messages"]
    assert tree_bytes(out) == changed
    # regenerating restores the seeds byte for byte
    code, _s = run(capfd, "synth", eng, "--seeds", "1,2,3", "--out", str(out))
    assert code == 0
    assert tree_bytes(out) == before


def test_check_fails_when_a_seed_is_missing(tmp_path, make_engagement, quiet, capfd):
    eng, _e, out = setup_engagement(tmp_path, make_engagement)
    code, _s = run(capfd, "synth", eng, "--seeds", "1,2", "--out", str(out))
    assert code == 0
    code, summary = run(
        capfd, "synth", eng, "--seeds", "1,2,3", "--out", str(out), "--check"
    )
    assert code == 1
    assert summary["counts"]["differences"] > 0
    assert not (out / "seed-3").exists()


def test_names_generated_lookup_columns(tmp_path, make_engagement, quiet, capfd):
    eng, _e, out = setup_engagement(tmp_path, make_engagement)
    code, summary = run(capfd, "synth", eng, "--seeds", "1", "--out", str(out))
    assert code == 0
    assert "generated codes: Orders.Channel" in summary["messages"]
    assert "codes from rules.json values: OrderStates.StateID" in summary["messages"]
    assert not any("Kinds" in m or "PayMethod" in m for m in summary["messages"])


@pytest.mark.parametrize(
    "argv, message",
    [
        (["--seeds", "0"], "--seeds takes positive integers, comma-separated"),
        (["--seeds", "1,1"], "--seeds lists a seed twice"),
        (["--seeds", "a"], "--seeds takes positive integers, comma-separated"),
    ],
)
def test_bad_seeds(tmp_path, make_engagement, quiet, capfd, argv, message):
    eng, _e, out = setup_engagement(tmp_path, make_engagement)
    code, summary = run(capfd, "synth", eng, *argv, "--out", str(out))
    assert code == 2
    assert summary["messages"] == [message]
    assert not out.exists()


def test_refuses_data_zone_output_and_missing_inputs(
    tmp_path, make_engagement, quiet, capfd
):
    eng, e, out = setup_engagement(tmp_path, make_engagement)
    private = Path(e["paths"]["private"])
    code, summary = run(
        capfd, "synth", eng, "--out", str(private / "datasets" / "seeds")
    )
    assert code == 2
    assert "data zone" in summary["messages"][0]
    assert not (private / "datasets").exists()
    (Path(e["paths"]["target"]) / "analysis" / "rules.json").unlink()
    code, summary = run(capfd, "synth", eng, "--out", str(out))
    assert code == 2
    assert summary["messages"] == ["analysis/rules.json missing: S1 writes it"]
    shutil.rmtree(private / "out" / "model")
    code, summary = run(capfd, "synth", eng, "--out", str(out))
    assert code == 2
    assert summary["messages"] == ["model schema.json missing: run parse first"]


# ------------------------------------------------------------------ inputs

_OPENED: list | None = None
_HOOKED: list = []


def _audit(event, args):
    if _OPENED is None:
        return
    if event in ("open", "os.listdir", "os.scandir") and args:
        path = args[0]
        if isinstance(path, (str, bytes, os.PathLike)):
            _OPENED.append(os.path.abspath(os.fsdecode(path)))


def _under(path: str, root) -> bool:
    root = os.path.abspath(str(root))
    return path == root or path.startswith(root + os.sep)


def test_reads_only_the_design_and_rules(tmp_path, make_engagement, quiet, capfd):
    global _OPENED
    eng, e, out = setup_engagement(tmp_path, make_engagement)
    private = Path(e["paths"]["private"])
    target = Path(e["paths"]["target"])
    # bait in every data-zone kind of place, next to the model and in the target
    (private / "datasets" / "engagement").mkdir(parents=True)
    (private / "datasets" / "engagement" / "dataset.json").write_text(
        "{}", encoding="utf-8"
    )
    (private / "sources").mkdir()
    (private / "sources" / "widgets.accdt").write_bytes(b"not a template")
    (private / "workbook").mkdir()
    (private / "workbook" / "cached.json").write_text("{}", encoding="utf-8")
    (private / "scrub-map.json").write_text("{}", encoding="utf-8")
    (private / "out" / "escape.txt").write_text(
        'If Region = "Bait" Then\n', encoding="utf-8"
    )
    (target / "analysis" / "notes.md").write_text("bait\n", encoding="utf-8")
    if not _HOOKED:
        sys.addaudithook(_audit)
        _HOOKED.append(True)
    _OPENED = []
    try:
        code, _summary = run(capfd, "synth", eng, "--seeds", "1,2,3", "--out", str(out))
    finally:
        opened, _OPENED = _OPENED, None
    assert code == 0
    for zone in data_zone(str(private)):
        assert not [p for p in opened if _under(p, zone)], zone
    model = private / "out" / "model"
    assert [p for p in opened if _under(p, private) and not _under(p, model)] == []
    rules = target / "analysis" / "rules.json"
    assert [
        p
        for p in opened
        if _under(p, target) and p != str(rules) and not _under(p, out)
    ] == []
    assert str(rules) in opened
    assert str(model / "schema.json") in opened
    # the code file outside the model directory was neither read nor used
    assert not any(v == "Bait" for v in _all_values(out, "Customers", "Region"))


def _all_values(out: Path, table: str, col: str) -> list:
    found = []
    for seed in SEEDS:
        _meta, tables = ds.read_dataset(out / f"seed-{seed}" / "input")
        found += [r[col] for r in tables[table]]
    return found


# ------------------------------------------------------------------ determinism

_HASH_SCRIPT = """
import hashlib, sys
from pathlib import Path
from toolkit import synth
fix = Path(sys.argv[1])
plan = synth.make_plan(synth.load_design(fix / "model", fix / "rules.json"))
out = Path(sys.argv[2])
for seed in (1, 2, 3):
    synth.write_seed(plan, seed, out / f"seed-{seed}")
h = hashlib.sha256()
for p in sorted(out.rglob("*")):
    if p.is_file():
        h.update(str(p.relative_to(out)).encode() + b"\\0" + p.read_bytes())
print(h.hexdigest())
"""


def test_deterministic_across_processes_and_hash_seeds(tmp_path):
    digests = []
    for hash_seed in ("0", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        env.pop("MP_QUIET", None)
        res = subprocess.run(
            [sys.executable, "-c", _HASH_SCRIPT, str(FIX), str(tmp_path / hash_seed)],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parents[2],
        )
        digests.append(res.stdout.strip())
    assert digests[0] == digests[1]
    assert len(digests[0]) == 64


def test_deterministic_in_process(plan, seeds):
    again = {
        s: {t["name"]: t["rows"] for t in synth.generate(plan, s).tables} for s in SEEDS
    }
    assert again == seeds


def test_seeds_differ(seeds):
    for a, b in ((1, 2), (1, 3), (2, 3)):
        assert seeds[a]["Customers"] != seeds[b]["Customers"]
        assert seeds[a]["Orders"] != seeds[b]["Orders"]


def test_column_streams_come_from_sha256(plan):
    # the Faker stream of a column is seeded from sha256(seed, table, column)
    expected = int.from_bytes(
        hashlib.sha256(b"2\x1fCustomers\x1fEmail").digest()[:8], "big"
    )
    assert synth._sha_int(2, "Customers", "Email") == expected
    gen = synth.TableGen(plan.tables["Kinds"], 2, {})
    assert (
        gen.states["KindName"].frng.random()
        == synth.random.Random(synth._sha_int(2, "Kinds", "KindName")).random()
    )


# ------------------------------------------------------------------ structure


def test_skips_documentation_tables_and_computed_columns(plan, seeds):
    assert "Glossary" not in plan.order
    assert "Glossary" not in seeds[1]
    lines = plan.tables["OrderLines"]
    assert [c.name for c in lines.cols] == ["LineID", "OrderID", "Sku", "Qty", "Price"]
    assert lines.omitted == [
        {"name": "LineTotal", "type": "currency", "reason": "computed"}
    ]
    assert plan.tables["Staff"].omitted == [
        {"name": "Photo", "type": "complex", "reason": "complex"}
    ]
    for seed in SEEDS:
        assert all("LineTotal" not in r for r in seeds[seed]["OrderLines"])


def test_load_order_and_row_counts(plan, seeds):
    assert (
        plan.order.index("Kinds")
        < plan.order.index("Customers")
        < plan.order.index("Orders")
    )
    assert plan.order.index("Orders") < plan.order.index("OrderLines")
    for seed in SEEDS:
        assert len(seeds[seed]["Orders"]) == synth.ROWS
        assert len(seeds[seed]["Customers"]) == synth.ROWS
        assert len(seeds[seed]["Kinds"]) == 3  # one row per code


def test_foreign_keys_hold(seeds):
    for seed in SEEDS:
        s = seeds[seed]
        for rel in SCHEMA["relationships"]:
            parent = {
                tuple(r[c] for c in rel["to_columns"]) for r in s[rel["to_table"]]
            }
            for r in s[rel["from_table"]]:
                key = tuple(r[c] for c in rel["from_columns"])
                assert None in key or key in parent, (seed, rel["name"])
        managers = [r["ManagerID"] for r in s["Staff"] if r["ManagerID"] is not None]
        assert managers, seed


def test_required_unique_and_autonumber(seeds):
    for seed in SEEDS:
        for t in SCHEMA["tables"]:
            if t["role"] == "documentation":
                continue
            rows = seeds[seed][t["name"]]
            for c in t["columns"]:
                if (
                    c["required"]
                    and c["type"] not in ("complex",)
                    and not c.get("computed")
                ):
                    assert all(r[c["name"]] is not None for r in rows), (
                        seed,
                        t["name"],
                        c["name"],
                    )
                if c["type"] == "autonumber":
                    assert values(rows, c["name"]) == list(range(1, len(rows) + 1))
            uniques = [t["primary_key"]] + [
                i["columns"] for i in t["indexes"] if i["unique"]
            ]
            for u in uniques:
                if not u:
                    continue
                keys = [
                    tuple(
                        ds.format_value(column(t["name"], c)["type"], r[c]) for c in u
                    )
                    for r in rows
                ]
                assert len(keys) == len(set(keys)), (seed, t["name"], u)


def test_types_sizes_and_validation(seeds):
    start = dt.datetime(2023, 1, 1)
    end = dt.datetime(2025, 12, 31, 23, 59, 59)
    for seed in SEEDS:
        for t in SCHEMA["tables"]:
            if t["role"] == "documentation":
                continue
            for c in t["columns"]:
                if c["type"] == "complex" or c.get("computed"):
                    continue
                for v in values(seeds[seed][t["name"]], c["name"]):
                    ds.format_value(c["type"], v)
                    if v is None:
                        continue
                    if c["type"] in ("text", "memo") and c["size"]:
                        assert len(v) <= c["size"]
                    if c["type"] == "datetime":
                        assert start <= v <= end
        assert all(q > 0 for q in values(seeds[seed]["Orders"], "Quantity"))
        dates = [d for d in values(seeds[seed]["Orders"], "OrderDate")]
        assert any(d.weekday() >= 5 for d in dates), seed
        assert any((d + dt.timedelta(days=1)).day == 1 for d in dates), seed


# ------------------------------------------------------------------ codes and pools


def test_lookup_codes_from_the_model_win_over_values(plan, seeds):
    # enumKind (compared with KindID in code, and named after the table) holds 0..3; the
    # autonumber key keeps 1..3. rules.json values 7 and 8 are not used.
    assert plan.tables["Kinds"].codes == [1, 2, 3]
    for seed in SEEDS:
        assert values(seeds[seed]["Kinds"], "KindID") == [1, 2, 3]
        assert set(values(seeds[seed]["Customers"], "KindID")) == {1, 2, 3}


def test_lookup_values_only_when_the_model_has_none(plan, seeds):
    assert plan.tables["OrderStates"].codes == [1, 2, 4]
    assert plan.from_rules == ["OrderStates.StateID"]
    for seed in SEEDS:
        assert values(seeds[seed]["OrderStates"], "StateID") == [1, 2, 4]
        assert set(values(seeds[seed]["Orders"], "StateID")) == {1, 2, 4}


def test_lookup_value_list_and_enum_table(plan, seeds):
    assert plan.tables["Orders"].pools["PayMethod"] == ["Card", "Cash", "Voucher"]
    assert plan.tables["Settings"].codes == [1, 3]
    for seed in SEEDS:
        pays = set(values(seeds[seed]["Orders"], "PayMethod")) - {None}
        assert pays == {"Card", "Cash", "Voucher"}
        assert values(seeds[seed]["Settings"], "SettingID") == [1, 3]


def test_generated_lookup_codes(plan, seeds):
    assert plan.generated == ["Orders.Channel"]
    pool = plan.tables["Orders"].pools["Channel"]
    assert len(pool) == synth.GENERATED_CODES
    assert all(len(v) <= 10 for v in pool)
    for seed in SEEDS:
        assert set(values(seeds[seed]["Orders"], "Channel")) - {None} == set(pool)


def test_value_pools_hold_the_design_literals(plan, seeds):
    extras = plan.tables["Orders"].extras
    assert extras["Priority"] == [
        3,
        5,
        7,
        8,
    ]  # code, string filter, SQL In list; not the comment
    assert extras["Discount"] == [0.15]
    assert plan.tables["Customers"].extras["Region"] == [
        "Nord"
    ]  # copy_values false: generated + literal
    assert plan.tables["Staff"].extras["Rating"] == [4.5]  # data macro condition
    for seed in SEEDS:
        s = seeds[seed]
        assert "Nord" in values(s["Customers"], "Region")
        assert {3, 5, 7, 8} <= set(values(s["Orders"], "Priority"))
        assert 0.15 in values(s["Orders"], "Discount")
        assert 4.5 in values(s["Staff"], "Rating")
        assert not any(
            isinstance(v, str) and "&" in v for v in values(s["Customers"], "Region")
        )
        assert 99 not in values(s["Orders"], "Priority")


def test_literal_extraction_rules():
    design = synth.Design(
        schema=SCHEMA,
        schema_sha256="0" * 64,
        code={
            "m": 'If Me.Region = "South" Then\nx = "Region = " & y & ""\nIf Err.Description = "Nord" Then\nIf Me.Region = "A very long message that is no code at all" Then\n\' If Me.Region = "Commented" Then\ns = "SELECT * FROM Customers WHERE Region In (\'East\', \'West\')"'
        },
    )
    lits = synth.Literals(design)
    col = synth.make_col("Customers", column("Customers", "Region"))
    got = synth._sort_values(
        col,
        [
            synth.coerce(col, t)
            for t in lits.raw(("Customers", "Region"))
            if synth.coerce(col, t)
        ],
    )
    assert got == ["East", "South", "West"]


def test_coerce_respects_type_size_window_and_rules():
    region = synth.make_col("Customers", column("Customers", "Region"))
    assert synth.coerce(region, ("str", "Nord")) == "Nord"
    assert synth.coerce(region, ("str", "x" * 11)) is None
    qty = synth.make_col("Orders", column("Orders", "Quantity"))
    assert synth.coerce(qty, ("num", "0")) is None  # validation rule >0
    assert synth.coerce(qty, ("enum", "e", 4)) == 4
    when = synth.make_col("Orders", column("Orders", "OrderDate"))
    assert synth.coerce(when, ("date", "3/31/2024")) == dt.datetime(2024, 3, 31)
    assert synth.coerce(when, ("date", "1/1/2030")) is None
    balance = synth.make_col("Customers", column("Customers", "Balance"))
    assert synth.coerce(balance, ("num", "-2.5")) == Decimal("-2.5")
    key = synth.make_col("Kinds", column("Kinds", "KindID"))
    assert synth.coerce(key, ("enum", "e", 0)) is None  # autonumber runs from 1


# ------------------------------------------------------------------ seed variants


def _text_cols(table):
    return [
        c["name"]
        for c in schema_table(table)["columns"]
        if c["type"] in ("text", "memo") and not c.get("computed")
    ]


def test_seed_1_is_plain(seeds):
    s = seeds[1]
    for t in ("Customers", "Orders"):
        for c in schema_table(t)["columns"]:
            assert all(v is not None for v in values(s[t], c["name"])), (t, c["name"])
        for c in _text_cols(t):
            assert all(v != "" for v in values(s[t], c)), (t, c)
            assert all(v is None or v.isascii() for v in values(s[t], c)), (t, c)


def test_seed_2_adds_nulls_empties_boundaries_and_non_ascii(seeds):
    s = seeds[2]
    for t in SCHEMA["tables"]:
        if t["role"] == "documentation":
            continue
        rows = s[t["name"]]
        for c in t["columns"]:
            if (
                c["required"]
                or c["type"] == "complex"
                or c.get("computed")
                or c["name"] in t["primary_key"]
            ):
                continue
            assert None in values(rows, c["name"]), (t["name"], c["name"])
    assert "" in values(s["Customers"], "Email")
    assert "" in values(s["Customers"], "Notes")
    assert "" not in values(s["Customers"], "Phone")  # AllowZeroLength 0
    assert any(
        v is not None and len(v) == 40 for v in values(s["Customers"], "CustName")
    )
    assert any(
        v is not None and not v.isascii() for v in values(s["Customers"], "CustName")
    )
    balances = [v for v in values(s["Customers"], "Balance") if v is not None]
    assert any(v < 0 for v in balances) and Decimal(0) in balances
    created = values(s["Customers"], "Created")
    assert dt.datetime(2023, 1, 1) in created
    assert dt.datetime(2025, 12, 31, 23, 59, 59) in created
    assert any(
        v is not None and "\r\n" in v and '"' in v
        for v in values(s["Customers"], "Notes")
    )


def test_seed_3_adds_duplicates_and_many_children(seeds):
    s = seeds[3]
    names = [v for v in values(s["Customers"], "Region") if v is not None]
    assert len(names) > len(set(names))
    per_order: dict = {}
    for r in s["OrderLines"]:
        per_order[r["OrderID"]] = per_order.get(r["OrderID"], 0) + 1
    assert max(per_order.values()) >= 10
    per_customer: dict = {}
    for r in s["Orders"]:
        per_customer[r["CustomerID"]] = per_customer.get(r["CustomerID"], 0) + 1
    assert max(per_customer.values()) >= 10
    one: dict = {}
    for r in seeds[1]["OrderLines"]:
        one[r["OrderID"]] = one.get(r["OrderID"], 0) + 1
    assert max(per_order.values()) > max(one.values())


def test_a_hard_coded_answer_fails_on_some_seed(seeds):
    # e.g. a fixed order count per customer or a fixed total: at least one seed disagrees
    def total(seed):
        return sum(r["Price"] * r["Qty"] for r in seeds[seed]["OrderLines"])

    assert len({total(s) for s in SEEDS}) == 3
