"""Gathering a table's rows under the value they share.

The tree drawn from this is tested in test_table_group_page.py. Here: which
columns are worth offering as headings, what a heading is called, and which
rows end up under which one.
"""
import pandas as pd
import pytest

from kdbmonitor.core import tablegroup as tg
from kdbmonitor.core.dashboard_models import Widget
from kdbmonitor.core.dataset import DatasetResult
from kdbmonitor.core.plotmodel import build_plot_model, referenced_columns


def _frame() -> pd.DataFrame:
    return pd.DataFrame({
        "venue": ["LSE", "BATS", "LSE", "CHIX", "LSE", "BATS"],
        "sym": ["VOD.LN", "VOD.LN", "BP.LN", "BP.LN", "AZN.LN", "AZN.LN"],
        "qty": [100, 250, 300, 400, 500, 600],
    })


def _keys(frame: pd.DataFrame, column: str) -> pd.Series:
    return tg.labels(frame[column])


# --- what a heading is called ------------------------------------------------

def test_a_heading_is_the_value_as_it_reads():
    assert tg.label_of("LSE") == "LSE"
    assert tg.label_of(42) == "42"


def test_rows_with_no_value_are_headed_the_way_an_empty_cell_prints():
    """The em dash a cell already uses, so a null reads the same in both
    places."""
    assert tg.label_of(None) == tg.MISSING
    assert tg.label_of(float("nan")) == tg.MISSING
    assert tg.label_of(pd.NaT) == tg.MISSING


def test_a_date_is_headed_as_a_date_rather_than_as_midnight():
    """A trade-date column headed '2026-08-05 00:00:00' says the same thing at
    twice the width, and the extra half is always zeroes."""
    assert tg.label_of(pd.Timestamp("2026-08-05")) == "2026-08-05"


def test_a_timestamp_that_is_not_midnight_keeps_its_time():
    assert tg.label_of(pd.Timestamp("2026-08-05 09:15:00")) == \
        "2026-08-05 09:15:00"


# --- which columns are worth offering ----------------------------------------

def test_a_column_of_a_few_repeated_values_is_offered():
    assert "venue" in tg.groupable(_frame())


def test_a_column_with_a_different_value_in_every_row_is_not_offered():
    """A heading per row is not a grouping — it is the table it started as,
    with a fold between every row."""
    frame = _frame()
    frame["order_id"] = [f"o{i}" for i in range(len(frame))]
    assert "order_id" not in tg.groupable(frame)


def test_a_column_with_more_values_than_a_tree_can_carry_is_not_offered():
    frame = pd.DataFrame({
        "basket": [f"b{i % (tg.MAX_GROUPS + 1)}" for i in range(400)],
        "qty": list(range(400))})
    assert "basket" not in tg.groupable(frame)
    assert tg.group_count(frame["basket"]) == tg.MAX_GROUPS + 1


def test_a_column_right_on_the_limit_is_still_offered():
    frame = pd.DataFrame({"basket": [f"b{i % tg.MAX_GROUPS}" for i in range(400)],
                          "qty": list(range(400))})
    assert "basket" in tg.groupable(frame)


def test_the_columns_are_offered_in_the_order_they_print():
    assert tg.groupable(_frame()) == ["venue", "sym"]


def test_an_empty_table_offers_nothing_to_group_by():
    assert tg.groupable(_frame().head(0)) == []


def test_a_column_of_nulls_can_still_be_grouped_on():
    """One heading, holding everything — useless, but a rule that dropped it
    would need a special case, and the empty tree it makes is honest."""
    frame = pd.DataFrame({"note": [None] * 5, "qty": list(range(5))})
    assert "note" in tg.groupable(frame)


def test_one_row_is_not_treated_as_a_heading_per_row():
    """The all-distinct rule would throw away every column of a one-row
    snapshot, which a refresh can produce at any moment."""
    assert tg.groupable(_frame().head(1)) == ["venue", "sym", "qty"]


# --- which rows land under which heading -------------------------------------

def test_every_row_lands_under_the_value_it_shares():
    frame = _frame()
    parts = dict(tg.split(frame, _keys(frame, "venue")))
    assert sorted(parts) == ["BATS", "CHIX", "LSE"]
    assert list(parts["LSE"]["sym"]) == ["VOD.LN", "BP.LN", "AZN.LN"]


def test_no_row_is_lost_and_none_is_counted_twice():
    frame = _frame()
    parts = tg.split(frame, _keys(frame, "venue"))
    assert sum(len(p) for _, p in parts) == len(frame)


def test_the_headings_come_in_the_order_the_query_put_them_in():
    """The dataset arrived sorted by somebody's xdesc; the tree has no business
    overruling it."""
    frame = _frame()
    assert [label for label, _ in tg.split(frame, _keys(frame, "venue"))] == \
        ["LSE", "BATS", "CHIX"]


def test_the_rows_with_no_value_come_last():
    frame = _frame()
    frame.loc[0, "venue"] = None
    assert [label for label, _ in tg.split(frame, _keys(frame, "venue"))][-1] \
        == tg.MISSING


def test_splitting_an_empty_table_gives_no_headings():
    frame = _frame().head(0)
    assert tg.split(frame, _keys(frame, "venue")) == []


def test_a_key_for_every_row_or_nothing_at_all():
    """A mismatch means the caller sliced one and not the other; guessing which
    rows the keys belong to would silently mis-file them."""
    frame = _frame()
    assert tg.split(frame, _keys(frame, "venue").head(2)) == []


def test_the_rows_under_a_heading_keep_their_own_values():
    frame = _frame()
    parts = dict(tg.split(frame, _keys(frame, "venue")))
    assert list(parts["BATS"]["qty"]) == [250, 600]


def test_a_repeated_index_does_not_multiply_the_rows():
    """A frame that has been through a transform can carry one, and matching
    the keys on it would either raise or quietly duplicate rows."""
    frame = _frame()
    frame.index = [0] * len(frame)
    parts = tg.split(frame, _keys(frame, "venue"))
    assert sum(len(p) for _, p in parts) == len(frame)


# --- the author's starting point ---------------------------------------------

def _table(**spec) -> Widget:
    return Widget(type="table", dataset="d", title="Orders", spec=spec)


def _built(**spec):
    return build_plot_model(_table(**spec),
                            {"d": DatasetResult("d", _frame(), "q", None,
                                                row_count=6)})


def test_a_table_carries_the_grouping_its_author_chose():
    assert _built(group_by="venue").group_by == "venue"


def test_a_table_with_no_grouping_carries_none():
    assert _built().group_by == ""


def test_the_grouping_travels_as_the_header_the_column_is_shown_under():
    """The screen groups the frame, and the frame wears the display headers."""
    assert _built(group_by="venue", labels={"venue": "Venue"}).group_by == "Venue"


def test_a_grouping_on_a_column_the_table_does_not_show_is_dropped():
    """The heading would be the one thing on screen whose value the reader
    cannot see."""
    assert _built(group_by="venue", columns=["sym", "qty"]).group_by == ""


def test_the_grouping_is_a_column_reference_like_any_other():
    """So the editor's check catches one left pointing at a column a group-by
    upstream has since removed."""
    assert "venue" in referenced_columns(_table(group_by="venue"))
