"""The tree as it actually appears over a table.

Which rows land under which heading is tested in test_table_group.py. This
drives the page: that the picker is there, that choosing a column really does
fold the rows under it, that the choice is the reader's and survives the
refresh underneath them, and that a table nobody has grouped is the flat list
it always was.
"""
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from kdbmonitor.core import tablegroup as tg
from kdbmonitor.ui.tables import NO_GROUP, group_key

GROUP = group_key("w1")
SEARCH = "tbl_q_w1"

SCRIPT = '''
import pandas as pd
import streamlit as st
from kdbmonitor.core.plotmodel import PlotModel
from kdbmonitor.ui import tables

frame = pd.DataFrame({
    "venue": ["LSE", "BATS", "LSE", "CHIX", "LSE", "BATS", "LSE", "CHIX",
              "LSE", "BATS", "CHIX", "LSE"],
    "sym": ["VOD.LN", "BP.LN", "AZN.LN", "VOD.LN", "BP.LN", "AZN.LN",
            "VOD.LN", "BP.LN", "AZN.LN", "VOD.LN", "BP.LN", "AZN.LN"],
    "qty": [100, 250, 300, 400, 500, 600, 700, 800, 900, 1000, 1100, 1200],
})
pm = PlotModel(kind="table", title="Orders", columns=list(frame.columns),
               rows=[], frame=frame, column_formats=["", "", ""],
               group_by=st.session_state.get("author_group", ""))
tables.render(pm, 400, "w1")
'''


def _run(**state) -> AppTest:
    app = AppTest.from_string(SCRIPT, default_timeout=90)
    for k, v in state.items():
        app.session_state[k] = v
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]
    return app


@pytest.fixture
def at() -> AppTest:
    return _run()


def _headings(app) -> list[str]:
    return [b.proto.label for b in app.get("expander")]


def _open(app) -> list[bool]:
    return [b.proto.expanded for b in app.get("expander")]


def _picker(app):
    return [s for s in app.selectbox if s.key == GROUP][0]


def _captions(app) -> str:
    return " ".join(str(c.value) for c in app.caption)


# 600 rows under however many headings the test asks for, already grouped.
CROWDED = '''
import pandas as pd
import streamlit as st
from kdbmonitor.core.plotmodel import PlotModel
from kdbmonitor.ui import tables

n = st.session_state["baskets"]
frame = pd.DataFrame({"basket": [f"b{i % n:03d}" for i in range(600)],
                      "qty": list(range(600))})
pm = PlotModel(kind="table", title="Orders", columns=list(frame.columns),
               rows=[], frame=frame, column_formats=["", ""],
               group_by="basket")
tables.render(pm, 400, "w1")
'''


def _crowded(baskets: int) -> AppTest:
    app = AppTest.from_string(CROWDED, default_timeout=90)
    app.session_state["baskets"] = baskets
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]
    return app


# --- the picker --------------------------------------------------------------

def test_a_table_offers_to_gather_its_rows_under_a_column(at):
    assert _picker(at).options[0] == NO_GROUP
    assert "venue" in _picker(at).options


def test_a_column_with_a_value_per_row_is_not_offered_as_a_heading(at):
    """Twelve quantities would make twelve headings of one row each — the same
    table, plus a fold between every row."""
    assert "qty" not in _picker(at).options


def test_a_table_starts_flat_when_nobody_has_asked_for_anything_else(at):
    assert _picker(at).value == NO_GROUP
    assert not _headings(at)
    assert len(at.dataframe[0].value) == 12


# --- grouping on the fly -----------------------------------------------------

def _grouped(app, column="venue") -> AppTest:
    _picker(app).set_value(column)
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]
    return app


def test_choosing_a_column_folds_the_rows_under_its_values(at):
    _grouped(at)
    assert [h.split("·")[0].strip() for h in _headings(at)] == \
        ["LSE", "BATS", "CHIX"]


def test_each_heading_says_how_many_rows_are_behind_it(at):
    _grouped(at)
    assert _headings(at)[0].endswith("6")          # six LSE rows
    assert _headings(at)[2].endswith("3")          # three on CHIX


def test_the_rows_under_a_heading_are_that_value_s_rows(at):
    _grouped(at)
    assert list(at.dataframe[0].value["sym"]) == \
        ["VOD.LN", "AZN.LN", "BP.LN", "VOD.LN", "AZN.LN", "AZN.LN"]


def test_no_row_is_lost_to_the_folding(at):
    _grouped(at)
    assert sum(len(d.value) for d in at.dataframe) == 12


def test_the_column_gathered_on_is_not_repeated_in_every_row(at):
    """Its value is the heading; printing it again down a column of its own
    says the same thing twelve times."""
    _grouped(at)
    assert "venue" not in at.dataframe[0].value.columns


def test_the_page_says_how_many_groups_there_are_and_on_what(at):
    _grouped(at)
    assert "3 groups by venue" in _captions(at)


def test_changing_the_column_regroups_without_touching_the_dashboard(at):
    """The whole point: the same table read by venue at nine and by symbol at
    ten, without opening the editor."""
    _grouped(at)
    _grouped(at, "sym")
    assert [h.split("·")[0].strip() for h in _headings(at)] == \
        ["VOD.LN", "BP.LN", "AZN.LN"]


def test_flattening_it_again_puts_the_table_back(at):
    _grouped(at)
    _picker(at).set_value(NO_GROUP)
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert not _headings(at)
    assert len(at.dataframe[0].value) == 12


# --- what starts open --------------------------------------------------------

def test_a_few_headings_start_open_because_that_is_the_whole_table(at):
    _grouped(at)
    assert _open(at) == [True, True, True]


def test_many_headings_start_folded_so_the_page_is_a_summary():
    """A dozen groups opened at once is a wall to scroll past, not a summary.
    The reader opens the one they came for."""
    app = _crowded(12)
    assert len(_headings(app)) == 12 and not any(_open(app))


def test_a_search_opens_the_headings_it_left_rows_under(at):
    """"4 of 12 rows" over four closed doors is that line telling the truth and
    showing nothing."""
    _grouped(at)
    [t for t in at.text_input if t.key == SEARCH][0].set_value("BP.LN")
    at.run()
    assert all(_open(at))


# --- it narrows nothing ------------------------------------------------------

def test_grouping_hides_no_rows_so_there_is_nothing_to_clear(at):
    _grouped(at)
    assert not [b for b in at.button if b.key == "tbl_clearall_w1"]


def test_a_search_inside_a_grouped_table_narrows_it(at):
    _grouped(at)
    [t for t in at.text_input if t.key == SEARCH][0].set_value("BP.LN")
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert sum(len(d.value) for d in at.dataframe) == 4
    assert "4 of 12 rows" in _captions(at)


def test_clearing_a_search_leaves_the_grouping_alone(at):
    """Clear all puts every row back. The reader did not ask for the tree to be
    taken down, and grouping was hiding nothing anyway."""
    _grouped(at)
    [t for t in at.text_input if t.key == SEARCH][0].set_value("BP.LN")
    at.run()
    [b for b in at.button if b.key == "tbl_clearall_w1"][0].click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert _picker(at).value == "venue"
    assert sum(len(d.value) for d in at.dataframe) == 12


# --- the author's starting point ---------------------------------------------

def test_a_table_built_grouped_arrives_grouped():
    app = _run(author_group="venue")
    assert _picker(app).value == "venue"
    assert len(_headings(app)) == 3


def test_the_reader_can_flatten_a_table_its_author_grouped():
    app = _run(author_group="venue")
    _picker(app).set_value(NO_GROUP)
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert not _headings(app)


def test_the_author_s_grouping_does_not_spring_back_on_the_next_refresh():
    """A dashboard re-runs on a timer. A reader who turned the tree off and
    found it back four seconds later would have no way to keep it off."""
    app = _run(author_group="venue")
    _picker(app).set_value(NO_GROUP)
    app.run()
    app.run()                      # the refresh
    assert not app.exception, [str(e.value) for e in app.exception]
    assert _picker(app).value == NO_GROUP and not _headings(app)


def test_the_reader_s_grouping_outlasts_a_refresh_too(at):
    _grouped(at, "sym")
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert _picker(at).value == "sym" and len(_headings(at)) == 3


# --- a short table -----------------------------------------------------------

SHORT = '''
import pandas as pd
import streamlit as st
from kdbmonitor.core.plotmodel import PlotModel
from kdbmonitor.ui import tables

frame = pd.DataFrame({"venue": ["LSE", "BATS", "LSE", "CHIX"],
                      "qty": [1, 2, 3, 4]})
pm = PlotModel(kind="table", title="Few", columns=list(frame.columns),
               rows=[], frame=frame, column_formats=["", ""],
               group_by=st.session_state.get("author_group", ""))
tables.render(pm, 400, "w1")
'''


def test_a_short_table_nobody_has_grouped_is_still_left_plain():
    app = AppTest.from_string(SHORT, default_timeout=90).run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert not app.selectbox and len(app.dataframe[0].value) == 4


def test_a_short_table_that_is_grouped_keeps_the_picker_that_can_flatten_it():
    """Otherwise a refresh that briefly returns four rows leaves the reader
    inside a tree with no way out of it."""
    app = AppTest.from_string(SHORT, default_timeout=90)
    app.session_state["author_group"] = "venue"
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert _picker(app).value == "venue" and len(_headings(app)) == 3


# --- more headings than a tree can carry -------------------------------------

def test_too_many_headings_are_listed_flat_rather_than_folded():
    """600 rows under 600 headings is the table it started as, plus a fold
    between every row. The rows are all still there."""
    app = _crowded(tg.MAX_GROUPS + 1)
    assert not _headings(app)
    assert len(app.dataframe[0].value) == 600


def test_and_the_page_says_why_rather_than_appearing_to_ignore_the_picker():
    said = _captions(_crowded(tg.MAX_GROUPS + 1))
    assert f"{tg.MAX_GROUPS + 1} groups by basket" in said
    assert "too many to fold" in said
