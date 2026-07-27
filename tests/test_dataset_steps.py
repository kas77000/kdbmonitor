"""Running a dataset stage by stage, so a pipeline can be checked as it is built.

The point of a trace is that it is the *same* run as the dashboard's — same
query, same transforms, same order — with the intermediate frames kept. These
tests hold that equivalence, because a preview that quietly does something else
is worse than none.
"""
from datetime import date

import pandas as pd
import pytest

from kdbmonitor.core.client import ConnectionManager, FakeClient
from kdbmonitor.core.dashboard_models import Dashboard, Dataset, Transform
from kdbmonitor.core.dataset import run_datasets, trace_datasets
from kdbmonitor.core.models import Connection, Filter
from kdbmonitor.core.storage import Storage

TODAY = date(2026, 7, 26)

ORDERS = pd.DataFrame([
    {"id_target": 1, "sym": "5.HK", "size": 100, "executed": 50},
    {"id_target": 2, "sym": "700.HK", "size": 200, "executed": 200},
    {"id_target": 3, "sym": "7203.JP", "size": 50, "executed": 0},
])

MARKET = Transform(kind="derive", params={
    "column": "market", "kind": "suffix_map", "source": "sym",
    "mapping": {".HK": "Hong Kong", ".JP": "Japan"}, "default": "Unknown"})
BY_MARKET = Transform(kind="groupby", params={"keys": ["market"], "aggs": [
    {"column": "id_target", "func": "nunique", "as": "n_orders"},
    {"column": "size", "func": "sum", "as": "order_qty"}]})


@pytest.fixture()
def store(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="order-rdb", host="rdb", port=1,
                                kind="realtime", env="orders"))
    s.add_connection(Connection(id=None, name="order-hdb", host="hdb", port=2,
                                kind="historical", env="orders"))
    return s


def _mgr(responses: dict) -> ConnectionManager:
    client = FakeClient(responses)
    return ConnectionManager(client_factory=lambda host, port: client)


def _dashboard(transforms=None, **kwargs) -> Dashboard:
    return Dashboard(id=1, name="d", datasets=[Dataset(
        name="orders", env="orders", table="target",
        transforms=transforms if transforms is not None else [MARKET, BY_MARKET],
        **kwargs)])


def test_a_trace_keeps_the_query_result_and_every_transform(store):
    mgr = _mgr({"select from target": ORDERS})
    trace = trace_datasets(_dashboard(), store, mgr, TODAY)["orders"]

    assert trace.error is None
    assert [s.index for s in trace.steps] == [0, 1, 2]
    assert trace.steps[0].rows == 3
    assert trace.steps[2].df["n_orders"].tolist() == [2, 1]


def test_a_trace_ends_where_the_plain_run_ends(store):
    mgr = _mgr({"select from target": ORDERS})
    plain = run_datasets(_dashboard(), store, mgr, TODAY)["orders"]
    trace = trace_datasets(_dashboard(), store, mgr, TODAY)["orders"]

    assert trace.qsql == plain.qsql
    assert trace.df.equals(plain.df)


def test_a_broken_transform_leaves_the_steps_before_it_intact(store):
    """The whole reason to keep the stages: you can see the frame the failing
    transform was handed, instead of one message about the pipeline."""
    mgr = _mgr({"select from target": ORDERS})
    dash = _dashboard([BY_MARKET])            # no 'market' column derived first
    trace = trace_datasets(dash, store, mgr, TODAY)["orders"]

    assert trace.error is None                # the query itself was fine
    assert trace.failed_step.index == 1
    assert "no column 'market'" in trace.failed_step.error
    assert trace.steps[0].rows == 3


def test_a_query_that_never_ran_has_no_steps(store):
    mgr = _mgr({})                            # FakeClient raises for anything
    trace = trace_datasets(_dashboard(), store, mgr, TODAY)["orders"]

    assert trace.steps == []
    assert trace.error
    assert trace.df is None


def test_a_refused_historical_query_reports_why_and_shows_the_q(store):
    dash = Dashboard(
        id=1, name="d",
        time_context={"mode": "historical",
                      "range": {"kind": "absolute",
                                "from": "2026-06-01", "to": "2026-06-30"}},
        datasets=[Dataset(name="orders", env="orders", mode="raw",
                          raw_qsql="select from target")])
    trace = trace_datasets(dash, store, _mgr({}), TODAY)["orders"]

    assert "must constrain 'date'" in trace.error
    assert trace.qsql == "select from target"


def test_a_later_dataset_still_sees_an_earlier_one(store):
    first, second = "select from target", "select from target_state where id_target in 1 2"
    mgr = _mgr({first: pd.DataFrame({"id_target": [1, 2]}),
                second: pd.DataFrame({"id_target": [1, 2], "open": [10, 0]})})
    dash = Dashboard(id=1, name="d", datasets=[
        Dataset(name="ids", env="orders", table="target"),
        Dataset(name="states", env="orders", mode="raw",
                raw_qsql="select from target_state where "
                         "id_target in {{ids.id_target}}")])

    traces = trace_datasets(dash, store, mgr, TODAY)
    assert traces["states"].error is None
    assert traces["states"].steps[0].df["open"].tolist() == [10, 0]


def test_the_trace_sends_the_same_query_as_the_run(store):
    """Filters and the date clause are built once, in _fetch, for both paths."""
    q = "select from target where side=`sellshort"
    mgr = _mgr({q: ORDERS})
    dash = _dashboard(transforms=[], filters=[
        Filter(column="side", op="=", value="sellshort", value_type="symbol")])

    assert trace_datasets(dash, store, mgr, TODAY)["orders"].qsql == q
    assert run_datasets(dash, store, mgr, TODAY)["orders"].qsql == q


def test_max_rows_does_not_truncate_what_a_step_reports(store):
    """The cap is a display concern for the dashboard; a trace is for checking
    what the data actually did, so its counts are the real ones."""
    mgr = _mgr({"select from target": ORDERS})
    dash = _dashboard(transforms=[], max_rows=1)
    trace = trace_datasets(dash, store, mgr, TODAY)["orders"]

    assert trace.steps[0].rows == 3
