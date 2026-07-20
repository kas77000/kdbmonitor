# tests/test_evaluate.py
from datetime import datetime
import pandas as pd
from kdbmonitor.core.models import Alert, Step, Filter, TriggerCondition, RearmPolicy, Channels
from kdbmonitor.core.client import FakeClient
from kdbmonitor.core.evaluate import evaluate_alert


def _alert(cond):
    return Alert(
        id=1, name="x", enabled=True, poll_interval_secs=30,
        steps=[Step(server="kdp", table="QATT", mode="form",
                    filters=[Filter("sym", "in", ["AAPL"], "symbol")], output_name="step1")],
        trigger=cond, channels=Channels(), rearm=RearmPolicy("transition"),
    )


def _client_for(df):
    c = FakeClient({"select from QATT where sym in enlist `AAPL": df})
    return lambda name: c


def test_evaluate_triggers_and_notifies_on_rising_edge():
    df = pd.DataFrame({"sym": ["AAPL"], "bid": [101.0]})
    alert = _alert(TriggerCondition(type="any_row", column="bid", op=">", value=100))
    res = evaluate_alert(alert, _client_for(df), prev_run=None,
                         now=datetime(2026, 7, 15, 10, 0, 0))
    assert res.status == "triggered"
    assert res.triggered is True and res.notify is True
    assert res.row_count == 1
    assert "x" in res.message


def test_evaluate_armed_when_condition_false():
    df = pd.DataFrame({"sym": ["AAPL"], "bid": [50.0]})
    alert = _alert(TriggerCondition(type="any_row", column="bid", op=">", value=100))
    res = evaluate_alert(alert, _client_for(df), prev_run=None,
                         now=datetime(2026, 7, 15, 10, 0, 0))
    assert res.status == "armed" and res.triggered is False and res.notify is False


def test_cooldown_throttles_and_rearms_with_last_notified_ts():
    from datetime import timedelta
    df = pd.DataFrame({"sym": ["AAPL"], "bid": [101.0]})
    cond = TriggerCondition(type="any_row", column="bid", op=">", value=100)
    alert = Alert(
        id=1, name="x", enabled=True, poll_interval_secs=30,
        steps=[Step(server="kdp", table="QATT", mode="form",
                    filters=[Filter("sym", "in", ["AAPL"], "symbol")], output_name="step1")],
        trigger=cond, channels=Channels(), rearm=RearmPolicy("cooldown", cooldown_secs=900),
    )
    now = datetime(2026, 7, 15, 10, 0, 0)
    prev = {"triggered": 1, "notified": 0, "ts": (now - timedelta(seconds=600)).isoformat()}

    res_throttled = evaluate_alert(
        alert, _client_for(df), prev_run=prev, now=now,
        last_notified_ts=(now - timedelta(seconds=600)).isoformat())
    assert res_throttled.triggered is True and res_throttled.notify is False

    res_rearmed = evaluate_alert(
        alert, _client_for(df), prev_run=prev, now=now,
        last_notified_ts=(now - timedelta(seconds=1200)).isoformat())
    assert res_rearmed.triggered is True and res_rearmed.notify is True


def test_evaluate_error_on_query_failure():
    def boom(name):
        raise RuntimeError("connection refused")
    alert = _alert(TriggerCondition(type="has_rows"))
    res = evaluate_alert(alert, boom, prev_run=None, now=datetime(2026, 7, 15, 10, 0, 0))
    assert res.status == "error" and res.notify is False
    assert "connection refused" in res.message
    assert res.df is None


def test_evaluate_returns_result_df():
    df = pd.DataFrame({"sym": ["AAPL"], "bid": [101.0]})
    res = evaluate_alert(_alert(TriggerCondition(type="has_rows")), _client_for(df),
                         prev_run=None, now=datetime(2026, 7, 15, 10, 0, 0))
    assert res.df is not None and len(res.df) == 1
    assert list(res.df["bid"]) == [101.0]


def test_evaluate_on_change_gates_trigger_by_snapshot():
    from kdbmonitor.core.fingerprint import result_fingerprint
    df = pd.DataFrame({"sym": ["AAPL"], "bid": [101.0]})
    alert = _alert(TriggerCondition(type="has_rows"))
    alert.rearm = RearmPolicy("on_change")
    now = datetime(2026, 7, 15, 10, 0, 0)
    prev = {"triggered": 1, "notified": 1, "ts": now.isoformat()}
    same = result_fingerprint(df)

    # same as the previous triggered snapshot -> NOT triggered (stays armed), no notify
    dup = evaluate_alert(alert, _client_for(df), prev_run=prev, now=now, last_triggered_hash=same)
    assert dup.triggered is False and dup.notify is False and dup.status == "armed"
    assert "unchanged" in dup.message

    # different snapshot -> triggered + notify
    changed = evaluate_alert(alert, _client_for(df), prev_run=prev, now=now,
                             last_triggered_hash="different-hash")
    assert changed.triggered is True and changed.notify is True

    # first ever (no prior trigger) -> triggered + notify
    first = evaluate_alert(alert, _client_for(df), prev_run=None, now=now,
                           last_triggered_hash=None)
    assert first.triggered is True and first.notify is True
