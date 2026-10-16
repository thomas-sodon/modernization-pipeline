"""Canonical output form of a unit's result (docs/contracts.md#canonical-output, canonical_version 1).

Standard library only, so the harness can be vendored into a target repo. The golden recorder
(toolkit/golden.py) and the harness compare step both use it, so recorder and comparer format and
order rows the same way. Prose and one example per type: docs/formats/canonical-output.md.

- `format_value(ctype, value)`: one value as its canonical text (NULL is `\\N`).
- `canonical_rows(columns, rows, order_by=None)`: formatted rows in canonical order.
- `canonical_csv(columns, rows, order_by=None)`: the CSV bytes (UTF-8, RFC 4180, CRLF, header).
- `canonical_document(doc)`: the canonical JSON bytes of a document unit.
- `digest(data, key=None)`: sha256 hex, or HMAC-SHA256 hex when a key is given.
- `key_from_file(path)`: the engagement digest key, HMAC-SHA256(b"modpipe-golden-v1", file bytes).
- `file_safe(name)`: the file-safe id of a unit (docs/contracts.md#conventions).

A value that does not fit its declared type raises CanonicalError, whose message never names the
value (the harness reports it as a test error, never a silent cast).
"""

from __future__ import annotations

import datetime as dt
import decimal
import functools
import hashlib
import hmac
import json
import math
import re
import unicodedata
import uuid
from decimal import Decimal
from pathlib import Path

CANONICAL_VERSION = 1
NULL = "\\N"
KEY_LABEL = b"modpipe-golden-v1"

INT_TYPES = ("byte", "integer", "longinteger", "autonumber")
DECIMAL_TYPES = ("currency", "decimal")
FLOAT_DIGITS = {"single": 6, "double": 12}
TEXT_TYPES = ("text", "memo", "hyperlink")
TYPES = (
    INT_TYPES
    + DECIMAL_TYPES
    + tuple(FLOAT_DIGITS)
    + TEXT_TYPES
    + (
        "datetime",
        "yesno",
        "guid",
    )
)
NUMERIC_TYPES = INT_TYPES + DECIMAL_TYPES + tuple(FLOAT_DIGITS) + ("yesno",)

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_ESCAPED_NULL = re.compile(r"\\+N")
_INT_TEXT = re.compile(r"[+-]?\d+")
_DATETIME_TEXT = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,9}))?)?)?"
)
_GUID = re.compile(r"\{?([0-9A-Fa-f-]{32,36})\}?")
_YES = {"1", "-1", "true", "yes", "on"}
_NO = {"0", "false", "no", "off"}
_CTX = decimal.Context(prec=80, rounding=decimal.ROUND_HALF_EVEN)
_ACCESS_ZERO_DAY = dt.date(1899, 12, 30)


class CanonicalError(ValueError):
    """A value does not fit its declared type (the message never names the value)."""


# ------------------------------------------------------------------ names


def file_safe(name: str) -> str:
    """Characters outside [A-Za-z0-9._-] become _; when anything was replaced, '-' and the first
    8 hex digits of sha256(name in UTF-8) are appended."""
    safe = _UNSAFE.sub("_", name)
    if safe != name:
        safe += "-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return safe


# ------------------------------------------------------------------ values


def _check_finite(x: float) -> float:
    if math.isnan(x) or math.isinf(x):
        raise CanonicalError("NaN or infinity")
    return x


def _to_decimal(value, ctype: str) -> Decimal:
    if isinstance(value, bool):
        raise CanonicalError(f"boolean given for a {ctype} column")
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, float):
        d = Decimal(repr(_check_finite(value)))
    elif isinstance(value, str):
        try:
            d = Decimal(value.strip())
        except decimal.InvalidOperation:
            raise CanonicalError(
                f"text that is not a number in a {ctype} column"
            ) from None
    else:
        raise CanonicalError(f"{type(value).__name__} given for a {ctype} column")
    if not d.is_finite():
        raise CanonicalError("NaN or infinity")
    return d


def _plain(d: Decimal) -> str:
    """Plain notation, no exponent, trailing zeros and a trailing '.' removed, -0 as 0."""
    text = format(d, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("-0", ""):
        text = "0"
    return text


def _int_text(value, ctype: str) -> str:
    if isinstance(value, bool):
        raise CanonicalError(f"boolean given for a {ctype} column")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and _INT_TEXT.fullmatch(value.strip()):
        return str(int(value.strip()))
    d = _to_decimal(value, ctype)
    if d != d.to_integral_value():
        raise CanonicalError(f"fraction given for a {ctype} column")
    return str(int(d))


def _decimal_text(value, ctype: str) -> str:
    d = _to_decimal(value, ctype).quantize(Decimal("0.0001"), context=_CTX)
    text = format(d, "f")
    if text.startswith("-") and d == 0:
        text = text[1:]
    return text


def _float_text(value, ctype: str) -> str:
    d = _to_decimal(value, ctype)
    if d == 0:
        return "0"
    ctx = decimal.Context(prec=FLOAT_DIGITS[ctype], rounding=decimal.ROUND_HALF_EVEN)
    return _plain(ctx.plus(d))


def _round_second(value: dt.datetime) -> dt.datetime:
    if value.microsecond >= 500000:
        value = value + dt.timedelta(seconds=1)
    return value.replace(microsecond=0)


def _datetime_text(value) -> str:
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            raise CanonicalError("datetime with a time zone")
        d = _round_second(value)
    elif isinstance(value, dt.date):
        d = dt.datetime(value.year, value.month, value.day)
    elif isinstance(value, dt.time):
        if value.tzinfo is not None:
            raise CanonicalError("time with a time zone")
        d = _round_second(dt.datetime.combine(_ACCESS_ZERO_DAY, value))
    elif isinstance(value, str):
        m = _DATETIME_TEXT.fullmatch(value.strip())
        if not m:
            raise CanonicalError("text that is not a datetime in a datetime column")
        y, mo, da, h, mi, s, frac = m.groups()
        try:
            d = dt.datetime(
                int(y), int(mo), int(da), int(h or 0), int(mi or 0), int(s or 0)
            )
        except ValueError:
            raise CanonicalError("invalid datetime") from None
        if frac:
            d = _round_second(
                d + dt.timedelta(microseconds=round(float("0." + frac) * 1e6))
            )
    else:
        raise CanonicalError(f"{type(value).__name__} given for a datetime column")
    return d.strftime("%Y-%m-%dT%H:%M:%S")


def _yesno_text(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, Decimal, float)):
        if isinstance(value, float):
            _check_finite(value)
        if value not in (0, 1, -1):
            raise CanonicalError("number other than 0, 1 or -1 in a yesno column")
        return "0" if value == 0 else "1"
    if isinstance(value, str):
        s = value.strip().lower()
        if s in _YES:
            return "1"
        if s in _NO:
            return "0"
        raise CanonicalError("text that is not yes/no in a yesno column")
    raise CanonicalError(f"{type(value).__name__} given for a yesno column")


def _text(value, ctype: str) -> str:
    if not isinstance(value, str):
        raise CanonicalError(f"{type(value).__name__} given for a {ctype} column")
    text = unicodedata.normalize("NFC", value)
    if _ESCAPED_NULL.fullmatch(text):
        return "\\" + text
    return text


def _guid_text(value) -> str:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, str):
        m = _GUID.fullmatch(value.strip())
        if m:
            try:
                return str(uuid.UUID(m.group(1)))
            except ValueError:
                pass
    raise CanonicalError("value that is not a guid in a guid column")


def format_value(ctype: str, value) -> str:
    """The canonical text of one value of neutral type `ctype` (before CSV quoting)."""
    if ctype not in TYPES:
        raise CanonicalError(f"column type {ctype!r} has no canonical form")
    if value is None:
        return NULL
    if ctype in INT_TYPES:
        return _int_text(value, ctype)
    if ctype in DECIMAL_TYPES:
        return _decimal_text(value, ctype)
    if ctype in FLOAT_DIGITS:
        return _float_text(value, ctype)
    if ctype == "datetime":
        return _datetime_text(value)
    if ctype == "yesno":
        return _yesno_text(value)
    if ctype == "guid":
        return _guid_text(value)
    return _text(value, ctype)


def unescape(text: str) -> str | None:
    """The value a canonical text field stands for, as text: None for NULL, the backslash rule
    undone for text."""
    if text == NULL:
        return None
    if _ESCAPED_NULL.fullmatch(text):
        return text[1:]
    return text


# ------------------------------------------------------------------ rows


def _columns(columns) -> list[tuple[str, str]]:
    out = []
    for c in columns:
        if isinstance(c, dict):
            out.append((c["name"], c["type"]))
        else:
            name, ctype = c
            out.append((name, ctype))
    return out


def format_row(columns, row) -> tuple[str, ...]:
    cols = _columns(columns)
    values = list(row)
    if len(values) != len(cols):
        raise CanonicalError("row length differs from the column count")
    return tuple(format_value(t, v) for (_n, t), v in zip(cols, values))


def _typed_key(ctype: str, text: str):
    value = unescape(text)
    if value is None:
        return None
    if ctype in NUMERIC_TYPES:
        return Decimal(value)
    return value  # datetime canonical text sorts chronologically; text by code point


def _cmp(a, b) -> int:
    return (a > b) - (a < b)


def _order_compare(spec):
    def compare(ra, rb) -> int:
        fa, ka = ra
        fb, kb = rb
        for i, desc in spec:
            va, vb = ka[i], kb[i]
            if va is None and vb is None:
                c = 0
            elif va is None:
                c = -1
            elif vb is None:
                c = 1
            else:
                c = _cmp(va, vb)
            if desc:
                c = -c
            if c:
                return c
        return _cmp(fa, fb)

    return compare


def _order_spec(cols: list[tuple[str, str]], order_by) -> list[tuple[int, bool]]:
    index = {}
    for i, (name, _t) in enumerate(cols):
        index.setdefault(name, i)
    spec = []
    for o in order_by or []:
        if isinstance(o, dict):
            column, desc = o["column"], bool(o.get("desc"))
        else:
            column, desc = o
        if column not in index:
            raise CanonicalError("order_by names a column the unit does not output")
        spec.append((index[column], bool(desc)))
    return spec


def canonical_rows(columns, rows, order_by=None) -> list[tuple[str, ...]]:
    """Formatted rows in canonical order: by `order_by` ([{column, desc}]) with a typed compare
    (NULL first, numbers numerically, datetimes chronologically, text by code point of its NFC
    form; desc reverses the column, NULL position included), ties broken by the full row tuple;
    without order_by by the full row tuple (field strings, i.e. UTF-8 byte order)."""
    cols = _columns(columns)
    formatted = [format_row(cols, r) for r in rows]
    spec = _order_spec(cols, order_by)
    if not spec:
        return sorted(formatted)
    keyed = [
        (f, {i: _typed_key(cols[i][1], f[i]) for i, _d in spec}) for f in formatted
    ]
    keyed.sort(key=functools.cmp_to_key(_order_compare(spec)))
    return [f for f, _k in keyed]


def _quote(field: str) -> str:
    if field == "" or any(ch in field for ch in ',"\r\n'):
        return '"' + field.replace('"', '""') + '"'
    return field


def csv_record(fields) -> str:
    return ",".join(_quote(f) for f in fields) + "\r\n"


def canonical_csv(columns, rows, order_by=None) -> bytes:
    """UTF-8 CSV (RFC 4180, CRLF) with a header of the output column names."""
    cols = _columns(columns)
    out = [csv_record(unicodedata.normalize("NFC", n) for n, _t in cols)]
    out += [csv_record(r) for r in canonical_rows(cols, rows, order_by)]
    return "".join(out).encode("utf-8")


# ------------------------------------------------------------------ documents


def _leaf(value):
    if isinstance(value, dict):
        return {str(k): _leaf(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_leaf(v) for v in value]
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    raise CanonicalError(
        f"{type(value).__name__} as a document leaf: leaves are canonical strings"
    )


def canonical_document(doc: dict) -> bytes:
    """Canonical JSON of a document unit: one object per key, every leaf a canonical string,
    sorted keys, compact separators, a final newline."""
    if not isinstance(doc, dict):
        raise CanonicalError("a document is an object keyed by document key")
    for value in doc.values():
        if not isinstance(value, dict):
            raise CanonicalError("each document key holds one object")
    body = json.dumps(
        _leaf(doc), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return (body + "\n").encode("utf-8")


# ------------------------------------------------------------------ digests


def _bytes(data) -> bytes:
    return data.encode("utf-8") if isinstance(data, str) else bytes(data)


def digest(data, key: bytes | None = None) -> str:
    """sha256 hex of the canonical bytes, or HMAC-SHA256 hex when `key` is given."""
    if key is None:
        return hashlib.sha256(_bytes(data)).hexdigest()
    return hmac.new(_bytes(key), _bytes(data), hashlib.sha256).hexdigest()


def key_from_file(path) -> bytes:
    """The engagement digest key: HMAC-SHA256(key=b"modpipe-golden-v1", message=the file's
    bytes). Computed in memory; callers never print or write it."""
    mac = hmac.new(KEY_LABEL, digestmod=hashlib.sha256)
    with open(Path(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            mac.update(chunk)
    return mac.digest()
