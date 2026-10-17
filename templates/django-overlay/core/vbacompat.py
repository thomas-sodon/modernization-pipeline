"""Access and VBA built-in functions for ported queries and reports.

The functions behave as Access does with United States regional settings (the
settings the source applications were built with): dates m/d/yyyy, "$" currency,
"." decimal separator, "," thousands separator. Dates are naive datetimes; Access
stores a time without a date on 1899-12-30, and numbers convert to dates as day
serials counted from that date. None is Access's Null.

The Format behaviour (named formats, custom date, number and text format strings,
the choice of format type, Null handling and half-even rounding) is ported from the
expression engine of Jackcess 5.1.7 (io.github.spannm.jackcess.impl.expr,
FormatUtil, NumberFormatter, TemporalConfig and NumericConfig; Apache License 2.0,
see THIRD_PARTY.md), written anew in this module's own structure. Where Jackcess
keeps 'm' as month after an hour, this module follows Microsoft's documented
behaviour ("h:m:s" shows minutes).

Python names are lower case (trailing underscore where a builtin has the name);
FUNCTIONS maps the Access names to them, and lookup(name) also finds the
engagement's own functions in core/local/vbacompat_local.py FUNCTIONS. Excel
worksheet functions (WORKDAY, SUMIFS) are not here: they belong to the domain app.
"""
# Access date arithmetic is naive by definition; VBA's numeric constants are kept
# literal so each line reads like the behaviour it ports; the format engine's
# dispatch functions branch once per format code; S105 mistakes format codes compared
# with a variable named token for passwords.
# ruff: noqa: DTZ001, DTZ005, PLR2004, FBT001, FBT002, C901, PLR0911, PLR0912, S105

from __future__ import annotations

import calendar
import datetime as dt
import importlib
import math
import re
from decimal import ROUND_FLOOR
from decimal import ROUND_HALF_EVEN
from decimal import Context
from decimal import Decimal
from decimal import InvalidOperation
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from collections.abc import Callable

# ------------------------------------------------------------------ errors


class VBAError(Exception):
    """A VBA run-time error: number and description (never a value)."""

    def __init__(self, number: int, description: str):
        super().__init__(f"VBA error {number}: {description}")
        self.number = number
        self.description = description


INVALID_PROCEDURE_CALL = 5
OVERFLOW = 6
TYPE_MISMATCH = 13
INVALID_USE_OF_NULL = 94


def _null_error() -> VBAError:
    return VBAError(INVALID_USE_OF_NULL, "Invalid use of Null")


def _type_mismatch() -> VBAError:
    return VBAError(TYPE_MISMATCH, "Type mismatch")


# ------------------------------------------------------------ United States locale

ZERO_DATE = dt.datetime(1899, 12, 30)
MONTHS = [calendar.month_name[i] for i in range(1, 13)]
DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
SHORT_DATE = "m/d/yyyy"
LONG_DATE = "dddd, mmmm dd, yyyy"
MEDIUM_DATE = "dd-mmm-yy"
LONG_TIME = "h:nn:ss AM/PM"
MEDIUM_TIME = "hh:nn AM/PM"
SHORT_TIME = "hh:nn"
AM_PM = ("AM", "PM")

NAMED_DATE_FORMATS = {
    "long date": LONG_DATE,
    "medium date": MEDIUM_DATE,
    "short date": SHORT_DATE,
    "long time": LONG_TIME,
    "medium time": MEDIUM_TIME,
    "short time": SHORT_TIME,
}
NAMED_NUMBER_FORMATS = {
    "currency": "$#,##0.00;($#,##0.00)",
    "euro": "€#,##0.00;(€#,##0.00)",
    "fixed": "0.00",
    "standard": "#,##0.00",
    "percent": "0.00%",
    "scientific": "0.00E+00",
}
NAMED_BOOLEAN_FORMATS = {
    "yes/no": ("Yes", "No"),
    "true/false": ("True", "False"),
    "on/off": ("On", "Off"),
}

DOUBLE_DIGITS = 15  # significant digits Access shows for a Double
DECIMAL_DIGITS = 28  # and for a Decimal or Currency

# ------------------------------------------------------------------ conversions


def to_serial(value: dt.datetime) -> float:
    """The Access day serial of a datetime (negative serials keep a positive time)."""
    days = (value.date() - ZERO_DATE.date()).days
    seconds = value.hour * 3600 + value.minute * 60 + value.second
    frac = (seconds + value.microsecond / 1_000_000) / 86400
    return days + frac if days >= 0 else days - frac


def from_serial(serial: float | Decimal) -> dt.datetime:
    """The datetime of an Access day serial, to the second."""
    x = Decimal(repr(serial)) if isinstance(serial, float) else Decimal(serial)
    days = int(x)  # toward zero, as Access splits a negative serial
    frac = abs(x - days)
    seconds = int((frac * 86400).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
    return ZERO_DATE + dt.timedelta(days=days, seconds=seconds)


def _decimal(value: Any) -> Decimal:
    """A number as Decimal (Booleans are -1 and 0, dates their serial)."""
    if isinstance(value, bool):
        return Decimal(-1 if value else 0)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise _type_mismatch()
        return Decimal(repr(value))
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return Decimal(repr(to_serial(to_date(value))))
    if isinstance(value, str):
        text = value.strip()
        lowered = text.lower()
        if lowered in ("true", "false"):
            return Decimal(-1 if lowered == "true" else 0)
        try:
            return Decimal(text.replace(",", "").replace("$", ""))
        except InvalidOperation:
            raise _type_mismatch() from None
    raise _type_mismatch()


def _parse_date_text(text: str) -> dt.datetime | None:
    """A date and/or time written in one of the forms Access accepts (US order)."""
    text = " ".join(text.strip().split())
    if not text:
        return None
    text = re.sub(r"^(\d{4}-\d{1,2}-\d{1,2})T", r"\1 ", text)
    m = re.fullmatch(
        r"(?P<date>.*?)\s*(?P<time>\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AaPp][Mm]?)?)?",
        text,
    )
    date_part = (m.group("date") or "").strip() if m else text
    time_part = (m.group("time") or "").strip() if m else ""
    day = _parse_date_part(date_part) if date_part else ZERO_DATE.date()
    if day is None:
        return None
    clock = _parse_time_part(time_part) if time_part else dt.time()
    if clock is None:
        return None
    return dt.datetime.combine(day, clock)


def _full_year(year: int, digits: int) -> int:
    if digits <= 2:
        return year + (2000 if year < 30 else 1900)
    return year


def _month_number(name: str) -> int | None:
    name = name.lower().rstrip(".")
    for i, month in enumerate(MONTHS, start=1):
        if month.lower() == name or month[:3].lower() == name:
            return i
    return None


def _parse_date_part(text: str) -> dt.date | None:
    text = text.strip().rstrip(",")
    patterns = (
        (r"(\d{4})-(\d{1,2})-(\d{1,2})", ("y", "m", "d")),
        (r"(\d{1,2})[/-](\d{1,2})[/-](\d{1,4})", ("m", "d", "y")),
        (r"(\d{1,2})[/-](\d{1,2})", ("m", "d")),
        (r"(\d{1,2})[\s-]([A-Za-z]+)\.?[\s,-]*(\d{1,4})", ("d", "M", "y")),
        (r"([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{1,4})", ("M", "d", "y")),
        (r"[A-Za-z]+,\s*([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{1,4})", ("M", "d", "y")),
    )
    for pattern, order in patterns:
        m = re.fullmatch(pattern, text)
        if not m:
            continue
        parts = dict(zip(order, m.groups(), strict=True))
        if "M" in parts:
            month = _month_number(parts["M"])
            if month is None:
                continue
        else:
            month = int(parts["m"])
        year_text = parts.get("y")
        year = (
            _full_year(int(year_text), len(year_text))
            if year_text
            else dt.datetime.now().year
        )
        try:
            return dt.date(year, month, int(parts["d"]))
        except ValueError:
            return None
    return None


def _parse_time_part(text: str) -> dt.time | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp])?[Mm]?", text.strip())
    if not m:
        return None
    hour, minute, second = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    marker = (m.group(4) or "").lower()
    if marker:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if marker == "p" else 0)
    try:
        return dt.time(hour, minute, second)
    except ValueError:
        return None


def to_date(value: Any) -> dt.datetime:
    """A value as a datetime (CDate without the Null check)."""
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day)
    if isinstance(value, dt.time):
        return dt.datetime.combine(ZERO_DATE.date(), value)
    if isinstance(value, str):
        parsed = _parse_date_text(value)
        if parsed is not None:
            return parsed
        try:
            return from_serial(_decimal(value))
        except VBAError:
            raise _type_mismatch() from None
    if isinstance(value, (bool, int, float, Decimal)):
        return from_serial(_decimal(value))
    raise _type_mismatch()


def _date_kind(value: dt.datetime) -> str:
    """'date', 'time' or 'datetime', as Access decides from a stored value."""
    has_time = value.time() != dt.time()
    if value.date() == ZERO_DATE.date() and has_time:
        return "time"
    if not has_time:
        return "date"
    return "datetime"


# ------------------------------------------------------------------ number text


def _plain(d: Decimal) -> str:
    if d == 0:
        return "0"
    text = format(d.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def number_text(value: Any) -> str:
    """How Access writes a number as text (CStr, General Number): a Double rounded
    half-even to 15 significant digits (a Decimal to 28), in plain notation unless
    that needs more digits than that, then as 1.5E+20."""
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    digits = DECIMAL_DIGITS if isinstance(value, Decimal) else DOUBLE_DIGITS
    d = Context(prec=digits, rounding=ROUND_HALF_EVEN).plus(_decimal(value))
    if d == 0:
        return "0"
    d = d.normalize()
    sign, figures, exponent = d.as_tuple()
    assert isinstance(exponent, int)
    needed = len(figures)
    if exponent > 0:
        needed += exponent
    elif -exponent > len(figures):
        needed = -exponent
    if needed > digits:
        power = d.adjusted()
        mantissa = _plain(abs(d).scaleb(-power))
        signs = ("-" if sign else "", "+" if power >= 0 else "-")
        return f"{signs[0]}{mantissa}E{signs[1]}{abs(power):02d}"
    return _plain(d)


def _quantize(d: Decimal, places: int) -> Decimal:
    return d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)


# ------------------------------------------------------------------ Format: sections

_DATE_CHARS = set(":/cdwmqyhnstampmAMPM")
_NUMBER_CHARS = set(".,0#%Ee")
_TEXT_CHARS = set("@&<>")
_GENERAL_CHARS = set('"!*\\[];')


def _format_type(fmt: str) -> str:
    """'date', 'number', 'text' or 'general': the first format character decides."""
    i = 0
    while i < len(fmt):
        c = fmt[i]
        if c == '"':
            end = fmt.find('"', i + 1)
            i = len(fmt) if end < 0 else end + 1
            continue
        if c == "[":
            end = fmt.find("]", i + 1)
            i = len(fmt) if end < 0 else end + 1
            continue
        if c in "\\*":
            i += 2
            continue
        if c in _DATE_CHARS:
            return "date"
        if c in _NUMBER_CHARS:
            return "number"
        if c in _TEXT_CHARS:
            return "text"
        i += 1
    return "general"


def _split_sections(fmt: str) -> list[str]:
    sections: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(fmt):
        c = fmt[i]
        if c == '"':
            end = fmt.find('"', i + 1)
            end = len(fmt) - 1 if end < 0 else end
            current.append(fmt[i : end + 1])
            i = end + 1
            continue
        if c in "\\*" and i + 1 < len(fmt):
            current.append(fmt[i : i + 2])
            i += 2
            continue
        if c == ";":
            sections.append("".join(current))
            current = []
        else:
            current.append(c)
        i += 1
    sections.append("".join(current))
    return sections


def _literal_text(section: str) -> str:
    """A section without placeholders, as text (quotes and escapes removed)."""
    out: list[str] = []
    i = 0
    while i < len(section):
        c = section[i]
        if c == '"':
            end = section.find('"', i + 1)
            end = len(section) if end < 0 else end
            out.append(section[i + 1 : end])
            i = end + 1
        elif c == "\\" and i + 1 < len(section):
            out.append(section[i + 1])
            i += 2
        elif c == "*" and i + 1 < len(section):
            i += 2
        elif c == "[":
            end = section.find("]", i + 1)
            i = len(section) if end < 0 else end + 1
        elif c == "!":
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


# ------------------------------------------------------------------ Format: numbers


def _number_tokens(section: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    seen_point = False
    i = 0
    while i < len(section):
        c = section[i]
        nxt = section[i + 1] if i + 1 < len(section) else ""
        if c == '"':
            end = section.find('"', i + 1)
            end = len(section) if end < 0 else end
            tokens.append(("lit", section[i + 1 : end]))
            i = end + 1
            continue
        if c == "\\" and nxt:
            tokens.append(("lit", nxt))
            i += 2
            continue
        if c == "*" and nxt:
            i += 2
            continue
        if c == "[":
            end = section.find("]", i + 1)
            i = len(section) if end < 0 else end + 1
            continue
        if c == "!":
            i += 1
            continue
        if c in "0#":
            tokens.append(("digit", c))
        elif c == "." and not seen_point:
            tokens.append(("point", c))
            seen_point = True
        elif c == ",":
            tokens.append(("comma", c))
        elif c == "%":
            tokens.append(("percent", c))
        elif c in "Ee" and nxt in "+-" and nxt:
            tokens.append(("exp", c + nxt))
            i += 2
            continue
        else:
            tokens.append(("lit", c))
        i += 1
    return tokens


class _NumberSection:
    """One parsed section of a custom number format."""

    def __init__(self, section: str):
        self.tokens = _number_tokens(section)
        kinds = [k for k, _v in self.tokens]
        self.has_digits = "digit" in kinds
        exp_at = kinds.index("exp") if "exp" in kinds else len(kinds)
        point_at = kinds.index("point") if "point" in kinds else exp_at
        self.exp_at, self.point_at = exp_at, point_at
        self.int_slots = [
            i for i, (k, _v) in enumerate(self.tokens[:point_at]) if k == "digit"
        ]
        self.dec_slots = [
            i
            for i, (k, _v) in enumerate(self.tokens)
            if k == "digit" and point_at < i < exp_at
        ]
        self.exp_slots = [
            i for i, (k, _v) in enumerate(self.tokens) if k == "digit" and i > exp_at
        ]
        self.percent = kinds.count("percent")
        last_int = self.int_slots[-1] if self.int_slots else -1
        first_int = self.int_slots[0] if self.int_slots else -1
        self.grouping = any(
            k == "comma" and first_int < i < last_int
            for i, (k, _v) in enumerate(self.tokens)
        )
        self.scale = sum(
            1
            for i, (k, _v) in enumerate(self.tokens[:point_at])
            if k == "comma" and i > last_int >= 0
        )
        self.decimals = len(self.dec_slots)
        dec_chars = [self.tokens[i][1] for i in self.dec_slots]
        self.min_decimals = (
            max(i + 1 for i, ch in enumerate(dec_chars) if ch == "0")
            if "0" in dec_chars
            else 0
        )
        self.min_int = sum(1 for i in self.int_slots if self.tokens[i][1] == "0")

    def scaled(self, d: Decimal) -> Decimal:
        return d * (Decimal(100) ** self.percent) / (Decimal(1000) ** self.scale)

    def rounds_to_zero(self, d: Decimal) -> bool:
        if self.exp_at < len(self.tokens):
            return False
        return _quantize(self.scaled(d), self.decimals) == 0

    def render(self, d: Decimal) -> str:
        """The section for a non-negative value."""
        if not self.has_digits:
            return _literal_text_tokens(self.tokens)
        d = self.scaled(d)
        exponent = None
        if self.exp_at < len(self.tokens):
            d, exponent = self._mantissa(d)
        d = _quantize(d, self.decimals)
        int_part = int(d)
        int_digits = str(int_part) if int_part else ""
        int_digits = int_digits.rjust(self.min_int, "0")
        frac = format(d - int_part, "f").partition(".")[2].ljust(self.decimals, "0")
        frac = frac[: self.decimals]
        while len(frac) > self.min_decimals and frac.endswith("0"):
            frac = frac[:-1]
        return self._emit(int_digits, frac, exponent)

    def _mantissa(self, d: Decimal) -> tuple[Decimal, int]:
        n_int = max(len(self.int_slots), 1)
        if d == 0:
            return d, 0
        exponent = d.adjusted() - (n_int - 1)
        mantissa = _quantize(d.scaleb(-exponent), self.decimals)
        if mantissa >= Decimal(10) ** n_int:
            exponent += 1
            mantissa = _quantize(d.scaleb(-exponent), self.decimals)
        return mantissa, exponent

    def _int_text(self, digits: str) -> dict[int, str]:
        """Text per integer slot index."""
        slots = self.int_slots
        if not slots:
            return {}
        if self.grouping:
            grouped = f"{int(digits):,}" if digits else ""
            if digits and len(digits) > len(str(int(digits))):
                grouped = digits[: len(digits) - len(str(int(digits)))] + grouped
            out = dict.fromkeys(slots, "")
            out[slots[0]] = grouped
            return out
        out = {}
        rest = digits
        for slot in reversed(slots[1:]):
            out[slot] = rest[-1:] if rest else ""
            rest = rest[:-1]
        out[slots[0]] = rest
        return out

    def _emit(self, int_digits: str, frac: str, exponent: int | None) -> str:
        per_slot = self._int_text(int_digits)
        out: list[str] = []
        dec_index = 0
        for i, (kind, value) in enumerate(self.tokens):
            if kind == "lit":
                out.append(value)
            elif kind == "percent":
                out.append("%")
            elif kind == "point":
                if self.decimals:
                    out.append(".")
            elif kind == "digit" and i in per_slot:
                out.append(per_slot[i])
            elif kind == "digit" and i in self.dec_slots:
                out.append(frac[dec_index] if dec_index < len(frac) else "")
                dec_index += 1
            elif kind == "exp" and exponent is not None:
                sign = "-" if exponent < 0 else ("+" if value[1] == "+" else "")
                width = sum(1 for s in self.exp_slots if self.tokens[s][1] == "0")
                out.append(f"{value[0]}{sign}{str(abs(exponent)).rjust(width, '0')}")
        return "".join(out)


def _literal_text_tokens(tokens: list[tuple[str, str]]) -> str:
    return "".join(
        v if k in ("lit", "percent") else "" for k, v in tokens if k != "digit"
    )


def _format_number(value: Any, fmt: str, general: bool = False) -> Any:
    sections = _split_sections(fmt)[:4]
    while len(sections) < 4:
        sections.append("")
    if value is None:
        null = sections[3]
        if general or not _NumberSection(null).has_digits:
            return _literal_text(null)
        return _NumberSection(null).render(Decimal(0))
    try:
        d = _decimal(value)
    except VBAError:
        return value
    pos = _NumberSection(sections[0])
    zero = _NumberSection(sections[2]) if sections[2] else pos
    if general:
        texts = [_literal_text(s) for s in sections[:3]]
        if d > 0:
            return texts[0]
        if d < 0:
            return texts[1] if sections[1] else "-" + texts[0]
        return texts[2] if sections[2] else texts[0]
    if d > 0:
        return zero.render(Decimal(0)) if pos.rounds_to_zero(d) else pos.render(d)
    if d < 0:
        neg = _NumberSection(sections[1]) if sections[1] else None
        fmt_neg = neg or pos
        if fmt_neg.rounds_to_zero(-d):
            return zero.render(Decimal(0))
        return neg.render(-d) if neg else "-" + pos.render(-d)
    return zero.render(Decimal(0))


# ------------------------------------------------------------------ Format: dates

_DATE_TOKENS = (
    "ttttt",
    "dddddd",
    "ddddd",
    "dddd",
    "ddd",
    "dd",
    "d",
    "ww",
    "w",
    "mmmm",
    "mmm",
    "mm",
    "m",
    "q",
    "yyyy",
    "yy",
    "y",
    "hh",
    "h",
    "nn",
    "n",
    "ss",
    "s",
    "c",
)
_AMPM_TOKENS = ("AM/PM", "am/pm", "A/P", "a/p", "AMPM")


def _date_tokens(fmt: str) -> list[tuple[str, str]]:
    section = _split_sections(fmt)[0]
    tokens: list[tuple[str, str]] = []
    i = 0
    while i < len(section):
        c = section[i]
        nxt = section[i + 1] if i + 1 < len(section) else ""
        if c == '"':
            end = section.find('"', i + 1)
            end = len(section) if end < 0 else end
            tokens.append(("lit", section[i + 1 : end]))
            i = end + 1
            continue
        if c == "\\" and nxt:
            tokens.append(("lit", nxt))
            i += 2
            continue
        if c == "*" and nxt:
            i += 2
            continue
        if c == "[":
            end = section.find("]", i + 1)
            i = len(section) if end < 0 else end + 1
            continue
        ampm = next((t for t in _AMPM_TOKENS if section.startswith(t, i)), None)
        if ampm:
            tokens.append(("ampm", ampm))
            i += len(ampm)
            continue
        lowered = section[i:].lower()
        token = next((t for t in _DATE_TOKENS if lowered.startswith(t)), None)
        if token:
            tokens.append(("date", token))
            i += len(token)
            continue
        tokens.append(("lit", c))
        i += 1
    return _minutes(tokens)


def _minutes(tokens: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """'m' and 'mm' right after an hour or right before seconds are minutes."""
    out = list(tokens)
    fields = [i for i, (k, _v) in enumerate(out) if k == "date"]
    for pos, i in enumerate(fields):
        value = out[i][1]
        if value not in ("m", "mm"):
            continue
        before = out[fields[pos - 1]][1] if pos > 0 else ""
        after = out[fields[pos + 1]][1] if pos + 1 < len(fields) else ""
        if before in ("h", "hh") or after in ("s", "ss"):
            out[i] = ("date", "n" if value == "m" else "nn")
    return out


def _ampm(value: dt.datetime, token: str) -> str:
    pm = value.hour >= 12
    if token == "AMPM":
        return AM_PM[pm]
    am_text, pm_text = token.split("/")
    return pm_text if pm else am_text


def _date_field(
    value: dt.datetime,
    token: str,
    twelve_hour: bool,
    firstdayofweek: int,
    firstweekofyear: int,
) -> str:
    hour = value.hour
    if twelve_hour:
        hour = hour % 12 or 12
    simple = {
        "d": str(value.day),
        "dd": f"{value.day:02d}",
        "ddd": DAYS[weekday(value) - 1][:3],
        "dddd": DAYS[weekday(value) - 1],
        "m": str(value.month),
        "mm": f"{value.month:02d}",
        "mmm": MONTHS[value.month - 1][:3],
        "mmmm": MONTHS[value.month - 1],
        "q": str((value.month - 1) // 3 + 1),
        "y": str(value.timetuple().tm_yday),
        "yy": f"{value.year % 100:02d}",
        "yyyy": f"{value.year:04d}",
        "h": str(hour),
        "hh": f"{hour:02d}",
        "n": str(value.minute),
        "nn": f"{value.minute:02d}",
        "s": str(value.second),
        "ss": f"{value.second:02d}",
    }
    if token in simple:
        return simple[token]
    if token == "w":
        return str(weekday(value, firstdayofweek))
    if token == "ww":
        return str(_week_of_year(value, firstdayofweek, firstweekofyear))
    if token == "ddddd":
        return _format_date(value, SHORT_DATE)
    if token == "dddddd":
        return _format_date(value, LONG_DATE)
    if token == "ttttt":
        return _format_date(value, LONG_TIME)
    return general_date(value)  # "c"


def _format_date(
    value: dt.datetime,
    fmt: str,
    firstdayofweek: int = 1,
    firstweekofyear: int = 1,
) -> str:
    tokens = _date_tokens(fmt)
    twelve_hour = any(k == "ampm" for k, _v in tokens)
    out = []
    for kind, token in tokens:
        if kind == "lit":
            out.append(token)
        elif kind == "ampm":
            out.append(_ampm(value, token))
        else:
            out.append(
                _date_field(value, token, twelve_hour, firstdayofweek, firstweekofyear),
            )
    return "".join(out)


def general_date(value: dt.datetime) -> str:
    """General Date: the date, the time, or both, as the value holds them."""
    kind = _date_kind(value)
    if kind == "date":
        return _format_date(value, SHORT_DATE)
    if kind == "time":
        return _format_date(value, LONG_TIME)
    return f"{_format_date(value, SHORT_DATE)} {_format_date(value, LONG_TIME)}"


# ------------------------------------------------------------------ Format: text


def _format_text(value: Any, fmt: str) -> str:
    sections = _split_sections(fmt)
    text = "" if value is None else cstr(value)
    if text == "":
        return _format_text_section("", sections[1]) if len(sections) > 1 else ""
    return _format_text_section(text, sections[0])


def _format_text_section(text: str, section: str) -> str:
    parts: list[tuple[str, str]] = []
    case = ""
    right_aligned = True
    i = 0
    while i < len(section):
        c = section[i]
        nxt = section[i + 1] if i + 1 < len(section) else ""
        if c == '"':
            end = section.find('"', i + 1)
            end = len(section) if end < 0 else end
            parts.append(("lit", section[i + 1 : end]))
            i = end + 1
            continue
        if c == "\\" and nxt:
            parts.append(("lit", nxt))
            i += 2
            continue
        if c == "!":
            right_aligned = False
        elif c in "@&":
            parts.append(("slot", c))
        elif c == ">":
            case = "" if case == "lower" else "upper"
        elif c == "<":
            case = "" if case == "upper" else "lower"
        else:
            parts.append(("lit", c))
        i += 1
    if case == "upper":
        text = text.upper()
    elif case == "lower":
        text = text.lower()
    slots = sum(1 for k, _v in parts if k == "slot")
    pad = slots - len(text) if right_aligned and slots > len(text) else 0
    pos = len(text) - slots if not right_aligned and slots < len(text) else 0
    out: list[str] = []
    for kind, value in parts:
        if kind == "lit":
            out.append(value)
            continue
        if pad:
            pad -= 1
            out.append(" " if value == "@" else "")
        elif pos < len(text):
            out.append(text[pos])
            pos += 1
        else:
            out.append(" " if value == "@" else "")
    out.append(text[pos:])
    return "".join(out)


# ------------------------------------------------------------------ Format


def format_(
    value: Any,
    fmt: str | None = None,
    firstdayofweek: int = 1,
    firstweekofyear: int = 1,
) -> Any:
    """Format(expression, [format], [firstdayofweek], [firstweekofyear]).

    Null gives "" (or a number format's fourth section); a value that cannot take
    the format comes back unchanged."""
    fmt = fmt or ""
    named = fmt.strip().lower()
    if (
        named in NAMED_BOOLEAN_FORMATS
        or named in NAMED_NUMBER_FORMATS
        or (named in NAMED_DATE_FORMATS or named in ("general date", "general number"))
    ):
        return _format_named(value, named)
    if not fmt:
        return "" if value is None else cstr(value)
    kind = _format_type(fmt)
    if kind == "date":
        if value is None or value == "":
            return ""
        try:
            moment = to_date(value)
        except VBAError:
            return value
        return _format_date(moment, fmt, firstdayofweek, firstweekofyear)
    if kind == "number":
        return _format_number(value, fmt)
    if kind == "text":
        return _format_text(value, fmt)
    return _format_number(value, fmt, general=True)


def _format_named(value: Any, named: str) -> Any:
    if value is None or value == "":
        return ""
    try:
        if named in NAMED_BOOLEAN_FORMATS:
            yes, no = NAMED_BOOLEAN_FORMATS[named]
            return yes if _decimal(value) != 0 else no
        if named == "general number":
            plain = isinstance(value, (int, float, Decimal))
            return number_text(
                value if plain and not isinstance(value, bool) else _decimal(value),
            )
        if named in NAMED_NUMBER_FORMATS:
            return _format_number(value, NAMED_NUMBER_FORMATS[named])
        moment = to_date(value)
    except VBAError:
        return value
    if named == "general date":
        return general_date(moment)
    return _format_date(moment, NAMED_DATE_FORMATS[named])


# ------------------------------------------------------------------ Null helpers


def nz(value: Any, value_if_null: Any = "") -> Any:
    """Nz(variant, [valueifnull]): in a query, Null without a second argument is ""."""
    return value_if_null if value is None else value


def iif(condition: Any, truepart: Any, falsepart: Any) -> Any:
    """IIf(expr, truepart, falsepart): Null counts as False."""
    if condition is None:
        return falsepart
    if isinstance(condition, str):
        condition = _decimal(condition)
    return truepart if condition else falsepart


# ------------------------------------------------------------------ conversions (C...)


def _round_half_even(d: Decimal) -> int:
    return int(d.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def cstr(value: Any) -> str:
    """CStr: dates as General Date, numbers as Access writes them."""
    if value is None:
        raise _null_error()
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return general_date(to_date(value))
    if isinstance(value, (int, float, Decimal)):
        return number_text(value)
    return str(value)


def _integer(value: Any, low: int, high: int) -> int:
    if value is None:
        raise _null_error()
    n = _round_half_even(_decimal(value))
    if not low <= n <= high:
        raise VBAError(OVERFLOW, "Overflow")
    return n


def cint(value: Any) -> int:
    """CInt: half-even rounding to an Integer (-32768..32767)."""
    return _integer(value, -32768, 32767)


def clng(value: Any) -> int:
    """CLng: half-even rounding to a Long."""
    return _integer(value, -(2**31), 2**31 - 1)


def cdbl(value: Any) -> float:
    if value is None:
        raise _null_error()
    return float(_decimal(value))


def ccur(value: Any) -> Decimal:
    """CCur: a Currency, four decimal places, half-even."""
    if value is None:
        raise _null_error()
    d = _quantize(_decimal(value), 4)
    if abs(d) > Decimal("922337203685477.5807"):
        raise VBAError(OVERFLOW, "Overflow")
    return d


def cdate(value: Any) -> dt.datetime:
    if value is None:
        raise _null_error()
    return to_date(value)


def _same_type(original: Any, d: Decimal) -> Any:
    if isinstance(original, bool):
        return int(d)
    if isinstance(original, int):
        return int(d)
    if isinstance(original, Decimal):
        return d
    return float(d)


def int_(value: Any) -> Any:
    """Int: the largest whole number not above the value (Int(-8.4) is -9)."""
    if value is None:
        return None
    return _same_type(value, _decimal(value).to_integral_value(rounding=ROUND_FLOOR))


def fix(value: Any) -> Any:
    """Fix: the whole part, toward zero (Fix(-8.4) is -8)."""
    if value is None:
        return None
    d = _decimal(value)
    return _same_type(value, Decimal(int(d)))


def round_(value: Any, places: int = 0) -> Any:
    """Round(expression, [numdecimalplaces]): banker's rounding, as VBA does."""
    if value is None:
        return None
    if places is None or int(places) < 0:
        raise VBAError(INVALID_PROCEDURE_CALL, "Invalid procedure call or argument")
    return _same_type(value, _quantize(_decimal(value), int(places)))


# ------------------------------------------------------------------ dates


def date() -> dt.datetime:
    """Date(): today, at midnight."""
    today = dt.datetime.now()
    return dt.datetime(today.year, today.month, today.day)


def now() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


def time() -> dt.datetime:
    """Time(): the time of day on 1899-12-30."""
    clock = dt.datetime.now().replace(microsecond=0).time()
    return dt.datetime.combine(ZERO_DATE.date(), clock)


def datevalue(value: Any) -> dt.datetime | None:
    """DateValue: the date part (midnight)."""
    if value is None:
        return None
    moment = to_date(value)
    return dt.datetime(moment.year, moment.month, moment.day)


def timevalue(value: Any) -> dt.datetime | None:
    """TimeValue: the time part, on 1899-12-30."""
    if value is None:
        return None
    return dt.datetime.combine(ZERO_DATE.date(), to_date(value).time())


def _two_digit_year(year: int) -> int:
    if 0 <= year <= 29:
        return year + 2000
    if 30 <= year <= 99:
        return year + 1900
    return year


def dateserial(year: Any, month: Any, day: Any) -> dt.datetime | None:
    """DateSerial: months and days outside their range roll over (day 0 is the last
    day of the month before)."""
    if year is None or month is None or day is None:
        return None
    y = _two_digit_year(cint(year))
    m, d = cint(month), cint(day)
    total = y * 12 + (m - 1)
    first = dt.datetime(total // 12, total % 12 + 1, 1)
    return first + dt.timedelta(days=d - 1)


def _add_months(value: dt.datetime, months: int) -> dt.datetime:
    total = value.year * 12 + (value.month - 1) + months
    year, month = total // 12, total % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


_INTERVALS = ("yyyy", "q", "m", "y", "d", "w", "ww", "h", "n", "s")


def _interval(interval: str) -> str:
    key = (interval or "").strip().lower()
    if key not in _INTERVALS:
        raise VBAError(INVALID_PROCEDURE_CALL, "Invalid procedure call or argument")
    return key


def dateadd(interval: str, number: Any, value: Any) -> dt.datetime | None:
    """DateAdd: month steps keep the day, or the month's last day when shorter."""
    if number is None or value is None:
        return None
    key = _interval(interval)
    n = clng(number)
    moment = to_date(value)
    months = {"yyyy": 12, "q": 3, "m": 1}
    if key in months:
        return _add_months(moment, n * months[key])
    steps = {
        "y": dt.timedelta(days=1),
        "d": dt.timedelta(days=1),
        "w": dt.timedelta(days=1),
        "ww": dt.timedelta(weeks=1),
        "h": dt.timedelta(hours=1),
        "n": dt.timedelta(minutes=1),
        "s": dt.timedelta(seconds=1),
    }
    return moment + steps[key] * n


def _week_start(value: dt.date, firstdayofweek: int) -> dt.date:
    offset = (weekday(value) - _first_day(firstdayofweek)) % 7
    return value - dt.timedelta(days=offset)


def _first_day(firstdayofweek: int | None) -> int:
    first = int(firstdayofweek or 1)
    if not 1 <= first <= 7:
        raise VBAError(INVALID_PROCEDURE_CALL, "Invalid procedure call or argument")
    return first


def datediff(
    interval: str,
    date1: Any,
    date2: Any,
    firstdayofweek: int = 1,
    firstweekofyear: int = 1,
) -> int | None:
    """DateDiff: the number of interval boundaries crossed from date1 to date2."""
    del firstweekofyear  # no effect on a difference
    if date1 is None or date2 is None:
        return None
    key = _interval(interval)
    a, b = to_date(date1), to_date(date2)
    if key == "yyyy":
        return b.year - a.year
    if key == "q":
        return (b.year * 4 + (b.month - 1) // 3) - (a.year * 4 + (a.month - 1) // 3)
    if key == "m":
        return (b.year * 12 + b.month) - (a.year * 12 + a.month)
    days = (b.date() - a.date()).days
    if key in ("y", "d"):
        return days
    if key == "w":
        return int(days / 7)
    if key == "ww":
        start_a = _week_start(a.date(), firstdayofweek)
        start_b = _week_start(b.date(), firstdayofweek)
        return (start_b - start_a).days // 7
    hours = days * 24 + (b.hour - a.hour)
    if key == "h":
        return hours
    minutes = hours * 60 + (b.minute - a.minute)
    if key == "n":
        return minutes
    return minutes * 60 + (b.second - a.second)


def _week_of_year(value: dt.datetime, firstdayofweek: int, firstweekofyear: int) -> int:
    rule = int(firstweekofyear or 1)
    day = value.date()

    def week_one(year: int) -> dt.date:
        jan1 = dt.date(year, 1, 1)
        if rule == 2:  # vbFirstFourDays
            return _week_start(dt.date(year, 1, 4), firstdayofweek)
        if rule == 3:  # vbFirstFullWeek
            start = _week_start(jan1, firstdayofweek)
            return start if start == jan1 else start + dt.timedelta(days=7)
        return _week_start(jan1, firstdayofweek)  # vbFirstJan1

    start = week_one(day.year)
    if day < start:
        start = week_one(day.year - 1)
    elif rule in (2, 3) and day >= week_one(day.year + 1):
        start = week_one(day.year + 1)
    return (day - start).days // 7 + 1


def datepart(
    interval: str,
    value: Any,
    firstdayofweek: int = 1,
    firstweekofyear: int = 1,
) -> int | None:
    if value is None:
        return None
    key = _interval(interval)
    moment = to_date(value)
    parts = {
        "yyyy": moment.year,
        "q": (moment.month - 1) // 3 + 1,
        "m": moment.month,
        "y": moment.timetuple().tm_yday,
        "d": moment.day,
        "h": moment.hour,
        "n": moment.minute,
        "s": moment.second,
    }
    if key in parts:
        return parts[key]
    if key == "w":
        return weekday(moment, firstdayofweek)
    return _week_of_year(moment, firstdayofweek, firstweekofyear)


def year(value: Any) -> int | None:
    return None if value is None else to_date(value).year


def month(value: Any) -> int | None:
    return None if value is None else to_date(value).month


def day(value: Any) -> int | None:
    return None if value is None else to_date(value).day


def hour(value: Any) -> int | None:
    return None if value is None else to_date(value).hour


def minute(value: Any) -> int | None:
    return None if value is None else to_date(value).minute


def second(value: Any) -> int | None:
    return None if value is None else to_date(value).second


def weekday(value: Any, firstdayofweek: int = 1) -> Any:
    """Weekday: 1 for the first day of the week (Sunday unless given)."""
    if value is None:
        return None
    moment = to_date(value) if not isinstance(value, dt.date) else value
    sunday_based = (moment.weekday() + 1) % 7 + 1
    return (sunday_based - _first_day(firstdayofweek)) % 7 + 1


# ------------------------------------------------------------------ text


def _text(value: Any) -> str:
    return value if isinstance(value, str) else cstr(value)


def _length(n: Any) -> int:
    count = clng(n)
    if count < 0:
        raise VBAError(INVALID_PROCEDURE_CALL, "Invalid procedure call or argument")
    return count


def left(value: Any, length: Any) -> str | None:
    if value is None:
        return None
    return _text(value)[: _length(length)]


def right(value: Any, length: Any) -> str | None:
    if value is None:
        return None
    n = _length(length)
    text = _text(value)
    return text[max(len(text) - n, 0) :] if n else ""


def mid(value: Any, start: Any, length: Any = None) -> str | None:
    if value is None:
        return None
    first = clng(start)
    if first < 1:
        raise VBAError(INVALID_PROCEDURE_CALL, "Invalid procedure call or argument")
    text = _text(value)
    if length is None:
        return text[first - 1 :]
    return text[first - 1 : first - 1 + _length(length)]


def len_(value: Any) -> int | None:
    return None if value is None else len(_text(value))


def instr(*args: Any) -> int | None:
    """InStr([start, ]string1, string2[, compare]); compare 1 ignores case."""
    if len(args) >= 3 and not isinstance(args[0], str):
        start, haystack, needle = args[0], args[1], args[2]
        compare = args[3] if len(args) > 3 else 0
        if start is None:
            raise _null_error()
        first = clng(start)
        if first < 1:
            raise VBAError(INVALID_PROCEDURE_CALL, "Invalid procedure call or argument")
    else:
        first, haystack, needle = 1, args[0], args[1]
        compare = args[2] if len(args) > 2 else 0
    if haystack is None or needle is None:
        return None
    text, find = _text(haystack), _text(needle)
    if first > len(text):
        return 0
    if find == "":
        return first
    if compare == 1:
        text, find = text.lower(), find.lower()
    return text.find(find, first - 1) + 1


def ltrim(value: Any) -> str | None:
    return None if value is None else _text(value).lstrip(" ")


def rtrim(value: Any) -> str | None:
    return None if value is None else _text(value).rstrip(" ")


def trim(value: Any) -> str | None:
    return None if value is None else _text(value).strip(" ")


def ucase(value: Any) -> str | None:
    return None if value is None else _text(value).upper()


def lcase(value: Any) -> str | None:
    return None if value is None else _text(value).lower()


def space(number: Any) -> str | None:
    return None if number is None else " " * _length(number)


def string_(number: Any, character: Any) -> str | None:
    """String(number, character): a character code or the first character."""
    if number is None or character is None:
        return None
    if isinstance(character, str):
        if not character:
            raise VBAError(INVALID_PROCEDURE_CALL, "Invalid procedure call or argument")
        char = character[0]
    else:
        char = chr(clng(character) % 256)
    return char * _length(number)


# ------------------------------------------------------------------ registry

FUNCTIONS: dict[str, Callable[..., Any]] = {
    "CCur": ccur,
    "CDate": cdate,
    "CDbl": cdbl,
    "CInt": cint,
    "CLng": clng,
    "CStr": cstr,
    "Date": date,
    "DateAdd": dateadd,
    "DateDiff": datediff,
    "DatePart": datepart,
    "DateSerial": dateserial,
    "DateValue": datevalue,
    "Day": day,
    "Fix": fix,
    "Format": format_,
    "Hour": hour,
    "IIf": iif,
    "InStr": instr,
    "Int": int_,
    "LCase": lcase,
    "LTrim": ltrim,
    "Left": left,
    "Len": len_,
    "Mid": mid,
    "Minute": minute,
    "Month": month,
    "Now": now,
    "Nz": nz,
    "RTrim": rtrim,
    "Right": right,
    "Round": round_,
    "Second": second,
    "Space": space,
    "String": string_,
    "Time": time,
    "TimeValue": timevalue,
    "Trim": trim,
    "UCase": ucase,
    "Weekday": weekday,
    "Year": year,
}

LOCAL_MODULE = f"{__package__}.local.vbacompat_local"


def functions() -> dict[str, Callable[..., Any]]:
    """FUNCTIONS plus core/local/vbacompat_local.py FUNCTIONS (the local ones win)."""
    table = dict(FUNCTIONS)
    try:
        local = importlib.import_module(LOCAL_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name not in (LOCAL_MODULE, LOCAL_MODULE.rpartition(".")[0]):
            raise
        return table
    table.update(getattr(local, "FUNCTIONS", {}) or {})
    return table


def lookup(name: str) -> Callable[..., Any]:
    """The function of an Access name, ignoring case (KeyError when unknown)."""
    table = {k.lower(): v for k, v in functions().items()}
    try:
        return table[name.lower()]
    except KeyError:
        msg = f"no Access function {name!r} in vbacompat"
        raise KeyError(msg) from None
