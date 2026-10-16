"""stage1 (S3.move): schema and data to PostgreSQL and SQL Server, without a database server.

DDL is compiled for postgresql and mssql and compared with expected text; loads and checks run on
SQLite in memory, through `toolkit.stage1.make_engine`, which these tests replace: a server's
maintenance database (postgres, master) is a fake engine that records statements and answers the
owner query, and every other database is an in-memory SQLite engine. Fixtures:
toolkit/tests/fixtures/stage1/ (hand-written, synthetic): a 3-table schema.json with autonumber,
yesno, currency, datetime, memo, single, a multi-value column and an enforced relationship, its
dataset directory, and the expected DDL. No client file, demo source or record is read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
from pathlib import Path

import jsonschema
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import mssql, postgresql
from sqlalchemy.pool import StaticPool

from toolkit import cli, stage1
from toolkit import dataset as tds

FIX = Path(__file__).parent / "fixtures" / "stage1"
SUMMARY_SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "summary.schema.json"
PG_AGENT = "postgresql://agent:agentpw@127.0.0.1:55432"
MSSQL_AGENT = "mssql://agent:agentpw@127.0.0.1:14330"


# ------------------------------------------------------------------ fakes


class FakeServer:
    """A server's maintenance database: records statements, answers the owner query."""

    def __init__(self, dialect: str):
        self.dialect = dialect
        self.databases: dict[str, str] = {}  # name -> owner
        self.statements: list[str] = []
        self.options: list[dict] = []

    def connect(self):
        return _FakeConn(self)

    def dispose(self):
        pass


class _FakeResult:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class _FakeConn:
    def __init__(self, server: FakeServer):
        self.server = server

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execution_options(self, **kw):
        self.server.options.append(kw)
        return self

    def execute(self, stmt, params=None):
        self.server.statements.append(str(stmt))
        owner = self.server.databases.get((params or {}).get("name"))
        return _FakeResult(None if owner is None else (owner,))

    def exec_driver_sql(self, sql):
        self.server.statements.append(sql)
        if sql.startswith("CREATE DATABASE"):
            name = sql.split(" ", 2)[2].strip('"[]')
            self.server.databases[name] = (
                "postgres" if self.server.dialect == "postgresql" else "sa"
            )


class Servers:
    """Replaces stage1.make_engine: fake maintenance databases, in-memory SQLite targets."""

    def __init__(self):
        self.servers = {d: FakeServer(d) for d in ("postgresql", "mssql")}
        self.targets: dict[tuple, sa.Engine] = {}
        self.urls: list[sa.URL] = []

    @staticmethod
    def dialect_of(url: sa.URL) -> str:
        return "postgresql" if url.drivername.startswith("postgresql") else "mssql"

    def make_engine(self, url: sa.URL):
        self.urls.append(url)
        d = self.dialect_of(url)
        if url.database == stage1.MAINTENANCE_DB[d]:
            return self.servers[d]
        key = (d, url.database)
        if key not in self.targets:
            engine = sa.create_engine(
                "sqlite://",
                poolclass=StaticPool,
                connect_args={"check_same_thread": False},
            )
            engine.dispose = lambda *a, **k: (
                None
            )  # keep the in-memory database between runs
            self.targets[key] = engine
        return self.targets[key]

    def target(self, dialect: str, name: str) -> sa.Engine:
        return self.targets[(dialect, name)]

    def statements(self) -> list[str]:
        return [s for srv in self.servers.values() for s in srv.statements]


@pytest.fixture
def servers(monkeypatch):
    s = Servers()
    monkeypatch.setattr(stage1, "make_engine", s.make_engine)
    return s


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MP_QUIET", "1")
    monkeypatch.delenv("MP_SANDBOX", raising=False)
    for name in ("MP_DATA_PG_URL", "MP_DATA_MSSQL_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PG_URL", PG_AGENT)
    monkeypatch.setenv("MSSQL_URL", MSSQL_AGENT)


def make_eng(
    tmp_path, make_engagement, schema: Path | None = FIX / "schema.json", **fields
):
    eng = make_engagement(tmp_path, **fields)
    private = Path(json.loads((eng.parent / "paths.local.json").read_text())["private"])
    if schema is not None:
        model = private / "out" / "model"
        model.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(schema, model / "schema.json")
    return eng


def private_of(eng: Path) -> Path:
    return Path(json.loads((eng.parent / "paths.local.json").read_text())["private"])


def run_cli(capfd, *argv) -> tuple[int, dict]:
    capfd.readouterr()
    code = cli.main([str(a) for a in argv])
    out, _err = capfd.readouterr()
    lines = out.strip().splitlines()
    assert len(lines) == 1, out
    return code, json.loads(lines[0])


def design_of(schema_path: Path = FIX / "schema.json") -> stage1.Design:
    raw = schema_path.read_bytes()
    schema = json.loads(raw)
    return stage1.Design(
        schema_path, hashlib.sha256(raw).hexdigest(), schema, schema["tables"]
    )


LOCAL = ("--target-db", "local:mpdata_stage1_test", "--dialects", "from-engagement")
DATASET = ("--dataset", FIX / "dataset")


def fixture_counts() -> dict:
    meta = json.loads((FIX / "dataset" / "dataset.json").read_text(encoding="utf-8"))
    return {t["name"]: t["rows"] for t in meta["tables"]}


# ------------------------------------------------------------------ DDL


@pytest.mark.parametrize(
    "name,dialect", [("postgresql", postgresql.dialect()), ("mssql", mssql.dialect())]
)
def test_ddl_matches_expected_text(name, dialect):
    plan = stage1.build_plan(design_of(), name)
    expected = (FIX / f"expected-{name}.sql").read_text(encoding="utf-8")
    assert stage1.ddl_text(plan, dialect) == expected


def test_plan_lists_what_is_not_created():
    plan = stage1.build_plan(design_of(), "mssql")
    assert plan.omitted == [("Customers", "Tags", "complex", "complex")]
    assert (
        "relationship",
        "CustomersOrdersLookup",
        "integrity not enforced in Access",
    ) in (plan.not_created)
    assert ("index", "Order Lines.OrdersOrderLines", "not unique") in plan.not_created
    assert ("validation rule", "Order Lines.Quantity", "field validation rule") in (
        plan.not_created
    )
    assert ("default", "Orders.Reference", "expression default") in plan.not_created
    assert plan.added_columns == [("Order Lines", "mp_rowversion")]
    assert stage1.build_plan(design_of(), "postgresql").added_columns == []


def test_default_clauses():
    def dflt(ctype, value):
        clause, why = stage1.default_clause(
            {"name": "c", "type": ctype, "properties": {"DefaultValue": value}}
        )
        text = None
        if clause is not None:
            text = (
                clause
                if isinstance(clause, str)
                else str(clause.compile(dialect=postgresql.dialect()))
            )
        return text, why

    assert dflt("longinteger", "0") == ("0", None)
    assert dflt("currency", "-2.5") == ("-2.5", None)
    assert dflt("integer", "1.5") == (None, "expression default")
    assert dflt("yesno", "Yes") == ("true", None)
    assert dflt("yesno", "No") == ("false", None)
    assert dflt("text", '"a ""b"""') == ('a "b"', None)
    assert dflt("text", "Format(Date())") == (None, "expression default")
    assert dflt("text", '"<EMAIL-1>"') == (None, "default holds a scrubbed token")
    assert dflt("datetime", "Now()") == ("CURRENT_TIMESTAMP", None)
    assert dflt("datetime", "Date()") == (None, "expression default")
    assert stage1.default_clause({"name": "c", "type": "text"}) == (None, None)


def _cascade_schema() -> dict:
    def table(name, cols):
        return {
            "name": name,
            "role": "data",
            "columns": [
                {"name": c, "type": "longinteger", "required": c == "ID"} for c in cols
            ],
            "primary_key": ["ID"],
            "indexes": [],
        }

    def rel(name, child, col, parent):
        return {
            "name": name,
            "from_table": child,
            "from_columns": [col],
            "to_table": parent,
            "to_columns": ["ID"],
            "enforce": True,
            "cascade_update": False,
            "cascade_delete": True,
        }

    return {
        "format_version": 1,
        "tables": [
            table("A", ["ID", "Up"]),
            table("B", ["ID", "A"]),
            table("C", ["ID", "A"]),
            table("D", ["ID", "B", "C"]),
        ],
        "relationships": [
            rel("r1", "B", "A", "A"),
            rel("r2", "C", "A", "A"),
            rel("r3", "D", "B", "B"),
            rel("r4", "D", "C", "C"),
            rel("r5", "A", "Up", "A"),
        ],
    }


def test_sql_server_second_cascade_path_becomes_no_action():
    schema = _cascade_schema()
    design = stage1.Design(Path("x"), "0" * 64, schema, schema["tables"])
    ms = stage1.build_plan(design, "mssql")
    assert ms.cascade_no_action == ["r4", "r5"]
    actions = {fk.name: fk.ondelete for fk in ms.fks}
    assert actions == {
        "r1": "CASCADE",
        "r2": "CASCADE",
        "r3": "CASCADE",
        "r4": None,
        "r5": None,
    }
    pg = stage1.build_plan(design, "postgresql")
    assert pg.cascade_no_action == []
    assert {fk.name: fk.ondelete for fk in pg.fks} == {
        f"r{i}": "CASCADE" for i in range(1, 6)
    }


# ------------------------------------------------------------------ targets and URLs


def test_parse_target_forms():
    assert stage1.parse_target("local:mpdata_x") == stage1.TargetSpec(
        "local", "mpdata_x"
    )
    assert stage1.parse_target("secret:stage1_target_url_ref") == stage1.TargetSpec(
        "secret", "stage1_target_url_ref"
    )
    spec = stage1.parse_target("postgresql://u:p@127.0.0.1:5432/dev_db")
    assert spec.kind == "url"
    for bad in (
        "local:Bad-Name",
        "secret:",
        "mysql://u@h/db",
        "postgresql://u:p@h:1",
        "dev_db",
    ):
        with pytest.raises(stage1.Stage1Error) as exc:
            stage1.parse_target(bad)
        assert exc.value.exit_code == 2
        assert "u:p" not in str(exc.value)


def test_url_mapping():
    pg = stage1.to_sa_url("postgresql://u:p@db.example.invalid:5432/one")
    assert pg.drivername == "postgresql+psycopg" and pg.database == "one"
    assert dict(pg.query) == {}
    local = stage1.to_sa_url("mssql://u:p@127.0.0.1:14330", "two")
    assert local.drivername == "mssql+mssqlpython" and local.database == "two"
    assert dict(local.query) == {"Encrypt": "yes", "TrustServerCertificate": "yes"}
    remote = stage1.to_sa_url("mssql://u:p@sql.example.invalid:1433/three")
    assert dict(remote.query) == {"Encrypt": "yes"}
    assert stage1.to_sa_url("postgres://u:p@h:1/x").drivername == "postgresql+psycopg"


def test_local_server_urls_prefer_mp_data(make_engagement, tmp_path):
    eng = stage1.endpoints(
        stage1.TargetSpec("local", "mpdata_x"),
        "from-engagement",
        {"database": {"primary": "postgresql", "verify_also": ["mssql"]}},
        {
            "MP_DATA_PG_URL": "postgresql://super:s@127.0.0.1:55432",
            "PG_URL": PG_AGENT,
            "MSSQL_URL": MSSQL_AGENT,
        },
    )
    assert [
        (e.dialect, e.url.username, e.url.database, e.server.database) for e in eng
    ] == [
        ("postgresql", "super", "mpdata_x", "postgres"),
        ("mssql", "agent", "mpdata_x", "master"),
    ]


def test_missing_server_url_exits_2_naming_the_variables(
    make_engagement, tmp_path, capfd, monkeypatch, servers
):
    monkeypatch.delenv("MSSQL_URL")
    eng = make_eng(tmp_path, make_engagement)
    code, summary = run_cli(capfd, "stage1", eng, *LOCAL, *DATASET)
    assert code == 2
    assert summary["messages"] == [
        "mssql: neither MP_DATA_MSSQL_URL nor MSSQL_URL is set"
    ]
    assert servers.urls == []


@pytest.mark.parametrize(
    "target,dialects,message",
    [
        ("local:mpdata_x", None, "needs --dialects"),
        (
            "postgresql://u:p@127.0.0.1:1/dev_db",
            "from-engagement",
            "--dialects is refused",
        ),
        ("secret:stage1_target_url_ref", "from-engagement", "--dialects is refused"),
    ],
)
def test_dialects_required_for_local_and_refused_otherwise(
    make_engagement, tmp_path, capfd, servers, target, dialects, message
):
    eng = make_eng(
        tmp_path, make_engagement, secrets={"stage1_target_url_ref": "env:STAGE1_URL"}
    )
    argv = ["stage1", eng, "--target-db", target, "--schema-only"]
    if dialects:
        argv += ["--dialects", dialects]
    code, summary = run_cli(capfd, *argv)
    assert code == 2
    assert message in summary["messages"][0]
    assert servers.urls == []


@pytest.mark.parametrize(
    "target,dataset",
    [
        ("local:plain_db", "engagement"),
        ("local:plain_db", "fresh-copy"),
        ("local:plain_db", "PRIVATE"),
        ("postgresql://u:p@127.0.0.1:1/dev_db", "engagement"),
        ("postgresql://u:p@127.0.0.1:1/dev_db", "fresh-copy"),
        ("postgresql://u:p@127.0.0.1:1/dev_db", "PRIVATE"),
    ],
)
def test_engagement_data_only_into_mpdata_or_secret(
    make_engagement, tmp_path, capfd, servers, target, dataset
):
    eng = make_eng(tmp_path, make_engagement)
    if dataset == "PRIVATE":
        dataset = private_of(eng) / "synthetic" / "seed-1"
        shutil.copytree(FIX / "dataset", dataset)
    argv = ["stage1", eng, "--target-db", target, "--dataset", dataset]
    if target.startswith("local:"):
        argv += ["--dialects", "from-engagement"]
    code, summary = run_cli(capfd, *argv)
    assert code == 2
    assert summary["messages"] == [stage1.ENGAGEMENT_ONLY]
    assert servers.urls == []
    # the same data with --schema-only reads no rows and is allowed
    code, _summary = run_cli(capfd, *argv, "--schema-only")
    assert code == 0


def test_replace_on_a_secret_target_exits_2(make_engagement, tmp_path, capfd, servers):
    eng = make_eng(
        tmp_path, make_engagement, secrets={"stage1_target_url_ref": "env:STAGE1_URL"}
    )
    code, summary = run_cli(
        capfd, "stage1", eng, "--target-db", "secret:stage1_target_url_ref", "--replace"
    )
    assert code == 2
    assert "--replace is refused" in summary["messages"][0]
    assert servers.urls == []


def test_data_model_is_not_built_yet(make_engagement, tmp_path, capfd, servers):
    eng = make_eng(tmp_path, make_engagement)
    code, summary = run_cli(
        capfd, "stage1", eng, *LOCAL, "--schema-only", "--schema-from", "data-model"
    )
    assert code == 2
    assert summary["messages"] == [stage1.DATA_MODEL_MSG]


# ------------------------------------------------------------------ databases on the server


def test_create_if_missing_statements_for_both_dialects(
    make_engagement, tmp_path, capfd, servers
):
    eng = make_eng(tmp_path, make_engagement)
    code, summary = run_cli(capfd, "stage1", eng, *LOCAL, *DATASET)
    assert code == 0, summary
    pg, ms = servers.servers["postgresql"], servers.servers["mssql"]
    assert 'CREATE DATABASE "mpdata_stage1_test"' in pg.statements
    assert "CREATE DATABASE [mpdata_stage1_test]" in ms.statements
    assert "pg_database" in pg.statements[0] and "sys.databases" in ms.statements[0]
    assert {"isolation_level": "AUTOCOMMIT"} in pg.options
    assert {"isolation_level": "AUTOCOMMIT"} in ms.options


@pytest.mark.parametrize("owner", ["postgres", "sa", "mp_runner"])
def test_existing_mpdata_database_with_an_allowed_owner_is_used(
    make_engagement, tmp_path, capfd, servers, owner
):
    for srv in servers.servers.values():
        srv.databases["mpdata_stage1_test"] = owner
    eng = make_eng(tmp_path, make_engagement)
    code, summary = run_cli(capfd, "stage1", eng, *LOCAL, *DATASET)
    assert code == 0, summary
    assert not any(s.startswith("CREATE DATABASE") for s in servers.statements())


def test_mpdata_database_owned_by_the_agent_exits_2_and_changes_nothing(
    make_engagement, tmp_path, capfd, servers
):
    servers.servers["mssql"].databases["mpdata_stage1_test"] = "mp_agent"
    eng = make_eng(tmp_path, make_engagement)
    code, summary = run_cli(capfd, "stage1", eng, *LOCAL, *DATASET, "--replace")
    assert code == 2
    assert summary["messages"] == [
        (
            "mpdata_ database mpdata_stage1_test is owned by mp_agent, not postgres, sa or "
            "mp_runner: drop it as the superuser"
        )
    ]
    assert not any(s.startswith("CREATE DATABASE") for s in servers.statements())
    assert servers.targets == {}


# ------------------------------------------------------------------ load and checks on SQLite


def test_load_verify_and_require_rows(make_engagement, tmp_path, capfd, servers):
    eng = make_eng(tmp_path, make_engagement)
    report = tmp_path / "report.md"
    code, summary = run_cli(
        capfd,
        "stage1",
        eng,
        *LOCAL,
        *DATASET,
        "--replace",
        "--verify",
        "--require-rows",
        "--report",
        report,
    )
    assert code == 0, summary
    counts = fixture_counts()
    assert summary["counts"]["rows"] == sum(counts.values())
    assert len(summary["tables"]) == 2 * len(counts)
    for e in summary["tables"]:
        assert e["dataset_rows"] == e["target_rows"] == counts[e["table"]]
        assert e["digest_match"] is True and e["fk_orphans"] == 0
    with servers.target("postgresql", "mpdata_stage1_test").connect() as conn:
        assert conn.execute(sa.text('SELECT count(*) FROM "Order Lines"')).scalar() == 6
        cols = [c["name"] for c in sa.inspect(conn).get_columns("Customers")]
    assert cols == ["CustomerID", "Company Name", "Active", "Notes"]  # Tags omitted
    text = report.read_text(encoding="utf-8")
    assert "| `Customers.Tags` | complex |" in text
    assert "| `CustomersOrdersLookup` | integrity not enforced in Access |" in text
    assert "Every check passed." in text


def test_summary_validates_against_the_summary_schema(
    make_engagement, tmp_path, capfd, servers
):
    eng = make_eng(tmp_path, make_engagement)
    code, summary = run_cli(
        capfd,
        "stage1",
        eng,
        *LOCAL,
        *DATASET,
        "--replace",
        "--verify",
        "--require-rows",
    )
    assert code == 0
    schema = json.loads(SUMMARY_SCHEMA.read_text(encoding="utf-8"))
    jsonschema.validate(summary, schema)
    assert summary["tables"] and set(summary["tables"][0]) == {
        "table",
        "dialect",
        "dataset_rows",
        "target_rows",
        "digest_match",
        "fk_orphans",
    }


def test_one_changed_cell_gives_a_digest_mismatch(
    make_engagement, tmp_path, capfd, servers
):
    eng = make_eng(tmp_path, make_engagement)
    assert run_cli(capfd, "stage1", eng, *LOCAL, *DATASET, "--replace")[0] == 0
    with servers.target("mssql", "mpdata_stage1_test").begin() as conn:
        conn.execute(sa.text('UPDATE "Orders" SET "Total" = 1 WHERE "OrderID" = 2'))
    code, summary = run_cli(capfd, "stage1", eng, *LOCAL, *DATASET, "--verify")
    assert code == 1
    bad = [
        (e["dialect"], e["table"]) for e in summary["tables"] if not e["digest_match"]
    ]
    assert bad == [("mssql", "Orders")]
    assert (
        "SQL Server mpdata_stage1_test: Orders: digest differs" in summary["messages"]
    )


def test_empty_dataset_fails_require_rows(make_engagement, tmp_path, capfd, servers):
    meta = json.loads((FIX / "dataset" / "dataset.json").read_text(encoding="utf-8"))
    empty = tmp_path / "empty"
    tds.write_dataset(
        empty,
        {k: meta[k] for k in ("name", "kind", "seed", "schema_sha256", "load_order")},
        [
            {
                "name": t["name"],
                "columns": t["columns"],
                "omitted_columns": t["omitted_columns"],
            }
            for t in meta["tables"]
        ],
    )
    eng = make_eng(tmp_path, make_engagement)
    code, summary = run_cli(
        capfd,
        "stage1",
        eng,
        *LOCAL,
        "--dataset",
        empty,
        "--replace",
        "--verify",
        "--require-rows",
    )
    assert code == 1
    assert (
        "PostgreSQL mpdata_stage1_test: every dataset table is empty"
        in summary["messages"]
    )


def test_second_run_without_replace_is_a_no_op(
    make_engagement, tmp_path, capfd, servers
):
    eng = make_eng(tmp_path, make_engagement)
    assert run_cli(capfd, "stage1", eng, *LOCAL, *DATASET)[0] == 0
    code, summary = run_cli(capfd, "stage1", eng, *LOCAL, *DATASET)
    assert code == 0
    assert summary["messages"] == [
        "PostgreSQL mpdata_stage1_test: tables already match; nothing to do",
        "SQL Server mpdata_stage1_test: tables already match; nothing to do",
    ]
    with servers.target("postgresql", "mpdata_stage1_test").connect() as conn:
        assert conn.execute(sa.text('SELECT count(*) FROM "Orders"')).scalar() == 4


def test_differing_target_exits_1(make_engagement, tmp_path, capfd, servers):
    eng = make_eng(tmp_path, make_engagement)
    assert run_cli(capfd, "stage1", eng, *LOCAL, *DATASET)[0] == 0
    with servers.target("postgresql", "mpdata_stage1_test").begin() as conn:
        conn.execute(sa.text('DELETE FROM "Order Lines" WHERE "LineID" = 7'))
    code, summary = run_cli(capfd, "stage1", eng, *LOCAL, *DATASET)
    assert code == 1
    assert (
        f"PostgreSQL mpdata_stage1_test: {stage1.TARGET_DIFFERS}" in summary["messages"]
    )
    # --replace restores it
    code, _ = run_cli(capfd, "stage1", eng, *LOCAL, *DATASET, "--replace", "--verify")
    assert code == 0


def test_verify_without_replace_on_a_missing_target_fails_and_creates_nothing(
    make_engagement, tmp_path, capfd, servers
):
    eng = make_eng(tmp_path, make_engagement)
    code, summary = run_cli(capfd, "stage1", eng, *LOCAL, *DATASET, "--verify")
    assert code == 1
    assert "PostgreSQL mpdata_stage1_test: database missing" in summary["messages"]
    assert not any(s.startswith("CREATE DATABASE") for s in servers.statements())
    assert servers.targets == {}


def test_verify_without_replace_on_an_empty_target_fails_and_creates_nothing(
    make_engagement, tmp_path, capfd, servers
):
    for srv in servers.servers.values():
        srv.databases["mpdata_stage1_test"] = "mp_runner"
    eng = make_eng(tmp_path, make_engagement)
    code, summary = run_cli(capfd, "stage1", eng, *LOCAL, *DATASET, "--verify")
    assert code == 1
    assert "PostgreSQL mpdata_stage1_test: table Orders missing" in summary["messages"]
    with servers.target("postgresql", "mpdata_stage1_test").connect() as conn:
        assert sa.inspect(conn).get_table_names() == []


def test_report_is_the_same_for_replace_verify_and_verify_alone(
    make_engagement, tmp_path, capfd, servers
):
    eng = make_eng(tmp_path, make_engagement)
    first, second = tmp_path / "a.md", tmp_path / "b.md"
    args = (*LOCAL, *DATASET, "--verify", "--require-rows", "--report")
    assert run_cli(capfd, "stage1", eng, *args, first, "--replace")[0] == 0
    assert run_cli(capfd, "stage1", eng, *args, second)[0] == 0
    assert first.read_bytes() == second.read_bytes()
    text = first.read_text(encoding="utf-8")
    sha = hashlib.sha256((FIX / "dataset" / "dataset.json").read_bytes()).hexdigest()
    assert sha in text and design_of().sha256 in text
    for table in (
        "Customers",
        "Orders",
        "Order Lines",
    ):  # no per-table digests in the report
        rows = list(tds.read_dataset(FIX / "dataset")[1][table])
        _n, digest = stage1.canonical_digest(
            rows, stage1.kept_columns(design_of().by_name[table])
        )
        assert digest not in text


def test_schema_only_reads_no_dataset(
    make_engagement, tmp_path, capfd, servers, monkeypatch
):
    def refuse(*_a, **_k):
        raise AssertionError("a dataset was read")

    monkeypatch.setattr(tds, "read_meta", refuse)
    monkeypatch.setattr(tds, "iter_table", refuse)
    monkeypatch.setattr(tds, "open_backend", refuse)
    eng = make_eng(tmp_path, make_engagement)
    assert not (private_of(eng) / "datasets").exists()
    code, summary = run_cli(capfd, "stage1", eng, *LOCAL, "--schema-only", "--replace")
    assert code == 0, summary
    code, summary = run_cli(capfd, "stage1", eng, *LOCAL, "--schema-only", "--verify")
    assert code == 0, summary
    assert all(e["dataset_rows"] is None for e in summary["tables"])
    with servers.target("mssql", "mpdata_stage1_test").connect() as conn:
        assert sorted(sa.inspect(conn).get_table_names()) == [
            "Customers",
            "Order Lines",
            "Orders",
        ]
        assert conn.execute(sa.text('SELECT count(*) FROM "Orders"')).scalar() == 0


def test_literal_url_target_for_a_synthetic_dataset(
    make_engagement, tmp_path, capfd, servers
):
    eng = make_eng(tmp_path, make_engagement)
    code, summary = run_cli(
        capfd,
        "stage1",
        eng,
        "--target-db",
        "postgresql://agent:agentpw@127.0.0.1:55432/dev_db",
        *DATASET,
        "--replace",
        "--verify",
    )
    assert code == 0, summary
    assert [e["dialect"] for e in summary["tables"]] == ["postgresql"] * 3
    assert 'CREATE DATABASE "dev_db"' in servers.servers["postgresql"].statements
    assert "agentpw" not in json.dumps(summary)


# ------------------------------------------------------------------ secret targets


SECRET_URL = "postgresql://clientuser:s3cretpw@db.client.invalid:5432/clientdb"
SECRET_PARTS = ("clientuser", "s3cretpw", "db.client.invalid", "clientdb", "5432")


def test_secret_target_resolves_inside_mp_data_and_never_shows_the_url(
    make_engagement, tmp_path, capfd, servers, monkeypatch, caplog
):
    from toolkit import secrets

    def no_data_env(*_a, **_k):
        raise AssertionError("data.env was opened")

    monkeypatch.setattr(secrets, "parse_env_file", no_data_env)
    monkeypatch.setattr(secrets, "data_env_path", no_data_env)
    monkeypatch.setenv("MP_SANDBOX", "mp-data")
    monkeypatch.setenv("MP_SECRET_SECRETS_STAGE1_TARGET_URL_REF", SECRET_URL)
    caplog.set_level(logging.DEBUG)
    eng = make_eng(
        tmp_path, make_engagement, secrets={"stage1_target_url_ref": "env:STAGE1_URL_X"}
    )
    report = tmp_path / "report.md"
    target = ("--target-db", "secret:stage1_target_url_ref", *DATASET)
    capfd.readouterr()
    code = cli.main([str(a) for a in ("stage1", eng, *target)])
    out1, err1 = capfd.readouterr()
    assert code == 0, out1
    code = cli.main(
        [
            str(a)
            for a in (
                "stage1",
                eng,
                *target,
                "--verify",
                "--require-rows",
                "--report",
                report,
            )
        ]
    )
    out2, err2 = capfd.readouterr()
    assert code == 0, out2
    summary = json.loads(out2)
    assert {e["dialect"] for e in summary["tables"]} == {"postgresql"}
    assert all(e["digest_match"] for e in summary["tables"])
    assert ("postgresql", "clientdb") in servers.targets  # the URL reached the engine
    assert not any(s.startswith("CREATE DATABASE") for s in servers.statements())
    text = report.read_text(encoding="utf-8")
    assert "## PostgreSQL" in text and "secret target" in text
    for part in SECRET_PARTS:
        for where in (out1, err1, out2, err2, text, caplog.text):
            assert part not in where


def test_secret_target_errors_do_not_show_the_url(
    make_engagement, tmp_path, capfd, monkeypatch
):
    class Broken:
        dialect = sa.create_engine("sqlite://").dialect

        def begin(self):
            raise sa.exc.OperationalError(
                "connect", {}, Exception(f"could not connect to {SECRET_URL}")
            )

        connect = begin

        def dispose(self):
            pass

    monkeypatch.setattr(stage1, "make_engine", lambda url: Broken())
    monkeypatch.setenv("MP_SANDBOX", "mp-data")
    monkeypatch.setenv("MP_SECRET_SECRETS_STAGE1_TARGET_URL_REF", SECRET_URL)
    eng = make_eng(
        tmp_path, make_engagement, secrets={"stage1_target_url_ref": "env:STAGE1_URL_X"}
    )
    capfd.readouterr()
    code = cli.main(
        [
            str(a)
            for a in (
                "stage1",
                eng,
                "--target-db",
                "secret:stage1_target_url_ref",
                *DATASET,
            )
        ]
    )
    out, err = capfd.readouterr()
    assert code == 2
    assert "PostgreSQL (secret target): cannot reach the database (Exception)" in out
    for part in SECRET_PARTS:
        assert part not in out and part not in err


def test_secret_target_that_is_not_empty_is_refused(
    make_engagement, tmp_path, capfd, servers, monkeypatch
):
    monkeypatch.setenv("MP_SANDBOX", "mp-data")
    monkeypatch.setenv("MP_SECRET_SECRETS_STAGE1_TARGET_URL_REF", SECRET_URL)
    eng = make_eng(
        tmp_path, make_engagement, secrets={"stage1_target_url_ref": "env:STAGE1_URL_X"}
    )
    target = ("--target-db", "secret:stage1_target_url_ref", *DATASET)
    assert run_cli(capfd, "stage1", eng, *target)[0] == 0
    with servers.target("postgresql", "clientdb").begin() as conn:
        conn.execute(sa.text('DELETE FROM "Order Lines"'))
    code, summary = run_cli(capfd, "stage1", eng, *target)
    assert code == 1
    assert summary["messages"] == [
        f"PostgreSQL (secret target): {stage1.SECRET_NOT_EMPTY}"
    ]


# ------------------------------------------------------------------ fresh copy (Access file)


def test_fresh_copy_loads_an_access_back_end(make_engagement, tmp_path, capfd, servers):
    """An .accdb created in tmp_path from the fixture dataset through the S2 Jackcess bridge
    (without the multi-value column, which Jackcess 5.1.7 cannot create), loaded with
    --dataset fresh-copy into a local mpdata_ database whose engine is SQLite in memory."""
    from toolkit.reference import bridge
    from toolkit.reference import build as bld

    schema = json.loads((FIX / "schema.json").read_text(encoding="utf-8"))
    for t in schema["tables"]:
        t["columns"] = [c for c in t["columns"] if c["type"] != "complex"]
    schema_file = tmp_path / "schema-no-complex.json"
    schema_file.write_text(json.dumps(schema, indent=2), encoding="utf-8")
    accdb = tmp_path / "fresh.accdb"
    conn = bridge.connect(accdb, read_only=False, new_version="V2010")
    try:
        bld.create_tables(conn, schema["tables"])
    finally:
        conn.close()
    assert bld.load_rows(accdb, FIX / "dataset") == (3, 13)
    eng = make_eng(
        tmp_path,
        make_engagement,
        schema=schema_file,
        database={
            "primary": "postgresql",
            "verify_also": ["mssql"],
            "stage1": {"fresh_copy": str(accdb)},
        },
    )
    code, summary = run_cli(
        capfd,
        "stage1",
        eng,
        *LOCAL,
        "--dataset",
        "fresh-copy",
        "--verify",
        "--replace",
        "--require-rows",
    )
    assert code == 0, summary
    counts = fixture_counts()
    for dialect in ("postgresql", "mssql"):
        with servers.target(dialect, "mpdata_stage1_test").connect() as c:
            got = {
                t: c.execute(sa.text(f'SELECT count(*) FROM "{t}"')).scalar()
                for t in counts
            }
        assert got == counts
    assert (
        summary["sha256s"]["fresh-copy"]
        == hashlib.sha256(accdb.read_bytes()).hexdigest()
    )
    assert all(e["digest_match"] for e in summary["tables"])


def test_fresh_copy_needs_the_engagement_setting(
    make_engagement, tmp_path, capfd, servers
):
    eng = make_eng(tmp_path, make_engagement)
    code, summary = run_cli(capfd, "stage1", eng, *LOCAL, "--dataset", "fresh-copy")
    assert code == 2
    assert summary["messages"] == [
        "--dataset fresh-copy: database.stage1.fresh_copy is not set"
    ]


def test_args_namespace_is_what_the_command_builds():
    parser = argparse.ArgumentParser()
    from toolkit.commands import stage1 as cmd

    cmd.add_arguments(parser)
    args = parser.parse_args(["e/engagement.json", "--target-db", "local:mpdata_x"])
    assert (args.dataset, args.schema_from, args.dialects) == (
        "engagement",
        "auto",
        None,
    )
    assert not (args.schema_only or args.replace or args.verify or args.require_rows)
