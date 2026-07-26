import pandas as pd
import pytest

from kdbmonitor.core.dashboard_models import Transform
from kdbmonitor.core.transform import apply_transforms


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
