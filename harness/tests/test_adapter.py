"""Set-up failure descriptions (adapter.describe_failure, clean_failure, failure_text): class
and design names only, never the exception message. Synthetic exceptions; CANARY-<n> marks text
that must never come out."""

from __future__ import annotations

from types import SimpleNamespace

from .. import adapter as ad


class WidgetError(Exception):
    pass


def chained() -> Exception:
    """A load error raised from a driver error, as Django wraps psycopg."""
    driver = WidgetError("Key (ParentID)=(CANARY-1) is not present")
    driver.diag = SimpleNamespace(
        table_name="Widgets",
        column_name=None,
        constraint_name="FK_Widgets_Parent",
        sqlstate="23503",
    )
    try:
        try:
            raise driver
        except WidgetError as exc:
            raise ValueError("row CANARY-2 broke") from exc
    except ValueError as exc:
        return exc


def test_the_chain_gives_class_names_and_driver_names_never_the_message():
    info = ad.describe_failure(chained())
    assert info == {
        "phase": "load_dataset",
        "exception": "ValueError",
        "chain": ["ValueError", "WidgetError"],
        "table": "Widgets",
        "constraint": "FK_Widgets_Parent",
        "sqlstate": "23503",
    }
    assert "CANARY" not in repr(info)
    text = ad.failure_text(info)
    assert text == (
        "load_dataset failed: ValueError <- WidgetError "
        "(table Widgets, constraint FK_Widgets_Parent, sqlstate 23503)"
    )


def test_attributes_on_the_raised_exception_come_first():
    exc = chained()
    exc.table = "Gears"
    exc.column = "ParentID"
    exc.code = "IntegrityError"
    info = ad.describe_failure(exc, "load_dataset")
    assert info["table"] == "Gears"
    assert info["column"] == "ParentID"
    assert info["code"] == "IntegrityError"
    assert info["constraint"] == "FK_Widgets_Parent"


def test_an_implicit_context_counts_unless_suppressed():
    try:
        try:
            raise WidgetError("CANARY-3")
        except WidgetError:
            raise KeyError("CANARY-4")
    except KeyError as exc:
        assert ad.describe_failure(exc)["chain"] == ["KeyError", "WidgetError"]
    try:
        try:
            raise WidgetError("CANARY-3")
        except WidgetError:
            raise KeyError("CANARY-4") from None
    except KeyError as exc:
        assert ad.describe_failure(exc)["chain"] == ["KeyError"]


def test_names_that_are_not_design_names_are_dropped():
    exc = WidgetError("CANARY-5")
    exc.table = "Widgets\nCANARY-6"
    exc.column = 42
    exc.constraint = "x" * 200
    exc.sqlstate = "not a state"
    info = ad.describe_failure(exc, "no-such-phase")
    assert info == {
        "phase": "setup",
        "exception": "WidgetError",
        "chain": ["WidgetError"],
    }


def test_clean_failure_rechecks_a_description_read_from_a_file():
    raw = {
        "phase": "load_dataset",
        "exception": "LoadError",
        "chain": ["LoadError", "Integrity Error CANARY-7", "IntegrityError"],
        "table": "Order Details",
        "column": "(row)",
        "constraint": "CANARY-8\n",
        "sqlstate": "23000",
        "message": "CANARY-9",
    }
    out = ad.clean_failure(raw)
    assert out == {
        "phase": "load_dataset",
        "exception": "LoadError",
        "chain": ["LoadError", "IntegrityError"],
        "table": "Order Details",
        "column": "(row)",
        "sqlstate": "23000",
    }
    assert ad.clean_failure("CANARY-10") is None
    assert ad.failure_text({"chain": ["CANARY 11"]}) == "setup failed: Exception"
