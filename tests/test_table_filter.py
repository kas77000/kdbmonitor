"""Narrowing a table by its columns.

The search box beside these answers a different question — see
test_table_search.py. These are the spreadsheet ones: a named column, a
condition on it, and several of them at once.
"""
from datetime import date

import pandas as pd
import pytest

from kdbmonitor.core.tablefilter import (
    ColumnFilter, MAX_PICKABLE, active_count, apply, blank, bounds_of,
    control_kind, filters_from, is_set, kind_of, options_for, options_with,
    summary)


@pytest.fixture
def orders():
    return pd.DataFrame({
        "sym": ["6981.JP", "AAPL.US", "6981.JP", "VOD.LN", "AAPL.US"],
        "side": ["BUY", "SELL", "SELL", "BUY", "BUY"],
        "qty": [100, 250_000, 300, 400_000, 50],
        "venue": ["TSE", "NASDAQ", "TSE", "LSE", "NASDAQ"],
    })


def _syms(frame):
    return list(frame["sym"])


# --- which control a column gets --------------------------------------------

def test_a_column_of_a_few_names_offers_a_tick_list(orders):
    assert kind_of(orders["side"]) == "pick"


def test_a_column_of_numbers_offers_a_range(orders):
    assert kind_of(orders["qty"]) == "range"


def test_a_column_of_thousands_of_ids_offers_a_box_to_type_in():
    """A tick-list of nine thousand order ids is a second table to read, not a
    way to choose."""
    ids = pd.Series([f"ORD{n}" for n in range(MAX_PICKABLE + 1)])
    assert kind_of(ids) == "contains"


def test_the_boundary_between_ticking_and_typing_is_inclusive():
    assert kind_of(pd.Series([f"x{n}" for n in range(MAX_PICKABLE)])) == "pick"


def test_dates_offer_a_range():
    days = pd.to_datetime(pd.Series(["2026-01-01", "2026-01-02"]))
    assert kind_of(days) == "range"


def test_a_column_of_true_and_false_is_ticked_not_ranged():
    """Booleans are numeric to pandas, and 'quantity at least 0.5' is not a
    question anybody asks of a flag."""
    assert kind_of(pd.Series([True, False, True])) == "pick"


# --- what a control should offer --------------------------------------------

def test_a_tick_list_offers_each_value_once_in_the_order_it_appears(orders):
    assert options_for(orders["venue"]) == ["TSE", "NASDAQ", "LSE"]


def test_a_tick_list_does_not_offer_blank(orders):
    column = pd.Series(["BUY", None, "SELL"])
    assert options_for(column) == ["BUY", "SELL"]


def test_a_range_covers_the_values_present(orders):
    assert bounds_of(orders["qty"]) == (50, 400_000)


def test_a_range_over_an_empty_column_has_no_bounds():
    assert bounds_of(pd.Series([], dtype=float)) == (None, None)


def test_a_range_ignores_the_blanks_when_finding_its_bounds():
    assert bounds_of(pd.Series([None, 3.0, 9.0])) == (3.0, 9.0)


# --- narrowing ---------------------------------------------------------------

def test_ticking_one_value_keeps_that_column_only(orders):
    kept = apply(orders, {"side": ColumnFilter(values=["BUY"])})
    assert _syms(kept) == ["6981.JP", "VOD.LN", "AAPL.US"]


def test_ticking_two_values_keeps_either(orders):
    kept = apply(orders, {"venue": ColumnFilter(values=["TSE", "LSE"])})
    assert len(kept) == 3


def test_two_columns_filtered_at_once_narrow_rather_than_widen(orders):
    """The whole point: side is BUY *and* quantity over a hundred thousand.
    A single search box cannot say this at all."""
    kept = apply(orders, {
        "side": ColumnFilter(values=["BUY"]),
        "qty": ColumnFilter(minimum=100_000),
    })
    assert _syms(kept) == ["VOD.LN"]


def test_three_columns_at_once_still_narrow(orders):
    kept = apply(orders, {
        "side": ColumnFilter(values=["BUY", "SELL"]),
        "venue": ColumnFilter(values=["NASDAQ"]),
        "qty": ColumnFilter(maximum=100),
    })
    assert _syms(kept) == ["AAPL.US"]          # the 50-share one


def test_a_floor_and_a_ceiling_on_one_column_meet_in_the_middle(orders):
    kept = apply(orders, {"qty": ColumnFilter(minimum=100, maximum=1000)})
    assert sorted(kept["qty"]) == [100, 300]


def test_a_bound_is_inclusive(orders):
    kept = apply(orders, {"qty": ColumnFilter(minimum=400_000)})
    assert list(kept["qty"]) == [400_000]


def test_typing_into_a_high_cardinality_column_matches_part_of_a_value(orders):
    kept = apply(orders, {"sym": ColumnFilter(contains="6981")})
    assert _syms(kept) == ["6981.JP", "6981.JP"]


def test_typing_ignores_case(orders):
    assert len(apply(orders, {"sym": ColumnFilter(contains="aapl")})) == 2


def test_typing_matches_the_text_rather_than_a_pattern(orders):
    """'.JP' is a suffix somebody typed, not any-character-then-JP."""
    kept = apply(orders, {"sym": ColumnFilter(contains=".JP")})
    assert _syms(kept) == ["6981.JP", "6981.JP"]


def test_typing_only_spaces_filters_nothing(orders):
    assert len(apply(orders, {"sym": ColumnFilter(contains="   ")})) == 5


def test_a_column_with_nothing_set_filters_nothing(orders):
    assert len(apply(orders, {"sym": ColumnFilter()})) == 5


def test_no_filters_at_all_returns_the_table(orders):
    assert len(apply(orders, {})) == 5


def test_a_condition_nobody_can_meet_returns_an_empty_table(orders):
    kept = apply(orders, {"side": ColumnFilter(values=["HOLD"])})
    assert kept.empty and list(kept.columns) == list(orders.columns)


# --- blanks ------------------------------------------------------------------

def test_a_blank_is_not_a_match_for_a_range():
    """A row with no quantity is not a row over a hundred thousand."""
    frame = pd.DataFrame({"qty": [None, 5.0, 500.0]})
    assert list(apply(frame, {"qty": ColumnFilter(minimum=1)})["qty"]) == [5.0, 500.0]


def test_a_blank_is_not_a_match_for_a_ceiling_either():
    frame = pd.DataFrame({"qty": [None, 5.0, 500.0]})
    assert list(apply(frame, {"qty": ColumnFilter(maximum=100)})["qty"]) == [5.0]


def test_a_blank_is_not_a_match_for_typed_text():
    frame = pd.DataFrame({"sym": [None, "AAPL"]})
    assert list(apply(frame, {"sym": ColumnFilter(contains="a")})["sym"]) == ["AAPL"]


# --- being edited underneath -------------------------------------------------

def test_a_filter_on_a_column_that_has_gone_is_ignored(orders):
    """A dashboard can have its columns changed while somebody has a filter
    set; that should redraw the table, not raise."""
    kept = apply(orders, {"gone": ColumnFilter(values=["x"]),
                          "side": ColumnFilter(values=["BUY"])})
    assert len(kept) == 3


def test_an_empty_table_comes_back_as_it_went_in():
    empty = pd.DataFrame({"sym": []})
    assert apply(empty, {"sym": ColumnFilter(values=["x"])}).empty


def test_nothing_at_all_is_survivable():
    assert apply(None, {"a": ColumnFilter(values=["x"])}) is None


# --- saying what is being hidden ---------------------------------------------

def test_the_caption_names_the_column_and_the_values(orders):
    told = summary(orders, {"side": ColumnFilter(values=["BUY"])})
    assert told == "side: BUY"


def test_the_caption_shortens_a_long_tick_list(orders):
    told = summary(orders, {"venue": ColumnFilter(values=["A", "B", "C", "D", "E"])})
    assert told == "venue: A, B, C +2"


def test_the_caption_joins_several_columns(orders):
    told = summary(orders, {"side": ColumnFilter(values=["BUY"]),
                            "qty": ColumnFilter(minimum=100)})
    assert "side: BUY" in told and "qty" in told and "·" in told


def test_the_caption_says_nothing_when_nothing_is_filtered(orders):
    assert summary(orders, {"side": ColumnFilter()}) == ""


def test_the_caption_skips_a_column_that_has_gone(orders):
    assert summary(orders, {"gone": ColumnFilter(values=["x"])}) == ""


def test_the_count_is_of_columns_actually_narrowed():
    assert active_count({"a": ColumnFilter(values=["x"]),
                         "b": ColumnFilter(),
                         "c": ColumnFilter(minimum=3)}) == 2


def test_the_count_of_nothing_is_zero():
    assert active_count({}) == 0 and active_count(None) == 0


# --- a clock is a clock, not 1 January 1970 ----------------------------------
# prepare() anchors a time-of-day column to 1970 so Streamlit prints "09:15:00"
# instead of "9 hours". The filter controls have to undo that, or a reader
# asking to see the open gets a date picker on a day nobody cares about.

def test_a_time_of_day_column_is_recognised_under_its_anchor():
    from kdbmonitor.ui.tables import is_clock
    clock = pd.to_datetime(pd.Series(["1970-01-01 09:15", "1970-01-01 09:30"]))
    assert is_clock(clock)


def test_a_real_date_column_is_not_mistaken_for_a_clock():
    from kdbmonitor.ui.tables import is_clock
    days = pd.to_datetime(pd.Series(["2026-01-01 09:15", "2026-01-02 09:30"]))
    assert not is_clock(days)


def test_an_empty_or_untyped_column_is_not_a_clock():
    from kdbmonitor.ui.tables import is_clock
    assert not is_clock(pd.Series(["09:15", "09:30"]))
    assert not is_clock(pd.to_datetime(pd.Series([], dtype="object")))


def test_the_controls_offer_a_clock_as_words_to_tick():
    from kdbmonitor.ui.tables import filterable
    frame = pd.DataFrame({
        "Time": pd.to_datetime(["1970-01-01 09:15", "1970-01-01 09:30"]),
        "vol": [10, 20]})
    against = filterable(frame, ["", ""])
    assert list(against["Time"]) == ["09:15:00", "09:30:00"]
    assert kind_of(against["Time"]) == "pick"


def test_ticking_a_time_off_that_list_keeps_that_bucket():
    from kdbmonitor.ui.tables import filterable
    frame = pd.DataFrame({
        "Time": pd.to_datetime(["1970-01-01 09:15", "1970-01-01 09:30"]),
        "vol": [10, 20]})
    against = filterable(frame, ["", ""])
    kept = apply(against, {"Time": ColumnFilter(values=["09:15:00"])})
    assert list(frame.loc[kept.index]["vol"]) == [10]


def test_a_clock_keeps_the_words_its_own_format_asked_for():
    from kdbmonitor.ui.tables import filterable
    frame = pd.DataFrame({
        "Time": pd.to_datetime(["1970-01-01 09:15", "1970-01-01 09:30"])})
    assert list(filterable(frame, ["%H:%M"])["Time"]) == ["09:15", "09:30"]


def test_a_column_that_is_not_a_clock_is_handed_over_untouched():
    from kdbmonitor.ui.tables import filterable
    frame = pd.DataFrame({"sym": ["A", "B"], "qty": [1, 2]})
    out = filterable(frame, ["", ""])
    assert list(out["sym"]) == ["A", "B"] and list(out["qty"]) == [1, 2]


def test_filtering_leaves_the_table_itself_alone():
    """The controls work against a readable copy; the frame that gets drawn —
    and printed — keeps its types so the columns still sort as numbers."""
    from kdbmonitor.ui.tables import filterable
    frame = pd.DataFrame({
        "Time": pd.to_datetime(["1970-01-01 09:15", "1970-01-01 09:30"])})
    filterable(frame, [""])
    assert pd.api.types.is_datetime64_any_dtype(frame["Time"])


# --- what a filter has to survive ------------------------------------------
#
# The frame under a table is replaced every time a dashboard refreshes. These
# are the pieces that let a condition outlive the snapshot it was set against.

def test_a_ticked_value_stays_on_the_list_when_the_data_loses_it():
    """No BUYs on the book this second does not mean the reader stopped asking
    for BUYs — and a list that no longer offers it is a list that unticks it."""
    column = pd.Series(["SELL", "SELL", "SELL"])
    assert options_with(column, ["BUY"]) == ["SELL", "BUY"]


def test_a_value_the_data_still_has_is_not_offered_twice():
    column = pd.Series(["BUY", "SELL", "BUY"])
    assert options_with(column, ["BUY"]) == ["BUY", "SELL"]


def test_nothing_ticked_leaves_the_list_as_the_column_presents_it():
    column = pd.Series(["BUY", "SELL"])
    assert options_with(column, []) == options_for(column)


def test_a_column_narrowed_by_a_tick_list_keeps_its_tick_list():
    """Even once a refresh brings back more distinct values than a list is
    meant to show: the control that set a filter must be able to clear it."""
    column = pd.Series([f"id{i}" for i in range(MAX_PICKABLE + 5)])
    assert kind_of(column) == "contains"
    assert control_kind(column, {**blank(), "in": ["id1"]}) == "pick"


def test_a_column_narrowed_by_a_typed_box_keeps_the_box():
    column = pd.Series(["BUY", "SELL"])
    assert kind_of(column) == "pick"
    assert control_kind(column, {**blank(), "txt": "BU"}) == "contains"


def test_an_unfiltered_column_gets_whatever_its_values_ask_for():
    column = pd.Series(["BUY", "SELL"])
    assert control_kind(column, blank()) == "pick"
    assert control_kind(column, None) == "pick"


def test_a_number_column_is_a_range_whatever_is_remembered():
    """The pick/box pair swap places as a column's spread changes. A range does
    not, so nothing about it is worth holding still."""
    column = pd.Series([1, 2, 3])
    assert control_kind(column, {**blank(), "in": [1]}) == "range"


def test_what_was_typed_becomes_what_it_meant():
    filters = filters_from({"side": {"in": ["BUY"], "txt": "", "lo": None,
                                     "hi": None},
                            "qty": {"in": [], "txt": "", "lo": 50, "hi": 100}})
    assert filters["side"].values == ["BUY"] and filters["side"].active
    assert filters["qty"].minimum == 50 and filters["qty"].maximum == 100


def test_a_day_named_as_a_ceiling_means_all_of_it():
    """"Up to the 3rd" that hides the 3rd is a trap."""
    filters = filters_from({"when": {"in": [], "txt": "",
                                     "lo": date(2026, 8, 1),
                                     "hi": date(2026, 8, 3)}})
    assert filters["when"].minimum == pd.Timestamp("2026-08-01 00:00:00")
    assert filters["when"].maximum == pd.Timestamp("2026-08-03 23:59:59.999999999")


def test_a_day_is_remembered_as_a_day_so_it_can_go_back_in_the_box():
    """The store holds what was typed, not what it was read as — a ceiling
    already turned into the last instant of a day cannot be put back into a
    date picker."""
    raw = {"when": {"in": [], "txt": "", "lo": None, "hi": date(2026, 8, 3)}}
    filters_from(raw)
    assert raw["when"]["hi"] == date(2026, 8, 3)


def test_empty_controls_mean_nothing_is_filtered():
    assert not is_set(blank()) and not is_set(None)
    assert not active_count(filters_from({"side": blank()}))


def test_a_single_typed_thing_counts_as_filtered():
    assert is_set({**blank(), "txt": " BU "})
    assert is_set({**blank(), "lo": 0})
    assert not is_set({**blank(), "txt": "   "})
