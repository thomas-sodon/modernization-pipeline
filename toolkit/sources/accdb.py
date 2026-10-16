"""Access database files (.accdb, .mdb) through the Jackcess bridge of S2.reference.

Only `toolkit.reference.bridge` is used, and only its `start_jvm`, `open_database`, `tables`,
`columns` (name, neutral type, size, required; type `unsupported:<DataType>` when it cannot map
one), `iter_rows`, `queries` and `connect`. Until S2.reference builds the bridge, every use
raises `BridgeMissing` and parse exits 2 with 'accdb and mdb sources need the Jackcess bridge
from S2.reference'. No Jackcess codec is pinned and the bridge takes no password, so an
encrypted or password-protected file raises `EncryptedSource` (exit 2, custom quote).
"""

from __future__ import annotations

from pathlib import Path

from toolkit.sources import BridgeMissing, EncryptedSource, SourceError
from toolkit.sources.access import AccessDesign

_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def bridge():
    try:
        from toolkit.reference import bridge as b  # S2.reference
    except ImportError:
        raise BridgeMissing() from None
    return b


def _is_encryption_error(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return any(w in name or w in text for w in ("encrypt", "password", "codec"))


def _open(path: Path):
    b = bridge()
    b.start_jvm()
    try:
        return b, b.open_database(str(path))
    except Exception as exc:  # noqa: BLE001 - Java exceptions surface through JPype
        if _is_encryption_error(exc):
            raise EncryptedSource() from None
        raise SourceError("the database cannot be opened") from None


def probe_encrypted(path: Path) -> bool:
    """True when a received back end is encrypted. Office-encrypted containers are recognised
    from their header; anything else is opened through the bridge when it exists."""
    path = Path(path)
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head == _CFB_MAGIC:
        try:
            import olefile

            with olefile.OleFileIO(str(path)) as ole:
                if ole.exists("EncryptionInfo"):
                    return True
        except OSError:
            return False
        return False
    if path.suffix.lower() not in (".accdb", ".mdb"):
        return False
    try:
        _open(path)
    except BridgeMissing:
        return False
    except EncryptedSource:
        return True
    except SourceError:
        return False
    return False


class AccdbReader:
    def __init__(self, path: Path, kind: str):
        self.path = Path(path)
        self.kind = kind
        if not self.path.is_file():
            raise SourceError("source file missing")
        if probe_encrypted(self.path):
            raise EncryptedSource()
        self.b, self.db = _open(self.path)

    def tables(self) -> list[str]:
        return sorted(self.b.tables(self.db))

    def iter_rows(self, table: str):
        yield from self.b.iter_rows(self.db, table)

    def iter_texts(self):
        for table in self.tables():
            for row in self.iter_rows(table):
                for value in row.values():
                    if isinstance(value, str) and value:
                        yield "data", table, value

    def design(self) -> AccessDesign:
        d = AccessDesign(source_kind=self.kind)
        for name in self.tables():
            connect = self.b.connect(self.db, name)
            if connect:
                d.linked_tables.append(
                    {"name": name, "connect": connect, "source_table": None}
                )
                continue
            columns = []
            for c in self.b.columns(self.db, name):
                ctype = c["type"]
                if str(ctype).startswith("unsupported:"):
                    d.error("unsupported-type", f"table:{name}", detail=str(ctype)[:60])
                    ctype = "complex"
                columns.append(
                    {
                        "name": c["name"],
                        "type": ctype,
                        "size": c.get("size"),
                        "precision": None,
                        "scale": None,
                        "required": bool(c.get("required")),
                    }
                )
            d.tables.append(
                {
                    "name": name,
                    "role": "data",
                    "columns": columns,
                    "primary_key": [],
                    "indexes": [],
                }
            )
        for q in self.b.queries(self.db):
            d.sql_queries.append((q["name"], q.get("sql") or ""))
        d.parts = {
            "tables": len(d.tables) + len(d.linked_tables),
            "queries": len(d.sql_queries),
        }
        return d


def compare_data_copy(path: Path, schema: dict) -> dict:
    """Compare design tables, columns and types with the data copy (per-table counts only)."""
    path = Path(path)
    if not path.is_file():
        raise SourceError("source.data_copy missing")
    if probe_encrypted(path):
        raise EncryptedSource("data copy")
    b, db = _open(path)
    copy_tables = set(b.tables(db))
    out, differences = [], []
    for t in schema["tables"]:
        name = t["name"]
        design_cols = {(c["name"], c["type"]) for c in t["columns"]}
        copy_cols = set()
        if name in copy_tables:
            copy_cols = {(c["name"], c["type"]) for c in b.columns(db, name)}
        out.append(
            {
                "table": name,
                "design_columns": len(design_cols),
                "copy_columns": len(copy_cols),
                "matching_columns": len(design_cols & copy_cols),
            }
        )
        if design_cols != copy_cols:
            differences.append(name)
    return {"tables": out, "differences": differences}
