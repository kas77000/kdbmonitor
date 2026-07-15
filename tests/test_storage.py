# tests/test_storage.py
from kdbmonitor.core.storage import Storage
from kdbmonitor.core.models import Connection


def test_connection_crud():
    store = Storage(":memory:")
    store.init_db()

    cid = store.add_connection(Connection(id=None, name="orders", host="h", port=5010))
    assert isinstance(cid, int)

    conns = store.list_connections()
    assert len(conns) == 1 and conns[0].name == "orders" and conns[0].id == cid

    got = store.get_connection(cid)
    got.schema = {"target": ["sym", "orderId"]}
    got.last_introspected_at = "2026-07-15T10:00:00"
    store.update_connection(got)
    assert store.get_connection(cid).schema == {"target": ["sym", "orderId"]}

    store.delete_connection(cid)
    assert store.list_connections() == []


def test_connection_name_unique():
    store = Storage(":memory:")
    store.init_db()
    store.add_connection(Connection(id=None, name="dup", host="h", port=1))
    import pytest
    with pytest.raises(Exception):
        store.add_connection(Connection(id=None, name="dup", host="h", port=2))


def _sample_alert():
    from kdbmonitor.core.models import (
        Alert, Step, Filter, TriggerCondition, RearmPolicy, Channels,
    )
    return Alert(
        id=None, name="a1", enabled=True, poll_interval_secs=30,
        steps=[Step(server="orders", table="target", mode="form",
                    filters=[Filter("sym", "in", ["AAPL"], "symbol")],
                    raw_qsql=None, output_name="step1")],
        trigger=TriggerCondition(type="has_rows"),
        channels=Channels(), rearm=RearmPolicy(),
    )


def test_alert_crud_and_toggle():
    store = Storage(":memory:")
    store.init_db()

    aid = store.add_alert(_sample_alert())
    got = store.get_alert(aid)
    assert got.name == "a1" and got.id == aid and got.enabled is True

    got.name = "a1-renamed"
    store.update_alert(got)
    assert store.get_alert(aid).name == "a1-renamed"

    store.set_alert_enabled(aid, False)
    assert store.get_alert(aid).enabled is False
    assert store.list_alerts()[0].enabled is False

    store.delete_alert(aid)
    assert store.list_alerts() == []


def test_runs_record_and_latest():
    store = Storage(":memory:")
    store.init_db()
    aid = store.add_alert(_sample_alert())
    store.record_run(aid, ts="2026-07-15T10:00:00", status="armed", triggered=False,
                     notified=False, row_count=0, message="")
    store.record_run(aid, ts="2026-07-15T10:00:30", status="triggered", triggered=True,
                     notified=True, row_count=3, message="hit")
    latest = store.latest_run(aid)
    assert latest["status"] == "triggered" and latest["triggered"] == 1
    assert len(store.list_runs(aid)) == 2
    assert store.latest_run(9999) is None


def test_last_notified_at():
    store = Storage(":memory:")
    store.init_db()
    aid = store.add_alert(_sample_alert())
    assert store.last_notified_at(aid) is None
    store.record_run(aid, ts="2026-07-15T10:00:00", status="triggered", triggered=True,
                     notified=True, row_count=3, message="hit")
    store.record_run(aid, ts="2026-07-15T10:05:00", status="armed", triggered=False,
                     notified=False, row_count=0, message="")
    assert store.last_notified_at(aid) == "2026-07-15T10:00:00"


def test_settings_get_set():
    store = Storage(":memory:")
    store.init_db()
    assert store.get_setting("missing") is None
    assert store.get_setting("missing", "dflt") == "dflt"
    store.set_setting("smtp_host", "mail.example.com")
    assert store.get_setting("smtp_host") == "mail.example.com"
    store.set_setting("smtp_host", "mail2.example.com")
    assert store.get_setting("smtp_host") == "mail2.example.com"
