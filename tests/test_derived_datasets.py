"""A dataset built on another one: source == "derived".

The runtime half is about which frame it starts from and what happens when that
frame never arrives; the editor half is about the ordering rule that keeps a
chain runnable and a cycle unrepresentable.
"""
from datetime import date

import pandas as pd

from kdbmonitor.core.dashboard_models import (
    ColumnSpec, Dashboard, Dataset, FileShape, Row, Transform, Widget,
    dashboard_from_json, dashboard_to_json,
)
from kdbmonitor.core.dataset import run_datasets, trace_datasets
from kdbmonitor.ui.dashboard_editor import (
    _derived_dataset_problems, produced_columns, validate,
)

TODAY = date(2026, 8, 3)


def _frame() -> pd.DataFrame:
    return pd.DataFrame({"basket": ["A", "B", "A", "B"],
                         "sym": ["VOD.L", "HSBA.L", "BP.L", "AZN.L"],
                         "qty": [10.0, 20.0, 30.0, 40.0]})


def _shape() -> FileShape:
    return FileShape(columns=[ColumnSpec(name="basket"), ColumnSpec(name="sym"),
                              ColumnSpec(name="qty", type="number")])


def _by_basket(name="orders_by_basket", base="orders", **kw) -> Dataset:
    return Dataset(name=name, env="", source="derived", base=base,
                   transforms=[Transform(kind="groupby", params={
                       "keys": ["basket"],
                       "aggs": [{"column": "qty", "func": "sum",
                                 "as": "total"}]})], **kw)


def _dash(*datasets, base_transforms=None) -> Dashboard:
    orders = Dataset(name="orders", env="", source="file", shape=_shape(),
                     file_label="your orders export",
                     transforms=base_transforms or [])
    return Dashboard(id=1, name="Orders", source="file",
                     datasets=[orders, *datasets],
                     rows=[Row(widgets=[Widget(type="table",
                                               dataset="orders")])])


# --- what a derived dataset starts from --------------------------------------

def test_a_derived_dataset_transforms_the_frame_its_base_finished_on():
    out = run_datasets(_dash(_by_basket()), None, None, TODAY,
                       uploads={"orders": _frame()})["orders_by_basket"]
    assert out.error is None
    assert out.df.set_index("basket")["total"].to_dict() == {"A": 40.0,
                                                             "B": 60.0}


def test_deriving_leaves_the_dataset_it_reads_alone():
    results = run_datasets(_dash(_by_basket()), None, None, TODAY,
                           uploads={"orders": _frame()})
    # The whole point: every widget already pointed at 'orders' still sees the
    # rows it saw before anything was derived from it.
    assert list(results["orders"].df.columns) == ["basket", "sym", "qty"]
    assert len(results["orders"].df) == 4


def test_it_starts_after_the_base_own_transforms_not_before_them():
    dash = _dash(_by_basket(), base_transforms=[Transform(
        kind="filter", params={"column": "basket", "op": "=", "value": "A"})])
    out = run_datasets(dash, None, None, TODAY,
                       uploads={"orders": _frame()})["orders_by_basket"]
    assert out.df.set_index("basket")["total"].to_dict() == {"A": 40.0}


def test_a_derived_dataset_can_itself_be_derived_from():
    top = Dataset(name="biggest", env="", source="derived",
                  base="orders_by_basket",
                  transforms=[Transform(kind="sort",
                                        params={"columns": ["total"],
                                                "ascending": False}),
                              Transform(kind="limit", params={"n": 1})])
    out = run_datasets(_dash(_by_basket(), top), None, None, TODAY,
                       uploads={"orders": _frame()})["biggest"]
    assert out.df["basket"].tolist() == ["B"]


def test_max_rows_caps_a_derived_frame_like_any_other():
    out = run_datasets(_dash(Dataset(name="some", env="", source="derived",
                                     base="orders", max_rows=2)),
                       None, None, TODAY,
                       uploads={"orders": _frame()})["some"]
    assert len(out.df) == 2 and out.row_count == 4 and out.truncated


def test_the_query_panel_says_where_it_reads_from():
    out = run_datasets(_dash(_by_basket()), None, None, TODAY,
                       uploads={"orders": _frame()})["orders_by_basket"]
    assert out.qsql == "from dataset: orders"


# --- when the base has no frame ----------------------------------------------

def test_waiting_for_an_upload_travels_down_the_chain():
    results = run_datasets(_dash(_by_basket()), None, None, TODAY, uploads={})
    out = results["orders_by_basket"]
    assert out.df is None
    # Waiting, not failed: the reader has an upload to make, and saying so is
    # an instruction rather than a red panel.
    assert out.waiting is True
    assert "your orders export" in out.error


def test_a_failing_base_is_reported_as_the_reason_this_one_cannot_run():
    dash = _dash(_by_basket(), base_transforms=[Transform(
        kind="filter", params={"column": "nope", "op": "=", "value": 1})])
    out = run_datasets(dash, None, None, TODAY,
                       uploads={"orders": _frame()})["orders_by_basket"]
    assert out.waiting is False
    assert "'orders' failed" in out.error and "nope" in out.error


def test_a_base_declared_below_has_not_run_yet():
    dash = _dash(_by_basket(base="later"),
                 Dataset(name="later", env="", source="file", shape=_shape()))
    out = run_datasets(dash, None, None, TODAY,
                       uploads={"orders": _frame(),
                                "later": _frame()})["orders_by_basket"]
    assert out.df is None and "not defined above it" in out.error


def test_no_base_chosen_at_all_says_so():
    out = run_datasets(_dash(Dataset(name="d", env="", source="derived")),
                       None, None, TODAY,
                       uploads={"orders": _frame()})["d"]
    assert out.df is None and "no dataset to derive from" in out.error


# --- step by step -------------------------------------------------------------

def test_a_trace_starts_the_chain_on_the_base_finished_frame():
    traces = trace_datasets(_dash(_by_basket()), None, None, TODAY,
                            uploads={"orders": _frame()})
    steps = traces["orders_by_basket"].steps
    assert steps[0].columns == ["basket", "sym", "qty"]
    assert steps[1].columns == ["basket", "total"]


def test_a_trace_passes_the_failing_step_of_the_base_along():
    dash = _dash(_by_basket(), base_transforms=[Transform(
        kind="filter", params={"column": "nope", "op": "=", "value": 1})])
    trace = trace_datasets(dash, None, None, TODAY,
                           uploads={"orders": _frame()})["orders_by_basket"]
    assert trace.steps == [] and "nope" in trace.error


# --- storage ------------------------------------------------------------------

def test_the_base_survives_a_round_trip():
    back = dashboard_from_json(dashboard_to_json(_dash(_by_basket())))
    assert back.datasets[1].source == "derived"
    assert back.datasets[1].base == "orders"


def test_a_dashboard_stored_before_derived_datasets_existed_still_loads():
    back = dashboard_from_json(
        '{"name": "d", "datasets": [{"name": "orders", "env": "e"}]}')
    assert back.datasets[0].source == "kdb" and back.datasets[0].base == ""


# --- the editor's rules -------------------------------------------------------

def test_a_derived_dataset_must_say_what_it_derives_from():
    problems = _derived_dataset_problems(
        Dataset(name="d", env="", source="derived"), [])
    assert any("does not say which dataset" in p for p in problems)


def test_a_dataset_cannot_derive_from_itself():
    problems = _derived_dataset_problems(
        Dataset(name="d", env="", source="derived", base="d"), ["d"])
    assert any("derives from itself" in p for p in problems)


def test_a_base_declared_below_is_refused_while_building():
    problems = _derived_dataset_problems(_by_basket(), [])
    assert any("not defined above it" in p for p in problems)


def test_a_base_declared_above_is_fine():
    assert _derived_dataset_problems(_by_basket(), ["orders"]) == []


class _Store:
    def list_environments(self):
        return {}


def test_a_derived_dataset_belongs_to_a_file_dashboard_too():
    # It reads neither a server nor a file, so the source that governs every
    # other dataset does not govern this one.
    dash = _dash(_by_basket())
    assert not [p for p in validate(dash, _Store()) if "does not match" in p]


def test_a_derived_dataset_belongs_to_a_query_dashboard_too():
    dash = Dashboard(id=1, name="D", source="kdb", datasets=[
        Dataset(name="orders", env="prod", mode="raw", raw_qsql="select from o"),
        _by_basket()],
        rows=[Row(widgets=[Widget(type="table", dataset="orders_by_basket")])])
    assert not [p for p in validate(dash, _Store()) if "does not match" in p]


# --- what the column pickers can offer ----------------------------------------

def test_column_pickers_follow_the_chain_back_to_the_file_shape():
    dash = _dash(_by_basket())
    assert produced_columns(dash, dash.datasets[1], _Store()) == ["basket",
                                                                 "total"]


def test_column_pickers_follow_a_derived_dataset_of_a_derived_one():
    top = Dataset(name="labelled", env="", source="derived",
                  base="orders_by_basket",
                  transforms=[Transform(kind="derive", params={
                      "column": "share", "kind": "arithmetic",
                      "expr": "total / 2"})])
    dash = _dash(_by_basket(), top)
    assert produced_columns(dash, top, _Store()) == ["basket", "total",
                                                     "share"]


def test_a_hand_edited_cycle_is_answered_rather_than_recursed_into():
    a = Dataset(name="a", env="", source="derived", base="b")
    b = Dataset(name="b", env="", source="derived", base="a")
    dash = Dashboard(id=1, name="D", datasets=[a, b])
    assert produced_columns(dash, a, _Store()) == []
