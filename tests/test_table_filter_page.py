"""The filter controls as they actually appear over a table.

The narrowing itself is tested in test_table_filter.py. This drives the page:
that a column of names gets a tick-list and a column of numbers gets two
boxes, that ticking one really does narrow the table drawn underneath, and
that two of them at once narrow rather than widen.
"""
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

SCRIPT = '''
import pandas as pd
import streamlit as st
from kdbmonitor.core.plotmodel import PlotModel
from kdbmonitor.ui import tables

frame = pd.DataFrame({
    "sym": ["6981.JP", "AAPL.US", "6981.JP", "VOD.LN", "AAPL.US",
            "6981.JP", "VOD.LN", "AAPL.US", "6981.JP"],
    "side": ["BUY", "SELL", "SELL", "BUY", "BUY", "SELL", "BUY", "SELL", "BUY"],
    "qty": [100, 250000, 300, 400000, 50, 900, 1200, 75, 999999],
})
pm = PlotModel(kind="table", title="Orders", columns=list(frame.columns),
               rows=[], frame=frame, column_formats=["", "", ""])
tables.render(pm, 400, "w1")
'''


@pytest.fixture
def at():
    app = AppTest.from_string(SCRIPT, default_timeout=90).run()
    assert not app.exception, [str(e.value) for e in app.exception]
    return app


def _drawn(app) -> pd.DataFrame:
    return app.dataframe[0].value


def test_the_table_is_drawn_whole_before_anybody_filters(at):
    assert len(_drawn(at)) == 9


def test_a_column_of_names_gets_a_tick_list(at):
    """Excel's checkbox list: the values that are actually in the column."""
    picks = [m for m in at.multiselect if m.key == "tbl_fin1_w1"]
    assert picks and picks[0].options == ["BUY", "SELL"]


def test_a_column_of_numbers_gets_a_floor_and_a_ceiling(at):
    keys = {n.key for n in at.number_input}
    assert {"tbl_flo2_w1", "tbl_fhi2_w1"} <= keys


def test_a_column_of_many_codes_gets_a_box_to_type_in(at):
    """Nine syms is under the tick-list limit, so this one is a list too —
    what matters is that the column got its own control either way."""
    assert any(k.startswith("tbl_f") for k in
               [m.key for m in at.multiselect] + [t.key for t in at.text_input])


def test_the_search_box_is_still_there_for_a_quick_lookup(at):
    assert any(t.key == "tbl_q_w1" for t in at.text_input)


def test_ticking_one_value_narrows_the_table(at):
    [m for m in at.multiselect if m.key == "tbl_fin1_w1"][0].set_value(["BUY"])
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert set(_drawn(at)["side"]) == {"BUY"} and len(_drawn(at)) == 5


def test_a_floor_on_a_number_narrows_the_table(at):
    [n for n in at.number_input if n.key == "tbl_flo2_w1"][0].set_value(100000)
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert sorted(_drawn(at)["qty"]) == [250000, 400000, 999999]


def test_two_columns_at_once_narrow_rather_than_widen(at):
    """The thing one search box could not do."""
    [m for m in at.multiselect if m.key == "tbl_fin1_w1"][0].set_value(["BUY"])
    [n for n in at.number_input if n.key == "tbl_flo2_w1"][0].set_value(100000)
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert list(_drawn(at)["sym"]) == ["VOD.LN", "6981.JP"]


def test_the_page_says_how_much_it_is_hiding_and_why(at):
    [m for m in at.multiselect if m.key == "tbl_fin1_w1"][0].set_value(["BUY"])
    at.run()
    printed = " ".join(str(c.value) for c in at.caption)
    assert "5 of 9 rows" in printed and "side: BUY" in printed


def test_search_and_filters_apply_together(at):
    [m for m in at.multiselect if m.key == "tbl_fin1_w1"][0].set_value(["BUY"])
    at.run()
    [t for t in at.text_input if t.key == "tbl_q_w1"][0].set_value("6981")
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert list(_drawn(at)["sym"]) == ["6981.JP", "6981.JP"]


def test_clearing_puts_every_row_back(at):
    [m for m in at.multiselect if m.key == "tbl_fin1_w1"][0].set_value(["BUY"])
    at.run()
    [b for b in at.button if b.key == "tbl_clear_w1"][0].click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert len(_drawn(at)) == 9


def test_there_is_nothing_to_clear_until_something_is_filtered(at):
    assert not [b for b in at.button if b.key == "tbl_clear_w1"]


SHORT_SCRIPT = '''
import pandas as pd
from kdbmonitor.core.plotmodel import PlotModel
from kdbmonitor.ui import tables

frame = pd.DataFrame({"sym": ["A", "B", "C", "D", "E"],
                      "qty": [1, 2, 3, 4, 5]})
pm = PlotModel(kind="table", title="Few", columns=list(frame.columns),
               rows=[], frame=frame, column_formats=["", ""])
tables.render(pm, 400, "w1")
'''


def test_a_short_table_is_left_plain():
    """Five rows do not need hunting through, and the controls would cost more
    of the page than the scrolling they save."""
    app = AppTest.from_string(SHORT_SCRIPT, default_timeout=90).run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert not app.multiselect and not app.text_input and not app.number_input
    assert len(app.dataframe[0].value) == 5
