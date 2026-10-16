"""S2.golden: golden record (manifest, params, seed and engagement outputs, --check,
--private-only) and golden status (docs/contracts.md#goldens-manifest, #pass-rate).

Fixtures are hand-written and synthetic (toolkit/tests/fixtures/golden/). Units run through a
fake adapter that computes them from the fixture dataset, so these tests need no JVM; the real
ReferenceAdapter is exercised by the S2.golden acceptance on the engagement and its seeds.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import shutil
from pathlib import Path
from typing import ClassVar

import pytest

from harness import canonical as canon
from toolkit import cli
from toolkit import golden as gl
from toolkit.dataset import DatasetDirReader, read_meta
from toolkit.engagement import load
from toolkit.reference import catalog as cat

FIX = Path(__file__).parent / "fixtures" / "golden"
ENGAGEMENT_BYTES = b"synthetic engagement file for golden tests\n"


# ------------------------------------------------------------------ fake adapter


class FakeAdapter:
    """Computes the fixture units from the loaded dataset directory."""

    COLUMNS: ClassVar[dict] = {
        "qOrders": [
            ("OrderID", "autonumber"),
            ("Amount", "currency"),
            ("Shipped", "yesno"),
        ],
        "qByCustomer": [("OrderID", "autonumber"), ("Total", "currency")],
        "qEmpty": [("OrderID", "autonumber")],
        "qDropped": [("Name", "text")],
        "qOrphan": [("CustID", "autonumber"), ("Name", "text")],
    }
    missing: ClassVar[set[str]] = set()
    calls: ClassVar[list] = []

    def __init__(self, private):
        self.private = Path(private)
        self.reader = None

    def load_dataset(self, directory):
        name = read_meta(directory)["name"]
        if name in self.missing:
            raise FileNotFoundError(f"reference database for {name} missing")
        self.reader = DatasetDirReader(Path(directory))

    def describe(self, unit_id):
        if unit_id not in self.COLUMNS:
            raise NotImplementedError(unit_id)
        return list(self.COLUMNS[unit_id])

    def run_saved_query(self, unit_id, params=None):
        FakeAdapter.calls.append((unit_id, dict(params or {})))
        orders = list(self.reader.iter_rows("Orders"))
        if unit_id == "qOrders":
            return [(o["OrderID"], o["Total"], o["Shipped"]) for o in orders]
        if unit_id == "qByCustomer":  # large orders of one customer
            want = int(params["Forms!frmCust!CustID"])
            return [
                (o["OrderID"], o["Total"])
                for o in orders
                if o["CustID"] == want and o["Total"] is not None and o["Total"] > 50
            ]
        if unit_id == "qEmpty":
            return []
        if unit_id == "qOrphan":
            return [
                (c["CustID"], c["Name"]) for c in self.reader.iter_rows("Customers")
            ]
        raise NotImplementedError(unit_id)

    def render_document(self, unit_id, key=None, params=None):
        raise NotImplementedError(unit_id)


@pytest.fixture
def eng(tmp_path, make_engagement, monkeypatch):
    monkeypatch.setenv("MP_QUIET", "1")
    monkeypatch.delenv("MP_SANDBOX", raising=False)
    monkeypatch.setattr(gl, "make_adapter", FakeAdapter)
    FakeAdapter.missing = set()
    FakeAdapter.calls = []
    path = make_engagement(
        tmp_path,
        id="golden-test",
        source={
            "kind": "accdt",
            "path": "{{p.private}}/sources/app.accdt",
            "saved_with_data": True,
        },
    )
    e = load(path)
    e["_path"] = str(path)
    private, target = Path(e["paths"]["private"]), Path(e["paths"]["target"])
    ref = private / "out" / "reference"
    ref.mkdir(parents=True)
    shutil.copy(FIX / "catalog.json", ref / "catalog.json")
    shutil.copytree(FIX / "sql", ref / "sql")
    (target / "analysis").mkdir(parents=True)
    shutil.copy(FIX / "rules.json", target / "analysis" / "rules.json")
    shutil.copytree(
        FIX / "dataset", target / "goldens" / "synthetic" / "seed-1" / "input"
    )
    edir = private / "datasets" / "engagement"
    shutil.copytree(FIX / "dataset", edir)
    meta = json.loads((edir / "dataset.json").read_text(encoding="utf-8"))
    meta.update(name="engagement", kind="engagement", seed=None)
    (edir / "dataset.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    (private / "sources").mkdir(parents=True)
    (private / "sources" / "app.accdt").write_bytes(ENGAGEMENT_BYTES)
    return e


def run_cli(capfd, *argv) -> tuple[int, dict]:
    capfd.readouterr()
    code = cli.main([str(a) for a in argv])
    out, _err = capfd.readouterr()
    lines = out.strip().splitlines()
    assert len(lines) == 1, out
    return code, json.loads(lines[0])


def record(capfd, eng, *extra):
    return run_cli(capfd, "golden", "record", eng["_path"], *extra)


def paths(eng):
    return Path(eng["paths"]["private"]), Path(eng["paths"]["target"])


def manifest(eng) -> dict:
    _p, target = paths(eng)
    return json.loads(
        (target / "goldens" / "manifest.json").read_text(encoding="utf-8")
    )


def snapshot(*roots: Path) -> dict[str, bytes]:
    out = {}
    for root in roots:
        if root.is_dir():
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    out[str(p)] = p.read_bytes()
    return out


# ------------------------------------------------------------------ conventions


@pytest.mark.parametrize(
    "name",
    ["qryOrderTotals", "Order Details", "InvoiceTable[Net Amount]", "a.b-c_d", "Café"],
)
def test_file_safe_id_matches_reference_sql(name):
    assert canon.file_safe(name) == cat.file_safe(name)
    assert canon.file_safe("Order Details") == "Order_Details-fe3ac0d4"


def test_order_by_maps_terms_to_output_columns():
    cols = ["OrderID", "FullName", "Total"]
    sql = "SELECT Orders.OrderID, [First] & ' ' & [Last] AS FullName, Orders.Total FROM Orders ORDER BY [First] & ' ' & [Last], Orders.OrderID DESC;"
    assert gl.order_by(sql, cols) == [
        {"column": "FullName", "desc": False},
        {"column": "OrderID", "desc": True},
    ]
    assert gl.order_by("SELECT a, b FROM t ORDER BY 2 DESC, 1", ["a", "b"]) == [
        {"column": "b", "desc": True},
        {"column": "a", "desc": False},
    ]
    # a term that is not an output column ends the list; none at all gives null
    assert gl.order_by("SELECT t.a FROM t ORDER BY t.a, t.hidden, t.b", ["a", "b"]) == [
        {"column": "a", "desc": False}
    ]
    assert gl.order_by("SELECT t.a FROM t ORDER BY t.hidden", ["a"]) is None
    assert gl.order_by("SELECT t.a FROM t", ["a"]) is None
    # an ORDER BY inside a subquery is not the unit's
    assert (
        gl.order_by("SELECT x.a FROM (SELECT TOP 5 a FROM t ORDER BY a) AS x", ["a"])
        is None
    )
    # union: the names of the first SELECT
    sql = "SELECT 1 AS k, t.Name FROM t UNION ALL SELECT 2 AS k, u.Name FROM u ORDER BY k, Name;"
    assert gl.order_by(sql, ["k", "Name"]) == [
        {"column": "k", "desc": False},
        {"column": "Name", "desc": False},
    ]
    # an aliased column ordered by its source expression
    sql = "SELECT c.CompanyName AS Customer FROM c ORDER BY c.CompanyName"
    assert gl.order_by(sql, ["Customer"]) == [{"column": "Customer", "desc": False}]


def test_param_sites_find_the_compared_column():
    p = {
        "name": "Forms!frmCust!CustID",
        "type": "longinteger",
        "source": "form_control",
    }
    sql = (FIX / "sql" / "qByCustomer.sql").read_text(encoding="utf-8")
    assert gl.param_sites([sql], p) == [("Orders", "CustID")]
    sql2 = "SELECT x FROM t WHERE [Parent]![VendorID] = PV.VendorID"
    p2 = {"name": "Parent!VendorID", "type": "longinteger", "source": "form_control"}
    assert gl.param_sites([sql2], p2) == [("PV", "VendorID")]
    p3 = {"name": "Start", "type": "datetime", "source": "prompt"}
    assert gl.param_sites(["SELECT a FROM t WHERE t.OrderDate >= [Start]"], p3) == [
        ("t", "OrderDate")
    ]
    assert gl.param_sites(["SELECT a FROM t WHERE x = '[Start]'"], p3) == []


# ------------------------------------------------------------------ record


def test_record_seeds_writes_manifest_params_and_expected(eng, capfd):
    code, summary = record(capfd, eng, "--dataset", "seeds")
    assert code == 0, summary
    private, target = paths(eng)
    m = manifest(eng)
    assert m["format_version"] == 1 and m["engagement"] == "golden-test"
    # dropped units get no entry; a unit whose object rules.json lacks gets area null
    assert sorted(m["units"]) == [
        "qAction",
        "qByCustomer",
        "qEmpty",
        "qOrders",
        "qOrphan",
    ]
    assert m["units"]["qOrphan"]["area"] is None
    u = m["units"]["qOrders"]
    assert (
        u["area"] == "sales"
        and u["kind"] == "saved_query"
        and u["canonical_version"] == 1
    )
    assert u["columns"] == [
        {"name": "OrderID", "type": "autonumber"},
        {"name": "Amount", "type": "currency"},
        {"name": "Shipped", "type": "yesno"},
    ]
    assert u["order_by"] == [{"column": "OrderID", "desc": True}]
    s = u["datasets"]["seed-1"]
    assert s["status"] == "ok" and s["reason"] is None and s["rows"] == 4
    assert s["expected"] == "synthetic/seed-1/expected/qOrders.csv"
    data = (target / "goldens" / s["expected"]).read_bytes()
    assert s["digest"] == {"alg": "sha256", "value": hashlib.sha256(data).hexdigest()}
    assert data == (
        b"OrderID,Amount,Shipped\r\n10,99.1250,1\r\n3,7.2500,0\r\n2,\\N,0\r\n1,10.5000,1\r\n"
    )
    # per-dataset status and reason
    ds = {k: v["datasets"]["seed-1"] for k, v in m["units"].items()}
    assert ds["qEmpty"] == {
        "status": "empty",
        "reason": "empty",
        "rows": 0,
        "digest": {
            "alg": "sha256",
            "value": hashlib.sha256(b"OrderID\r\n").hexdigest(),
        },
        "expected": "synthetic/seed-1/expected/qEmpty.csv",
    }
    assert ds["qAction"] == {
        "status": "unsupported",
        "reason": "action-query",
        "rows": None,
        "digest": None,
    }
    assert m["units"]["qEmpty"]["needs_access_confirmation"] is True
    # parameter: the smallest key of the compared column (Orders.CustID: 2, 3) with a non-empty
    # result (customer 2 has no large order)
    assert ds["qByCustomer"]["status"] == "ok" and ds["qByCustomer"]["rows"] == 1
    params = json.loads(
        (target / "goldens" / "params.json").read_text(encoding="utf-8")
    )
    assert params == {
        "format_version": 1,
        "units": {
            "qByCustomer": {
                "params": [
                    {
                        "name": "Forms!frmCust!CustID",
                        "type": "longinteger",
                        "source": "form_control",
                    }
                ],
                "values": {"seed-1": {"Forms!frmCust!CustID": "3"}},
                "keys": {},
            }
        },
    }
    tried = [p for uid, p in FakeAdapter.calls if uid == "qByCustomer"]
    assert tried == [{"Forms!frmCust!CustID": "2"}, {"Forms!frmCust!CustID": "3"}]
    # nothing in the data zone; no engagement entry
    assert not (private / "goldens").exists()
    assert "engagement" not in u["datasets"]
    assert sorted(
        p.name for p in (target / "goldens/synthetic/seed-1/expected").iterdir()
    ) == ["qByCustomer.csv", "qEmpty.csv", "qOrders.csv", "qOrphan.csv"]


def test_needs_params_reason_other_when_no_value_works(eng, capfd, monkeypatch):
    orig = FakeAdapter.run_saved_query

    def never(self, unit_id, params=None):
        if unit_id == "qByCustomer":
            FakeAdapter.calls.append((unit_id, dict(params or {})))
            return []
        return orig(self, unit_id, params)

    monkeypatch.setattr(FakeAdapter, "run_saved_query", never)
    code, summary = record(capfd, eng, "--dataset", "seeds")
    assert code == 0, summary
    e = manifest(eng)["units"]["qByCustomer"]["datasets"]["seed-1"]
    assert e == {
        "status": "needs-params",
        "reason": "other",
        "rows": None,
        "digest": None,
    }
    assert {
        "id": "qByCustomer",
        "status": "needs-params",
        "reason": "other",
        "dataset": "seed-1",
    } in summary["units"]
    assert (
        len([1 for uid, _p in FakeAdapter.calls if uid == "qByCustomer"]) == 2
    )  # 2 keys


def test_engagement_records_only_through_mp_data(eng, capfd):
    for ds in ("engagement", "all"):
        code, summary = record(capfd, eng, "--dataset", ds)
        assert code == 2
        assert "mp-data golden record" in "\n".join(summary["messages"])
    assert not (paths(eng)[1] / "goldens" / "manifest.json").exists()


def test_record_all_hmac_for_engagement_sha256_for_seeds(eng, capfd, monkeypatch):
    monkeypatch.setenv("MP_SANDBOX", "mp-data")
    code, summary = record(capfd, eng, "--dataset", "all")
    assert code == 0, summary
    private, target = paths(eng)
    u = manifest(eng)["units"]["qOrders"]["datasets"]
    data = (private / "goldens" / "engagement" / "qOrders.csv").read_bytes()
    key = hmac.new(b"modpipe-golden-v1", ENGAGEMENT_BYTES, hashlib.sha256).digest()
    assert u["engagement"] == {
        "status": "ok",
        "reason": None,
        "rows": 4,
        "digest": {
            "alg": "hmac-sha256",
            "value": hmac.new(key, data, hashlib.sha256).hexdigest(),
        },
    }
    assert (
        u["seed-1"]["digest"]["value"] == hashlib.sha256(data).hexdigest()
    )  # same rows
    assert u["engagement"]["digest"]["value"] != u["seed-1"]["digest"]["value"]
    # engagement parameter values only in the private params
    public = (target / "goldens" / "params.json").read_text(encoding="utf-8")
    assert "engagement" not in public
    priv = json.loads((private / "goldens" / "params.json").read_text(encoding="utf-8"))
    assert priv["units"]["qByCustomer"]["values"] == {
        "engagement": {"Forms!frmCust!CustID": "3"}
    }
    # the manifest holds no values: no cell of the dataset appears in it
    text = (target / "goldens" / "manifest.json").read_text(encoding="utf-8")
    for value in ("Alpha Widgets", "Beta Gears", "99.125", "7.25"):
        assert value not in text
    # nothing written outside goldens/ and the private goldens
    assert not (target / "goldens" / "synthetic" / "engagement").exists()


def test_check_catches_a_one_cell_change(eng, capfd, monkeypatch):
    monkeypatch.setenv("MP_SANDBOX", "mp-data")
    assert record(capfd, eng, "--dataset", "all")[0] == 0
    private, target = paths(eng)
    code, summary = record(capfd, eng, "--dataset", "all", "--check")
    assert code == 0, summary
    assert summary["counts"]["differences"] == 0
    # one cell in a seed expected file
    f = target / "goldens" / "synthetic" / "seed-1" / "expected" / "qOrders.csv"
    f.write_bytes(f.read_bytes().replace(b"7.2500", b"7.2501"))
    before = snapshot(target / "goldens", private / "goldens")
    code, summary = record(capfd, eng, "--dataset", "all", "--check")
    assert code == 1
    assert "seed-1: qOrders.csv differs" in summary["messages"]
    assert snapshot(target / "goldens", private / "goldens") == before  # writes nothing
    # one cell in the seed data: manifest digest and expected file both differ
    record(capfd, eng, "--dataset", "seeds")
    csv = (
        target / "goldens" / "synthetic" / "seed-1" / "input" / "tables" / "Orders.csv"
    )
    csv.write_bytes(csv.read_bytes().replace(b"10.5", b"10.75"))
    code, summary = record(capfd, eng, "--dataset", "seeds", "--check")
    assert code == 1
    assert "manifest.json differs" in summary["messages"]
    # one cell in a private golden
    record(capfd, eng, "--dataset", "all")
    g = private / "goldens" / "engagement" / "qOrders.csv"
    g.write_bytes(g.read_bytes().replace(b"99.1250", b"99.1251"))
    code, summary = record(capfd, eng, "--dataset", "all", "--check")
    assert code == 1
    assert "engagement: qOrders.csv differs" in summary["messages"]


def test_private_only_rebuilds_and_refuses_a_digest_mismatch(eng, capfd, monkeypatch):
    monkeypatch.setenv("MP_SANDBOX", "mp-data")
    assert record(capfd, eng, "--dataset", "all")[0] == 0
    private, target = paths(eng)
    committed = snapshot(target / "goldens")
    shutil.rmtree(private / "goldens")  # lost private state
    code, summary = record(
        capfd, eng, "--dataset", "engagement", "--private-only", "--check"
    )
    assert code == 0, summary
    assert (private / "goldens" / "engagement" / "qOrders.csv").is_file()
    assert (private / "goldens" / "params.json").is_file()
    assert snapshot(target / "goldens") == committed
    # the engagement data changes one cell: same rows, different digest
    csv = private / "datasets" / "engagement" / "tables" / "Orders.csv"
    csv.write_bytes(csv.read_bytes().replace(b"99.125", b"99.25"))
    code, summary = record(capfd, eng, "--dataset", "engagement", "--private-only")
    assert code == 1
    assert any("qOrders (digest)" in m for m in summary["messages"])
    assert snapshot(target / "goldens") == committed  # never writes the target repo
    assert (
        b"99.2500" in (private / "goldens" / "engagement" / "qOrders.csv").read_bytes()
    )
    code, _s = record(capfd, eng, "--private-only", "--dataset", "seeds")
    assert code == 2


def test_missing_reference_database_names_the_build_command(eng, capfd, monkeypatch):
    FakeAdapter.missing = {"seed-1"}
    code, summary = record(capfd, eng, "--dataset", "seeds")
    assert code == 2
    assert "uv run toolkit reference build " + eng[
        "_path"
    ] + " --dataset seeds" in "\n".join(summary["messages"])
    monkeypatch.setenv("MP_SANDBOX", "mp-data")
    FakeAdapter.missing = {"engagement"}
    code, summary = record(capfd, eng, "--dataset", "all")
    assert code == 2
    assert "mp-data reference build " + eng[
        "_path"
    ] + " --dataset engagement" in "\n".join(summary["messages"])
    assert not (paths(eng)[1] / "goldens" / "manifest.json").exists()


def test_missing_catalog_names_reference_sql(eng, capfd):
    private, _t = paths(eng)
    (private / "out" / "reference" / "catalog.json").unlink()
    code, summary = record(capfd, eng, "--dataset", "seeds")
    assert code == 2
    assert f"uv run toolkit reference sql {eng['_path']} --all" in "\n".join(
        summary["messages"]
    )


def test_stale_expected_files_and_dropped_units_are_removed(eng, capfd):
    assert record(capfd, eng, "--dataset", "seeds")[0] == 0
    _p, target = paths(eng)
    rules_path = target / "analysis" / "rules.json"
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    for o in rules["objects"]:
        if o["name"] == "qEmpty":
            o.update(decision="drop", reason="unused", target_component=None)
    rules_path.write_text(json.dumps(rules), encoding="utf-8")
    assert record(capfd, eng, "--dataset", "seeds")[0] == 0
    assert "qEmpty" not in manifest(eng)["units"]
    assert not (target / "goldens/synthetic/seed-1/expected/qEmpty.csv").exists()


def test_a_unit_failure_exits_1_and_writes_nothing(eng, capfd, monkeypatch):
    def boom(self, unit_id, params=None):
        raise RuntimeError("java.sql.SQLException")

    monkeypatch.setattr(FakeAdapter, "run_saved_query", boom)
    code, summary = record(capfd, eng, "--dataset", "seeds")
    assert code == 1
    assert {"id": "qOrders", "status": "failed", "reason": None} in summary["units"]
    assert not (paths(eng)[1] / "goldens" / "manifest.json").exists()


# ------------------------------------------------------------------ status


def status(capfd, goldens, rules, *extra):
    return run_cli(
        capfd,
        "golden",
        "status",
        goldens,
        "--rules",
        rules,
        "--dataset",
        "engagement",
        *extra,
    )


@pytest.fixture
def status_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MP_QUIET", "1")
    d = tmp_path / "goldens"
    d.mkdir()
    shutil.copy(FIX / "status" / "manifest.json", d / "manifest.json")
    shutil.copy(FIX / "status" / "rules.json", tmp_path / "rules.json")
    return d, tmp_path / "rules.json"


def test_status_rates_on_a_fixture_manifest(status_dir, capfd):
    goldens, rules = status_dir
    code, s = status(capfd, goldens, rules, "--min-ok", "0.8", "--max-empty", "0.1")
    assert code == 0, s
    assert s["rates"] == {"characterization": 0.8, "empty_share": 0.1}
    assert s["counts"]["units"] == 10
    assert (
        s["counts"]["ok"] == 8
        and s["counts"]["empty"] == 1
        and s["counts"]["needs-params"] == 1
    )
    assert s["counts"]["alpha.ok"] == 6 and s["counts"]["beta.ok"] == 2
    assert {u["id"] for u in s["units"]} == {"qUnit09", "qUnit10"}
    assert {
        "id": "qUnit10",
        "status": "needs-params",
        "reason": "form-reference-unresolved",
        "dataset": "engagement",
    } in s["units"]
    code, s = status(capfd, goldens, rules, "--min-ok", "0.9", "--max-empty", "0.25")
    assert code == 1 and any("below 0.9" in m for m in s["messages"])
    code, s = status(capfd, goldens, rules, "--min-ok", "0.5", "--max-empty", "0.05")
    assert code == 1 and any("above 0.05" in m for m in s["messages"])
    code, s = run_cli(
        capfd,
        "golden",
        "status",
        goldens,
        "--rules",
        rules,
        "--dataset",
        "seed-1",
        "--min-ok",
        "1.0",
    )
    assert code == 0 and s["rates"]["characterization"] == 1.0


def test_status_rejects_unknown_area_and_kept_query_without_unit(status_dir, capfd):
    goldens, rules = status_dir
    m = json.loads((goldens / "manifest.json").read_text(encoding="utf-8"))
    m["units"]["qUnit01"]["area"] = None
    (goldens / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    code, s = status(capfd, goldens, rules, "--min-ok", "0.5")
    assert code == 1 and any("qUnit01: area null" in x for x in s["messages"])
    m["units"]["qUnit01"]["area"] = "alpha"
    del m["units"]["qUnit02"]
    (goldens / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    code, s = status(capfd, goldens, rules, "--min-ok", "0.5")
    assert code == 1 and any("qUnit02 has no unit" in x for x in s["messages"])


def test_status_on_recorded_fixture(eng, capfd, monkeypatch):
    monkeypatch.setenv("MP_SANDBOX", "mp-data")
    assert record(capfd, eng, "--dataset", "all")[0] == 0
    _p, target = paths(eng)
    code, s = status(
        capfd,
        target / "goldens",
        target / "analysis" / "rules.json",
        "--min-ok",
        "0.5",
        "--max-empty",
        "0.5",
    )
    assert code == 1  # qOrphan has area null
    assert s["rates"] == {"characterization": 0.6, "empty_share": 0.2}
