"""The Jackcess and UCanAccess bridge: one in-process JVM through JPype1.

Importing this module starts nothing and imports nothing else from toolkit.reference; JPype1 is
imported inside the functions. S0's readers (toolkit/sources/accdb.py for accdb and mdb designs,
toolkit/dataset.py for rows of accdb and mdb files and of `source.data_copy`) use it, and so do
`reference build|run` and `ReferenceAdapter`.

- `start_jvm()` starts one JVM per process from `$JAVA_HOME` (environment item `jdk`), or does
  nothing when it runs. Classpath: the jars that `uv run toolkit fetch --jars` put in the toolkit
  root's vendor/ (ucanaccess 5.1.8, jackcess 5.1.7, hsqldb 2.7.4), plus, when the pipeline's
  java/ holds sources, a jar that `compile_java` builds from them with `$JAVA_HOME/bin/javac`
  into a new directory under `tempfile.gettempdir()` (which follows `$TMPDIR`), removed at exit.
  Nothing is ever written under the toolkit root (read-only in mp-data's jail and in a pinned
  release). The JVM gets `-Djava.io.tmpdir` = `tempfile.gettempdir()`, because Java ignores
  `$TMPDIR`. JPype cannot restart a JVM, so the classpath is fixed at the first start.
- `open_database(path, read_only=True)`: a Jackcess Database.
- `tables(db)`: user table names (no MSys tables); linked tables are included and
  `link_connect(db, table)` gives their connect string.
- `columns(db, table)`: `Column(name, type, size, required)` per column, with the neutral types of
  docs/contracts.md#object-model; a type without a neutral match is `unsupported:<DataType>`.
- `iter_rows(db, table)`: one dict per row, keyed by column name in column order, of Python values
  (int, Decimal, float, str, bool, datetime, None); complex and OLE columns are None.
  `tuple(row.values())` is the row tuple.
- `queries(db)`: `Query(name, type, sql)` per saved query, from `Database.getQueries()` and
  `toSQLString()` (hidden `~sq_` row-source queries are left out).
- `connect(path, read_only=True, memory=True, new_version=None)`: a UCanAccess JDBC connection;
  `new_version` V2010 or V2003 creates a missing file in that format. `connect(db, table)` with a
  Jackcess Database is `link_connect` (the form S0's accdb reader calls).

A missing `JAVA_HOME` or jar raises `BridgeUnavailable` (a SourceError: commands exit 2) naming
the `jdk` environment item or the command that restores the jars.
"""

from __future__ import annotations

import atexit
import contextlib
import datetime as dt
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path

from toolkit.sources import SourceError

ENGINE = "ucanaccess-5.1.8"
DEFAULT_JARS = ("ucanaccess-5.1.8.jar", "jackcess-5.1.7.jar", "hsqldb-2.7.4.jar")
NEW_VERSIONS = ("V2010", "V2003", "V2007", "V2000")
JAVA_DIR_NAME = "java"

_STATE: dict = {"function_classes": [], "jar_dir": None}


class BridgeUnavailable(SourceError):
    """The JVM cannot start: JAVA_HOME, a jar or the java/ compile step is missing. Exit 2."""


# ------------------------------------------------------------------ records


class _Record(tuple):
    """A tuple whose fields can also be read by name: rec[0], rec["name"], rec.get("name")."""

    _fields: tuple = ()

    def __new__(cls, *values):
        if len(values) != len(cls._fields):
            raise TypeError(f"{cls.__name__} takes {len(cls._fields)} values")
        return super().__new__(cls, values)

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return tuple.__getitem__(self, self._fields.index(key))
            except ValueError:
                raise KeyError(key) from None
        return tuple.__getitem__(self, key)

    def __getattr__(self, name):
        if name in type(self)._fields:
            return self[name]
        raise AttributeError(name)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self):
        return list(self._fields)

    def _asdict(self) -> dict:
        return dict(zip(self._fields, self))


class Column(_Record):
    _fields = ("name", "type", "size", "required")


class Query(_Record):
    _fields = ("name", "type", "sql")


# ------------------------------------------------------------------ locations


def toolkit_root() -> Path:
    return Path(__file__).resolve().parents[2]


def vendor_dir() -> Path:
    return toolkit_root() / "vendor"


def java_dir() -> Path:
    return toolkit_root() / JAVA_DIR_NAME


def jar_names() -> list[str]:
    """The reference-engine jar file names pinned in pipeline.json stack.reference_engine."""
    try:
        from toolkit.fetch import jar_path, jar_specs

        names = [jar_path(spec["coord"])[1] for spec in jar_specs()]
    except Exception:  # noqa: BLE001 - pipeline.json missing or unreadable: use the pins
        names = []
    return names or list(DEFAULT_JARS)


def fetch_hint() -> str:
    if os.environ.get("MP_SANDBOX") == "mp-data":
        return "run `mp-data fetch <eng> --verify` (it restores the jars of the toolkit root mp-data runs)"
    return "run `uv run toolkit fetch --jars`"


def classpath(vendor: Path | None = None) -> list[str]:
    vendor = Path(vendor) if vendor is not None else vendor_dir()
    out, missing = [], []
    for name in jar_names():
        p = vendor / name
        (out if p.is_file() else missing).append(str(p) if p.is_file() else name)
    if missing:
        raise BridgeUnavailable(
            f"reference-engine jar missing from vendor/ ({', '.join(missing)}): {fetch_hint()}"
        )
    return out


def java_home() -> Path:
    home = os.environ.get("JAVA_HOME")
    if not home:
        raise BridgeUnavailable(
            "JAVA_HOME is not set: run the environment item jdk (it writes JAVA_HOME into the env file)"
        )
    path = Path(home)
    if not (path / "bin" / "java").exists():
        raise BridgeUnavailable(
            "JAVA_HOME does not hold a JDK: run the environment item jdk"
        )
    return path


def libjvm(home: Path) -> str:
    for rel in (
        "lib/server/libjvm.so",
        "lib/server/libjvm.dylib",
        "bin/server/jvm.dll",
    ):
        p = home / rel
        if p.exists():
            return str(p)
    raise BridgeUnavailable(
        "libjvm not found under JAVA_HOME: run the environment item jdk"
    )


# ------------------------------------------------------------------ java/ app functions

_FUNCTION_NAME = re.compile(r'functionName\s*=\s*"([A-Za-z_][A-Za-z0-9_]*)"')
_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)


def java_sources(directory: Path | None = None) -> list[Path]:
    directory = Path(directory) if directory is not None else java_dir()
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.rglob("*.java") if p.is_file())


def java_function_names(directory: Path | None = None) -> dict[str, str]:
    """{function name (lower case): class name} for every @FunctionType method in java/."""
    out = {}
    for src in java_sources(directory):
        text = src.read_text(encoding="utf-8")
        m = _PACKAGE.search(text)
        cls = (m.group(1) + "." if m else "") + src.stem
        for name in _FUNCTION_NAME.findall(text):
            out[name.lower()] = cls
    return out


def javac_argv(
    javac: str, sources: list[Path], classes_dir: Path, cp: list[str]
) -> list[str]:
    return [
        str(javac),
        "-encoding",
        "UTF-8",
        "-nowarn",
        "-d",
        str(classes_dir),
        "-cp",
        os.pathsep.join(str(p) for p in cp),
        *[str(s) for s in sources],
    ]


def compile_java(
    directory: Path | None = None,
    tmp_root: Path | str | None = None,
    home: Path | None = None,
    cp: list[str] | None = None,
    runner=subprocess.run,
) -> tuple[Path | None, list[str]]:
    """Compile java/ sources into a jar in a new directory under `tmp_root` (default
    tempfile.gettempdir()). Returns (jar path or None when there are no sources, class names).
    Writes nothing outside that new directory."""
    sources = java_sources(directory)
    if not sources:
        return None, []
    home = home or java_home()
    cp = cp if cp is not None else classpath()
    root = Path(tmp_root) if tmp_root is not None else Path(tempfile.gettempdir())
    work = Path(tempfile.mkdtemp(prefix="mp-java-", dir=root))
    classes = work / "classes"
    classes.mkdir()
    argv = javac_argv(home / "bin" / "javac", sources, classes, cp)
    proc = runner(argv, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        shutil.rmtree(work, ignore_errors=True)
        raise BridgeUnavailable(
            f"javac failed on {len(sources)} java/ source(s) (exit {proc.returncode})"
        )
    jar = work / "app-functions.jar"
    names = []
    with zipfile.ZipFile(jar, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\n\r\n")
        for f in sorted(classes.rglob("*.class")):
            rel = f.relative_to(classes).as_posix()
            z.write(f, rel)
            if "$" not in f.name:
                names.append(rel[: -len(".class")].replace("/", "."))
    shutil.rmtree(classes, ignore_errors=True)
    wanted = set(java_function_names(directory).values())
    return jar, [n for n in names if n in wanted]


# ------------------------------------------------------------------ JVM


def jvm_started() -> bool:
    try:
        import jpype
    except ImportError:
        return False
    return bool(jpype.isJVMStarted())


def start_jvm() -> None:
    """Start the one JVM of this process (no-op when it runs)."""
    try:
        import jpype
    except ImportError:
        raise BridgeUnavailable("JPype1 is not installed: run `uv sync`") from None
    if jpype.isJVMStarted():
        return
    home = java_home()
    jvm = libjvm(home)
    cp = classpath()
    jar, classes = compile_java(home=home, cp=cp)
    if jar is not None:
        _STATE["jar_dir"] = jar.parent
        atexit.register(shutil.rmtree, jar.parent, True)
        cp = cp + [str(jar)]
    _STATE["function_classes"] = classes
    tmp = tempfile.gettempdir()
    jpype.startJVM(
        jvm,
        f"-Djava.io.tmpdir={tmp}",
        "-Djava.awt.headless=true",
        "-Dfile.encoding=UTF-8",
        classpath=cp,
        convertStrings=False,
    )
    # warnings of the engines go to stderr; keep only severe ones
    with contextlib.suppress(Exception):
        logging = jpype.JPackage("java").util.logging
        logging.Logger.getLogger("").setLevel(logging.Level.SEVERE)


def jclass(name: str):
    import jpype

    start_jvm()
    return jpype.JClass(name)


def java_exception_class(exc: BaseException) -> str:
    """The Java exception class name of a JPype exception (or the Python type name)."""
    try:
        return str(exc.getClass().getName())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return type(exc).__name__


# ------------------------------------------------------------------ values


def _dt_from_local(v) -> dt.datetime:
    return dt.datetime(
        int(v.getYear()),
        int(v.getMonthValue()),
        int(v.getDayOfMonth()),
        int(v.getHour()),
        int(v.getMinute()),
        int(v.getSecond()),
        int(v.getNano()) // 1000,
    )


def py_value(v, unsigned_byte: bool = False):
    """A Java value (from Jackcess or JDBC) as a Python value; binary and complex values are None."""
    if v is None:
        return None
    if isinstance(v, (bool, int, float, str)) and not hasattr(v, "getClass"):
        return v
    cls = type(v).__name__
    if cls == "java.lang.String":
        return str(v)
    if cls == "java.lang.Boolean":
        return bool(v)
    if cls == "java.lang.Byte":
        n = int(v)
        return n & 0xFF if unsigned_byte else n
    if cls in (
        "java.lang.Short",
        "java.lang.Integer",
        "java.lang.Long",
        "java.math.BigInteger",
    ):
        return int(v)
    if cls in ("java.lang.Float", "java.lang.Double"):
        return float(v)
    if cls == "java.math.BigDecimal":
        return Decimal(str(v.toPlainString()))
    if cls == "java.time.LocalDateTime":
        return _dt_from_local(v)
    if cls == "java.sql.Timestamp":
        return _dt_from_local(v.toLocalDateTime())
    if cls == "java.time.LocalDate":
        return dt.datetime(
            int(v.getYear()), int(v.getMonthValue()), int(v.getDayOfMonth())
        )
    if cls == "java.sql.Date":
        return py_value(v.toLocalDate())
    if cls == "java.sql.Time":
        lt = v.toLocalTime()
        return dt.datetime(
            1899, 12, 30, int(lt.getHour()), int(lt.getMinute()), int(lt.getSecond())
        )
    if cls == "java.util.Date":
        inst = v.toInstant()
        zone = jclass("java.time.ZoneId").systemDefault()
        return _dt_from_local(jclass("java.time.LocalDateTime").ofInstant(inst, zone))
    if cls == "java.util.UUID":
        return str(v)
    if cls.startswith("[") or "Blob" in cls or "ComplexValue" in cls or cls == "byte[]":
        return None
    try:
        return str(v)
    except Exception:  # noqa: BLE001
        return None


def java_value(neutral: str, value):
    """A Python dataset value as the Java value Jackcess writes for a column of `neutral` type."""
    if value is None:
        return None
    import jpype

    start_jvm()
    if neutral in ("text", "memo", "hyperlink"):
        return jpype.JString(str(value))
    if neutral == "guid":
        s = str(value)
        return jpype.JString(s if s.startswith("{") else "{" + s.upper() + "}")
    if neutral in ("byte", "integer", "longinteger", "autonumber"):
        return jclass("java.lang.Integer")(int(value))
    if neutral in ("currency", "decimal"):
        return jclass("java.math.BigDecimal")(str(Decimal(value)))
    if neutral == "single":
        return jclass("java.lang.Float")(float(value))
    if neutral == "double":
        return jclass("java.lang.Double")(float(value))
    if neutral == "yesno":
        return jclass("java.lang.Boolean")(bool(value))
    if neutral == "datetime":
        if not isinstance(value, dt.datetime):
            value = dt.datetime(value.year, value.month, value.day)
        return jclass("java.time.LocalDateTime").of(
            value.year,
            value.month,
            value.day,
            value.hour,
            value.minute,
            value.second,
            value.microsecond * 1000,
        )
    return None


# ------------------------------------------------------------------ Jackcess


def open_database(path, read_only: bool = True):
    """A Jackcess Database opened with DatabaseBuilder."""
    start_jvm()
    builder = jclass("io.github.spannm.jackcess.DatabaseBuilder")
    File = jclass("java.io.File")
    b = builder(File(str(path))).withReadOnly(bool(read_only))
    if read_only:
        b = b.withAutoSync(False)
    return b.open()


def tables(db) -> list[str]:
    """User table names (no MSys tables), linked tables included."""
    out = []
    for n in db.getTableNames():
        name = str(n)
        if name.startswith(("MSys", "~")):
            continue
        out.append(name)
    return out


def link_connect(db, table: str) -> str | None:
    """The connect string of a linked table, or None for a local table."""
    md = db.getTableMetaData(table)
    if md is None or not md.isLinked():
        return None
    conn = md.getConnectionName()
    if conn is not None and str(conn):
        return str(conn)
    dbname = md.getLinkedDbName()
    return ";DATABASE=" + str(dbname) if dbname is not None else ";DATABASE="


_SIMPLE_TYPES = {
    "TEXT": "text",
    "BYTE": "byte",
    "INT": "integer",
    "MONEY": "currency",
    "FLOAT": "single",
    "DOUBLE": "double",
    "NUMERIC": "decimal",
    "SHORT_DATE_TIME": "datetime",
    "EXT_DATE_TIME": "datetime",
    "BOOLEAN": "yesno",
    "GUID": "guid",
    "OLE": "oleobject",
    "COMPLEX_TYPE": "complex",
    "BIG_INT": "longinteger",
}


def neutral_type(column) -> str:
    """The neutral type of a Jackcess Column (autonumber and hyperlink from the column flags)."""
    name = str(column.getType().name())
    if name == "LONG":
        return "autonumber" if column.isAutoNumber() else "longinteger"
    if name == "MEMO":
        return "hyperlink" if column.isHyperlink() else "memo"
    if name in _SIMPLE_TYPES:
        return _SIMPLE_TYPES[name]
    return f"unsupported:{name}"


def _required(column) -> bool:
    try:
        v = column.getProperties().getValue("Required")
        return bool(v) if v is not None else False
    except Exception:  # noqa: BLE001
        return False


def columns(db, table: str) -> list[Column]:
    t = db.getTable(table)
    out = []
    for c in t.getColumns():
        ntype = neutral_type(c)
        size = int(c.getLengthInUnits()) if ntype == "text" else None
        out.append(Column(str(c.getName()), ntype, size, _required(c)))
    return out


def iter_rows(db, table: str):
    """Rows as dicts keyed by column name (column order) of Python values."""
    t = db.getTable(table)
    cols = [(str(c.getName()), neutral_type(c)) for c in t.getColumns()]
    for row in t:
        out = {}
        for name, ntype in cols:
            if ntype in ("complex", "oleobject") or ntype.startswith("unsupported:"):
                out[name] = None
                continue
            out[name] = py_value(row.get(name), unsigned_byte=(ntype == "byte"))
        yield out


_QUERY_TYPES = {
    "SELECT": "select",
    "UNION": "union",
    "CROSS_TAB": "crosstab",
    "PASSTHROUGH": "pass-through",
    "MAKE_TABLE": "action",
    "APPEND": "action",
    "UPDATE": "action",
    "DELETE": "action",
    "DATA_DEFINITION": "action",
}


def queries(db) -> list[Query]:
    out = []
    for q in db.getQueries():
        name = str(q.getName())
        if name.startswith("~"):
            continue
        qtype = _QUERY_TYPES.get(str(q.getType().name()), "unknown")
        try:
            sql = str(q.toSQLString())
        except Exception:  # noqa: BLE001 - an unparsable design has no SQL
            sql = None
        out.append(Query(name, qtype, sql))
    return out


# ------------------------------------------------------------------ UCanAccess


def connect(path, read_only=True, memory: bool = True, new_version: str | None = None):
    """A UCanAccess JDBC connection. `new_version` (V2010, V2003) creates a missing file in that
    format. Called as connect(db, table) with a Jackcess Database, returns link_connect(db, table)."""
    if not isinstance(path, (str, os.PathLike)):
        return link_connect(path, read_only)
    start_jvm()
    path = Path(path).resolve()
    jclass("net.ucanaccess.jdbc.UcanaccessDriver")
    props = jclass("java.util.Properties")()
    props.setProperty("readOnly", "true" if read_only else "false")
    props.setProperty("memory", "true" if memory else "false")
    if new_version is not None:
        if new_version not in NEW_VERSIONS:
            raise ValueError(f"new_version must be one of {', '.join(NEW_VERSIONS)}")
        props.setProperty("newDatabaseVersion", new_version)
    elif not path.is_file():
        raise SourceError("database file missing")
    conn = jclass("java.sql.DriverManager").getConnection(
        f"jdbc:ucanaccess://{path}", props
    )
    for name in _STATE["function_classes"]:
        conn.addFunctions(jclass(name))
    return conn


__all__ = [
    "ENGINE",
    "BridgeUnavailable",
    "Column",
    "Query",
    "classpath",
    "columns",
    "compile_java",
    "connect",
    "iter_rows",
    "java_function_names",
    "javac_argv",
    "link_connect",
    "neutral_type",
    "open_database",
    "py_value",
    "queries",
    "start_jvm",
    "tables",
]
