"""Source readers. One module per source kind:

- `accdt`: Access templates (.accdt, an OPC zip of SaveAsText, XSD and AXL parts);
- `vcs_export`: msaccess-vcs-addin export folders;
- `accdb`: .accdb and .mdb files through the Jackcess bridge of S2.reference;
- `xlsm`: Excel macro-enabled workbooks.

Every reader exposes `tables()`, `iter_rows(table)` and `iter_texts()` for S0.dataset and S0.guard,
and `design()` (Access kinds) or `build_model()` (xlsm) for parse. Rows and cell values are only
ever reachable through `iter_rows` and `iter_texts`; nothing in the design path copies them.

`open_reader(eng)` picks the reader for `source.kind`.
"""

from __future__ import annotations

from pathlib import Path


class SourceError(Exception):
    """A source that cannot be read: exit 2. str() never carries source text."""

    exit_code = 2


class EncryptedSource(SourceError):
    """The source or a back end is encrypted or password-protected."""

    def __init__(self, what: str = "source"):
        self.what = what
        super().__init__("encrypted source: custom quote")


class BridgeMissing(SourceError):
    def __init__(self):
        super().__init__(
            "accdb and mdb sources need the Jackcess bridge from S2.reference"
        )


def decode_text(data: bytes, default: str = "cp1252") -> tuple[str, str, bool]:
    """Decode a text part. Returns (text, encoding, lossy).

    A BOM decides (UTF-16LE, UTF-16BE, UTF-8); UTF-16LE without a BOM is recognised by its zero
    bytes; anything else uses `default` (cp1252 for VBA modules). `lossy` is True when a byte had
    no mapping in cp1252 and was replaced."""
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le"), "utf-16le-bom", False
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be"), "utf-16be-bom", False
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), "utf-8-bom", False
    if len(data) >= 4 and data[1] == 0 and data[3] == 0 and data[0] != 0:
        return data.decode("utf-16-le"), "utf-16le", False
    try:
        return data.decode(default), default, False
    except UnicodeDecodeError:
        return data.decode(default, errors="replace"), default, True


def open_reader(eng: dict):
    """The reader for the engagement's source.kind (raises SourceError when missing)."""
    src = eng["source"]
    kind = src["kind"]
    path = Path(src["path"])
    if kind == "accdt":
        from toolkit.sources.accdt import AccdtReader

        return AccdtReader(path)
    if kind == "vcs-export":
        from toolkit.sources.vcs_export import VcsExportReader

        return VcsExportReader(path)
    if kind in ("accdb", "mdb"):
        from toolkit.sources.accdb import AccdbReader

        return AccdbReader(path, kind)
    if kind == "xlsm":
        from toolkit.sources.xlsm import XlsmReader

        return XlsmReader(path, eng)
    raise SourceError(f"source.kind {kind!r} has no reader")
