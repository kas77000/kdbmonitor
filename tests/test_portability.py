import json

import pytest

from kdbmonitor.core.models import (
    Alert, Step, Filter, TriggerCondition, RearmPolicy, Channels, Connection,
    alert_to_dict,
)
from kdbmonitor.core.portability import (
    export_bundle_json, import_bundle_json, conflicting_alert_names,
)


def _sample_conns():
    return [
        Connection(id=None, name="kdp", host="localhost", port=5010,
                   schema={"QATT": ["sym", "bid", "ask"]},
                   last_introspected_at="2026-07-17T10:00:00"),
        Connection(id=None, name="orders", host="10.0.0.5", port=5020),
    ]


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


def test_bundle_roundtrip():
    conns, alerts = _sample_conns(), _sample_alerts()
    r_conns, r_alerts = import_bundle_json(export_bundle_json(conns, alerts))
    assert r_alerts == alerts          # like/negated filters + cross-server chain survive
    assert r_conns == conns            # host, port, schema, last_introspected_at survive


def test_export_strips_ids():
    conns, alerts = _sample_conns(), _sample_alerts()
    conns[0].id, alerts[0].id = 3, 5
    doc = json.loads(export_bundle_json(conns, alerts))
    assert doc["kind"] == "kdbmonitor-export" and doc["version"] == 2
    assert all(c["id"] is None for c in doc["connections"])
    assert all(a["id"] is None for a in doc["alerts"])
    r_conns, r_alerts = import_bundle_json(export_bundle_json(conns, alerts))
    assert all(c.id is None for c in r_conns)
    assert all(a.id is None for a in r_alerts)


def test_exported_at_included():
    doc = json.loads(export_bundle_json([], [], exported_at="2026-07-17T10:00:00+00:00"))
    assert doc["exported_at"] == "2026-07-17T10:00:00+00:00"
    assert doc["alerts"] == [] and doc["connections"] == []


def test_conflicting_alert_names():
    alerts = _sample_alerts()
    assert conflicting_alert_names({"bid breakout"}, alerts) == ["bid breakout"]
    assert conflicting_alert_names(set(), alerts) == []
    assert conflicting_alert_names({"nope"}, alerts) == []
    # order-preserving + deduped
    both = conflicting_alert_names({"bid breakout", "orders -> quotes"}, alerts * 2)
    assert both == ["bid breakout", "orders -> quotes"]


def test_legacy_alert_only_file_imports():
    doc = json.dumps({"kind": "kdbmonitor-alerts", "version": 1,
                      "alerts": [alert_to_dict(_sample_alerts()[0])]})
    conns, alerts = import_bundle_json(doc)
    assert conns == []
    assert len(alerts) == 1 and alerts[0].name == "bid breakout"


def test_import_rejects_bad_json():
    with pytest.raises(ValueError, match="Not valid JSON"):
        import_bundle_json("{not json")


def test_import_rejects_wrong_kind():
    with pytest.raises(ValueError, match="KdbMonitor export"):
        import_bundle_json(json.dumps({"kind": "something-else", "alerts": []}))


def test_import_rejects_missing_alerts_list():
    with pytest.raises(ValueError, match="no 'alerts' list"):
        import_bundle_json(json.dumps({"kind": "kdbmonitor-export", "version": 2}))


def test_import_rejects_malformed_alert():
    bad = {"kind": "kdbmonitor-export", "version": 2, "connections": [],
           "alerts": [{"name": "x"}]}
    with pytest.raises(ValueError, match="Alert #1 is malformed"):
        import_bundle_json(json.dumps(bad))


def test_import_rejects_malformed_connection():
    bad = {"kind": "kdbmonitor-export", "version": 2,
           "connections": [{"host": "h"}], "alerts": []}
    with pytest.raises(ValueError, match="Connection #1 is malformed"):
        import_bundle_json(json.dumps(bad))


def test_store_bundle_roundtrip():
    from kdbmonitor.core.storage import Storage
    src = Storage(":memory:")
    src.init_db()
    for c in _sample_conns():
        src.add_connection(c)
    for a in _sample_alerts():
        src.add_alert(a)

    doc = export_bundle_json(src.list_connections(), src.list_alerts())

    dst = Storage(":memory:")
    dst.init_db()
    conns, alerts = import_bundle_json(doc)
    for c in conns:
        dst.add_connection(c)
    for a in alerts:
        dst.add_alert(a)

    assert [c.name for c in dst.list_connections()] == [c.name for c in src.list_connections()]
    assert [a.name for a in dst.list_alerts()] == [a.name for a in src.list_alerts()]
