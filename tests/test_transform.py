import pandas as pd
import pytest

from kdbmonitor.core.dashboard_models import Transform
from kdbmonitor.core.summaries import transform_summary
from kdbmonitor.core.transform import apply_transforms, transform_steps


def _orders() -> pd.DataFrame:
    return pd.DataFrame([
        {"id_target": 1, "sym": "5.HK",    "size": 100, "executed": 50,  "nReject": 0},
        {"id_target": 2, "sym": "700.HK",  "size": 200, "executed": 200, "nReject": 1},
        {"id_target": 3, "sym": "7203.JP", "size": 50,  "executed": 0,   "nReject": 2},
    ])


def test_derive_arithmetic():
    out = apply_transforms(_orders(), [Transform(kind="derive", params={
        "column": "completion_pct", "kind": "arithmetic",
        "expr": "100 * executed / size"})])
    assert out["completion_pct"].tolist() == [50.0, 100.0, 0.0]


def test_derive_suffix_map():
    out = apply_transforms(_orders(), [Transform(kind="derive", params={
        "column": "market", "kind": "suffix_map", "source": "sym",
        "mapping": {".HK": "Hong Kong", ".JP": "Japan"}, "default": "Unknown"})])
    assert out["market"].tolist() == ["Hong Kong", "Hong Kong", "Japan"]


def test_derive_suffix_map_takes_the_last_n_characters():
    """The suffix is however many trailing characters you say it is, so a
    symbology without a dot separator works the same way."""
    df = pd.DataFrame({"sym": ["0005HK", "7203JP", "AAPLUS"]})
    out = apply_transforms(df, [Transform(kind="derive", params={
        "column": "market", "kind": "suffix_map", "source": "sym", "length": 2,
        "mapping": {"HK": "Hong Kong", "JP": "Japan"}, "default": "Unknown"})])
    assert out["market"].tolist() == ["Hong Kong", "Japan", "Unknown"]


def test_a_suffix_length_counts_the_dot_when_the_mapping_does():
    out = apply_transforms(_orders(), [Transform(kind="derive", params={
        "column": "market", "kind": "suffix_map", "source": "sym", "length": 3,
        "mapping": {".HK": "Hong Kong", ".JP": "Japan"}, "default": "Unknown"})])
    assert out["market"].tolist() == ["Hong Kong", "Hong Kong", "Japan"]


def test_a_value_shorter_than_the_suffix_gets_the_fallback():
    df = pd.DataFrame({"sym": ["HK", "0005.HK"]})
    out = apply_transforms(df, [Transform(kind="derive", params={
        "column": "market", "kind": "suffix_map", "source": "sym", "length": 3,
        "mapping": {".HK": "Hong Kong"}, "default": "Unknown"})])
    assert out["market"].tolist() == ["Unknown", "Hong Kong"]


def test_a_map_without_a_length_still_splits_on_the_last_dot():
    """Maps built before the length existed keep the behaviour they had."""
    out = apply_transforms(_orders(), [Transform(kind="derive", params={
        "column": "market", "kind": "suffix_map", "source": "sym",
        "mapping": {".HK": "Hong Kong", ".JP": "Japan"}, "default": "Unknown"})])
    assert out["market"].tolist() == ["Hong Kong", "Hong Kong", "Japan"]


def test_derive_suffix_map_falls_back_to_default():
    df = pd.DataFrame({"sym": ["AAPL", "5.XX"]})
    out = apply_transforms(df, [Transform(kind="derive", params={
        "column": "market", "kind": "suffix_map", "source": "sym",
        "mapping": {".HK": "Hong Kong"}, "default": "Unknown"})])
    assert out["market"].tolist() == ["Unknown", "Unknown"]


def test_filter_on_a_derived_column():
    out = apply_transforms(_orders(), [
        Transform(kind="derive", params={"column": "completion_pct",
                                         "kind": "arithmetic",
                                         "expr": "100 * executed / size"}),
        Transform(kind="filter", params={"column": "completion_pct",
                                         "op": ">", "value": 40}),
    ])
    assert out["id_target"].tolist() == [1, 2]


def test_filter_in_operator():
    out = apply_transforms(_orders(), [Transform(kind="filter", params={
        "column": "id_target", "op": "in", "value": [1, 3]})])
    assert out["id_target"].tolist() == [1, 3]


def test_groupby_aggregates():
    out = apply_transforms(_orders(), [
        Transform(kind="derive", params={
            "column": "market", "kind": "suffix_map", "source": "sym",
            "mapping": {".HK": "Hong Kong", ".JP": "Japan"}, "default": "Unknown"}),
        Transform(kind="groupby", params={"keys": ["market"], "aggs": [
            {"column": "id_target", "func": "nunique", "as": "n_orders"},
            {"column": "size", "func": "sum", "as": "order_qty"},
            {"column": "nReject", "func": "sum", "as": "n_rejections"},
        ]}),
    ])
    hk = out[out["market"] == "Hong Kong"].iloc[0]
    assert hk["n_orders"] == 2
    assert hk["order_qty"] == 300
    assert hk["n_rejections"] == 1
    assert list(out.columns) == ["market", "n_orders", "order_qty", "n_rejections"]


def test_sort_descending():
    out = apply_transforms(_orders(), [Transform(kind="sort", params={
        "columns": ["size"], "ascending": False})])
    assert out["size"].tolist() == [200, 100, 50]


def test_limit():
    assert len(apply_transforms(_orders(),
                                [Transform(kind="limit", params={"n": 2})])) == 2


def test_rename():
    out = apply_transforms(_orders(), [Transform(kind="rename", params={
        "mapping": {"size": "order_qty"}})])
    assert "order_qty" in out.columns and "size" not in out.columns


def test_transforms_apply_in_order():
    out = apply_transforms(_orders(), [
        Transform(kind="sort", params={"columns": ["size"], "ascending": False}),
        Transform(kind="limit", params={"n": 1}),
    ])
    assert out["size"].tolist() == [200]


def test_original_frame_is_not_mutated():
    df = _orders()
    apply_transforms(df, [Transform(kind="rename",
                                    params={"mapping": {"size": "order_qty"}})])
    assert "size" in df.columns


def test_empty_frame_survives_every_transform():
    empty = pd.DataFrame(columns=["sym", "size", "executed", "id_target", "nReject"])
    out = apply_transforms(empty, [
        Transform(kind="derive", params={"column": "market", "kind": "suffix_map",
                                         "source": "sym", "mapping": {},
                                         "default": "Unknown"}),
        Transform(kind="groupby", params={"keys": ["market"], "aggs": [
            {"column": "id_target", "func": "nunique", "as": "n_orders"}]}),
    ])
    assert out.empty


def test_unknown_transform_kind_is_an_error():
    with pytest.raises(ValueError, match="unknown transform"):
        apply_transforms(_orders(), [Transform(kind="teleport", params={})])


def test_missing_column_names_the_transform():
    with pytest.raises(ValueError, match="sort: no column 'nope'"):
        apply_transforms(_orders(), [Transform(kind="sort",
                                               params={"columns": ["nope"]})])


# --- step by step ----------------------------------------------------------

MARKET = Transform(kind="derive", params={
    "column": "market", "kind": "suffix_map", "source": "sym",
    "mapping": {".HK": "Hong Kong", ".JP": "Japan"}, "default": "Unknown"})
BY_MARKET = Transform(kind="groupby", params={"keys": ["market"], "aggs": [
    {"column": "id_target", "func": "nunique", "as": "n_orders"},
    {"column": "size", "func": "sum", "as": "order_qty"}]})
BIG_ONLY = Transform(kind="filter", params={"column": "size", "op": ">",
                                            "value": 60})


def test_the_first_step_is_the_untouched_query_result():
    steps = transform_steps(_orders(), [MARKET])
    assert steps[0].index == 0
    assert steps[0].label == "Query result"
    assert steps[0].rows == 3
    assert "market" not in steps[0].columns


def test_every_transform_gets_its_own_frame():
    steps = transform_steps(_orders(), [MARKET, BY_MARKET])
    assert [s.index for s in steps] == [0, 1, 2]
    assert steps[1].df["market"].tolist() == ["Hong Kong", "Hong Kong", "Japan"]
    assert steps[2].df["n_orders"].tolist() == [2, 1]
    assert steps[-1].df.equals(apply_transforms(_orders(), [MARKET, BY_MARKET]))


def test_a_step_reports_the_columns_it_added_and_dropped():
    steps = transform_steps(_orders(), [MARKET, BY_MARKET])
    assert steps[1].added == ["market"]
    assert steps[1].dropped == []
    assert set(steps[2].dropped) == {"id_target", "sym", "size", "executed",
                                     "nReject"}
    assert steps[2].added == ["n_orders", "order_qty"]


def test_a_step_reports_the_rows_it_gained_or_lost():
    steps = transform_steps(_orders(), [BIG_ONLY])
    assert steps[1].rows == 2
    assert steps[1].rows_before == 3
    assert steps[1].row_delta == -1
    assert steps[0].row_delta is None       # nothing to compare the query with


def test_a_failing_step_carries_the_error_and_stops_the_rest():
    steps = transform_steps(_orders(), [
        BY_MARKET,                                   # 'market' does not exist yet
        Transform(kind="limit", params={"n": 1}),
    ])
    assert len(steps) == 2                           # the limit never ran
    assert steps[1].error and "no column 'market'" in steps[1].error
    assert steps[1].df is None
    assert steps[0].rows == 3                        # what it was handed


def test_stepping_does_not_mutate_the_source_frame():
    df = _orders()
    transform_steps(df, [MARKET, BY_MARKET])
    assert "market" not in df.columns


def test_steps_are_labelled_with_what_each_one_does():
    steps = transform_steps(_orders(), [MARKET, BY_MARKET])
    assert steps[1].label == "1. derive market from sym"
    assert steps[2].label.startswith("2. group by market → nunique(id_target)")


@pytest.mark.parametrize("transform, expected", [
    (BIG_ONLY, "keep rows where size > 60"),
    (Transform(kind="sort", params={"columns": ["size"], "ascending": False}),
     "sort by size descending"),
    (Transform(kind="limit", params={"n": 10}), "keep the first 10 rows"),
    (Transform(kind="rename", params={"mapping": {"size": "qty"}}),
     "rename size → qty"),
    (Transform(kind="derive", params={"column": "pct", "kind": "arithmetic",
                                      "expr": "100 * executed / size"}),
     "derive pct = 100 * executed / size"),
])
def test_transform_summaries_read_as_the_action(transform, expected):
    assert transform_summary(transform) == expected


def test_an_unconfigured_transform_still_has_a_summary():
    assert transform_summary(Transform(kind="sort", params={})) == \
        "sort by ? ascending"


# --- results that are not numbers -------------------------------------------
#
# The short-sell report's completion percentage is the case these guard: a
# market's total order quantity is not always the positive number the
# arithmetic assumes it is.

def test_a_division_by_zero_is_a_gap_not_an_infinity():
    """A market with no order quantity has no completion to report.

    Left as inf it formats, colours against a threshold and plots like a real
    figure, and it takes every aggregate over the column with it.
    """
    df = pd.DataFrame({"executed_qty": [50, 10], "order_qty": [100, 0]})
    out = apply_transforms(df, [Transform(kind="derive", params={
        "column": "completion_pct", "kind": "arithmetic",
        "expr": "100 * executed_qty / order_qty"})])
    assert out["completion_pct"][0] == 50.0
    assert pd.isna(out["completion_pct"][1])
    assert out["completion_pct"].mean() == 50.0        # not inf


def test_a_negative_result_is_left_exactly_as_it_computed():
    """An order quantity that summed below zero is a real problem in the data.

    Blanking the percentage would hide the only sign of it anyone sees, so only
    results that are *not numbers* are removed — a negative is a number.
    """
    df = pd.DataFrame({"executed_qty": [504], "order_qty": [-500]})
    out = apply_transforms(df, [Transform(kind="derive", params={
        "column": "completion_pct", "kind": "arithmetic",
        "expr": "100 * executed_qty / order_qty"})])
    assert out["completion_pct"][0] == pytest.approx(-100.8)


def test_an_expression_with_nothing_to_scrub_keeps_its_type():
    """No infinity means no float cast — an integer total stays an integer."""
    out = apply_transforms(_orders(), [Transform(kind="derive", params={
        "column": "total", "kind": "arithmetic", "expr": "size + executed"})])
    assert out["total"].tolist() == [150, 400, 50]
    assert str(out["total"].dtype) == "int64"
