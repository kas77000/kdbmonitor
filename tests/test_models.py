from kdbmonitor.core.models import (
    Filter, Step, TriggerCondition, RearmPolicy, Channels, Alert, Connection,
    alert_to_json, alert_from_json,
)


def test_alert_json_roundtrip():
    alert = Alert(
        id=None,
        name="AAPL bid check",
        enabled=True,
        poll_interval_secs=30,
        steps=[
            Step(server="orders", table="target", mode="form",
                 filters=[Filter(column="sym", op="in", value=["AAPL", "MSFT"], value_type="symbol")],
                 raw_qsql=None, output_name="step1"),
            Step(server="kdp", table="QATT", mode="raw",
                 filters=[], raw_qsql="select from QATT where sym in {{step1.sym}}",
                 output_name="step2"),
        ],
        trigger=TriggerCondition(type="any_row", column="bid", op=">", value=100.0, n=None, agg=None),
        channels=Channels(in_app=True, sound=True, email_to=["me@x.com"], webhook_urls=[]),
        rearm=RearmPolicy(mode="transition", cooldown_secs=0),
    )
    restored = alert_from_json(alert_to_json(alert))
    assert restored == alert
    assert restored.steps[0].filters[0].value == ["AAPL", "MSFT"]
    assert restored.trigger.value == 100.0


def test_trigger_value_type_roundtrips():
    alert = Alert(
        id=None, name="sym check", enabled=True, poll_interval_secs=30,
        steps=[Step(server="orders", table="target", mode="form", filters=[],
                    raw_qsql=None, output_name="step1")],
        trigger=TriggerCondition(type="any_row", column="sym", op="=",
                                 value="AAPL", value_type="symbol"),
        channels=Channels(), rearm=RearmPolicy(),
    )
    restored = alert_from_json(alert_to_json(alert))
    assert restored.trigger.value_type == "symbol"
    assert restored.trigger.value == "AAPL"


def test_connection_defaults():
    c = Connection(id=None, name="orders", host="localhost", port=5010)
    assert c.schema == {}
    assert c.last_introspected_at is None


def _minimal_alert():
    return Alert(
        id=None, name="x", enabled=True, poll_interval_secs=30,
        steps=[Step(server="kdp", table="QATT", mode="form", filters=[],
                    raw_qsql=None, output_name="step1")],
        trigger=TriggerCondition(type="has_rows"),
        channels=Channels(), rearm=RearmPolicy(),
    )


def test_result_retention_defaults_and_roundtrips():
    a = _minimal_alert()
    assert a.result_retention == "latest"                      # default
    a.result_retention = "snapshot"
    assert alert_from_json(alert_to_json(a)).result_retention == "snapshot"


def test_missing_result_retention_defaults_to_latest():
    import json
    raw = json.loads(alert_to_json(_minimal_alert()))
    del raw["result_retention"]                                # simulate an older file
    assert alert_from_json(json.dumps(raw)).result_retention == "latest"
