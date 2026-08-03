"""Per-column widths on a table widget.

A table has two outputs and the width has to mean the same thing in both: on
screen a column is sized in pixels by Streamlit, on the page in its share of the
paper. So the setting is a *name* — narrow, medium, wide — and each renderer
spends its own currency on it.

The rule being bought is that a column stops arguing its case from its own
content. Automatic width is proportional to the longest text in a column, which
is right until one note field earns half the table.
"""
import pandas as pd
import pytest

from kdbmonitor.core.dashboard_models import Widget
from kdbmonitor.core.dataset import DatasetResult
from kdbmonitor.core.plotmodel import build_plot_model
from kdbmonitor.core.render_mpl import (
    TABLE_FONT, TABLE_MIN_FONT, _column_widths, table_fit_font,
)
from kdbmonitor.ui.tables import prepare

COLUMNS = ["sym", "note"]
LONG = "a reason nobody needs to read all of, at length, in full"
ROWS = [["VOD.L", LONG]]


def _widths(widths=None):
    return _column_widths(COLUMNS, ROWS, widths)


# --- the share of the page a column gets --------------------------------------

def test_by_default_the_longest_text_takes_the_width():
    sym, note = _widths()
    assert note > sym * 5           # the defect, stated: 'note' eats the table


def test_naming_a_width_stops_a_column_pleading_its_own_length():
    was, now = _widths()[1], _widths(["", "small"])[1]
    # From five sixths of the table to well under two thirds: 'note' is now
    # charged for the eight characters 'narrow' means, not the fifty-six it
    # happens to hold.
    assert was > 0.8 and now < 0.65


def test_the_named_widths_are_ordered_as_they_read():
    narrow, medium, wide = (_widths(["", w])[1]
                            for w in ("small", "medium", "large"))
    assert narrow < medium < wide


def test_a_named_width_does_not_depend_on_what_is_in_the_column():
    """The whole point: two tables whose note column differs wildly in length
    print that column at the same width once it has been set."""
    short = _column_widths(COLUMNS, [["VOD.L", "ok"]], ["", "small"])
    long = _column_widths(COLUMNS, ROWS, ["", "small"])
    assert short == long


def test_the_shares_always_add_up_to_the_whole_width():
    assert sum(_widths(["small", "large"])) == pytest.approx(1.0)


def test_an_unknown_width_falls_back_to_automatic():
    """A dashboard is stored data and can be hand-edited."""
    assert _widths(["", "enormous"]) == _widths()


def test_a_width_list_shorter_than_the_table_is_not_an_error():
    assert _widths(["small"]) == _column_widths(COLUMNS, ROWS, ["small", ""])


# --- and what that does to the type size --------------------------------------

def test_a_narrowed_column_does_not_drag_the_type_size_down():
    """It took no part in choosing the size — it is cut to its box instead.
    Letting it bind would shrink the whole table's text, which is the opposite
    of what narrowing it was for."""
    assert (table_fit_font(COLUMNS, ROWS, 4.0, ["", "small"])
            > table_fit_font(COLUMNS, ROWS, 4.0))


def test_a_table_of_nothing_but_named_widths_keeps_the_preferred_size():
    assert table_fit_font(COLUMNS, ROWS, 4.0, ["small", "small"]) == TABLE_FONT


def test_the_columns_left_automatic_still_have_their_say():
    """Narrowing the note must not let 'sym' overflow unnoticed: the automatic
    columns are still what the type size is chosen to fit."""
    tight = table_fit_font(["sym"], [["RELIANCE.IN"]], 0.4, [""])
    assert tight < TABLE_MIN_FONT


# --- the same setting, on screen ----------------------------------------------

def _frame():
    return pd.DataFrame({"sym": ["VOD.L"], "qty": [10.0]})


def test_a_width_reaches_the_column_config():
    _, config = prepare(["sym", "qty"], ["", ""], _frame(), ["small", ""])
    assert config["sym"]["width"] == "small"
    assert "qty" not in config


def test_a_column_can_be_both_formatted_and_narrowed():
    """Two separate questions — the format branches have no business knowing
    about the width, so it is applied over whatever they decided."""
    _, config = prepare(["sym", "qty"], ["", ",.0f"], _frame(), ["", "large"])
    assert config["qty"]["width"] == "large"
    assert config["qty"]["type_config"]["format"] == "localized"


def test_an_unknown_width_is_never_handed_to_streamlit():
    """One bad value would otherwise cost the whole table, not the one column."""
    _, config = prepare(["sym", "qty"], ["", ""], _frame(), ["enormous", ""])
    assert config == {}


def test_no_widths_at_all_configures_nothing_new():
    _, config = prepare(["sym", "qty"], ["", ""], _frame(), None)
    assert config == {}


# --- from the widget's spec to the renderers ----------------------------------

def _model(spec):
    results = {"d": DatasetResult("d", _frame(), "q", None, row_count=1)}
    return build_plot_model(
        Widget(type="table", dataset="d", title="T", spec=spec), results)


def test_the_spec_carries_the_widths_to_the_model():
    pm = _model({"columns": ["sym", "qty"], "widths": {"qty": "small"}})
    assert pm.column_widths == ["", "small"]


def test_a_renamed_header_keeps_the_width_it_was_given():
    """Widths key off the real column name, like formats — renaming a header is
    presentation and must not silently widen the column again."""
    pm = _model({"columns": ["sym", "qty"], "labels": {"qty": "Quantity"},
                 "widths": {"qty": "small"}})
    assert pm.columns == ["sym", "Quantity"]
    assert pm.column_widths == ["", "small"]


def test_a_table_saved_before_widths_existed_has_none():
    pm = _model({"columns": ["sym", "qty"]})
    assert pm.column_widths == ["", ""]


# --- actually drawn on a page --------------------------------------------------

def _drawn(widths, width_in: float = 7.07):
    import matplotlib.pyplot as plt

    from kdbmonitor.core import theme
    from kdbmonitor.core.plotmodel import PlotModel
    from kdbmonitor.core.render_mpl import draw

    pm = PlotModel(kind="table", title="T", columns=COLUMNS, rows=ROWS,
                   column_widths=widths)
    theme.apply_seaborn_theme()
    fig = plt.figure(figsize=(8.27, 11.69))
    ax = fig.add_axes([0.1, 0.3, width_in / 8.27, 2.5 / 11.69])
    draw(ax, pm)
    fig.canvas.draw()
    printed = [c.get_text().get_text()
               for c in ax.tables[0].get_celld().values()]
    size = ax.tables[0].get_celld()[(0, 0)].get_fontsize()
    plt.close(fig)
    return printed, size


def test_a_narrowed_column_is_cut_to_its_box_and_says_so():
    printed, _ = _drawn(["", "small"])
    assert LONG not in printed
    assert any(t.endswith("…") for t in printed)


def test_the_column_beside_it_is_left_whole():
    """Only the column the author narrowed gives anything up — the rest still
    fit, and cutting them would cost characters the width was never short of."""
    printed, _ = _drawn(["", "small"])
    assert "VOD.L" in printed


def test_narrowing_a_column_buys_the_table_a_bigger_type_size():
    """In a slot too narrow to hold it, the note used to shrink every figure
    on the page with it. Now it gives way instead."""
    assert _drawn(["", "small"], 2.5)[1] > _drawn(["", ""], 2.5)[1]
