"""mp-data's Python half (docs/contracts.md#mp-data, #data-plane-jail; decisions L1, L12.2-L12.4).

`scripts/mp-data` (bash) sources the env file, exports MP_DB_SUFFIX, picks the toolkit root and
runs `uv run --project <root> python -m toolkit.dataplane <command> <args>`. This module then:

5. builds the toolkit child's environment (data.env parsed, never sourced; only the database URLs
   of the command, under two names each; MP_QUIET, MP_SANDBOX, MP_TEST_DB_PREFIX, a fresh TMPDIR
   in the data zone; MP_OTHER_DB_NAMES for purge);
6. checks and creates the declared outputs (--out, --report, --matrix parents, --junit-dir);
7. refuses literal or non-mpdata_ --target-db values and mpdata_ databases owned by other roles;
8. resolves exactly the secret refs the command needs and hands each over as MP_SECRET_<FIELD>;
9. runs `<root>/.venv/bin/toolkit <command> <args>` under bubblewrap (read-only root, then the
   writable binds, then the masks), writing its full output to <private>/logs/<ts>-<slot>-<pid>-<command>.log;
10-11. validates the child's summary line, adds `log`, withholds it when it holds a corpus string,
   and prints exactly one line.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema

from toolkit import secrets as mpsecrets
from toolkit.engagement import (
    DATA_ZONE_DIRS,
    DESIGN_ZONE_DIRS,
    EngagementError,
    derive_db_name,
    load,
    toolkit_root,
)

COMMANDS = (
    "fetch",
    "parse",
    "dataset",
    "guard",
    "reference",
    "golden",
    "stage1",
    "equivalence",
    "results",
    "purge",
    "push",
    "gh",
    "pin",
)
WITH_SUBCOMMAND = ("dataset", "reference", "golden", "results")
RUNNER_URLS = (
    "equivalence",
    "reference build",
    "reference run",
    "reference selfcheck",
    "golden record",
)
SUPER_URLS = ("stage1", "purge")
OWNER_CHECK = RUNNER_URLS + ("stage1",)
OUT_FLAGS = ("--out", "--report", "--matrix")
DIR_FLAGS = ("--junit-dir",)
VALUE_OUTPUTS = ("dataset export --out", "guard --report")
SLOT_RE = re.compile(r"^[a-z0-9_]{1,24}$")
DB_NAME_RE = re.compile(r"^[a-z0-9_]+$")
ALLOWED_OWNERS = {"postgresql": {"postgres", "mp_runner"}, "mssql": {"sa", "mp_runner"}}
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "summary.schema.json"
MAX_MSG = 200


class Refused(Exception):
    """A refused data-plane rule or a usage error: exit 2, nothing runs."""


@dataclass
class Jail:
    writable: list = field(default_factory=list)  # paths bound read-write, in order
    tmpfs: list = field(
        default_factory=list
    )  # directories hidden behind an empty tmpfs
    null: list = field(default_factory=list)  # files hidden behind /dev/null
    chdir: str = "/"


# ------------------------------------------------------------------ helpers


def _home() -> Path:
    return Path(os.environ.get("HOME") or str(Path.home()))


def _real(p) -> Path:
    return Path(os.path.realpath(p))


def _within(path: Path, base: Path) -> bool:
    return path == base or base in path.parents


def command_words(command: str, args: list[str]) -> str:
    if command in WITH_SUBCOMMAND:
        for a in args:
            if a.startswith("-") or a.endswith("/engagement.json"):
                break
            if re.match(r"^[a-z][a-z0-9-]*$", a):
                return f"{command} {a}"
            break
    return command


def find_engagement(args: list[str]) -> str | None:
    for a in args:
        if a.endswith("/engagement.json"):
            return a
    return None


def flag_values(args: list[str], flags) -> list[tuple[str, str]]:
    out = []
    i = 0
    while i < len(args):
        a = args[i]
        for f in flags:
            if a == f and i + 1 < len(args):
                out.append((f, args[i + 1]))
                i += 1
                break
            if a.startswith(f + "="):
                out.append((f, a[len(f) + 1 :]))
                break
        i += 1
    return out


def read_data_env(home: Path) -> dict:
    path = home / ".config" / "modernization" / "data.env"
    try:
        return mpsecrets.parse_env_file(path)
    except FileNotFoundError:
        return {}


def other_db_names(eng: dict, eng_path: str, home: Path) -> str:
    """db_names of every other engagement (for purge, computed before the jail starts)."""
    own = _real(eng_path)
    candidates = list((home / "engagements").glob("*/record/engagement.json"))
    candidates += list(
        Path(eng["paths"]["pipeline"], "engagements").glob("*/engagement.json")
    )
    names = set()
    for c in candidates:
        if c.parent.name == "_template" or _real(c) == own:
            continue
        try:
            data = json.loads(c.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("id") == eng.get("id"):
            continue
        name = derive_db_name(data)
        if name:
            names.add(name)
    return ",".join(sorted(names))


# ------------------------------------------------------------------ checks


def check_outputs(words: str, args: list[str], eng: dict) -> list[Path]:
    """Return the writable directories the outputs need (created later). Refuse paths outside
    the four engagement paths, and value outputs outside the data zone."""
    paths = eng["paths"]
    roots = [_real(paths[k]) for k in ("pipeline", "target_repo", "records", "private")]
    zone = [_real(Path(paths["private"]) / d) for d in DATA_ZONE_DIRS]
    dirs = []
    for flag, value in flag_values(args, OUT_FLAGS + DIR_FLAGS):
        if not value:
            raise Refused(f"{flag}: empty path")
        real = _real(value)
        if not any(_within(real, r) for r in roots):
            raise Refused(
                f"{flag}: outside the pipeline, target repo, records and private directories"
            )
        if f"{words} {flag}" in VALUE_OUTPUTS and not any(
            _within(real, z) and real != z for z in zone
        ):
            raise Refused(f"{words} {flag}: must lie in the data zone")
        dirs.append(real if flag in DIR_FLAGS else real.parent)
    return dirs


def check_target_db(args: list[str]) -> str | None:
    """Return the secret field named by --target-db secret:<name>, or None."""
    field_name = None
    for _flag, value in flag_values(args, ("--target-db",)):
        if value.startswith("local:"):
            name = value[len("local:") :]
            if not name.startswith("mpdata_") or not DB_NAME_RE.match(name):
                raise Refused("--target-db local:<name> must name an mpdata_ database")
        elif value.startswith("secret:"):
            name = value[len("secret:") :]
            if not re.match(r"^[a-z][a-z0-9_]*$", name):
                raise Refused("--target-db secret:<name>: bad name")
            field_name = f"secrets.{name}"
        else:
            raise Refused(
                "--target-db must be local:mpdata_<name> or secret:<ref>; literal URLs are refused"
            )
    return field_name


def database_urls(words: str, data_env: dict) -> dict:
    if words in RUNNER_URLS:
        src = {
            "pg": data_env.get("MP_RUNNER_PG_URL"),
            "mssql": data_env.get("MP_RUNNER_MSSQL_URL"),
        }
    elif words in SUPER_URLS:
        src = {
            "pg": data_env.get("MP_DATA_PG_URL"),
            "mssql": data_env.get("MP_DATA_MSSQL_URL"),
        }
    else:
        return {}
    out = {}
    if src["pg"]:
        out["MP_DATA_PG_URL"] = out["PG_URL"] = src["pg"]
    if src["mssql"]:
        out["MP_DATA_MSSQL_URL"] = out["MSSQL_URL"] = src["mssql"]
    return out


def list_owners(dialect: str, url: str) -> list[tuple[str, str]]:
    """(database, owner) pairs on a server, as the superuser. Raises when it does not answer."""
    if dialect == "postgresql":
        import psycopg

        with psycopg.connect(url, dbname="postgres", connect_timeout=5) as conn:
            rows = conn.execute(
                "SELECT datname, pg_get_userbyid(datdba) FROM pg_database"
            ).fetchall()
        return [(r[0], r[1]) for r in rows]
    import mssql_python

    u = urllib.parse.urlsplit(url)
    conn_str = (
        f"Server=tcp:{u.hostname},{u.port or 1433};Database=master;"
        f"Uid={urllib.parse.unquote(u.username or '')};Pwd={urllib.parse.unquote(u.password or '')};"
        "Encrypt=yes;TrustServerCertificate=yes;Connection Timeout=5"
    )
    conn = mssql_python.connect(conn_str)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name, SUSER_SNAME(owner_sid) FROM sys.databases")
        rows = cur.fetchall()
    finally:
        conn.close()
    return [(r[0], r[1]) for r in rows]


def check_owners(words: str, db_env: dict, data_env: dict, lister) -> list[str]:
    """Exit-2 problems for mpdata_ databases owned by another role; messages for skipped servers."""
    if words not in OWNER_CHECK:
        return []
    notes = []
    for dialect, handed, super_key in (
        ("postgresql", "MP_DATA_PG_URL", "MP_DATA_PG_URL"),
        ("mssql", "MP_DATA_MSSQL_URL", "MP_DATA_MSSQL_URL"),
    ):
        if handed not in db_env:
            continue
        url = data_env.get(super_key)
        if not url:
            notes.append(
                f"{dialect}: no superuser URL in data.env; owner check left to the command"
            )
            continue
        try:
            rows = lister(dialect, url)
        except Exception as exc:  # noqa: BLE001 - a server that does not answer is left to the command
            notes.append(
                f"{dialect}: server did not answer ({type(exc).__name__}); left to the command"
            )
            continue
        for name, owner in rows:
            if str(name).startswith("mpdata_") and owner not in ALLOWED_OWNERS[dialect]:
                raise Refused(
                    f"mpdata_ database {name} on {dialect} is owned by {owner}, not "
                    f"{' or '.join(sorted(ALLOWED_OWNERS[dialect]))}: drop it as the superuser"
                )
    return notes


def needed_secrets(
    command: str, eng: dict, target_field: str | None
) -> list[tuple[str, str]]:
    """(field, ref) pairs this call needs. secrets.accdb_password_ref and
    source.backends[].password_ref are reserved (docs/contracts.md#secret-refs): never resolved."""
    secrets = eng.get("secrets") or {}
    out = []
    if command in ("push", "gh"):
        gp = secrets.get("github_push") or {}
        ref = gp.get("ref") if isinstance(gp, dict) else None
        if ref:
            out.append(("secrets.github_push.ref", ref))
    if target_field:
        key = target_field.split(".", 1)[1]
        ref = secrets.get(key)
        if not isinstance(ref, str) or not ref:
            raise Refused(f"--target-db secret:{key}: {target_field} is not set")
        out.append((target_field, ref))
    return out


def resolve_outside(ref: str, field_name: str) -> str:
    saved = os.environ.pop("MP_SANDBOX", None)
    try:
        return mpsecrets.resolve(ref, field_name)
    finally:
        if saved is not None:
            os.environ["MP_SANDBOX"] = saved


# ------------------------------------------------------------------ jail


def git_dirs(repo: str) -> list[Path]:
    out = []
    for flag in ("--absolute-git-dir", "--git-common-dir"):
        proc = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--path-format=absolute", flag],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            p = _real(proc.stdout.strip())
            if p not in out:
                out.append(p)
    return out


def plan(
    words: str,
    eng: dict,
    home: Path,
    root: Path,
    tmpdir: Path,
    output_dirs: list[Path],
    env: dict | None = None,
) -> Jail:
    """Decide the jail's writable binds and masks. Inspects the filesystem; creates nothing."""
    env = os.environ if env is None else env
    command = words.split()[0]
    paths = eng["paths"]
    private = Path(paths["private"])
    jail = Jail(chdir=str(root))
    w = jail.writable
    for d in DATA_ZONE_DIRS:
        w.append(private / d)
    w.append(private / "scrub-map.json")
    w.append(tmpdir)
    for d in output_dirs:
        if d not in w:
            w.append(d)
    if command == "fetch":
        w += [Path(paths["records"]), root / "vendor"]
    elif command == "guard":
        w.append(home / ".local" / "share" / "modernization" / "pre-commit")
    elif words == "golden record":
        w.append(Path(paths["target"]) / "goldens")
    elif command in ("push", "gh"):
        w += git_dirs(paths["target_repo"])
    elif command == "purge":
        w += [private, Path(paths["records"])]
    # masks: other engagements, the pass store, the operator's session sockets
    own = _real(private.parent)
    eng_root = home / "engagements"
    if eng_root.is_dir():
        for d in sorted(eng_root.iterdir()):
            if not d.is_dir() or _real(d) == own:
                continue
            for sub in ("private", "record"):
                if (d / sub).is_dir():
                    jail.tmpfs.append(d / sub)
    store = Path(env.get("PASSWORD_STORE_DIR") or home / ".password-store")
    if store.is_dir():
        jail.tmpfs.append(store)
    uid = os.getuid()
    xdg = Path(env.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}")
    if xdg.is_dir():
        jail.tmpfs.append(xdg)
    tmux = Path(env.get("TMUX_TMPDIR") or "/tmp") / f"tmux-{uid}"
    if tmux.is_dir():
        jail.tmpfs.append(tmux)
    try:
        user = pwd.getpwuid(uid).pw_name
        screen = Path(f"/run/screen/S-{user}")
        if screen.is_dir():
            jail.tmpfs.append(screen)
    except KeyError:
        pass
    data_env = home / ".config" / "modernization" / "data.env"
    if data_env.exists():
        jail.null.append(data_env)
    for sock in ("/var/run/docker.sock",):
        if os.path.exists(sock):
            jail.null.append(_real(sock))
    docker_host = env.get("DOCKER_HOST") or ""
    if docker_host.startswith("unix://"):
        sock = Path(docker_host[len("unix://") :])
        if (
            sock.exists()
            and not _within(_real(sock), _real(xdg))
            and _real(sock) not in jail.null
        ):
            jail.null.append(_real(sock))
    return jail


def jail_argv(jail: Jail, child: list[str]) -> list[str]:
    """Pure: bwrap argv with the read-only root first, then the writable binds, then the masks."""
    argv = [
        "bwrap",
        "--unshare-user",
        "--unshare-pid",
        "--die-with-parent",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
    ]
    for p in jail.writable:
        argv += ["--bind", str(p), str(p)]
    for p in jail.tmpfs:
        argv += ["--tmpfs", str(p)]
    for p in jail.null:
        argv += ["--ro-bind", "/dev/null", str(p)]
    argv += ["--chdir", jail.chdir, "--", *child]
    return argv


def prepare_binds(jail: Jail) -> None:
    """Create the writable bind sources that are missing (scrub-map.json as an empty file)."""
    for p in jail.writable:
        p = Path(p)
        if p.name == "scrub-map.json":
            if not p.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(p, os.O_WRONLY | os.O_CREAT, 0o600)
                os.close(fd)
        else:
            p.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------------ run


def _default_runner(argv, env, cwd, log):
    proc = subprocess.run(
        argv,
        env=env,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=log,
        check=False,
    )
    return proc.returncode, proc.stdout, b""


def _open_log(logs: Path, slot: str, pid: int, command: str, now: dt.datetime):
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    base = f"{ts}-{slot}-{pid}-{command}"
    for n in range(100):
        path = logs / (f"{base}.log" if n == 0 else f"{base}-{n}.log")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        os.fchmod(fd, 0o600)
        return path, os.fdopen(fd, "wb")
    raise Refused("cannot create a log file")


def _error(words, eng_id, exit_code, message, log=None) -> dict:
    out = {"status": "error", "command": words, "exit_code": exit_code}
    if eng_id:
        out["engagement"] = eng_id
    out["messages"] = [message[:MAX_MSG]]
    if log:
        out["log"] = str(log)
    return out


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def validate_summary(line: str, rc: int, eng: dict) -> tuple[dict | None, str | None]:
    try:
        obj = json.loads(line)
    except ValueError:
        return None, "summary is not JSON"
    if not isinstance(obj, dict):
        return None, "summary is not an object"
    obj.pop("log", None)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(obj))
    if errors:
        return (
            None,
            f"summary does not match summary.schema.json ({len(errors)} problems)",
        )
    if obj["exit_code"] != rc:
        return None, "summary exit_code differs from the command's"
    paths = eng["paths"]
    allowed = [_real(paths[k]) for k in ("pipeline", "target_repo", "records")]
    allowed += [_real(Path(paths["private"]) / d) for d in DESIGN_ZONE_DIRS]
    for p in obj.get("paths", []):
        if not any(_within(_real(p), a) for a in allowed):
            return (
                None,
                "summary names a path outside the repos, records and design zone",
            )
    return obj, None


def corpus_hit(line: str, private: Path) -> bool:
    path = private / "corpus" / "strings.txt"
    try:
        with open(path, encoding="utf-8") as fh:
            for s in fh:
                s = s.rstrip("\r\n")
                if s and s in line:
                    return True
    except FileNotFoundError:
        return False
    return False


def run(
    argv: list[str],
    runner=None,
    owner_lister=None,
    child: list[str] | None = None,
    now=None,
    pid=None,
) -> int:
    runner = runner or _default_runner
    owner_lister = owner_lister or list_owners
    if not argv or argv[0] not in COMMANDS or argv[0] == "pin":
        _emit(_error("mp-data", None, 2, "unknown data-plane command"))
        return 2
    command, args = argv[0], list(argv[1:])
    words = command_words(command, args)
    eng_id = None
    try:
        slot = os.environ.get("MP_DB_SUFFIX") or "orchestrator"
        if not SLOT_RE.match(slot):
            raise Refused("--slot must match ^[a-z0-9_]{1,24}$")
        eng_path = find_engagement(args)
        if not eng_path:
            raise Refused("no <records>/engagement.json argument")
        try:
            eng = load(eng_path)
        except EngagementError as exc:
            raise Refused(str(exc)) from None
        eng_id = eng["id"]
        home = _home()
        root = toolkit_root()
        private = Path(eng["paths"]["private"])
        data_env = read_data_env(home)
        output_dirs = check_outputs(words, args, eng)
        target_field = check_target_db(args)
        db_env = database_urls(words, data_env)
        notes = check_owners(words, db_env, data_env, owner_lister)
        secret_values = {}
        for fld, ref in needed_secrets(command, eng, target_field):
            try:
                secret_values[mpsecrets.field_env_name(fld)] = resolve_outside(ref, fld)
            except mpsecrets.SecretError as exc:
                raise Refused(str(exc)) from None
        toolkit_bin = root / ".venv" / "bin" / "toolkit"
        if child is None and not toolkit_bin.exists():
            raise Refused(
                f"{toolkit_bin} is missing: run `cd {root} && uv sync --locked --all-groups`"
            )
    except Refused as exc:
        _emit(_error(words, eng_id, 2, str(exc)))
        return 2

    logs = private / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    tmpdir = Path(tempfile.mkdtemp(prefix="tmp-", dir=logs))
    log_path = None
    try:
        jail = plan(words, eng, home, root, tmpdir, output_dirs)
        prepare_binds(jail)
        env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in (
                "PG_URL",
                "MSSQL_URL",
                "MP_OTHER_DB_NAMES",
                "PRE_COMMIT_HOME",
                "TMP",
                "TEMP",
            )
            and not k.startswith(("MP_SECRET_", "MP_DATA_", "MP_RUNNER_"))
        }
        env.update(
            {
                "MP_QUIET": "1",
                "MP_DB_SUFFIX": slot,
                "MP_TEST_DB_PREFIX": "mpdata_test_",
                "MP_SANDBOX": "mp-data",
                "TMPDIR": str(tmpdir),
                "TMP": str(tmpdir),
                "TEMP": str(tmpdir),
            }
        )
        env.update(db_env)
        env.update(secret_values)
        if command == "purge":
            env["MP_OTHER_DB_NAMES"] = other_db_names(eng, eng_path, home)
        if command == "guard":
            env["PRE_COMMIT_HOME"] = str(
                home / ".local" / "share" / "modernization" / "pre-commit"
            )
        child_argv = (child or [str(toolkit_bin)]) + [command, *args]
        argv_full = jail_argv(jail, child_argv)
        log_path, log = _open_log(
            logs,
            slot,
            os.getpid() if pid is None else pid,
            command,
            now or dt.datetime.now(dt.UTC),
        )
        with log:
            log.write(f"# mp-data {words} slot={slot}\n".encode())
            for n in notes:
                log.write(f"# {n}\n".encode())
            log.write(b"--- stderr ---\n")
            log.flush()
            rc, out, err = runner(argv_full, env, str(root), log)
            if err:
                log.write(err)
            log.write(b"\n--- stdout ---\n")
            log.write(out or b"")
        text = (out or b"").decode("utf-8", "replace")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) != 1:
            obj, reason = (
                None,
                ("no summary line" if not lines else "more than one stdout line"),
            )
        else:
            obj, reason = validate_summary(lines[0], rc, eng)
            if obj is not None and corpus_hit(lines[0], private):
                obj, reason = None, "summary withheld: it contains a corpus string"
        if obj is None:
            code = 2 if rc == 0 else rc
            _emit(_error(words, eng_id, code, reason, log_path))
            return code
        obj["log"] = str(log_path)
        if notes:
            obj["messages"] = (obj.get("messages", []) + [n[:MAX_MSG] for n in notes])[
                :50
            ]
        _emit(obj)
        return rc
    except Refused as exc:
        _emit(_error(words, eng_id, 2, str(exc), log_path))
        return 2
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    return run(list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
