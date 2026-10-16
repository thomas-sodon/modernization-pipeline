"""S2.reference: query designs to Access SQL (Jackcess's formatter), the reference catalog, the
reference databases (UCanAccess) and runs, ReferenceAdapter and the bridge.

Fixtures are hand-written and synthetic (toolkit/tests/fixtures/reference/): a tiny model with a
join, an outer join, a group by with having, a declared parameter, a union, a query on a query, a
form reference and an app-function call rewrite. Expected SQL is written by hand; expected rows
come from the fixture dataset loaded through `reference build`. Nothing is copied from Northwind.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from toolkit import cli
from toolkit.engagement import load
from toolkit.reference import bridge, qbe
from toolkit.reference import catalog as cat

FIX = Path(__file__).parent / "fixtures" / "reference"
ROOT = Path(__file__).resolve().parents[2]
D = dt.datetime


def model_queries() -> dict[str, dict]:
    doc = json.loads((FIX / "model" / "objects.json").read_text(encoding="utf-8"))
    return {q["name"]: q for q in doc["queries"]}


def run_cli(capfd, *argv) -> tuple[int, dict]:
    capfd.readouterr()
    code = cli.main([str(a) for a in argv])
    out, _err = capfd.readouterr()
    lines = out.strip().splitlines()
    assert len(lines) == 1, out
    return code, json.loads(lines[0])


def tree(root: Path) -> dict[str, int]:
    """{relative file: mtime_ns} under root."""
    out = {}
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            if p.is_file():
                out[str(p.relative_to(root))] = p.stat().st_mtime_ns
    return out


@pytest.fixture
def eng(tmp_path, make_engagement, monkeypatch):
    """A synthetic Access engagement: the fixture model in the design zone, the rewrite file and
    seed-1 in the target. Returns the engagement dict (paths filled in)."""
    monkeypatch.setenv("MP_QUIET", "1")
    monkeypatch.delenv("MP_SANDBOX", raising=False)
    path = make_engagement(
        tmp_path,
        id="ref-test",
        source={
            "kind": "accdt",
            "path": "{{p.private}}/sources/app.accdt",
            "saved_with_data": True,
        },
    )
    e = load(path)
    e["_path"] = str(path)
    private, target = Path(e["paths"]["private"]), Path(e["paths"]["target"])
    shutil.copytree(FIX / "model", private / "out" / "model")
    shutil.copytree(FIX / "functions", target / "goldens" / "reference" / "functions")
    shutil.copytree(
        FIX / "dataset", target / "goldens" / "synthetic" / "seed-1" / "input"
    )
    return e


def seed_ref(eng, capfd):
    code, summary = run_cli(
        capfd, "reference", "build", eng["_path"], "--dataset", "seeds"
    )
    assert code == 0, summary
    from toolkit.reference.engine import AccessReference

    private = Path(eng["paths"]["private"])
    return AccessReference(
        private, private / "synthetic" / "reference" / "seed-1.accdb"
    ).open()


# ------------------------------------------------------------------ conventions


def test_file_safe_id_rule():
    assert cat.file_safe("qryOrderTotals") == "qryOrderTotals"
    assert cat.file_safe("Order Details") == "Order_Details-fe3ac0d4"
    assert (
        cat.file_safe("InvoiceTable[Net Amount]") == "InvoiceTable_Net_Amount_-f34d32b9"
    )
    assert cat.file_safe("a.b-c_d") == "a.b-c_d"


# ------------------------------------------------------------------ designs to rows and SQL


def test_select_rows_follow_msysqueries_attributes():
    flag, rows = qbe.design_rows(model_queries()["qAllCustomers"])
    assert flag == 0
    assert rows == [
        (0, None, None, None, None),
        (1, None, 1, None, None),
        (3, None, 0, None, None),
        (5, None, None, "Customers", None),
        (5, None, None, "Orders", None),
        (6, "Customers.Name", None, None, None),
        (6, "Orders.OrderID", None, None, None),
        (7, "Customers.CustID = Orders.CustID", 2, "Customers", "Orders"),
        (11, "Customers.CustID", None, None, None),
        (11, "Orders.OrderID", None, "D", None),
        (255, None, None, None, None),
    ]


def test_union_and_parameter_rows():
    flag, rows = qbe.design_rows(model_queries()["qRegions"])
    assert flag == 128
    assert (1, None, 9, None, None) in rows
    assert (3, None, 2, None, None) in rows  # flag 2: UNION without ALL
    assert (
        5,
        "SELECT Customers.Region FROM Customers",
        None,
        None,
        "X7YZ_____1",
    ) in rows
    _flag, rows = qbe.design_rows(model_queries()["qOrdersSince"])
    assert (2, None, 8, "Since", None) in rows  # PARAMETER, DataType 8 = date/time


EXPECTED_SQL = {
    "qCustOrders": (
        "SELECT Customers.Name, Orders.OrderID, Orders.Amount\n"
        "FROM Customers INNER JOIN Orders ON Customers.CustID = Orders.CustID\n"
        "ORDER BY Orders.OrderID;"
    ),
    "qAllCustomers": (
        "SELECT Customers.Name, Orders.OrderID\n"
        "FROM Customers LEFT JOIN Orders ON Customers.CustID = Orders.CustID\n"
        "ORDER BY Customers.CustID, Orders.OrderID DESC;"
    ),
    "qRegionTotals": (
        "SELECT Customers.Region, Sum(Orders.Amount) AS Total\n"
        "FROM Customers INNER JOIN Orders ON Customers.CustID = Orders.CustID\n"
        "GROUP BY Customers.Region\n"
        "HAVING (((Sum(Orders.Amount))>10));"
    ),
    "qOrdersSince": (
        "PARAMETERS Since DateTime;\n"
        "SELECT o.OrderID, o.OrderDate\n"
        "FROM Orders AS o\n"
        "WHERE (((o.OrderDate)>=[Since]))\n"
        "ORDER BY o.OrderDate;"
    ),
    "qBigSpenders": (
        "SELECT qRegionTotals.Region\n"
        "FROM qRegionTotals\n"
        "WHERE (((qRegionTotals.Total)>50));"
    ),
    "qRegions": (
        "SELECT Customers.Region FROM Customers\n"
        "UNION SELECT Customers.Region FROM Customers WHERE Customers.Active=True\n"
        "ORDER BY Region;"
    ),
    "qCustBalance": (
        "SELECT Customers.Name, OpenAmount([Customers].[CustID]) AS OpenTotal\n"
        "FROM Customers\n"
        "ORDER BY Customers.CustID;"
    ),
}


@pytest.mark.parametrize("name", sorted(EXPECTED_SQL))
def test_formatter_sql_by_hand(name):
    status, reason, sql = qbe.query_sql(model_queries()[name])
    assert (status, reason) == ("ok", None)
    assert sql == EXPECTED_SQL[name]


def test_sql_text_passes_through_and_unsupported_kinds():
    q = model_queries()
    assert qbe.query_sql(q["qRegionsText"]) == ("ok", None, q["qRegionsText"]["sql"])
    assert qbe.query_sql(q["qMakeTable"])[:2] == ("unsupported", "action-query")
    assert qbe.query_sql(q["qXtab"])[:2] == ("unsupported", "crosstab")
    passthrough = {
        "name": "qRemote",
        "storage": "sql",
        "query_type": "pass-through",
        "sql": "SELECT 1",
        "design": {},
    }
    assert qbe.query_sql(passthrough)[:2] == ("unsupported", "pass-through")


def test_qbe_main_prints_ids_and_sql(eng, capsys):
    assert qbe.main([eng["_path"]]) == 0
    out = capsys.readouterr().out
    assert "-- qCustOrders\n" + EXPECTED_SQL["qCustOrders"] in out
    assert "-- qMakeTable: unsupported (action-query)" in out
    assert not (Path(eng["paths"]["private"]) / "out" / "reference").exists()


# ------------------------------------------------------------------ reference sql


def test_reference_sql_writes_catalog_and_sql(eng, capfd):
    code, summary = run_cli(capfd, "reference", "sql", eng["_path"], "--all")
    assert code == 0 and summary["status"] == "ok"
    assert summary["counts"]["units"] == 12
    private = Path(eng["paths"]["private"])
    catalog = json.loads((private / "out" / "reference" / "catalog.json").read_text())
    assert (
        catalog["engine"] == "ucanaccess-5.1.8" and catalog["engagement"] == "ref-test"
    )
    units = {u["id"]: u for u in catalog["units"]}
    assert len(units) == 12
    for u in units.values():
        assert u["kind"] == "saved_query"
        assert u["object"] == {"type": "query", "name": u["id"]}
        assert u["sql_ref"] == f"sql/{cat.file_safe(u['id'])}.sql"
        assert (private / "out" / "reference" / u["sql_ref"]).is_file()
        assert (
            u["reason"] is None if u["status"] == "ok" else u["reason"] in cat.REASONS
        )
    assert units["qBigSpenders"]["depends_on"] == ["qRegionTotals"]
    assert units["qCustBalance"]["app_functions"] == ["OpenAmount"]
    assert units["qCustBalance"]["status"] == "ok"
    assert units["qCustBalance"]["needs_access_confirmation"] is True
    assert units["qCustOrders"]["needs_access_confirmation"] is False
    balance = (private / "out" / "reference" / "sql" / "qCustBalance.sql").read_text()
    assert "OpenAmount" not in balance
    assert (
        "(Nz((SELECT Sum(oa_o.Amount) FROM Orders AS oa_o WHERE oa_o.CustID = [Customers].[CustID]), 0)) AS OpenTotal"
        in balance
    )
    assert units["qOrdersSince"]["status"] == "needs-params"
    assert units["qOrdersSince"]["params"] == [
        {"name": "Since", "type": "datetime", "source": "prompt"}
    ]
    assert units["qFormFilter"]["status"] == "needs-params"
    assert units["qFormFilter"]["reason"] == "form-reference-unresolved"
    assert units["qFormFilter"]["params"] == [
        {
            "name": "Forms!frmMain!cboCust",
            "type": "longinteger",
            "source": "form_control",
        }
    ]
    assert (units["qNoRewrite"]["status"], units["qNoRewrite"]["reason"]) == (
        "unsupported",
        "unsupported-function",
    )
    assert (units["qMakeTable"]["status"], units["qMakeTable"]["reason"]) == (
        "unsupported",
        "action-query",
    )
    assert (units["qXtab"]["status"], units["qXtab"]["reason"]) == (
        "unsupported",
        "crosstab",
    )
    assert {u["id"] for u in summary["units"]} == set(units)


def test_query_on_unsupported_query_inherits_status(tmp_path):
    private = tmp_path / "p"
    shutil.copytree(FIX / "model", private / "out" / "model")
    objects = json.loads((private / "out" / "model" / "objects.json").read_text())
    objects["queries"].append(
        {
            "name": "qOnNoRewrite",
            "storage": "sql",
            "query_type": "select",
            "design": {},
            "params": [],
            "functions": [],
            "form_refs": [],
            "sql": "SELECT qNoRewrite.X FROM qNoRewrite;",
        }
    )
    objects["queries"].append(
        {
            "name": "qOnForm",
            "storage": "sql",
            "query_type": "select",
            "design": {},
            "params": [],
            "functions": [],
            "form_refs": [],
            "sql": "SELECT qFormFilter.OrderID FROM qFormFilter;",
        }
    )
    (private / "out" / "model" / "objects.json").write_text(json.dumps(objects))
    catalog, _sqls, _msgs = cat.build(private, tmp_path / "t", "x", java_functions={})
    units = {u["id"]: u for u in catalog["units"]}
    assert (units["qOnNoRewrite"]["status"], units["qOnNoRewrite"]["reason"]) == (
        "unsupported",
        "unsupported-function",
    )
    assert units["qOnForm"]["status"] == "needs-params"
    assert units["qOnForm"]["params"] == units["qFormFilter"]["params"]


def test_format_quarter_pattern_is_rewritten_with_datepart():
    sql, n = cat.engine_compat(
        'SELECT Format([d],"q-yyyy") AS Q FROM T GROUP BY Format([d],"q-yyyy");'
    )
    assert n == 2
    assert sql == (
        'SELECT (DatePart("q", [d]) & "-" & Format([d], "yyyy")) AS Q FROM T '
        'GROUP BY (DatePart("q", [d]) & "-" & Format([d], "yyyy"));'
    )
    assert cat.engine_compat('SELECT Format(d,"mmm-yyyy") FROM T')[1] == 0


# ------------------------------------------------------------------ build and run (seeds)


def test_seed_build_and_expected_rows(eng, capfd):
    ref = seed_ref(eng, capfd)
    try:
        assert ref.view_errors == {}
        assert ref.rows("qCustOrders") == [
            ("Alder Supply", 10, Decimal("12.5")),
            ("Alder Supply", 11, Decimal(30)),
            ("Birch Works", 12, Decimal("7.25")),
            ("Cedar Goods", 13, Decimal(40)),
        ]
        assert ref.rows("qAllCustomers") == [
            ("Alder Supply", 11),
            ("Alder Supply", 10),
            ("Birch Works", 12),
            ("Cedar Goods", 13),
            ("Dune Trading", None),
        ]
        assert ref.rows("qRegionTotals") == [("North", Decimal("82.5"))]
        assert ref.rows("qBigSpenders") == [("North",)]
        assert ref.rows("qRegions") == [("East",), ("North",), ("South",)]
        assert len(ref.rows("qRegionsText")) == 5
        assert ref.rows("qCustBalance") == [
            ("Alder Supply", Decimal("42.5")),
            ("Birch Works", Decimal("7.25")),
            ("Cedar Goods", Decimal(40)),
            ("Dune Trading", 0),
        ]
        assert ref.rows("qOrdersSince", {"Since": "2024-02-11T00:00:00"}) == [
            (12, D(2024, 2, 11)),
            (13, D(2024, 3, 1, 8, 15)),
        ]
        assert sorted(ref.rows("qFormFilter", {"Forms!frmMain!cboCust": "1"})) == [
            (10,),
            (11,),
        ]
        assert ref.describe("qRegionTotals") == [
            ("Region", "text"),
            ("Total", "currency"),
        ]
        assert ref.describe("qCustOrders") == [
            ("Name", "text"),
            ("OrderID", "longinteger"),
            ("Amount", "currency"),
        ]
    finally:
        ref.close()


def test_seed_database_keeps_keys_and_types(eng, capfd):
    seed_ref(eng, capfd).close()
    path = Path(eng["paths"]["private"]) / "synthetic" / "reference" / "seed-1.accdb"
    db = bridge.open_database(path)
    try:
        assert sorted(bridge.tables(db)) == ["Customers", "Orders"]
        cols = {c.name: c.type for c in bridge.columns(db, "Orders")}
        assert cols == {
            "OrderID": "autonumber",
            "CustID": "longinteger",
            "OrderDate": "datetime",
            "Amount": "currency",
            "Qty": "integer",
        }
        assert [r["OrderID"] for r in bridge.iter_rows(db, "Orders")] == [
            10,
            11,
            12,
            13,
        ]
        logo = [r["Logo"] for r in bridge.iter_rows(db, "Customers")]
        assert logo == [
            None,
            None,
            None,
            None,
        ]  # complex column: an all-NULL placeholder
    finally:
        db.close()


def test_reference_run_summary_counts_and_no_values(eng, capfd):
    seed_ref(eng, capfd).close()
    code, summary = run_cli(
        capfd,
        "reference",
        "run",
        eng["_path"],
        "--dataset",
        "seed-1",
        "--all",
        "--summary",
    )
    assert code == 0, summary
    units = {u["id"]: u for u in summary["units"]}
    assert units["qCustOrders"] == {
        "id": "qCustOrders",
        "status": "ok",
        "reason": None,
        "dataset": "seed-1",
        "total": 4,
    }
    assert (
        units["qOrdersSince"]["status"] == "needs-params"
        and "total" not in units["qOrdersSince"]
    )
    assert units["qMakeTable"]["status"] == "unsupported"
    assert summary["counts"]["ok"] == 7 and summary["counts"]["needs-params"] == 2
    assert summary["counts"]["unsupported"] == 3 and summary["counts"]["failed"] == 0
    line = json.dumps(summary)
    for value in ("Alder", "Birch", "North", "12.5", "2024-"):
        assert value not in line
    # parameter values from the committed params file make needs-params units run
    params = Path(eng["paths"]["target"]) / "goldens" / "params.json"
    params.write_text(
        json.dumps(
            {
                "format_version": 1,
                "units": {
                    "qOrdersSince": {
                        "params": [],
                        "values": {"seed-1": {"Since": "2024-02-11T00:00:00"}},
                        "keys": {},
                    },
                    "qFormFilter": {
                        "params": [],
                        "values": {"seed-1": {"Forms!frmMain!cboCust": "4"}},
                        "keys": {},
                    },
                },
            }
        )
    )
    code, summary = run_cli(
        capfd,
        "reference",
        "run",
        eng["_path"],
        "--dataset",
        "seed-1",
        "--all",
        "--summary",
    )
    units = {u["id"]: u for u in summary["units"]}
    assert (
        units["qOrdersSince"]["status"] == "ok" and units["qOrdersSince"]["total"] == 2
    )
    assert (
        units["qFormFilter"]["status"] == "empty" and units["qFormFilter"]["total"] == 0
    )


def test_seed_build_skips_a_current_database(eng, capfd):
    seed_ref(eng, capfd).close()
    code, summary = run_cli(
        capfd, "reference", "build", eng["_path"], "--dataset", "seed-1"
    )
    assert code == 0
    assert (
        summary["counts"]["databases_skipped"] == 1
        and summary["counts"]["databases_built"] == 0
    )


def test_two_read_only_opens_at_once(eng, capfd):
    from toolkit.reference.engine import AccessReference

    first = seed_ref(eng, capfd)
    private = Path(eng["paths"]["private"])
    db = private / "synthetic" / "reference" / "seed-1.accdb"
    second = AccessReference(private, db).open()
    try:
        assert first.count("qCustOrders") == second.count("qCustOrders") == 4
        assert first.count("qBigSpenders") == second.count("qBigSpenders") == 1
    finally:
        first.close()
        second.close()
    before = db.stat().st_mtime_ns
    env = {**os.environ, "MP_QUIET": "1"}
    env.pop("MP_SANDBOX", None)
    cmd = [
        sys.executable,
        "-c",
        "import sys; from toolkit.cli import main; sys.exit(main())",
        "reference",
        "run",
        eng["_path"],
        "--dataset",
        "seed-1",
        "--all",
        "--summary",
    ]
    procs = [
        subprocess.Popen(
            cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        for _ in range(2)
    ]
    results = [p.communicate(timeout=300) for p in procs]
    for p, (out, _err) in zip(procs, results):
        assert p.returncode == 0
        assert json.loads(out.decode().strip())["counts"]["failed"] == 0
    assert db.stat().st_mtime_ns == before


def test_reference_adapter(eng, capfd, monkeypatch):
    from toolkit.commands.reference import ReferenceAdapter

    seed_ref(eng, capfd).close()
    private = eng["paths"]["private"]
    monkeypatch.setenv("EQUIVALENCE_PRIVATE", private)
    adapter = ReferenceAdapter()
    adapter.load_dataset(
        Path(eng["paths"]["target"]) / "goldens" / "synthetic" / "seed-1" / "input"
    )
    assert adapter.run_saved_query("qBigSpenders", {}) == [("North",)]
    assert adapter.run_saved_query(
        "qOrdersSince", {"Since": "2024-03-01T00:00:00"}
    ) == [(13, D(2024, 3, 1, 8, 15))]
    assert adapter.describe("qBigSpenders") == [("Region", "text")]
    with pytest.raises(NotImplementedError):
        adapter.render_document("qBigSpenders", None, {})
    with pytest.raises(NotImplementedError):
        adapter.run_saved_query("qMakeTable", {})
    adapter._impl.close()
    assert ReferenceAdapter(private=private).describe is not None


# ------------------------------------------------------------------ engagement build


def _engagement_dataset(eng, empty=False):
    private = Path(eng["paths"]["private"])
    dest = private / "datasets" / "engagement"
    shutil.copytree(FIX / "dataset", dest)
    if empty:
        from toolkit.dataset import read_meta, write_dataset

        meta = read_meta(dest)
        shutil.rmtree(dest)
        write_dataset(
            dest,
            {**meta, "name": "engagement", "kind": "engagement", "seed": None},
            [
                {
                    "name": t["name"],
                    "columns": t["columns"],
                    "omitted_columns": t["omitted_columns"],
                    "rows": [],
                }
                for t in meta["tables"]
            ],
        )
    return dest


def _outside_reference(eng) -> dict:
    private = Path(eng["paths"]["private"])
    snap = {k: v for k, v in tree(private).items() if not k.startswith("reference/")}
    snap.update(
        {"target/" + k: v for k, v in tree(Path(eng["paths"]["target_repo"])).items()}
    )
    return snap


def test_engagement_build_refuses_missing_or_stale_catalog(eng, capfd, monkeypatch):
    monkeypatch.setenv("MP_SANDBOX", "mp-data")
    _engagement_dataset(eng)
    private = Path(eng["paths"]["private"])
    before = _outside_reference(eng)
    code, summary = run_cli(
        capfd, "reference", "build", eng["_path"], "--dataset", "engagement"
    )
    assert code == 2
    assert (
        f"uv run toolkit reference sql {eng['_path']} --all" in summary["messages"][0]
    )
    assert "missing" in summary["messages"][0]
    assert _outside_reference(eng) == before
    assert tree(private / "reference") == {}
    # a catalog older than the model is refused too
    monkeypatch.delenv("MP_SANDBOX")
    assert run_cli(capfd, "reference", "sql", eng["_path"], "--all")[0] == 0
    future = (private / "out" / "reference" / "catalog.json").stat().st_mtime + 60
    os.utime(private / "out" / "model" / "objects.json", (future, future))
    monkeypatch.setenv("MP_SANDBOX", "mp-data")
    before = _outside_reference(eng)
    code, summary = run_cli(
        capfd, "reference", "build", eng["_path"], "--dataset", "engagement"
    )
    assert code == 2 and "older than the model" in summary["messages"][0]
    assert _outside_reference(eng) == before and tree(private / "reference") == {}
    # with a current catalog it writes only reference/engagement.accdb
    monkeypatch.delenv("MP_SANDBOX")
    past = future - 3600
    os.utime(private / "out" / "model" / "objects.json", (past, past))
    assert run_cli(capfd, "reference", "sql", eng["_path"], "--all")[0] == 0
    monkeypatch.setenv("MP_SANDBOX", "mp-data")
    before = _outside_reference(eng)
    code, summary = run_cli(
        capfd, "reference", "build", eng["_path"], "--dataset", "engagement"
    )
    assert code == 0, summary
    assert summary["counts"]["databases_built"] == 1 and summary["counts"]["rows"] == 8
    assert summary["counts"]["query_failures"] == 0
    assert _outside_reference(eng) == before
    assert list(tree(private / "reference")) == ["engagement.accdb"]
    code, summary = run_cli(
        capfd,
        "reference",
        "run",
        eng["_path"],
        "--dataset",
        "engagement",
        "--all",
        "--summary",
    )
    assert code == 0 and summary["counts"]["ok"] == 7
    assert _outside_reference(eng) == before


def test_empty_reference_fails(eng, capfd, monkeypatch):
    _engagement_dataset(eng, empty=True)
    assert run_cli(capfd, "reference", "sql", eng["_path"], "--all")[0] == 0
    monkeypatch.setenv("MP_SANDBOX", "mp-data")
    code, summary = run_cli(
        capfd, "reference", "build", eng["_path"], "--dataset", "engagement"
    )
    assert code == 1 and summary["messages"][0] == "empty reference"
    assert tree(Path(eng["paths"]["private"]) / "reference") == {}


def test_engagement_without_data_exits_2(tmp_path, make_engagement, capfd, monkeypatch):
    monkeypatch.setenv("MP_QUIET", "1")
    monkeypatch.setenv("MP_SANDBOX", "mp-data")
    path = make_engagement(
        tmp_path,
        id="ref-nodata",
        source={
            "kind": "accdt",
            "path": "{{p.private}}/sources/app.accdt",
            "saved_with_data": False,
            "data_copy": None,
        },
    )
    code, summary = run_cli(
        capfd, "reference", "build", path, "--dataset", "engagement"
    )
    assert code == 2 and summary["messages"] == [
        "no engagement data: set source.data_copy"
    ]


def test_dataset_all_exits_2_and_names_both_commands(eng, capfd):
    code, summary = run_cli(
        capfd, "reference", "build", eng["_path"], "--dataset", "all"
    )
    assert code == 2
    msg = "\n".join(summary["messages"])
    assert f"uv run toolkit reference build {eng['_path']} --dataset seeds" in msg
    assert f"mp-data reference build {eng['_path']} --dataset engagement" in msg


def test_engagement_dataset_runs_only_through_mp_data(eng, capfd):
    assert (
        run_cli(capfd, "reference", "build", eng["_path"], "--dataset", "engagement")[0]
        == 2
    )
    assert (
        run_cli(
            capfd,
            "reference",
            "run",
            eng["_path"],
            "--dataset",
            "engagement",
            "--all",
            "--summary",
        )[0]
        == 2
    )


def test_selfcheck_is_xlsm_only(eng, capfd):
    code, summary = run_cli(capfd, "reference", "selfcheck", eng["_path"])
    assert code == 2 and "only for xlsm" in summary["messages"][0]


# ------------------------------------------------------------------ bridge


def test_import_bridge_starts_no_jvm():
    code = (
        "import sys; import toolkit.reference.bridge as b; "
        "print(int('jpype' in sys.modules), int(b.jvm_started()), "
        "sorted(m for m in sys.modules if m.startswith('toolkit.reference')))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert (
        proc.stdout.strip() == "0 0 ['toolkit.reference', 'toolkit.reference.bridge']"
    )


def test_bridge_on_a_created_database(tmp_path):
    path = tmp_path / "made.accdb"
    conn = bridge.connect(path, read_only=False, new_version="V2010")
    try:
        st = conn.createStatement()
        st.execute(
            "CREATE TABLE [Item List] ([ItemID] COUNTER, [Label] TEXT(30), [Notes] MEMO, [Small] SMALLINT, "
            "[Tiny] BYTE, [Big] LONG, [Price] CURRENCY, [Ratio] DOUBLE, [Weight] SINGLE, [Exact] NUMERIC(9,3), "
            "[Seen] DATETIME, [Done] YESNO, [Link] HYPERLINK, [Tag] GUID, [Blob] OLE, PRIMARY KEY ([ItemID]))"
        )
        st.execute(
            "INSERT INTO [Item List] ([Label], [Notes], [Small], [Tiny], [Big], [Price], [Ratio], [Weight], [Exact], "
            "[Seen], [Done], [Link]) VALUES ('alpha', 'long text', -3, 200, 70000, 9.99, 0.5, 0.25, 12.345, "
            "#2024-05-06 07:08:09#, True, '#http://example.invalid#')"
        )
    finally:
        conn.close()
    assert path.is_file()
    db = bridge.open_database(path, read_only=False)
    try:
        assert bridge.tables(db) == ["Item List"]
        cols = bridge.columns(db, "Item List")
        assert [(c.name, c.type) for c in cols] == [
            ("ItemID", "autonumber"),
            ("Label", "text"),
            ("Notes", "memo"),
            ("Small", "integer"),
            ("Tiny", "byte"),
            ("Big", "longinteger"),
            ("Price", "currency"),
            ("Ratio", "double"),
            ("Weight", "single"),
            ("Exact", "decimal"),
            ("Seen", "datetime"),
            ("Done", "yesno"),
            ("Link", "hyperlink"),
            ("Tag", "guid"),
            ("Blob", "oleobject"),
        ]
        assert (
            cols[1]["size"] == 30 and cols[1].get("type") == "text"
        )  # records read by name too
        (row,) = list(bridge.iter_rows(db, "Item List"))
        assert (
            row["ItemID"] == 1
            and row["Label"] == "alpha"
            and row["Notes"] == "long text"
        )
        assert row["Small"] == -3 and row["Tiny"] == 200 and row["Big"] == 70000
        assert row["Price"] == Decimal("9.99") and isinstance(row["Price"], Decimal)
        assert (
            row["Ratio"] == 0.5
            and row["Weight"] == 0.25
            and row["Exact"] == Decimal("12.345")
        )
        assert row["Seen"] == D(2024, 5, 6, 7, 8, 9) and row["Done"] is True
        assert row["Link"] == "#http://example.invalid#" and row["Blob"] is None
        assert isinstance(row["Tag"], str)
        assert list(row) == [c.name for c in cols]
        rows = [
            (qbe.TABLE, None, None, "Item List", None),
            (qbe.COLUMN, "[Item List].Label", None, None, None),
        ]
        rows = (
            [
                (0, None, None, None, None),
                (1, None, 1, None, None),
                (3, None, 0, None, None),
            ]
            + rows
            + [(255, None, None, None, None)]
        )
        qbe.write_saved_query(db, "qLabels", rows)
        assert [tuple(q) for q in bridge.queries(db)] == [
            ("qLabels", "select", "SELECT [Item List].Label\nFROM [Item List];")
        ]
    finally:
        db.close()
    assert (
        bridge.connect(bridge.open_database(path), "Item List") is None
    )  # a local table has no link


def test_bridge_errors_name_the_fix(tmp_path, monkeypatch):
    with pytest.raises(bridge.BridgeUnavailable, match="uv run toolkit fetch --jars"):
        bridge.classpath(tmp_path)
    monkeypatch.setenv("MP_SANDBOX", "mp-data")
    with pytest.raises(bridge.BridgeUnavailable, match="mp-data fetch <eng> --verify"):
        bridge.classpath(tmp_path)
    monkeypatch.delenv("JAVA_HOME")
    with pytest.raises(bridge.BridgeUnavailable, match="environment item jdk"):
        bridge.java_home()


def test_missing_java_home_exits_2(eng):
    env = {**os.environ, "MP_QUIET": "1"}
    env.pop("JAVA_HOME", None)
    env.pop("MP_SANDBOX", None)
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from toolkit.cli import main; sys.exit(main())",
            "reference",
            "build",
            eng["_path"],
            "--dataset",
            "seeds",
        ],
        check=False,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 2
    assert "environment item jdk" in json.loads(proc.stdout)["messages"][0]


# ------------------------------------------------------------------ java/ app functions

JAVA_SOURCE = """package synthetic;

import net.ucanaccess.converters.TypesMap.AccessType;
import net.ucanaccess.ext.FunctionType;

public final class Twice {
    private Twice() {}

    @FunctionType(functionName = "Twice", argumentTypes = {AccessType.LONG}, returnType = AccessType.LONG)
    public static int twice(int x) {
        return 2 * x;
    }
}
"""


def test_javac_argv_and_output_under_the_temp_dir(tmp_path):
    src = tmp_path / "java" / "synthetic"
    src.mkdir(parents=True)
    (src / "Twice.java").write_text(JAVA_SOURCE, encoding="utf-8")
    assert bridge.java_function_names(tmp_path / "java") == {"twice": "synthetic.Twice"}
    seen = []

    def fake(argv, **kw):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    work = tmp_path / "tmp"
    work.mkdir()
    jar, classes = bridge.compile_java(
        tmp_path / "java",
        work,
        home=Path("/jdk"),
        cp=["/v/a.jar", "/v/b.jar"],
        runner=fake,
    )
    (argv,) = seen
    assert argv[:6] == [
        "/jdk/bin/javac",
        "-encoding",
        "UTF-8",
        "-nowarn",
        "-d",
        argv[5],
    ]
    assert Path(argv[5]).parent == jar.parent and jar.parent.parent == work
    assert argv[6:8] == ["-cp", os.pathsep.join(["/v/a.jar", "/v/b.jar"])]
    assert argv[8:] == [str(src / "Twice.java")]
    assert classes == []  # the fake javac wrote no class
    # the real javac: the jar and nothing else is written, under the temp dir only
    before = tree(tmp_path / "java")
    work2 = tmp_path / "tmp2"
    work2.mkdir()
    jar, classes = bridge.compile_java(tmp_path / "java", work2)
    assert classes == ["synthetic.Twice"]
    assert jar.is_file() and jar.parent.parent == work2
    assert [str(p.relative_to(work2)) for p in sorted(work2.rglob("*"))] == [
        jar.parent.name,
        f"{jar.parent.name}/app-functions.jar",
    ]
    assert tree(tmp_path / "java") == before
    assert bridge.compile_java(tmp_path / "empty", work2) == (None, [])
