import json

import pytest

from kdbmonitor.core.models import (
    Alert, Step, Filter, TriggerCondition, RearmPolicy, Channels,
)
from kdbmonitor.core.portability import export_alerts_json, import_alerts_json


def _sample_alerts():
    a1 = Alert(
        id=None, name="bid breakout", enabled=True, poll_interval_secs=30,
        steps=[Step(server="kdp", table="QATT", mode="form",
                    filters=[Filter("sym", "like", "A*", "string"),
                             Filter("state", "=", "done", "symbol", negated=True)],
                    output_name="step1")],
        trigger=TriggerCondition(type="any_row", column="bid", op=">", value=100.0,
                                 value_type="number"),
        channels=Channels(email_to=["me@x.com"], webhook_urls=["http://hook"]),
        rearm=RearmPolicy("cooldown", cooldown_secs=900),
    )
    a2 = Alert(
        id=None, name="orders -> quotes", enabled=False, poll_interval_secs=60,
        steps=[
            Step(server="orders", table="target", mode="form",
                 filters=[Filter("sym", "in", ["AAPL", "MSFT"], "symbol")],
                 output_name="step1"),
            Step(server="kdp", table="QATT", mode="raw",
                 raw_qsql="select from QATT where sym in {{step1.sym}}",
                 output_name="step2"),
        ],
        trigger=TriggerCondition(type="has_rows"),
        channels=Channels(), rearm=RearmPolicy(),
    )
    return [a1, a2]


def test_export_import_roundtrip():
    alerts = _sample_alerts()
    restored = import_alerts_json(export_alerts_json(alerts))
    assert restored == alerts  # negated/like filters, cross-server chain all survive


def test_export_strips_ids():
    alerts = _sample_alerts()
    alerts[0].id = 5
    alerts[1].id = 9
    doc = json.loads(export_alerts_json(alerts))
    assert doc["kind"] == "kdbmonitor-alerts" and doc["version"] == 1
    assert all(a["id"] is None for a in doc["alerts"])
    restored = import_alerts_json(export_alerts_json(alerts))
    assert all(a.id is None for a in restored)


def test_exported_at_included():
    doc = json.loads(export_alerts_json([], exported_at="2026-07-17T10:00:00+00:00"))
    assert doc["exported_at"] == "2026-07-17T10:00:00+00:00"
    assert doc["alerts"] == []


def test_import_rejects_bad_json():
    with pytest.raises(ValueError, match="Not valid JSON"):
        import_alerts_json("{not json")


def test_import_rejects_wrong_kind():
    with pytest.raises(ValueError, match="KdbMonitor alerts export"):
        import_alerts_json(json.dumps({"kind": "something-else", "alerts": []}))


def test_import_rejects_missing_alerts_list():
    with pytest.raises(ValueError, match="no 'alerts' list"):
        import_alerts_json(json.dumps({"kind": "kdbmonitor-alerts", "version": 1}))


def test_import_rejects_malformed_alert():
    bad = {"kind": "kdbmonitor-alerts", "version": 1, "alerts": [{"name": "x"}]}
    with pytest.raises(ValueError, match="malformed"):
        import_alerts_json(json.dumps(bad))


def test_store_export_import_roundtrip():
    from kdbmonitor.core.storage import Storage
    src = Storage(":memory:")
    src.init_db()
    for a in _sample_alerts():
        src.add_alert(a)

    doc = export_alerts_json(src.list_alerts())

    dst = Storage(":memory:")
    dst.init_db()
    for a in import_alerts_json(doc):
        dst.add_alert(a)

    src_alerts, dst_alerts = src.list_alerts(), dst.list_alerts()
    assert [a.name for a in dst_alerts] == [a.name for a in src_alerts]
    for s, d in zip(src_alerts, dst_alerts):
        s.id = d.id = None
        assert s == d
