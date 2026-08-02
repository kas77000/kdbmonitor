"""The filter controls as they actually appear over a table.

The narrowing itself is tested in test_table_filter.py. This drives the page:
that a column of names gets a tick-list and a column of numbers gets two
boxes, that ticking one really does narrow the table drawn underneath, and
that two of them at once narrow rather than widen.
"""
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from kdbmonitor.ui.tables import filter_key

SIDE = filter_key("w1", "side", "in")
QTY_LO = filter_key("w1", "qty", "lo")
QTY_HI = filter_key("w1", "qty", "hi")

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
    picks = [m for m in at.multiselect if m.key == SIDE]
    assert picks and picks[0].options == ["BUY", "SELL"]


def test_a_column_of_numbers_gets_a_floor_and_a_ceiling(at):
    keys = {n.key for n in at.number_input}
    assert {QTY_LO, QTY_HI} <= keys


def test_a_column_of_many_codes_gets_a_box_to_type_in(at):
    """Nine syms is under the tick-list limit, so this one is a list too —
    what matters is that the column got its own control either way."""
    assert any(k.startswith("tbl_f") for k in
               [m.key for m in at.multiselect] + [t.key for t in at.text_input])


def test_the_search_box_is_still_there_for_a_quick_lookup(at):
    assert any(t.key == "tbl_q_w1" for t in at.text_input)


def test_ticking_one_value_narrows_the_table(at):
    [m for m in at.multiselect if m.key == SIDE][0].set_value(["BUY"])
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert set(_drawn(at)["side"]) == {"BUY"} and len(_drawn(at)) == 5


def test_a_floor_on_a_number_narrows_the_table(at):
    [n for n in at.number_input if n.key == QTY_LO][0].set_value(100000)
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert sorted(_drawn(at)["qty"]) == [250000, 400000, 999999]


def test_two_columns_at_once_narrow_rather_than_widen(at):
    """The thing one search box could not do."""
    [m for m in at.multiselect if m.key == SIDE][0].set_value(["BUY"])
    [n for n in at.number_input if n.key == QTY_LO][0].set_value(100000)
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert list(_drawn(at)["sym"]) == ["VOD.LN", "6981.JP"]


def test_the_page_says_how_much_it_is_hiding_and_why(at):
    [m for m in at.multiselect if m.key == SIDE][0].set_value(["BUY"])
    at.run()
    printed = " ".join(str(c.value) for c in at.caption)
    assert "5 of 9 rows" in printed and "side: BUY" in printed


def test_search_and_filters_apply_together(at):
    [m for m in at.multiselect if m.key == SIDE][0].set_value(["BUY"])
    at.run()
    [t for t in at.text_input if t.key == "tbl_q_w1"][0].set_value("6981")
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert list(_drawn(at)["sym"]) == ["6981.JP", "6981.JP"]


def test_clearing_puts_every_row_back(at):
    [m for m in at.multiselect if m.key == SIDE][0].set_value(["BUY"])
    at.run()
    [b for b in at.button if b.key == "tbl_clear_w1"][0].click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert len(_drawn(at)) == 9


def test_there_is_nothing_to_clear_until_something_is_filtered(at):
    assert not [b for b in at.button if b.key == "tbl_clear_w1"]


# --- getting every row back -------------------------------------------------
#
# Narrowing a table five columns deep takes five actions; widening it again
# should take one. And the way back has to be where somebody is looking when
# they want it — beside the line saying rows are hidden, not behind the door
# they would have to open to carry on filtering.

def _popovers(app) -> list:
    """What the Filters button says, without opening it."""
    return [b.proto.popover.label for b in app.get("popover")]


def _clear_all(app):
    return [b for b in app.button if b.key == "tbl_clearall_w1"]


def test_the_way_back_sits_beside_the_line_that_says_rows_are_hidden(at):
    [m for m in at.multiselect if m.key == SIDE][0].set_value(["BUY"])
    at.run()
    assert _clear_all(at)


def test_one_click_puts_every_row_back_however_many_columns_were_narrowed(at):
    [m for m in at.multiselect if m.key == SIDE][0].set_value(["BUY"])
    [n for n in at.number_input if n.key == QTY_LO][0].set_value(100)
    at.run()
    assert len(_drawn(at)) < 9
    _clear_all(at)[0].click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert len(_drawn(at)) == 9
    assert [m for m in at.multiselect if m.key == SIDE][0].value == []
    assert [n for n in at.number_input if n.key == QTY_LO][0].value is None


def test_clearing_takes_the_search_box_with_it(at):
    """It says Clear all, and a table still hiding rows behind a search nobody
    can see would be the lie the caption exists to prevent."""
    [t for t in at.text_input if t.key == "tbl_q_w1"][0].set_value("6981")
    at.run()
    _clear_all(at)[0].click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert len(_drawn(at)) == 9
    assert [t for t in at.text_input if t.key == "tbl_q_w1"][0].value == ""


def test_a_search_alone_can_be_undone_the_same_way(at):
    [t for t in at.text_input if t.key == "tbl_q_w1"][0].set_value("6981")
    at.run()
    assert len(_drawn(at)) == 4 and _clear_all(at)


def test_the_way_back_is_not_offered_while_the_whole_table_is_showing(at):
    assert not _clear_all(at)


def test_the_button_says_how_many_columns_are_narrowed_without_being_opened(at):
    """A popover is a closed door: unopened, "Filters" cannot say whether any
    are set, and a reader has no reason to open it to find out."""
    assert _popovers(at) == ["Filters"]
    [m for m in at.multiselect if m.key == SIDE][0].set_value(["BUY"])
    [n for n in at.number_input if n.key == QTY_LO][0].set_value(100)
    at.run()
    assert _popovers(at) == ["Filters (2)"]


def test_the_count_is_this_run_s_rather_than_the_one_before(at):
    """It used to be read after the controls were drawn, which is a run late."""
    [m for m in at.multiselect if m.key == SIDE][0].set_value(["BUY"])
    at.run()
    assert _popovers(at) == ["Filters (1)"]
    [m for m in at.multiselect if m.key == SIDE][0].set_value([])
    at.run()
    assert _popovers(at) == ["Filters"]


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


# --- a filter through a refresh --------------------------------------------
#
# A dashboard re-runs its queries on a timer, so the frame under a table is a
# different frame every few seconds. A filter has to be a thing the reader set,
# not a thing the last snapshot happened to permit: watching one venue means
# watching it until you say otherwise, and a table that quietly widened itself
# on the tick would be showing rows somebody had asked to be rid of, under a
# control that still claimed to be narrowing.

REFRESHING = '''
import pandas as pd
import streamlit as st
from kdbmonitor.core.plotmodel import PlotModel
from kdbmonitor.ui import tables

# Each tick is the snapshot a refresh would bring back.
TICKS = {
    0: (["BUY", "SELL"] * 5, list(range(10))),
    # the ordinary case: same values on the book, different rows
    1: (["SELL", "BUY", "BUY", "SELL", "BUY", "SELL", "BUY", "BUY", "SELL"],
        list(range(100, 109))),
    # no BUYs on the book this second
    2: (["SELL"] * 9, list(range(9))),
    # briefly too few rows for the controls to be worth their room
    3: (["BUY", "SELL", "BUY"], [1, 2, 3]),
}
sides, qty = TICKS[st.session_state.get("tick", 0)]
frame = pd.DataFrame({"side": sides, "qty": qty})
pm = PlotModel(kind="table", title="Orders", columns=list(frame.columns),
               rows=[], frame=frame, column_formats=["", ""])
tables.render(pm, 400, "w1")
'''


def _refreshing(tick_to=None):
    app = AppTest.from_string(REFRESHING, default_timeout=90).run()
    [m for m in app.multiselect if m.key == SIDE][0].set_value(["BUY"])
    app.run()
    assert set(_drawn(app)["side"]) == {"BUY"}
    if tick_to is not None:
        app.session_state["tick"] = tick_to
        app.run()
    assert not app.exception, [str(e.value) for e in app.exception]
    return app


def _ticked(app) -> list:
    return [m for m in app.multiselect if m.key == SIDE][0].value


def test_a_refresh_keeps_the_filter_the_reader_set():
    app = _refreshing(tick_to=1)
    assert _ticked(app) == ["BUY"]
    assert set(_drawn(app)["side"]) == {"BUY"} and len(_drawn(app)) == 5


def test_a_snapshot_without_the_ticked_value_shows_nothing_rather_than_everything():
    """The honest answer to "show me the BUYs" when there are none is an empty
    table. Untick it and the whole book comes back unannounced."""
    app = _refreshing(tick_to=2)
    assert _ticked(app) == ["BUY"]
    assert len(_drawn(app)) == 0


def test_the_tick_comes_back_when_the_value_does():
    app = _refreshing(tick_to=2)
    app.session_state["tick"] = 1
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert _ticked(app) == ["BUY"] and len(_drawn(app)) == 5


def test_a_refresh_too_short_to_draw_controls_still_keeps_the_filter():
    """The row count decides whether the controls are worth their room — it
    must not decide whether the filter exists."""
    app = _refreshing(tick_to=3)
    assert _ticked(app) == ["BUY"]
    assert len(_drawn(app)) == 2
    app.session_state["tick"] = 1
    app.run()
    assert _ticked(app) == ["BUY"] and len(_drawn(app)) == 5


def test_a_narrowed_short_table_still_offers_the_way_out():
    """Whatever the row count, a filtered table keeps the button that clears
    it — otherwise a tick becomes a state there is no way to leave."""
    app = _refreshing(tick_to=3)
    [b for b in app.button if b.key == "tbl_clear_w1"][0].click().run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert len(_drawn(app)) == 3


def test_a_refresh_keeps_the_search_box_too():
    app = AppTest.from_string(REFRESHING, default_timeout=90).run()
    [t for t in app.text_input if t.key == "tbl_q_w1"][0].set_value("SELL")
    app.run()
    app.session_state["tick"] = 3      # the run that used to drop every control
    app.run()
    app.session_state["tick"] = 1
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert [t for t in app.text_input if t.key == "tbl_q_w1"][0].value == "SELL"
    assert set(_drawn(app)["side"]) == {"SELL"}


REORDERED = '''
import pandas as pd
import streamlit as st
from kdbmonitor.core.plotmodel import PlotModel
from kdbmonitor.ui import tables

frame = pd.DataFrame({"side": ["BUY", "SELL"] * 5, "venue": ["LSE", "BATS"] * 5})
if st.session_state.get("tick"):
    frame = frame[["venue", "side"]]      # the same columns, the other way round
pm = PlotModel(kind="table", title="Orders", columns=list(frame.columns),
               rows=[], frame=frame, column_formats=["", ""])
tables.render(pm, 400, "w1")
'''


def test_a_filter_follows_its_column_rather_than_its_position():
    """Filters were keyed by where a column sat, so a refresh that brought the
    columns back in another order handed one column's condition to another."""
    app = AppTest.from_string(REORDERED, default_timeout=90).run()
    [m for m in app.multiselect if m.key == SIDE][0].set_value(["BUY"])
    app.run()
    app.session_state["tick"] = 1
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]
    drawn = _drawn(app)
    assert set(drawn["side"]) == {"BUY"} and set(drawn["venue"]) == {"LSE"}
