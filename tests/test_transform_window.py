import pandas as pd
import pytest

from kdbmonitor.core.dashboard_models import Transform
from kdbmonitor.core.transform import apply_transforms


def _derive(expr: str) -> Transform:
    return Transform(kind="derive", params={
        "column": "x", "kind": "arithmetic", "expr": expr})


def _profile() -> pd.DataFrame:
    """Two instruments stacked, each running 0 -> 1 over three buckets."""
    return pd.DataFrame({
        "sym": ["A", "A", "A", "B", "B", "B"],
        "t": [1, 2, 3, 1, 2, 3],
        "cum": [0.2, 0.5, 1.0, 0.4, 0.7, 1.0]})


def _window(**params) -> Transform:
    return Transform(kind="window", params=params)


def test_a_partitioned_diff_does_not_walk_into_the_next_instrument():
    """Unpartitioned this yields -0.6 at the boundary and each instrument's
    shares sum wrongly. It is the bug the whole transform exists for."""
    out = apply_transforms(_profile(), [_window(
        column="cum", op="diff", partition_by=["sym"], **{"as": "share"})])
    got = out["share"].tolist()
    assert pd.isna(got[0]) and pd.isna(got[3])
    assert got[1] == pytest.approx(0.3) and got[2] == pytest.approx(0.5)
    assert got[4] == pytest.approx(0.3) and got[5] == pytest.approx(0.3)
    assert out.groupby("sym")["share"].sum().round(6).tolist() == [0.8, 0.6]


def test_an_unpartitioned_diff_is_the_plain_series_difference():
    out = apply_transforms(_profile(), [_window(
        column="cum", op="diff", **{"as": "d"})])
    assert out["d"].iloc[3] == pytest.approx(-0.6)


def test_a_cumulative_sum_partitions_too():
    out = apply_transforms(_profile(), [_window(
        column="t", op="cumsum", partition_by=["sym"], **{"as": "run"})])
    assert out["run"].tolist() == [1, 3, 6, 1, 3, 6]


def test_shift_moves_values_down_within_a_partition():
    out = apply_transforms(_profile(), [_window(
        column="cum", op="shift", periods=1, partition_by=["sym"],
        **{"as": "prev"})])
    assert pd.isna(out["prev"].iloc[3])
    assert out["prev"].iloc[4] == pytest.approx(0.4)


def test_row_number_counts_from_zero_within_each_partition():
    """This is what makes an even-pace reference computable."""
    out = apply_transforms(_profile(), [_window(
        op="row_number", partition_by=["sym"], **{"as": "n"})])
    assert out["n"].tolist() == [0, 1, 2, 0, 1, 2]


def test_row_number_needs_no_source_column():
    out = apply_transforms(_profile(), [_window(op="row_number", **{"as": "n"})])
    assert out["n"].tolist() == [0, 1, 2, 3, 4, 5]


def test_a_rolling_mean_partitions_and_keeps_position():
    out = apply_transforms(_profile(), [_window(
        column="cum", op="rolling_mean", periods=2, partition_by=["sym"],
        **{"as": "r"})])
    assert out["r"].iloc[1] == pytest.approx(0.35)
    assert pd.isna(out["r"].iloc[3])          # first row of the next partition


def test_a_rolling_sum_partitions_too():
    out = apply_transforms(_profile(), [_window(
        column="t", op="rolling_sum", periods=2, partition_by=["sym"],
        **{"as": "r"})])
    assert out["r"].iloc[2] == pytest.approx(5.0)
    assert pd.isna(out["r"].iloc[3])


def test_the_frame_is_not_reordered():
    """These files are in session order; a profile resorted around midnight is
    wrong."""
    frame = _profile().iloc[[3, 0, 4, 1, 5, 2]].reset_index(drop=True)
    out = apply_transforms(frame, [_window(
        column="cum", op="diff", partition_by=["sym"], **{"as": "d"})])
    assert out["sym"].tolist() == frame["sym"].tolist()
    assert out["t"].tolist() == frame["t"].tolist()


def test_an_empty_frame_gains_the_column_rather_than_raising():
    empty = pd.DataFrame({"sym": [], "cum": []})
    out = apply_transforms(empty, [_window(
        column="cum", op="diff", partition_by=["sym"], **{"as": "d"})])
    assert "d" in out.columns and len(out) == 0


def test_an_empty_frame_takes_a_row_number_column_too():
    empty = pd.DataFrame({"sym": []})
    out = apply_transforms(empty, [_window(op="row_number", **{"as": "n"})])
    assert "n" in out.columns and len(out) == 0


def test_a_missing_column_says_which_and_what_there_was():
    with pytest.raises(ValueError, match="nope"):
        apply_transforms(_profile(), [_window(
            column="nope", op="diff", **{"as": "d"})])


def test_a_missing_partition_column_says_which():
    with pytest.raises(ValueError, match="ghost"):
        apply_transforms(_profile(), [_window(
            column="cum", op="diff", partition_by=["ghost"], **{"as": "d"})])


def test_an_unknown_op_says_what_it_was():
    with pytest.raises(ValueError, match="fourier"):
        apply_transforms(_profile(), [_window(
            column="cum", op="fourier", **{"as": "d"})])


def test_the_new_column_may_replace_an_existing_one():
    out = apply_transforms(_profile(), [_window(
        column="cum", op="cumsum", **{"as": "cum"})])
    assert out["cum"].iloc[1] == pytest.approx(0.7)


def test_the_input_frame_is_never_mutated():
    """apply_transforms copies; a window must not defeat that."""
    frame = _profile()
    apply_transforms(frame, [_window(column="cum", op="diff", **{"as": "d"})])
    assert "d" not in frame.columns


def test_a_share_of_the_partition_total_is_computed_per_partition():
    """Share-of-group is the commonest partitioned figure in any report:
    volume per venue, orders per desk, contribution per book."""
    out = apply_transforms(_profile(), [_window(
        column="t", op="pct_of_total", partition_by=["sym"], **{"as": "share"})])
    assert out["share"].tolist() == pytest.approx(
        [1/6, 2/6, 3/6, 1/6, 2/6, 3/6])
    assert out.groupby("sym")["share"].sum().round(6).tolist() == [1.0, 1.0]


def test_a_share_of_the_whole_frame_when_nothing_partitions_it():
    out = apply_transforms(_profile(), [_window(
        column="t", op="pct_of_total", **{"as": "share"})])
    assert out["share"].sum() == pytest.approx(1.0)


def test_a_partition_summing_to_zero_gives_no_share_rather_than_infinity():
    """Dividing by an empty total is how an infinity gets into a report, and
    this codebase already strips those out of derived columns."""
    frame = pd.DataFrame({"sym": ["A", "A"], "v": [0.0, 0.0]})
    out = apply_transforms(frame, [_window(
        column="v", op="pct_of_total", partition_by=["sym"], **{"as": "s"})])
    assert out["s"].isna().all()


def test_rank_numbers_rows_within_their_partition():
    """With rank, a per-group top-N is expressible: rank then filter. limit
    only ever takes the head of the whole frame."""
    out = apply_transforms(_profile(), [_window(
        column="cum", op="rank", partition_by=["sym"], **{"as": "r"})])
    assert out["r"].tolist() == [1.0, 2.0, 3.0, 1.0, 2.0, 3.0]


def test_rank_ties_take_the_lower_position():
    frame = pd.DataFrame({"g": ["A", "A", "A"], "v": [5.0, 5.0, 9.0]})
    out = apply_transforms(frame, [_window(
        column="v", op="rank", partition_by=["g"], **{"as": "r"})])
    assert out["r"].tolist() == [1.0, 1.0, 3.0]


def test_an_explicit_zero_periods_is_not_silently_promoted_to_one():
    """`p.get("periods") or 1` would treat a truthy check on 0 as "not given"
    and pick 1 instead — a real bug, since shift(0) (identity) and shift(1)
    (previous row) mean different things."""
    out = apply_transforms(_profile(), [_window(
        column="cum", op="shift", periods=0, **{"as": "s"})])
    assert out["s"].tolist() == pytest.approx(out["cum"].tolist())


# --- derive's arithmetic is arithmetic, not arbitrary code -------------------

def test_ordinary_arithmetic_still_evaluates():
    df = pd.DataFrame({"a": [1.0, 2.0], "b": [10.0, 20.0]})
    out = apply_transforms(df, [_derive("a + b * 2")])
    assert out["x"].tolist() == [21.0, 42.0]


def test_division_and_precedence_still_work():
    df = pd.DataFrame({"a": [10.0], "b": [4.0]})
    out = apply_transforms(df, [_derive("(a - b) / 2")])
    assert out["x"].tolist() == [3.0]


def test_comparisons_and_logic_still_evaluate():
    df = pd.DataFrame({"a": [1, 5], "b": [4, 4]})
    out = apply_transforms(df, [_derive("(a > 2) and (b == 4)")])
    assert out["x"].tolist() == [False, True]


def test_a_negative_and_a_power_still_evaluate():
    df = pd.DataFrame({"a": [2.0]})
    out = apply_transforms(df, [_derive("-a ** 2")])
    assert out["x"].tolist() == [-4.0]


def test_a_string_constant_still_evaluates():
    df = pd.DataFrame({"side": ["BUY", "SELL"]})
    out = apply_transforms(df, [_derive("side == 'BUY'")])
    assert out["x"].tolist() == [True, False]


def test_a_method_call_is_refused_and_points_at_the_window_transform():
    """It used to evaluate, and on a stacked frame it was silently wrong."""
    df = pd.DataFrame({"cum": [0.2, 0.5]})
    with pytest.raises(ValueError, match="window"):
        apply_transforms(df, [_derive("cum.diff()")])


def test_the_refusal_names_what_it_objected_to():
    df = pd.DataFrame({"cum": [0.2]})
    with pytest.raises(ValueError, match="diff"):
        apply_transforms(df, [_derive("cum.diff()")])


def test_attribute_traversal_is_refused():
    """cum.__class__.__mro__ reached Python's class hierarchy from a stored
    dashboard, and dashboards are imported from other people."""
    df = pd.DataFrame({"cum": [0.2, 0.5]})
    for expr in ("cum.__class__", "cum.__class__.__mro__", "cum.to_numpy()",
                 "cum.values"):
        with pytest.raises(ValueError):
            apply_transforms(df, [_derive(expr)])


def test_a_subscript_is_refused():
    df = pd.DataFrame({"cum": [0.2, 0.5]})
    with pytest.raises(ValueError):
        apply_transforms(df, [_derive("cum[0]")])


def test_a_lambda_is_refused():
    df = pd.DataFrame({"cum": [0.2, 0.5]})
    with pytest.raises(ValueError):
        apply_transforms(df, [_derive("(lambda: 1)()")])


def test_a_comprehension_is_refused():
    df = pd.DataFrame({"cum": [0.2]})
    with pytest.raises(ValueError):
        apply_transforms(df, [_derive("[c for c in cum]")])


def test_a_bare_import_is_refused():
    df = pd.DataFrame({"cum": [0.2]})
    with pytest.raises(ValueError):
        apply_transforms(df, [_derive("__import__('os')")])


def test_boolean_negation_with_tilde_still_evaluates():
    """pandas uses ~ for boolean negation on a Series, a legitimate thing to
    want, unlike the Python-level ~ on an int which this catalogue has no use
    for either way."""
    df = pd.DataFrame({"a": [1, 2, 3]})
    out = apply_transforms(df, [_derive("~(a > 1)")])
    assert out["x"].tolist() == [True, False, False]


def test_a_deeply_nested_expression_is_refused_not_a_recursion_error():
    """CPython's own parser recurses per nesting level and gives up around a
    few thousand deep — reachable with nothing but repeated unary operators,
    no parentheses required, in a string a stored dashboard can hold. That has
    to come out of check_expression as this function's usual ValueError, or
    the editor's build-time check (which only catches ValueError) would crash
    outright instead of showing a problem."""
    df = pd.DataFrame({"a": [1.0]})
    with pytest.raises(ValueError):
        apply_transforms(df, [_derive("-" * 4000 + "a")])


def test_nonsense_is_refused_as_a_message_not_a_traceback():
    df = pd.DataFrame({"cum": [0.2]})
    with pytest.raises(ValueError):
        apply_transforms(df, [_derive("a +")])


def test_an_empty_expression_is_refused():
    df = pd.DataFrame({"cum": [0.2]})
    with pytest.raises(ValueError):
        apply_transforms(df, [_derive("")])


def test_the_suffix_map_kind_is_untouched_by_the_guard():
    """Only arithmetic expressions are parsed; suffix_map has no expression."""
    df = pd.DataFrame({"sym": ["0005.HK", "7203.JP"]})
    out = apply_transforms(df, [Transform(kind="derive", params={
        "column": "market", "kind": "suffix_map", "source": "sym", "length": 3,
        "mapping": {".HK": "Hong Kong", ".JP": "Japan"}})])
    assert out["market"].tolist() == ["Hong Kong", "Japan"]
