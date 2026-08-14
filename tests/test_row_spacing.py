"""Air above a row, and between the widgets inside it.

A dashboard built in sections reads as one undifferentiated column: the second
heading sits hard against the first table, closer to the section it ends than
to the one it introduces. These tests hold the two fields that fix it, and hold
them to costing nothing until somebody asks.
"""
import pytest

from kdbmonitor.core.dashboard_models import (
    Dashboard, Row, Widget, dashboard_from_json, dashboard_to_json,
    row_from_dict,
)


def _row(**kw) -> Row:
    base = dict(widgets=[Widget(type="table", dataset="ds", title="T")],
                height_in=2.0)
    base.update(kw)
    return Row(**base)


# --- the model ---------------------------------------------------------------

def test_a_new_row_asks_for_no_extra_space():
    """0.0 means "exactly what this app did yesterday". Every existing
    dashboard depends on it."""
    row = Row()
    assert row.gap_above_in == 0.0
    assert row.col_gap_in == 0.0


def test_both_gaps_survive_a_save_and_a_load():
    board = Dashboard(id=None, name="d", rows=[
        _row(gap_above_in=0.6, col_gap_in=0.25)])
    back = dashboard_from_json(dashboard_to_json(board))
    assert back.rows[0].gap_above_in == 0.6
    assert back.rows[0].col_gap_in == 0.25


def test_a_row_stored_before_these_existed_loads_with_no_gaps():
    """No migration: an absent key reads as 0.0, like every other."""
    row = row_from_dict({"widgets": [], "height_in": 3.0})
    assert (row.gap_above_in, row.col_gap_in) == (0.0, 0.0)
    assert row.height_in == 3.0


@pytest.mark.parametrize("stored", ["", None, "wide", [], {}])
def test_a_hand_edited_gap_that_is_not_a_number_falls_back(stored):
    """A bundle can be edited by hand before it is imported. A bad value
    should cost that field its value, not cost pagination a TypeError."""
    row = row_from_dict({"widgets": [], "gap_above_in": stored,
                         "col_gap_in": stored, "height_in": stored})
    assert row.gap_above_in == 0.0
    assert row.col_gap_in == 0.0
    assert row.height_in == 2.5


def test_a_gap_written_as_text_is_still_read_as_a_number():
    row = row_from_dict({"widgets": [], "gap_above_in": "0.5"})
    assert row.gap_above_in == 0.5
