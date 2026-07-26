from datetime import date

import pandas as pd
import pytest

from kdbmonitor.core.client import ConnectionManager, FakeClient
from kdbmonitor.core.dashboard_models import Dashboard, Dataset, Transform
from kdbmonitor.core.dataset import (
    build_qsql, effective_time, resolve_connection, run_datasets,
)
from kdbmonitor.core.models import Connection, Filter
from kdbmonitor.core.storage import Storage
from kdbmonitor.core.timectx import ResolvedTime

TODAY = date(2026, 7, 26)
RT = ResolvedTime("realtime", None, None)
HIST = ResolvedTime("historical", date(2026, 6, 1), date(2026, 6, 30))


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


# --- connection resolution -------------------------------------------------

def test_resolve_connection_picks_the_matching_kind(store):
    assert resolve_connection(store, "orders", "realtime").name == "order-rdb"
    assert resolve_connection(store, "orders", "historical").name == "order-hdb"


def test_resolve_connection_reports_a_missing_side(store):
    store.add_connection(Connection(id=None, name="md-rdb", host="h", port=3,
                                    kind="realtime", env="marketdata"))
    with pytest.raises(ValueError, match="no historical server"):
        resolve_connection(store, "marketdata", "historical")


def test_resolve_connection_reports_an_unknown_env(store):
    with pytest.raises(ValueError, match="unknown environment"):
        resolve_connection(store, "nope", "realtime")


# --- effective time --------------------------------------------------------

def test_dataset_inherits_the_dashboard_time():
    ds = Dataset(name="d", env="orders", time_mode="inherit")
    assert effective_time(ds, HIST, TODAY) == HIST


def test_dataset_can_force_realtime():
    ds = Dataset(name="d", env="orders", time_mode="realtime")
    assert effective_time(ds, HIST, TODAY).mode == "realtime"


def test_dataset_can_carry_its_own_range():
    ds = Dataset(name="d", env="orders", time_mode="custom",
                 time_context={"mode": "historical",
                               "range": {"kind": "preset", "name": "yesterday"}})
    got = effective_time(ds, RT, TODAY)
    assert (got.start, got.end) == (date(2026, 7, 25), date(2026, 7, 25))


# --- query building --------------------------------------------------------

def test_guided_realtime_query_has_no_date():
    ds = Dataset(name="d", env="orders", table="target",
                 filters=[Filter(column="side", op="=", value="sellshort",
                                 value_type="symbol")])
    assert build_qsql(ds, RT, {}) == "select from target where side=`sellshort"


def test_guided_historical_puts_date_first():
    ds = Dataset(name="d", env="orders", table="target",
                 filters=[Filter(column="side", op="=", value="sellshort",
                                 value_type="symbol")])
    assert build_qsql(ds, HIST, {}) == (
        "select from target where date within (2026.06.01;2026.06.30), "
        "side=`sellshort")


def test_guided_historical_with_no_filters():
    ds = Dataset(name="d", env="orders", table="target")
    assert build_qsql(ds, HIST, {}) == \
        "select from target where date within (2026.06.01;2026.06.30)"


def test_raw_query_gets_date_placeholders_filled():
    ds = Dataset(name="d", env="orders", mode="raw",
                 raw_qsql="select from target where date within "
                          "({{date_from}};{{date_to}})")
    assert build_qsql(ds, HIST, {}) == \
        "select from target where date within (2026.06.01;2026.06.30)"


def test_raw_query_can_reference_another_dataset():
    ds = Dataset(name="d", env="orders", mode="raw",
                 raw_qsql="select from target_state where id_target in {{ids.id}}")
    outputs = {"ids": pd.DataFrame({"id": [1, 2]})}
    assert build_qsql(ds, RT, outputs) == \
        "select from target_state where id_target in 1 2"


# --- running ---------------------------------------------------------------

def test_run_datasets_applies_transforms(store):
    q = "select from target where side=`sellshort"
    mgr = _mgr({q: pd.DataFrame([
        {"id_target": 1, "sym": "5.HK", "size": 100},
        {"id_target": 2, "sym": "7203.JP", "size": 50},
    ])})
    dash = Dashboard(id=1, name="d", datasets=[Dataset(
        name="orders", env="orders", table="target",
        filters=[Filter(column="side", op="=", value="sellshort",
                        value_type="symbol")],
        transforms=[Transform(kind="derive", params={
            "column": "market", "kind": "suffix_map", "source": "sym",
            "mapping": {".HK": "Hong Kong", ".JP": "Japan"},
            "default": "Unknown"})])])

    res = run_datasets(dash, store, mgr, TODAY)
    assert res["orders"].error is None
    assert res["orders"].df["market"].tolist() == ["Hong Kong", "Japan"]
    assert res["orders"].row_count == 2


def test_historical_raw_query_without_a_date_is_refused(store):
    mgr = _mgr({})
    dash = Dashboard(
        id=1, name="d",
        time_context={"mode": "historical",
                      "range": {"kind": "absolute",
                                "from": "2026-06-01", "to": "2026-06-30"}},
        datasets=[Dataset(name="orders", env="orders", mode="raw",
                          raw_qsql="select from target where side=`sellshort")])

    res = run_datasets(dash, store, mgr, TODAY)
    assert res["orders"].df is None
    assert "must constrain 'date'" in res["orders"].error
    assert mgr.get(store.list_connections()[0]).calls == []   # never sent


def test_query_failure_is_captured_not_raised(store):
    mgr = _mgr({})                     # FakeClient raises KeyError for anything
    dash = Dashboard(id=1, name="d", datasets=[
        Dataset(name="orders", env="orders", table="target")])

    res = run_datasets(dash, store, mgr, TODAY)
    assert res["orders"].df is None
    assert res["orders"].error


def test_one_broken_dataset_does_not_stop_the_others(store):
    good = "select from work_order"
    mgr = _mgr({good: pd.DataFrame({"sym": ["AAPL"]})})
    dash = Dashboard(id=1, name="d", datasets=[
        Dataset(name="broken", env="orders", table="target"),
        Dataset(name="fine", env="orders", table="work_order"),
    ])

    res = run_datasets(dash, store, mgr, TODAY)
    assert res["broken"].error
    assert res["fine"].error is None


def test_results_are_capped_at_max_rows(store):
    q = "select from target"
    mgr = _mgr({q: pd.DataFrame({"sym": list("abcdefghij")})})
    dash = Dashboard(id=1, name="d", datasets=[
        Dataset(name="orders", env="orders", table="target", max_rows=3)])

    res = run_datasets(dash, store, mgr, TODAY)
    assert len(res["orders"].df) == 3
    assert res["orders"].row_count == 10
    assert res["orders"].truncated is True


def test_a_dataset_can_consume_an_earlier_one(store):
    first = "select from target"
    second = "select from target_state where id_target in 1 2"
    mgr = _mgr({
        first: pd.DataFrame({"id_target": [1, 2]}),
        second: pd.DataFrame({"id_target": [1, 2], "open": [10, 0]}),
    })
    dash = Dashboard(id=1, name="d", datasets=[
        Dataset(name="ids", env="orders", table="target"),
        Dataset(name="states", env="orders", mode="raw",
                raw_qsql="select from target_state where "
                         "id_target in {{ids.id_target}}"),
    ])

    res = run_datasets(dash, store, mgr, TODAY)
    assert res["states"].error is None
    assert res["states"].df["open"].tolist() == [10, 0]


def test_a_forward_reference_is_an_error(store):
    mgr = _mgr({})
    dash = Dashboard(id=1, name="d", datasets=[
        Dataset(name="states", env="orders", mode="raw",
                raw_qsql="select from target_state where "
                         "id_target in {{ids.id_target}}"),
        Dataset(name="ids", env="orders", table="target"),
    ])

    res = run_datasets(dash, store, mgr, TODAY)
    assert "unknown step reference" in res["states"].error


def test_a_realtime_dataset_on_a_historical_dashboard_hits_the_rdb(store):
    q = "select from target"
    mgr = _mgr({q: pd.DataFrame({"sym": ["AAPL"]})})
    dash = Dashboard(
        id=1, name="d",
        time_context={"mode": "historical",
                      "range": {"kind": "preset", "name": "last_30d"}},
        datasets=[Dataset(name="live", env="orders", table="target",
                          time_mode="realtime")])

    res = run_datasets(dash, store, mgr, TODAY)
    assert res["live"].error is None
    assert res["live"].qsql == q          # no date clause injected
