"""Dates and computed values in a guided filter.

Until these types existed the only way to write ``date=.z.D-1`` in guided mode
was to call it a number, which worked by accident — and calling a *literal*
date a number worked not at all: q reads ``2026-07-30`` as 2026 minus 7 minus
30 and answers 1989, with no error anywhere.
"""
from datetime import date, datetime

import pytest

from kdbmonitor.core.chain import filter_clause
from kdbmonitor.core.models import Filter
from kdbmonitor.core.qfmt import format_q_list, format_q_value, q_date


# --- the literal that used to become arithmetic -----------------------------

def test_a_written_date_becomes_a_q_date_and_not_a_subtraction():
    """2026-07-30 as a number emits 2026-07-30, which q evaluates to 1989."""
    assert format_q_value("2026-07-30", "date") == "2026.07.30"
    assert format_q_value("2026-07-30", "number") == "2026-07-30"   # the trap


def test_slashes_and_dots_are_accepted_as_readily_as_dashes():
    for written in ("2026-07-30", "2026/07/30", "2026.07.30"):
        assert format_q_value(written, "date") == "2026.07.30"


def test_a_single_digit_month_and_day_are_padded():
    assert format_q_value("2026-7-3", "date") == "2026.07.03"


def test_a_real_date_object_is_accepted():
    assert format_q_value(date(2026, 7, 30), "date") == "2026.07.30"
    assert format_q_value(datetime(2026, 7, 30, 9, 15), "date") == "2026.07.30"


def test_something_that_is_not_a_date_is_refused_rather_than_passed_through():
    """Passing it through is how it becomes a wrong number instead of an error."""
    with pytest.raises(ValueError, match="not a date"):
        format_q_value("yesterday", "date")


def test_the_refusal_points_at_the_expression_type():
    with pytest.raises(ValueError, match=r"\.z\.D-1"):
        q_date(".z.D-1")


def test_a_date_that_does_not_exist_is_refused():
    with pytest.raises(ValueError):
        format_q_value("2026-02-30", "date")


# --- q that works the value out for itself ----------------------------------

def test_an_expression_is_sent_exactly_as_written():
    assert format_q_value(".z.D-1", "expression") == ".z.D-1"
    assert format_q_value(".z.D", "expression") == ".z.D"
    assert format_q_value("  .z.P  ", "expression") == ".z.P"


def test_yesterday_reads_as_q_in_a_guided_filter():
    """The clause the user was trying to build."""
    clause = filter_clause(Filter(column="date", op="=", value=".z.D-1",
                                  value_type="expression"))
    assert clause == "date=.z.D-1"


def test_a_literal_date_reads_as_q_in_a_guided_filter():
    clause = filter_clause(Filter(column="date", op="=", value="2026-07-30",
                                  value_type="date"))
    assert clause == "date=2026.07.30"


def test_a_date_range_reads_as_q():
    assert filter_clause(Filter(column="date", op=">=", value="2026-06-01",
                                value_type="date")) == "date>=2026.06.01"


# --- lists ------------------------------------------------------------------

def test_several_dates_make_a_date_vector():
    assert format_q_list(["2026-07-29", "2026-07-30"], "date") \
        == "2026.07.29 2026.07.30"


def test_one_date_is_enlisted_so_in_still_compares_a_list():
    assert format_q_list(["2026-07-30"], "date") == "enlist 2026.07.30"


def test_no_dates_at_all_stays_a_date_vector():
    """0#0 would be longs, and `date in 0#0` compares the wrong types."""
    assert format_q_list([], "date") == "0#0d"


def test_a_date_filter_with_in_reads_as_q():
    clause = filter_clause(Filter(column="date", op="in",
                                  value=["2026-07-29", "2026-07-30"],
                                  value_type="date"))
    assert clause == "date in 2026.07.29 2026.07.30"


def test_expressions_in_a_list_are_each_sent_as_written():
    assert format_q_list([".z.D", ".z.D-1"], "expression") == "(.z.D;.z.D-1)"


# --- the existing types are untouched ---------------------------------------

def test_symbols_numbers_and_strings_are_unchanged():
    assert format_q_value("AAPL", "symbol") == "`AAPL"
    assert format_q_value(10, "number") == "10"
    assert format_q_value("buy", "string") == '"buy"'
    assert format_q_list(["A", "B"], "symbol") == "`A`B"
    assert format_q_list([], "symbol") == "`$()"


def test_an_unknown_value_type_still_says_so():
    with pytest.raises(ValueError, match="unknown value_type"):
        format_q_value("x", "sasquatch")
    with pytest.raises(ValueError, match="unknown value_type"):
        format_q_list(["x"], "sasquatch")
