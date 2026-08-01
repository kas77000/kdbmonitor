"""A table on screen sorts its values and can be searched.

The page and the screen want different things from one table. The page wants
text, already formatted. The screen wants the values — sorting the formatted
text puts 1,284.55 beside 1,2 and 9:15 before 10:00, so a column ordered by
clicking its header came out wrong in a way that looked deliberate.
"""
import pandas as pd
import pytest

from kdbmonitor.core.dashboard_models import Widget
from kdbmonitor.core.dataset import DatasetResult
from kdbmonitor.core.plotmodel import build_plot_model
from kdbmonitor.ui.tables import matching


def _results(df: pd.DataFrame) -> dict:
    return {"d": DatasetResult("d", df, "q", None, row_count=len(df))}


def _frame() -> pd.DataFrame:
    return pd.DataFrame({
        "sym": ["RELIANCE.IN", "INFY.IN", "TCS.IN"],
        "qty": [125000, 9000, 47000],
        "px": [1284.55, 1290.0, 903.25],
        "when": pd.to_datetime(["2026-07-30 09:15", "2026-07-30 10:00",
                                "2026-07-30 09:20"])})


def _table(**spec) -> Widget:
    return Widget(type="table", dataset="d", title="Orders", spec=spec)


# --- the values survive alongside the printed text ---------------------------

def test_a_table_carries_its_values_as_well_as_its_printed_text():
    pm = build_plot_model(_table(), _results(_frame()))
    assert pm.rows and pm.frame is not None
    assert list(pm.frame.columns) == pm.columns


def test_the_values_keep_their_types_so_a_sort_is_a_real_sort():
    """The formatted text sorts 125,000 below 47,000, because it compares '1'
    with '4'."""
    pm = build_plot_model(_table(formats={"qty": ",.0f"}), _results(_frame()))
    assert pd.api.types.is_numeric_dtype(pm.frame["qty"])
    assert pm.frame["qty"].sort_values().tolist() == [9000, 47000, 125000]
    printed = sorted(r[1] for r in pm.rows)
    assert printed[0].startswith("125")        # the text sort, and why it is wrong


def test_a_time_column_stays_a_time_so_it_orders_by_clock():
    pm = build_plot_model(_table(), _results(_frame()))
    assert pd.api.types.is_datetime64_any_dtype(pm.frame["when"])
    assert pm.frame["when"].is_monotonic_increasing is False
    assert pm.frame["when"].sort_values().iloc[0].hour == 9


def test_the_frame_wears_the_display_headers():
    pm = build_plot_model(_table(labels={"qty": "quantity"}),
                          _results(_frame()))
    assert "quantity" in pm.frame.columns and "qty" not in pm.frame.columns


def test_only_the_chosen_columns_travel():
    pm = build_plot_model(_table(columns=["sym", "qty"]), _results(_frame()))
    assert list(pm.frame.columns) == ["sym", "qty"]


def test_the_column_order_is_the_order_they_print():
    pm = build_plot_model(_table(columns=["qty", "sym"]), _results(_frame()))
    assert list(pm.frame.columns) == ["qty", "sym"]


def test_the_formats_travel_beside_the_columns():
    pm = build_plot_model(_table(columns=["sym", "qty"],
                                 formats={"qty": ",.0f"}), _results(_frame()))
    assert pm.column_formats == ["", ",.0f"]


def test_the_frame_is_a_copy_and_not_the_dataset_s_own():
    """A widget must not be able to edit the frame another widget reads."""
    frame = _frame()
    pm = build_plot_model(_table(), _results(frame))
    pm.frame.loc[0, "qty"] = -1
    assert frame.loc[0, "qty"] == 125000


def test_an_empty_table_still_carries_a_frame():
    pm = build_plot_model(_table(), _results(_frame().head(0)))
    assert pm.frame is not None and pm.frame.empty


# --- searching ---------------------------------------------------------------

def test_a_search_keeps_the_rows_that_mention_it():
    assert len(matching(_frame(), "INFY")) == 1


def test_a_search_looks_in_every_column():
    """A reader has a number in front of them and does not yet know which
    column it sits in."""
    assert len(matching(_frame(), "125000")) == 1
    assert len(matching(_frame(), "903.25")) == 1


def test_a_search_ignores_case():
    assert len(matching(_frame(), "infy")) == 1
    assert len(matching(_frame(), "InFy")) == 1


def test_a_search_matches_part_of_a_value():
    assert len(matching(_frame(), ".IN")) == 3


def test_an_empty_search_keeps_everything():
    for query in ("", "   ", None):
        assert len(matching(_frame(), query)) == 3


def test_a_search_matching_nothing_gives_nothing_rather_than_everything():
    assert len(matching(_frame(), "sasquatch")) == 0


def test_a_search_is_text_not_a_pattern():
    """Somebody typing a bracket is looking for a bracket, not writing a regex
    — and a stray one would otherwise raise rather than simply not match."""
    assert len(matching(_frame(), "RELIANCE.IN")) == 1
    assert len(matching(_frame(), "([")) == 0


def test_searching_an_empty_table_is_not_an_error():
    assert matching(_frame().head(0), "anything").empty


def test_a_search_reads_the_time_the_way_it_is_shown():
    assert len(matching(_frame(), "09:15")) == 1
