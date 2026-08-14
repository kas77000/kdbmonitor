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


# --- on paper ----------------------------------------------------------------

from kdbmonitor.core.dashpdf import (  # noqa: E402
    GUTTER, MARGIN, PORTRAIT, header_height, paginate, plan_rows, row_gutter,
)


def test_with_no_gaps_a_row_starts_exactly_where_it_always_did():
    """The claim the whole feature rests on, checked against arithmetic rather
    than against a remembered number: y is the header plus every earlier row's
    height and one gutter each."""
    rows = [_row(height_in=1.0), _row(height_in=2.0), _row(height_in=1.5)]
    placed = paginate(rows)[0]

    y = MARGIN + header_height()
    for (part, y_top), row in zip(placed, rows):
        assert y_top == pytest.approx(y)
        y += row.height_in + GUTTER


def test_a_gap_pushes_everything_below_it_down_by_exactly_its_size():
    without = paginate([_row(height_in=1.0), _row(height_in=2.0)])
    with_gap = paginate([_row(height_in=1.0),
                         _row(height_in=2.0, gap_above_in=0.75)])
    assert (with_gap[0][1][1] - without[0][1][1]) == pytest.approx(0.75)


def test_a_gap_on_the_first_row_of_a_page_is_swallowed():
    """Leading space separates a heading from what precedes it. At the top of a
    page nothing does, and printing it would open a hole in the margin."""
    plain = paginate([_row(height_in=1.0)])
    lead = paginate([_row(height_in=1.0, gap_above_in=1.5)])
    assert lead[0][0][1] == plain[0][0][1]


def test_a_gap_mid_page_is_not_swallowed():
    pages = paginate([_row(height_in=1.0),
                      _row(height_in=1.0, gap_above_in=1.5)])
    assert len(pages) == 1
    assert pages[0][1][1] - pages[0][0][1] == pytest.approx(1.0 + GUTTER + 1.5)


def test_a_big_enough_gap_pushes_a_row_onto_the_next_page():
    tall = _row(height_in=4.0)
    assert len(paginate([tall, _row(height_in=4.0)])) == 1
    assert len(paginate([tall, _row(height_in=4.0, gap_above_in=3.0)])) == 2


def test_the_editor_page_break_preview_agrees_with_the_pdf():
    """plan_rows derives from paginate, so it must account for a gap without
    being told — or the preview promises a page break that does not happen."""
    rows = [_row(height_in=4.0), _row(height_in=4.0, gap_above_in=3.0)]
    assert [p.page for p in plan_rows(rows)] == [1, 2]
    assert plan_rows(rows)[1].starts_page


# --- the gutter between widgets ---------------------------------------------

def test_a_row_with_no_column_gap_keeps_the_standard_gutter():
    assert row_gutter(_row()) == GUTTER


def test_a_column_gap_adds_to_the_gutter_rather_than_replacing_it():
    assert row_gutter(_row(col_gap_in=0.5)) == pytest.approx(GUTTER + 0.5)


def test_a_column_gap_narrows_each_widget_by_its_share():
    from kdbmonitor.core.dashpdf import _widget_width_in

    widgets = [Widget(type="table", dataset="ds", title="A"),
               Widget(type="table", dataset="ds", title="B")]
    plain = Row(widgets=widgets, height_in=2.0)
    spaced = Row(widgets=widgets, height_in=2.0, col_gap_in=0.6)

    narrower = (_widget_width_in(plain, widgets[0], PORTRAIT.content_w)
                - _widget_width_in(spaced, widgets[0], PORTRAIT.content_w))
    # One gap between two widgets, shared evenly by the two of them.
    assert narrower == pytest.approx(0.3)


def test_a_single_widget_row_is_unaffected_by_a_column_gap():
    """There is nothing to separate, so the widget keeps the full width."""
    from kdbmonitor.core.dashpdf import _widget_width_in

    widget = Widget(type="table", dataset="ds", title="A")
    plain = Row(widgets=[widget], height_in=2.0)
    spaced = Row(widgets=[widget], height_in=2.0, col_gap_in=2.0)
    assert (_widget_width_in(plain, widget, PORTRAIT.content_w)
            == _widget_width_in(spaced, widget, PORTRAIT.content_w))


# --- nothing changes until asked --------------------------------------------

import pathlib        # noqa: E402

from kdbmonitor.core.portability import import_dashboards_json   # noqa: E402

# The shipped bundles, loaded the way the app loads them — they carry
# connections and alerts alongside the dashboard, so json.loads is not enough.
EXAMPLES = sorted((pathlib.Path(__file__).resolve().parent.parent
                   / "docs" / "examples").glob("*.json"))


def _example(path):
    return import_dashboards_json(path.read_text(encoding="utf-8"))[0]


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_a_shipped_example_asks_for_no_extra_space(path):
    """These were built before the fields existed. If loading one produced a
    gap, every dashboard in the wild would have shifted on upgrade."""
    board = _example(path)
    for row in board.rows:
        assert row.gap_above_in == 0.0
        assert row.col_gap_in == 0.0


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_a_shipped_example_paginates_by_height_alone(path):
    """With no gaps, every row still lands at the header plus the heights and
    gutters before it — the same arithmetic as before this feature."""
    board = _example(path)
    if not board.rows:
        pytest.skip("no rows to place")

    pages = paginate(board.rows)
    y = MARGIN + header_height()
    for part, y_top in pages[0]:
        assert y_top == pytest.approx(y)
        y += part.height_in + GUTTER
