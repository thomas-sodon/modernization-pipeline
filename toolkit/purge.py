"""Purge an engagement's private data when retention ends (docs/contracts.md#databases, #records,
#data-plane-jail; cli-registry `purge`).

Runs only as `mp-data purge <eng> [--dry-run]`: mp-data hands it the superuser URLs (a
toolkit-only command), sets MP_OTHER_DB_NAMES (the other engagements' db_names, which the jail
hides) and declares <private> and <records> as its outputs.

1. Refuse (exit 2) when MP_OTHER_DB_NAMES is unset, when <private> is a symlink or when it does not
   end in /engagements/<id>/private.
2. Plan: files and bytes per top-level entry of <private>; on each local dialect (PostgreSQL,
   SQL Server), found with stage1's `local:` server resolution (MP_DATA_*_URL, else PG_URL or
   MSSQL_URL; URLs mapped by stage1.to_sa_url), the databases named mpdata_<db>_*,
   mpdata_test_<db>_*, <db>_dev, <db> or test_<db>_*, matched in Python with exact prefixes
   (never SQL LIKE), minus names that belong to another engagement whose db_name begins with
   <db>_. A dialect without a URL, or whose server does not answer, is "not checked".
3. --dry-run reports the plan (counts, sizes, database names) and changes nothing.
4. Otherwise drop the databases (autocommit; PostgreSQL `DROP DATABASE "<n>" WITH (FORCE)`,
   SQL Server `ALTER DATABASE [<n>] SET SINGLE_USER WITH ROLLBACK IMMEDIATE` then
   `DROP DATABASE [<n>]`), delete everything under <private> and the directory itself (a mount
   point, as <private> is inside the jail, is emptied and stays), and write
   <records>/destruction-record-<YYYY-MM-DD>.md. The record holds names, counts, sizes and
   hashes, never file contents.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from pathlib import Path

from toolkit import stage1
from toolkit.engagement import relative_to_private

OTHER_ENV = "MP_OTHER_DB_NAMES"
DIALECTS = ("postgresql", "mssql")
LIST_SQL = {
    "postgresql": "SELECT datname FROM pg_database WHERE NOT datistemplate",
    "mssql": "SELECT name FROM sys.databases",
}


class PurgeError(Exception):
    """A refused purge (exit 2). The message names paths and variables, never values."""

    exit_code = 2


# ------------------------------------------------------------------ servers


class SqlServer:
    """One local server through SQLAlchemy (stage1's URL mapping and engine factory)."""

    def __init__(self, dialect: str, url_text: str):
        self.dialect = dialect
        url = stage1.to_sa_url(url_text, stage1.MAINTENANCE_DB[dialect])
        if dialect == "postgresql":
            url = url.update_query_dict({"connect_timeout": "5"})
        self.engine = stage1.make_engine(url)

    def _conn(self):
        return self.engine.connect().execution_options(isolation_level="AUTOCOMMIT")

    def list_databases(self) -> list[str]:
        with self._conn() as conn:
            return [str(r[0]) for r in conn.exec_driver_sql(LIST_SQL[self.dialect])]

    def drop(self, name: str) -> None:
        q = stage1.quote_db(self.dialect, name)
        with self._conn() as conn:
            if self.dialect == "postgresql":
                conn.exec_driver_sql(f"DROP DATABASE {q} WITH (FORCE)")
            else:
                conn.exec_driver_sql(
                    f"ALTER DATABASE {q} SET SINGLE_USER WITH ROLLBACK IMMEDIATE"
                )
                conn.exec_driver_sql(f"DROP DATABASE {q}")

    def close(self) -> None:
        self.engine.dispose()


def connect(dialect: str, url_text: str):
    """The default server factory (tests inject fakes with the same three methods)."""
    return SqlServer(dialect, url_text)


# ------------------------------------------------------------------ names


def other_db_names(env) -> list[str]:
    value = env.get(OTHER_ENV)
    if value is None:
        raise PurgeError(
            f"{OTHER_ENV} is unset: run purge only as mp-data purge, which computes it"
        )
    return sorted({n.strip() for n in value.split(",") if n.strip()})


def name_matches(name: str, db: str) -> bool:
    if name in (db, f"{db}_dev"):
        return True
    return name.startswith((f"mpdata_{db}_", f"mpdata_test_{db}_", f"test_{db}_"))


def owned(name: str, db: str, others: list[str]) -> bool:
    """True when `name` is one of this engagement's databases and no other engagement's."""
    if not name_matches(name, db):
        return False
    for other in others:
        if other != db and other.startswith(f"{db}_") and name_matches(name, other):
            return False
    return True


# ------------------------------------------------------------------ files


@dataclass
class Entry:
    path: str  # private/<name>
    files: int
    bytes: int


def check_private(eng: dict) -> Path:
    raw = eng["paths"]["private"]
    if os.path.islink(raw):
        raise PurgeError(f"{raw} is a symlink: refused")
    if not raw.rstrip("/").endswith(f"/engagements/{eng['id']}/private"):
        raise PurgeError(
            f"{raw} does not end in /engagements/{eng['id']}/private: refused"
        )
    return Path(raw)


def _size(path: Path) -> tuple[int, int]:
    if os.path.islink(path) or not path.is_dir():
        return 1, os.lstat(path).st_size
    files = size = 0
    for root, dirs, names in os.walk(path, followlinks=False):
        for n in names + [d for d in dirs if os.path.islink(os.path.join(root, d))]:
            files += 1
            size += os.lstat(os.path.join(root, n)).st_size
    return files, size


def usage(private: Path) -> list[Entry]:
    if not private.is_dir():
        return []
    out = []
    for p in sorted(private.iterdir(), key=lambda x: x.name):
        files, size = _size(p)
        out.append(Entry(f"private/{p.name}", files, size))
    return out


def remove(path: Path) -> bool:
    """Delete `path` and everything under it; mount points are emptied and stay.
    Returns True when the path is gone."""
    if os.path.islink(path) or not path.is_dir():
        if os.path.ismount(path):
            with open(path, "w", encoding="utf-8"):
                pass
            return False
        os.unlink(path)
        return True
    gone = True
    for entry in list(os.scandir(path)):
        if not remove(Path(entry.path)):
            gone = False
    if not gone or os.path.ismount(path):
        return False
    os.rmdir(path)
    return True


# ------------------------------------------------------------------ plan and run


@dataclass
class ServerPlan:
    dialect: str
    reason: str | None = None  # not checked: why
    names: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    server: object = None


@dataclass
class Plan:
    private: Path
    entries: list[Entry]
    servers: list[ServerPlan]
    copies: list[dict]
    sources: list[tuple[str, str]]

    @property
    def files(self) -> int:
        return sum(e.files for e in self.entries)

    @property
    def bytes(self) -> int:
        return sum(e.bytes for e in self.entries)

    def databases(self) -> list[tuple[str, str]]:
        return [(s.dialect, n) for s in self.servers for n in s.names]

    def not_checked(self) -> list[tuple[str, str]]:
        return [(s.dialect, s.reason) for s in self.servers if s.reason]


def sources(eng: dict) -> list[tuple[str, str]]:
    """(path relative to <private>, sha256) of the source files, from source.lock.json, else from
    the engagement's own pins."""
    private = eng["paths"]["private"]
    lock = eng.get("source_lock")
    out: list[tuple[str, str]] = []
    if lock:
        for part in (lock.get("source"), lock.get("data_copy")):
            if isinstance(part, dict) and part.get("path"):
                out.append((str(part["path"]), str(part.get("sha256") or "unknown")))
        for b in lock.get("backends") or []:
            if isinstance(b, dict) and b.get("path"):
                out.append((str(b["path"]), str(b.get("sha256") or "unknown")))
        return out
    src = eng.get("source") or {}
    if src.get("path"):
        out.append(
            (
                relative_to_private(src["path"], private),
                str(src.get("sha256") or "unknown"),
            )
        )
    if src.get("data_copy"):
        out.append(
            (
                relative_to_private(src["data_copy"], private),
                str(src.get("data_copy_sha256") or "unknown"),
            )
        )
    for b in src.get("backends") or []:
        if isinstance(b, dict) and b.get("path"):
            out.append(
                (
                    relative_to_private(b["path"], private),
                    str(b.get("sha256") or "unknown"),
                )
            )
    return out


def copies(eng: dict) -> list[dict]:
    box = ((eng.get("environment") or {}).get("windows_box")) or {}
    return [
        {"path": str(c.get("path")), "what": str(c.get("what") or "")}
        for c in box.get("copies") or []
        if isinstance(c, dict) and c.get("path")
    ]


def make_plan(eng: dict, env, factory=None) -> Plan:
    factory = factory or connect
    private = check_private(eng)
    others = other_db_names(env)
    db = eng["db_name"]
    servers = []
    for dialect in DIALECTS:
        sp = ServerPlan(dialect)
        servers.append(sp)
        try:
            url_text = stage1.server_url_text(dialect, env)
        except stage1.Stage1Error:
            first, second = stage1.SERVER_ENV[dialect]
            sp.reason = f"no server URL ({first} and {second} unset)"
            continue
        try:
            server = factory(dialect, url_text)
            names = server.list_databases()
        except Exception as exc:  # noqa: BLE001 - a server that does not answer is named, not fatal
            sp.reason = f"server did not answer ({type(exc).__name__})"
            continue
        sp.server = server
        sp.names = sorted(n for n in names if owned(n, db, others))
    return Plan(private, usage(private), servers, copies(eng), sources(eng))


def execute(plan: Plan) -> list[str]:
    """Drop the databases, delete the private tree. Returns problems (names only)."""
    problems = []
    for sp in plan.servers:
        for name in sp.names:
            try:
                sp.server.drop(name)
                sp.dropped.append(name)
            except Exception as exc:  # noqa: BLE001 - recorded, purge goes on
                sp.failed.append((name, type(exc).__name__))
                problems.append(
                    f"{sp.dialect}: drop {name} failed ({type(exc).__name__})"
                )
    if plan.private.exists() or os.path.islink(plan.private):
        try:
            remove(plan.private)
        except OSError as exc:
            problems.append(f"deleting {plan.private} failed ({type(exc).__name__})")
    return problems


def close(plan: Plan) -> None:
    for sp in plan.servers:
        if sp.server is not None and hasattr(sp.server, "close"):
            try:
                sp.server.close()
            except Exception:  # noqa: BLE001, S110 - closing is best effort
                pass


def _md_table(head: list[str], rows: list[list]) -> list[str]:
    if not rows:
        return ["none"]
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += [
        "| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |"
        for row in rows
    ]
    return out


def render_record(eng: dict, plan: Plan, date: str) -> str:
    lines = [
        f"# Destruction record: {eng['id']}",
        f"date: {date}",
        f"engagement: {eng['id']}",
        "",
        "## Deleted",
        *_md_table(
            ["path", "files", "bytes"],
            [[e.path, e.files, e.bytes] for e in plan.entries],
        ),
        "",
        "## Databases dropped",
        *_md_table(
            ["server", "name"],
            [[s.dialect, n] for s in plan.servers for n in s.dropped],
        ),
    ]
    failed = [(s.dialect, n, why) for s in plan.servers for n, why in s.failed]
    if failed:
        lines += ["", "Not dropped (drop failed; drop them by hand as the superuser):"]
        lines += [f"- {d} {n} ({why})" for d, n, why in failed]
    lines += [
        "",
        "## Servers not checked",
        *_md_table(["server", "reason"], [list(x) for x in plan.not_checked()]),
        "",
        "## Sources",
        *_md_table(["path", "sha256"], [list(x) for x in plan.sources]),
        "",
        "## Copies to delete by hand",
        *_md_table(["path", "what"], [[c["path"], c["what"]] for c in plan.copies]),
    ]
    return "\n".join(lines) + "\n"


def record_path(eng: dict, date: str) -> Path:
    records = Path(eng["paths"]["records"])
    path = records / f"destruction-record-{date}.md"
    n = 2
    while path.exists():
        path = records / f"destruction-record-{date}-{n}.md"
        n += 1
    return path


def plan_messages(plan: Plan, dry_run: bool) -> list[str]:
    verb = "would drop" if dry_run else "dropped"
    msgs = [f"not checked {d}: {why}" for d, why in plan.not_checked()]
    for sp in plan.servers:
        names = sp.names if dry_run else sp.dropped
        msgs += [f"{verb} {sp.dialect} {n}" for n in names]
        msgs += [f"drop failed {sp.dialect} {n} ({why})" for n, why in sp.failed]
    d_verb = "would delete" if dry_run else "deleted"
    msgs += [
        f"{d_verb} {e.path}: {e.files} files, {e.bytes} bytes" for e in plan.entries
    ]
    msgs += [f"delete by hand: {c['path']}" for c in plan.copies]
    return msgs


def run(eng: dict, dry_run: bool, env=None, factory=None, today: str | None = None):
    """Returns (exit code, plan, record path or None, problems)."""
    env = os.environ if env is None else env
    plan = make_plan(eng, env, factory)
    try:
        if dry_run:
            return 0, plan, None, []
        problems = execute(plan)
    finally:
        close(plan)
    date = today or dt.datetime.now(dt.UTC).date().isoformat()
    path = record_path(eng, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_record(eng, plan, date), encoding="utf-8")
    return (1 if problems else 0), plan, path, problems


def counts(plan: Plan) -> dict:
    return {
        "directories": len(plan.entries),
        "files": plan.files,
        "bytes": plan.bytes,
        "databases": len(plan.databases()),
        "servers_not_checked": len(plan.not_checked()),
        "copies": len(plan.copies),
    }
