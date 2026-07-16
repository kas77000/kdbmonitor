import pandas as pd

from kdbmonitor.core.client import FakeClient
from kdbmonitor.core.chain import preview_chain
from kdbmonitor.core.models import (
    Alert, Step, Filter, TriggerCondition, RearmPolicy, Channels,
)


def _alert(steps):
    return Alert(id=1, name="x", enabled=True, poll_interval_secs=30, steps=steps,
                 trigger=TriggerCondition(type="has_rows"),
                 channels=Channels(), rearm=RearmPolicy())


def test_preview_captures_each_step_query_and_rows():
    orders = FakeClient({"select from target where sym in `AAPL`MSFT":
                         pd.DataFrame({"sym": ["AAPL", "MSFT"]})})
    kdp = FakeClient({"select from QATT where sym in `AAPL`MSFT":
                      pd.DataFrame({"sym": ["AAPL", "MSFT"], "bid": [101.0, 99.0]})})
    clients = {"orders": orders, "kdp": kdp}
    alert = _alert([
        Step("orders", "target", "form",
             [Filter("sym", "in", ["AAPL", "MSFT"], "symbol")], None, "step1"),
        Step("kdp", "QATT", "raw", [],
             "select from QATT where sym in {{step1.sym}}", "step2"),
    ])

    results = preview_chain(alert, lambda n: clients[n])

    assert len(results) == 2
    assert results[0].error is None
    assert results[0].qsql == "select from target where sym in `AAPL`MSFT"
    assert list(results[0].df["sym"]) == ["AAPL", "MSFT"]
    # cross-server substitution shows in the previewed query
    assert results[1].qsql == "select from QATT where sym in `AAPL`MSFT"
    assert list(results[1].df["bid"]) == [101.0, 99.0]


def test_preview_stops_and_records_error():
    def boom(name):
        raise RuntimeError("connection refused")

    alert = _alert([
        Step("kdp", "QATT", "form", [Filter("sym", "in", ["AAPL"], "symbol")],
             None, "step1"),
        Step("kdp", "QATT", "raw", [], "select from QATT", "step2"),
    ])

    results = preview_chain(alert, boom)

    assert len(results) == 1                    # stopped at the failing step
    assert results[0].df is None
    assert "connection refused" in results[0].error


def test_preview_with_demo_mock():
    from kdbmonitor.core.storage import Storage
    from kdbmonitor.core.client import ConnectionManager
    from kdbmonitor.core.mock import demo_connection_specs
    from kdbmonitor.ui.common import make_client_for

    store = Storage(":memory:")
    store.init_db()
    for spec in demo_connection_specs():
        store.add_connection(spec)
    resolve = make_client_for(store, ConnectionManager())

    alert = _alert([Step("kdp_demo", "QATT", "form",
                         [Filter("sym", "in", ["AAPL", "MSFT"], "symbol")], None, "step1")])
    results = preview_chain(alert, resolve)

    assert len(results) == 1 and results[0].error is None
    assert set(results[0].df["sym"]) == {"AAPL", "MSFT"}
    assert "bid" in results[0].df.columns


def test_preview_reports_bad_reference():
    # step2 references a column that step1 doesn't return
    orders = FakeClient({"select from target": pd.DataFrame({"sym": ["AAPL"]})})
    alert = _alert([
        Step("orders", "target", "form", [], None, "step1"),
        Step("orders", "target", "raw", [], "select from x where a in {{step1.nope}}",
             "step2"),
    ])

    results = preview_chain(alert, lambda n: orders)

    assert len(results) == 2
    assert results[0].error is None
    assert results[1].df is None
    assert "reference error" in results[1].error
