"""vbacompat: one table of cases per function family. Every expected value cites its
source: a Microsoft documentation page (MS: ...) or the Jackcess 5.1.7 expression
code it is ported from (Jackcess: class, behaviour). United States settings."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from .. import vbacompat as vba  # noqa: TID252

T = dt.datetime
D_1969 = T(1969, 2, 12)  # MS examples' #February 12, 1969#
D_2007 = T(2007, 6, 19, 17, 34, 23)  # MS: Format property (Date/Time) examples
TIME_17_04_23 = T(1899, 12, 30, 17, 4, 23)  # MS: Format function, MyTime
D_1993 = T(1993, 1, 27)  # MS: Format function, MyDate
D_1973 = T(1973, 1, 1, 13, 37, 25)

MS_FORMAT_FN = "MS: Format function (VBA), examples"
MS_FORMAT_FN_NAMED = "MS: Format function (VBA), predefined numeric formats"
MS_FORMAT_FN_DATE = "MS: Format function (VBA), user-defined date/time formats"
MS_DT_PROP = "MS: Format property - Date/Time data type (Access)"
MS_NUM_PROP = "MS: Format property - Number and Currency data types (Access)"
JK_PREDEF_NULL = "Jackcess: FormatUtil.putPredefFormat, Null gives empty string"
JK_TEMPORAL = "Jackcess: TemporalConfig US formats"
JK_ROUND = "Jackcess: NumberFormatter.ROUND_MODE is HALF_EVEN"
JK_NUMBER = "Jackcess: FormatUtil.createCustomNumberFormat"
JK_TYPE = "Jackcess: FormatUtil.determineFormatType"
JK_TEXT = "Jackcess: FormatUtil.parseCustomTextFormat"
JK_NUMTEXT = "Jackcess: NumberFormatter.TypeFormatter, 15 significant digits"

FORMAT_CASES = [
    # named date formats
    (D_2007, "General Date", "6/19/2007 5:34:23 PM", MS_DT_PROP),
    (D_2007, "Long Date", "Tuesday, June 19, 2007", MS_DT_PROP),
    (D_2007, "Medium Date", "19-Jun-07", MS_DT_PROP),
    (D_2007, "Short Date", "6/19/2007", MS_DT_PROP),
    (D_2007, "Long Time", "5:34:23 PM", MS_DT_PROP),
    (D_2007, "Short Time", "17:34", MS_DT_PROP),
    (D_2007, "Medium Time", "05:34 PM", JK_TEMPORAL),
    (D_1973, "Long Date", "Monday, January 01, 1973", JK_TEMPORAL),
    (T(1993, 4, 3), "General Date", "4/3/1993", MS_FORMAT_FN_NAMED),
    (TIME_17_04_23, "General Date", "5:04:23 PM", MS_FORMAT_FN_NAMED),
    (None, "Short Date", "", JK_PREDEF_NULL),
    # named number formats
    (3456.789, "General Number", "3456.789", MS_NUM_PROP),
    (3456.789, "Currency", "$3,456.79", MS_NUM_PROP),
    (-3456.789, "Currency", "($3,456.79)", MS_NUM_PROP),
    (3456.789, "Fixed", "3456.79", MS_NUM_PROP),
    (-3456.789, "Fixed", "-3456.79", MS_NUM_PROP),
    (3.56645, "Fixed", "3.57", MS_NUM_PROP),
    (3456.789, "Standard", "3,456.79", MS_NUM_PROP),
    (3456.789, "Scientific", "3.46E+03", MS_NUM_PROP),
    (-3456.789, "Scientific", "-3.46E+03", MS_NUM_PROP),
    (0.5, "Percent", "50.00%", MS_FORMAT_FN_NAMED),
    (Decimal("12.5"), "Currency", "$12.50", MS_FORMAT_FN_NAMED),
    (0, "Fixed", "0.00", MS_FORMAT_FN_NAMED),
    (None, "Currency", "", JK_PREDEF_NULL),
    # named Boolean formats
    (13, "Yes/No", "Yes", MS_FORMAT_FN_NAMED),
    (0, "Yes/No", "No", MS_FORMAT_FN_NAMED),
    (True, "Yes/No", "Yes", MS_FORMAT_FN_NAMED),
    (-1, "True/False", "True", MS_FORMAT_FN_NAMED),
    (0, "True/False", "False", MS_FORMAT_FN_NAMED),
    (1, "On/Off", "On", MS_FORMAT_FN_NAMED),
    (0, "On/Off", "Off", MS_FORMAT_FN_NAMED),
    # custom date and time formats
    (TIME_17_04_23, "h:m:s", "17:4:23", MS_FORMAT_FN),
    (TIME_17_04_23, "hh:mm:ss am/pm", "05:04:23 pm", MS_FORMAT_FN),
    (TIME_17_04_23, "hh:mm:ss AM/PM", "05:04:23 PM", MS_FORMAT_FN),
    (D_1993, "dddd, mmm d yyyy", "Wednesday, Jan 27 1993", MS_FORMAT_FN),
    (T(2024, 3, 5), "yyyy-mm", "2024-03", MS_FORMAT_FN_DATE),
    (T(2024, 3, 5), "mmm-yyyy", "Mar-2024", MS_FORMAT_FN_DATE),
    (T(2024, 3, 5), "q-yyyy", "1-2024", MS_FORMAT_FN_DATE),
    (T(2024, 11, 5), "q", "4", MS_FORMAT_FN_DATE),
    (T(2024, 3, 5), "mmmm d, yyyy", "March 5, 2024", MS_FORMAT_FN_DATE),
    (T(2024, 3, 5), "dd/mm/yy", "05/03/24", MS_FORMAT_FN_DATE),
    (T(2024, 3, 5), "ddd", "Tue", MS_FORMAT_FN_DATE),
    (T(2024, 3, 5), "y", "65", MS_FORMAT_FN_DATE),
    (T(2024, 3, 5), "w", "3", MS_FORMAT_FN_DATE),
    (T(2024, 3, 5, 9, 7), "hh:nn A/P", "09:07 A", MS_FORMAT_FN_DATE),
    (T(2024, 3, 5), '"Q"q yyyy', "Q1 2024", MS_FORMAT_FN_DATE),
    (T(2024, 3, 5), "ddddd", "3/5/2024", MS_FORMAT_FN_DATE),
    (None, "yyyy-mm", "", "Jackcess: FormatUtil.CustomFmt, Null formats as empty"),
    # custom number formats
    (23, None, "23", MS_FORMAT_FN),
    (5459.4, "##,##0.00", "5,459.40", MS_FORMAT_FN),
    (334.9, "###0.00", "334.90", MS_FORMAT_FN),
    (5, "0.00%", "500.00%", MS_FORMAT_FN),
    (-5, '0;(0);;"Null"', "(5)", MS_NUM_PROP),
    (None, '0;(0);;"Null"', "Null", MS_NUM_PROP),
    (5, "+0.0;-0.0;0.0", "+5.0", MS_NUM_PROP),
    (-5, "+0.0;-0.0;0.0", "-5.0", MS_NUM_PROP),
    (0, "+0.0;-0.0;0.0", "0.0", MS_NUM_PROP),
    (-5, "0.00", "-5.00", JK_NUMBER),
    (2.5, "0", "2", JK_ROUND),
    (3.5, "0", "4", JK_ROUND),
    (0.125, "0.00", "0.12", JK_ROUND),
    (0.5, "#.##", ".5", JK_NUMBER),
    (1, "#.##", "1.", JK_NUMBER),
    (-0.001, "0.00", "0.00", JK_NUMBER),
    (1234567, "#,##0", "1,234,567", MS_FORMAT_FN),
    (12345.6789, "0.00E+00", "1.23E+04", "Jackcess: NumberFormatter.ScientificFormat"),
    (D_1969, "0", "25246", JK_NUMBER),
    ("abc", "0.00", "abc", JK_NUMBER),
    # custom text formats
    ("HELLO", "<", "hello", MS_FORMAT_FN),
    ("This is it", ">", "THIS IS IT", MS_FORMAT_FN),
    ("ab", "@@@@", "  ab", JK_TEXT),
    ("ab", "!@@@@", "ab  ", JK_TEXT),
    ("abc", "&&&&", "abc", JK_TEXT),
    ("", '@;"empty"', "empty", JK_TEXT),
    # general (literal) formats
    (5, '"pos";"neg";"zero"', "pos", JK_TYPE),
    (-5, '"pos";"neg";"zero"', "neg", JK_TYPE),
    (0, '"pos";"neg";"zero"', "zero", JK_TYPE),
]


@pytest.mark.parametrize(
    ("value", "fmt", "expected", "source"),
    FORMAT_CASES,
    ids=[f"{c[1]}:{c[2]}" for c in FORMAT_CASES],
)
def test_format(value, fmt, expected, source):
    assert source
    assert vba.format_(value, fmt) == expected


CASES = [
    # Nz, IIf
    ("Nz", (None,), "", "MS: Nz function, a query gets a zero-length string"),
    ("Nz", (None, 0), 0, "MS: Nz function"),
    ("Nz", (5, 0), 5, "MS: Nz function"),
    ("IIf", (True, "Large", "Small"), "Large", "MS: IIf function, example"),
    ("IIf", (0, "Large", "Small"), "Small", "MS: IIf function, example"),
    ("IIf", (None, "a", "b"), "b", "Jackcess: DefaultFunctions.IIF, Null is false"),
    # conversions
    ("CStr", (437.324,), "437.324", "MS: Type conversion functions, CStr example"),
    ("CStr", (True,), "True", "MS: Type conversion functions, CStr of a Boolean"),
    ("CStr", (Decimal("12.5000"),), "12.5", JK_NUMTEXT),
    ("CStr", (1 / 3,), "0.333333333333333", JK_NUMTEXT),
    ("CStr", (1e20,), "1E+20", JK_NUMTEXT),
    ("CStr", (D_1973,), "1/1/1973 1:37:25 PM", "MS: CStr of a Date uses General Date"),
    ("CInt", (2345.5678,), 2346, "MS: Type conversion functions, CInt example"),
    ("CInt", (0.5,), 0, "MS: CInt rounds 0.5 to the nearest even number"),
    ("CInt", (1.5,), 2, "MS: CInt rounds 0.5 to the nearest even number"),
    ("CInt", (2.5,), 2, "MS: CInt rounds 0.5 to the nearest even number"),
    ("CInt", (True,), -1, "MS: True converts to -1"),
    ("CLng", (25427.45,), 25427, "MS: Type conversion functions, CLng example"),
    ("CLng", (25427.55,), 25428, "MS: Type conversion functions, CLng example"),
    ("CLng", ("12",), 12, "MS: Type conversion functions, string argument"),
    (
        "CDbl",
        (Decimal("234.4568") * Decimal("8.2") * Decimal("0.01"),),
        19.2254576,
        "MS: Type conversion functions, CDbl example",
    ),
    ("CCur", (543.214588 * 2,), Decimal("1086.4292"), "MS: CCur example"),
    ("CCur", (Decimal("0.00005"),), Decimal("0.0000"), "Jackcess: scale 4, HALF_EVEN"),
    ("Int", (99.8,), 99.0, "MS: Int, Fix functions, example"),
    ("Fix", (99.2,), 99.0, "MS: Int, Fix functions, example"),
    ("Int", (-99.8,), -100.0, "MS: Int, Fix functions, example"),
    ("Fix", (-99.8,), -99.0, "MS: Int, Fix functions, example"),
    ("Int", (-99.2,), -100.0, "MS: Int, Fix functions, example"),
    ("Fix", (-99.2,), -99.0, "MS: Int, Fix functions, example"),
    ("Int", (7,), 7, "MS: Int returns the argument's type"),
    ("Int", (None,), None, "MS: Int, Fix functions, Null gives Null"),
    ("Round", (2.5,), 2.0, "MS: Round function, banker's rounding"),
    ("Round", (3.5,), 4.0, "MS: Round function, banker's rounding"),
    ("Round", (Decimal("0.125"), 2), Decimal("0.12"), "MS: Round, banker's rounding"),
    ("Round", (-2.5,), -2.0, "MS: Round function, banker's rounding"),
    # dates
    ("DateValue", ("February 12, 1969",), D_1969, "MS: DateValue function, example"),
    ("DateValue", (T(1969, 2, 12, 13, 5),), D_1969, "MS: DateValue keeps the date"),
    ("DateValue", ("2/12/1969",), D_1969, "MS: DateValue, US short date"),
    ("DateSerial", (1969, 2, 12), D_1969, "MS: DateSerial function, example"),
    ("DateSerial", (1990 - 10, 8 - 2, 1 - 1), T(1980, 5, 31), "MS: DateSerial"),
    ("DateSerial", (2024, 13, 1), T(2025, 1, 1), "MS: DateSerial, month overflow"),
    ("DateSerial", (99, 1, 1), T(1999, 1, 1), "MS: DateSerial, two-digit years"),
    ("DateAdd", ("m", 1, T(1995, 1, 31)), T(1995, 2, 28), "MS: DateAdd example"),
    ("DateAdd", ("d", 1, T(1995, 1, 31)), T(1995, 2, 1), "MS: DateAdd, day interval"),
    ("DateAdd", ("yyyy", -1, T(2024, 2, 29)), T(2023, 2, 28), "MS: DateAdd"),
    ("DateAdd", ("ww", 2, T(2024, 3, 5)), T(2024, 3, 19), "MS: DateAdd, weeks"),
    ("DateAdd", ("h", 3, T(2024, 3, 5, 22)), T(2024, 3, 6, 1), "MS: DateAdd, hours"),
    ("DateAdd", ("q", 1, T(2024, 11, 30)), T(2025, 2, 28), "MS: DateAdd, quarters"),
    ("DateDiff", ("yyyy", T(2023, 12, 31), T(2024, 1, 1)), 1, "MS: DateDiff, Year"),
    ("DateDiff", ("m", T(2024, 1, 31), T(2024, 2, 1)), 1, "MS: DateDiff, months"),
    ("DateDiff", ("d", T(2024, 3, 1), T(2024, 3, 5)), 4, "MS: DateDiff, days"),
    ("DateDiff", ("d", T(2024, 3, 5), T(2024, 3, 1)), -4, "MS: DateDiff, negative"),
    ("DateDiff", ("w", T(2024, 3, 1), T(2024, 3, 7)), 0, "MS: DateDiff, weekday"),
    ("DateDiff", ("w", T(2024, 3, 1), T(2024, 3, 8)), 1, "MS: DateDiff, weekday"),
    ("DateDiff", ("ww", T(2024, 3, 2), T(2024, 3, 3)), 1, "MS: DateDiff, Sundays"),
    ("DateDiff", ("ww", T(2024, 3, 3), T(2024, 3, 9)), 0, "MS: DateDiff, Sundays"),
    ("DateDiff", ("h", T(2024, 3, 1, 23, 59), T(2024, 3, 2)), 1, "Jackcess: DateDiff"),
    ("DateDiff", ("q", T(2023, 12, 1), T(2024, 4, 1)), 2, "Jackcess: DateDiff"),
    ("DatePart", ("q", D_1969), 1, "MS: DatePart function"),
    ("DatePart", ("y", D_1969), 43, "MS: DatePart, day of year"),
    ("DatePart", ("ww", T(2024, 1, 1)), 1, "MS: DatePart, first week has Jan 1"),
    ("DatePart", ("ww", T(2024, 1, 7)), 2, "MS: DatePart, Sunday starts a week"),
    ("DatePart", ("w", D_1969), 4, "MS: DatePart, weekday"),
    ("Year", (D_1969,), 1969, "MS: Year function, example"),
    ("Month", (D_1969,), 2, "MS: Month function, example"),
    ("Day", (D_1969,), 12, "MS: Day function, example"),
    ("Weekday", (D_1969,), 4, "MS: Weekday function, example (Wednesday)"),
    ("Weekday", (D_1969, 2), 3, "MS: Weekday, vbMonday as first day"),
    ("Year", (None,), None, "MS: Year, Null gives Null"),
    # text
    ("Left", ("Hello World", 1), "H", "MS: Left function, example"),
    ("Left", ("Hello World", 7), "Hello W", "MS: Left function, example"),
    ("Left", ("Hello World", 20), "Hello World", "MS: Left function, example"),
    ("Right", ("Hello World", 1), "d", "MS: Right function, example"),
    ("Right", ("Hello World", 6), " World", "MS: Right function, example"),
    ("Right", ("Hello World", 20), "Hello World", "MS: Right function, example"),
    ("Mid", ("Mid Function Demo", 1, 3), "Mid", "MS: Mid function, example"),
    ("Mid", ("Mid Function Demo", 14, 4), "Demo", "MS: Mid function, example"),
    ("Mid", ("Mid Function Demo", 5), "Function Demo", "MS: Mid function, example"),
    ("Len", ("Hello World",), 11, "MS: Len function, example"),
    ("Len", (None,), None, "MS: Len, Null gives Null"),
    ("Left", (None, 2), None, "MS: Left, Null gives Null"),
    ("InStr", (4, "XXpXXpXXPXXP", "P", 1), 6, "MS: InStr function, example"),
    ("InStr", (1, "XXpXXpXXPXXP", "P", 0), 9, "MS: InStr function, example"),
    ("InStr", ("XXpXXpXXPXXP", "P"), 9, "MS: InStr function, example"),
    ("InStr", (1, "XXpXXpXXPXXP", "W"), 0, "MS: InStr function, example"),
    ("InStr", ("abc", ""), 1, "MS: InStr, zero-length string2 returns start"),
    ("LTrim", ("  <-Trim->  ",), "<-Trim->  ", "MS: LTrim, RTrim, Trim, example"),
    ("RTrim", ("  <-Trim->  ",), "  <-Trim->", "MS: LTrim, RTrim, Trim, example"),
    ("Trim", ("  <-Trim->  ",), "<-Trim->", "MS: LTrim, RTrim, Trim, example"),
    ("UCase", ("Hello World 1234",), "HELLO WORLD 1234", "MS: UCase function"),
    ("LCase", ("Hello World 1234",), "hello world 1234", "MS: LCase function"),
    ("Space", (10,), " " * 10, "MS: Space function, example"),
    ("String", (5, "*"), "*****", "MS: String function, example"),
    ("String", (5, 42), "*****", "MS: String function, example"),
]


@pytest.mark.parametrize(
    ("name", "args", "expected", "source"),
    CASES,
    ids=[f"{c[0]}{c[1]!r}" for c in CASES],
)
def test_function(name, args, expected, source):
    assert source
    result = vba.lookup(name)(*args)
    assert result == expected
    assert type(result) is type(expected)


ERROR_CASES = [
    ("CStr", (None,), vba.INVALID_USE_OF_NULL, "MS: Invalid use of Null (Error 94)"),
    ("CInt", (None,), vba.INVALID_USE_OF_NULL, "MS: Invalid use of Null (Error 94)"),
    ("CInt", (32767.5,), vba.OVERFLOW, "MS: CInt range -32,768 to 32,767"),
    ("CLng", (2**31,), vba.OVERFLOW, "MS: CLng range"),
    ("CInt", ("abc",), vba.TYPE_MISMATCH, "MS: Type mismatch (Error 13)"),
    ("Left", ("abc", -1), vba.INVALID_PROCEDURE_CALL, "MS: Left, length below 0"),
    ("Mid", ("abc", 0), vba.INVALID_PROCEDURE_CALL, "MS: Mid, start below 1"),
    ("DateAdd", ("x", 1, D_1969), vba.INVALID_PROCEDURE_CALL, "MS: DateAdd interval"),
    ("Round", (1.5, -1), vba.INVALID_PROCEDURE_CALL, "MS: Round, places below 0"),
]


@pytest.mark.parametrize(
    ("name", "args", "number", "source"),
    ERROR_CASES,
    ids=[f"{c[0]}{c[1]!r}" for c in ERROR_CASES],
)
def test_errors(name, args, number, source):
    assert source
    with pytest.raises(vba.VBAError) as info:
        vba.lookup(name)(*args)
    assert info.value.number == number


def test_serials_round_trip():
    """Access day serials: 0 is 1899-12-30, the time is the fraction, a negative
    serial keeps a positive time (MS: Date data type)."""
    assert vba.from_serial(0) == T(1899, 12, 30)
    assert vba.from_serial(2.5) == T(1900, 1, 1, 12)
    assert vba.from_serial(-1.25) == T(1899, 12, 29, 6)
    for value in (T(1899, 12, 29, 6), T(1969, 2, 12, 13, 5, 7), T(2024, 2, 29)):
        assert vba.from_serial(vba.to_serial(value)) == value


def test_lookup_ignores_case_and_knows_the_required_functions():
    required = [
        *("Format", "Nz", "IIf", "DateValue", "DateSerial", "DateAdd", "DateDiff"),
        *("DatePart", "Year", "Month", "Day", "Weekday", "Left", "Right", "Mid"),
        *("Len", "InStr", "Trim", "UCase", "LCase", "CStr", "CInt", "CLng", "CDbl"),
        *("CCur", "Int", "Fix", "Round"),
    ]
    for name in required:
        assert vba.lookup(name.upper()) is vba.lookup(name)
    with pytest.raises(KeyError):
        vba.lookup("NoSuchFunction")


def test_now_date_and_time_are_naive_and_whole_seconds():
    assert vba.date().time() == dt.time()
    assert vba.now().microsecond == 0
    assert vba.time().date() == vba.ZERO_DATE.date()
