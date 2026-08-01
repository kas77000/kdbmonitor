"""What a form value has to satisfy before a query is sent.

Two jobs: keep a value that cannot honestly be written into q from being
written into q, and hold a value to the rules this dashboard's author set. The
second is the point of the feature; the first is what stops it being a hole.
"""
from datetime import date

import pytest

from kdbmonitor.core.dashboard_models import Parameter
from kdbmonitor.core.paramrules import check, check_all, resolve_bound

MONDAY = date(2026, 8, 3)


def _p(**kw) -> Parameter:
    kw.setdefault("name", "sym")
    return Parameter(**kw)


def bad(parameter: Parameter, value, today: date = MONDAY) -> str:
    problem = check(parameter, value, today=today)
    assert problem, f"expected {value!r} to be refused"
    return problem


def good(parameter: Parameter, value, today: date = MONDAY) -> None:
    problem = check(parameter, value, today=today)
    assert problem is None, problem


# --- a value has to be writable as its q type --------------------------------

def test_a_symbol_with_a_semicolon_is_refused():
    """`AAPL; delete from t parses as a symbol *and* a delete, and q runs both.
    This one is not the author's to switch off."""
    problem = bad(_p(q_type="symbol"), "AAPL; delete from t")
    assert "cannot be a symbol" in problem


@pytest.mark.parametrize("attempt", [
    "AAPL; delete from t",
    "AAPL`; system \"rm -rf /\"",
    "AAPL) ; exit 0",
    "AAPL from t where 1b",
    "a b",
])
def test_nothing_that_would_end_the_symbol_gets_through(attempt):
    assert check(_p(q_type="symbol"), attempt, today=MONDAY)


@pytest.mark.parametrize("ok", ["AAPL", "BRK.B", "a_b", "RELIANCE.IN", ""])
def test_an_ordinary_symbol_is_fine(ok):
    good(_p(q_type="symbol"), ok)


def test_a_handle_keeps_its_colons():
    good(_p(q_type="symbol"), ":localhost:5000")


def test_a_string_may_hold_anything_because_qfmt_escapes_it():
    good(_p(q_type="string"), 'say "hi"; delete from t')


def test_an_expression_is_q_by_definition_and_is_not_second_guessed():
    """It exists to send q the author wrote. The editor says so in as many
    words, and the rules below are how an author narrows it again."""
    good(_p(q_type="expression"), ".z.D-1")


def test_a_number_that_is_not_a_number_is_refused():
    assert "is not a number" in bad(_p(kind="number", q_type="number"), "ten")


def test_a_date_that_is_not_a_date_is_refused():
    assert "is not a date" in bad(_p(kind="date", q_type="date"), "yesterday")
    assert check(_p(kind="date", q_type="date"), "2026-02-31", today=MONDAY)


# --- required ----------------------------------------------------------------

def test_a_required_value_may_not_be_blank():
    assert "is required" in bad(_p(required=True), "")
    assert "is required" in bad(_p(required=True), None)


def test_an_optional_blank_is_not_a_problem():
    good(_p(required=False), "")


def test_a_blank_is_not_also_reported_as_a_bad_shape():
    """One problem per value: 'sym is required' beats that plus 'is not a
    number' for the same empty box."""
    problem = bad(_p(kind="number", q_type="number", required=True), "")
    assert "required" in problem and "not a number" not in problem


# --- the author's own rules --------------------------------------------------

def test_a_pattern_holds_a_value_to_a_shape():
    p = _p(pattern="^[A-Z]+$")
    good(p, "AAPL")
    assert bad(p, "aapl")


def test_the_author_s_words_are_used_when_a_pattern_fails():
    p = _p(pattern="^[A-Z]+$", pattern_message="Use an uppercase ticker, e.g. AAPL")
    assert bad(p, "aapl") == "Use an uppercase ticker, e.g. AAPL"


def test_the_author_s_words_win_over_the_machinery_underneath():
    """A value with a space in it fails the pattern and could never be a symbol
    either. The author wrote a sentence for exactly this — use it."""
    p = _p(pattern="^[A-Z]+$", pattern_message="Use an uppercase ticker, e.g. AAPL")
    assert bad(p, "not a ticker") == "Use an uppercase ticker, e.g. AAPL"


def test_a_value_the_author_allows_still_has_to_be_writable():
    """The swap changes which message is heard, never what is let through."""
    assert "cannot be a symbol" in bad(_p(pattern=".*"), "AAPL; delete from t")


def test_a_pattern_that_does_not_compile_says_so_rather_than_passing_everything():
    assert "broken" in bad(_p(pattern="([unclosed"), "anything")


def test_a_number_is_held_between_its_bounds():
    p = _p(kind="number", q_type="number", minimum="1", maximum="100")
    good(p, "50")
    assert "below the minimum" in bad(p, "0")
    assert "above the maximum" in bad(p, "101")


def test_a_bound_on_its_own_still_binds():
    assert bad(_p(kind="number", q_type="number", minimum="10"), "9")
    assert bad(_p(kind="number", q_type="number", maximum="10"), "11")


def test_whole_numbers_only_refuses_a_fraction():
    p = _p(kind="number", q_type="number", integer=True)
    good(p, "7")
    assert "whole number" in bad(p, "7.5")


def test_a_date_is_held_between_its_bounds():
    p = _p(kind="date", q_type="date", minimum="2026-01-01", maximum="2026-12-31")
    good(p, "2026-07-30")
    assert "earlier than" in bad(p, "2025-12-31")
    assert "later than" in bad(p, "2027-01-01")


def test_a_relative_bound_follows_the_calendar():
    """'no earlier than 30 days ago' is written once, not retyped every
    morning."""
    p = _p(kind="date", q_type="date", minimum="today-30d", maximum="today")
    good(p, "2026-07-20")                      # inside the window on Aug 3
    assert bad(p, "2026-06-01")                # more than 30 days back
    assert "later than" in bad(p, "2026-08-04")   # tomorrow


def test_today_on_its_own_means_today():
    assert resolve_bound("today", MONDAY) == MONDAY
    assert resolve_bound("today-1d", MONDAY) == date(2026, 8, 2)
    assert resolve_bound("today + 2d", MONDAY) == date(2026, 8, 5)
    assert resolve_bound("2026-07-30", MONDAY) == date(2026, 7, 30)
    assert resolve_bound("", MONDAY) is None
    assert resolve_bound("next tuesday", MONDAY) is None


def test_a_failed_bound_says_the_date_it_worked_out_to():
    """'earlier than today-30d' sends somebody to a calendar; the date does
    not."""
    p = _p(kind="date", q_type="date", minimum="today-30d")
    assert "2026-07-04" in bad(p, "2026-06-01")


def test_a_weekend_is_refused_where_the_hdb_has_no_partition():
    p = _p(kind="date", q_type="date", weekdays_only=True)
    good(p, "2026-07-31")                      # a Friday
    problem = bad(p, "2026-08-01")             # the Saturday after
    assert "Saturday" in problem and "weekday" in problem


def test_the_label_is_what_the_reader_is_called_by():
    p = _p(name="sym", label="Instrument", required=True)
    assert bad(p, "").startswith("Instrument")


# --- a whole form ------------------------------------------------------------

def test_check_all_reports_every_bad_value_by_name():
    params = [_p(name="sym", required=True),
              _p(name="n", kind="number", q_type="number", minimum="1"),
              _p(name="ok", required=False)]
    found = check_all(params, {"sym": "", "n": "0", "ok": ""}, today=MONDAY)
    assert set(found) == {"sym", "n"}


def test_a_good_form_reports_nothing():
    params = [_p(name="sym", required=True), _p(name="n", kind="number",
                                                q_type="number")]
    assert check_all(params, {"sym": "AAPL", "n": "5"}, today=MONDAY) == {}


def test_only_narrows_the_check_to_what_is_about_to_be_used():
    """A parameter that feeds a chart cannot block a query it never reaches."""
    params = [_p(name="sym", required=True), _p(name="chart_title", required=True)]
    values = {"sym": "AAPL", "chart_title": ""}
    assert check_all(params, values, today=MONDAY, only={"sym"}) == {}
    assert "chart_title" in check_all(params, values, today=MONDAY)


def test_a_missing_value_falls_back_to_the_default_before_being_judged():
    params = [_p(name="sym", default="AAPL", required=True)]
    assert check_all(params, {}, today=MONDAY) == {}


# --- rules that cannot do what they say, caught while building ---------------

def _building(p: Parameter) -> list[str]:
    from kdbmonitor.ui.dashboard_editor import _rule_problems

    return _rule_problems(p, p.label or p.name)


def test_a_default_its_own_rules_reject_is_the_author_s_problem_now():
    """Otherwise it is the reader's problem later, as a dashboard that opens
    blocked on a value they did not choose."""
    problems = _building(_p(default="aapl", pattern="^[A-Z]+$"))
    assert problems and "its own default fails its rules" in problems[0]


def test_a_minimum_above_a_maximum_can_never_pass():
    assert any("nothing can pass" in p for p in
               _building(_p(kind="number", q_type="number",
                            minimum="100", maximum="1")))
    assert any("nothing can pass" in p for p in
               _building(_p(kind="date", q_type="date",
                            minimum="2026-12-31", maximum="2026-01-01")))


def test_a_pattern_that_does_not_compile_is_caught_before_it_is_saved():
    assert any("not a valid regular expression" in p
               for p in _building(_p(pattern="([unclosed")))


def test_a_bound_that_is_not_a_date_is_caught():
    assert any("neither a date nor a relative one" in p
               for p in _building(_p(kind="date", q_type="date",
                                     minimum="last tuesday")))


def test_a_bound_that_is_not_a_number_is_caught():
    assert any("is not a number" in p
               for p in _building(_p(kind="number", q_type="number",
                                     minimum="lots")))


def test_a_relative_bound_is_a_perfectly_good_bound():
    assert _building(_p(kind="date", q_type="date", minimum="today-90d",
                        maximum="today")) == []


def test_rules_that_agree_with_each_other_have_nothing_to_report():
    assert _building(_p(default="AAPL", pattern="^[A-Z]+$", required=True)) == []
