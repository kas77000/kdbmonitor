"""End-to-end: the demo mock drives the full evaluate pipeline, no real KDB."""
from datetime import datetime, timezone

from kdbmonitor.core.storage import Storage
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.mock import demo_connection_specs
from kdbmonitor.core.evaluate import evaluate_alert
from kdbmonitor.core.models import (
    Alert, Step, Filter, TriggerCondition, RearmPolicy, Channels,
)

NOW = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)


def _demo_env():
    store = Storage(":memory:")
    store.init_db()
    for spec in demo_connection_specs():
        store.add_connection(spec)
    mgr = ConnectionManager()  # host 'demo' routes to the mock; pykx never touched

    def resolve(name):
        return mgr.get(store.get_connection_by_name(name))

    return store, resolve


def test_single_step_demo_alert_triggers():
    _store, resolve = _demo_env()
    alert = Alert(
        id=1, name="bid check", enabled=True, poll_interval_secs=30,
        steps=[Step(server="kdp_demo", table="QATT", mode="form",
                    filters=[Filter("sym", "in", ["AAPL", "MSFT"], "symbol")],
                    output_name="step1")],
        trigger=TriggerCondition(type="has_rows"),
        channels=Channels(), rearm=RearmPolicy(),
    )
    res = evaluate_alert(alert, resolve, prev_run=None, now=NOW)
    assert res.status == "triggered"
    assert res.row_count == 2


def test_cross_server_demo_chain():
    _store, resolve = _demo_env()
    alert = Alert(
        id=2, name="orders -> quotes", enabled=True, poll_interval_secs=30,
        steps=[
            Step(server="orders_demo", table="target", mode="form",
                 filters=[Filter("sym", "in", ["AAPL"], "symbol")], output_name="step1"),
            Step(server="kdp_demo", table="QATT", mode="raw",
                 raw_qsql="select from QATT where sym in {{step1.sym}}",
                 output_name="step2"),
        ],
        trigger=TriggerCondition(type="any_row", column="bid", op=">", value=0,
                                 value_type="number"),
        channels=Channels(), rearm=RearmPolicy(),
    )
    res = evaluate_alert(alert, resolve, prev_run=None, now=NOW)
    assert res.triggered is True
    assert res.row_count == 1  # target AAPL -> QATT AAPL -> one quote row
