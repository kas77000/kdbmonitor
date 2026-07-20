import io
from datetime import date, datetime, timezone, timedelta

import pandas as pd

from kdbmonitor.core.storage import Storage
from kdbmonitor.core.reporting import build_report_model, report_to_excel_bytes
from kdbmonitor.core.models import (
    Alert, Step, TriggerCondition, RearmPolicy, Channels,
)


class _FakeClient:
    def query(self, qsql):
        return pd.DataFrame([{"sym": "AAPL", "side": "buy", "qty": 5000}])


def _alert(name="Algo on limit"):
    return Alert(
        id=None, name=name, enabled=True, poll_interval_secs=30,
        steps=[Step(server="orders", table="target", mode="raw",
                    raw_qsql="select from target", output_name="hits")],
        trigger=TriggerCondition(type="has_rows"),
        channels=Channels(), rearm=RearmPolicy(),
    )


def _store_with_runs():
    store = Storage(":memory:")
    store.init_db()
    aid = store.add_alert(_alert())
    now = datetime.now(timezone.utc)
    y = now - timedelta(days=1)
    # two triggers today, one armed today (excluded), one trigger yesterday (excluded)
    store.record_run(aid, now.isoformat(), "triggered", True, True, 1, "TRIGGERED (1 rows)")
    store.record_run(aid, now.isoformat(), "triggered", True, False, 1, "TRIGGERED (1 rows)")
    store.record_run(aid, now.isoformat(), "armed", False, False, 0, "armed (0 rows)")
    store.record_run(aid, y.isoformat(), "triggered", True, True, 1, "TRIGGERED (1 rows)")
    return store, aid, now


def test_list_runs_since_triggered_only():
    store, aid, now = _store_with_runs()
    since = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    all_today = store.list_runs_since(since)
    trig_today = store.list_runs_since(since, triggered_only=True)
    assert len(all_today) == 3          # 2 triggered + 1 armed (yesterday excluded by since)
    assert len(trig_today) == 2


def test_report_model_counts_and_result():
    store, aid, now = _store_with_runs()
    model = build_report_model(store, now.date(), client_for=lambda s: _FakeClient(), now=now)
    assert model["summary"] == {"alerts": 1, "triggers": 2}
    a = model["alerts"][0]
    assert a["triggers"] == 2
    assert a["result"]["row_count"] == 1          # fetched live via the fake client
    assert list(a["result"]["df"].columns) == ["sym", "side", "qty"]


def test_report_model_without_client_has_no_result():
    store, aid, now = _store_with_runs()
    model = build_report_model(store, now.date(), client_for=None, now=now)
    assert model["alerts"][0]["result"] is None


def test_report_model_records_query_error():
    store, aid, now = _store_with_runs()

    def boom(_server):
        class C:
            def query(self, q):
                raise RuntimeError("connection refused")
        return C()

    model = build_report_model(store, now.date(), client_for=boom, now=now)
    assert model["alerts"][0]["result"]["error"] == "connection refused"


def test_excel_bytes_have_expected_sheets():
    store, aid, now = _store_with_runs()
    model = build_report_model(store, now.date(), client_for=lambda s: _FakeClient(), now=now)
    data = report_to_excel_bytes(model)
    sheets = pd.ExcelFile(io.BytesIO(data)).sheet_names
    assert {"Summary", "About", "Triggers"} <= set(sheets)
    assert len(sheets) == 4                        # + one per-alert sheet
    summary = pd.read_excel(io.BytesIO(data), sheet_name="Summary")
    assert summary.iloc[0]["Triggers"] == 2


def test_excel_bytes_empty_day():
    store = Storage(":memory:")
    store.init_db()
    model = build_report_model(store, datetime(2020, 1, 1).date(), now=datetime.now(timezone.utc))
    data = report_to_excel_bytes(model)          # must still produce a valid workbook
    sheets = pd.ExcelFile(io.BytesIO(data)).sheet_names
    assert "Summary" in sheets


def test_report_uses_stored_snapshot_for_past_day():
    store = Storage(":memory:")
    store.init_db()
    aid = store.add_alert(_alert())
    d = "2026-07-10"
    store.record_run(aid, d + "T10:00:00+00:00", "triggered", True, True, 2, "TRIGGERED (2 rows)")
    store.save_result(aid, d + "T10:00:00+00:00",
                      pd.DataFrame([{"sym": "AAPL"}, {"sym": "MSFT"}]))
    # client_for is provided but must NOT be used for a past day
    model = build_report_model(store, date(2026, 7, 10),
                               client_for=lambda s: _FakeClient(),
                               now=datetime(2026, 7, 20, tzinfo=timezone.utc))
    r = model["alerts"][0]["result"]
    assert r["source"] == "snapshot"
    assert r["row_count"] == 2
    assert list(r["df"]["sym"]) == ["AAPL", "MSFT"]


def test_report_no_live_refetch_for_past_day_without_snapshot():
    store = Storage(":memory:")
    store.init_db()
    aid = store.add_alert(_alert())
    d = "2026-07-10"
    store.record_run(aid, d + "T10:00:00+00:00", "triggered", True, True, 2, "msg")
    model = build_report_model(store, date(2026, 7, 10),
                               client_for=lambda s: _FakeClient(),
                               now=datetime(2026, 7, 20, tzinfo=timezone.utc))
    assert model["alerts"][0]["result"] is None      # history never re-fetches live
