# tests/test_chain.py
from kdbmonitor.core.models import Step, Filter
from kdbmonitor.core.chain import build_step_qsql


def test_build_no_filters():
    step = Step(server="orders", table="target", mode="form", filters=[], output_name="step1")
    assert build_step_qsql(step) == "select from target"


def test_build_with_filters():
    step = Step(
        server="orders", table="target", mode="form",
        filters=[
            Filter("sym", "in", ["AAPL", "MSFT"], "symbol"),
            Filter("qty", ">", 100, "number"),
        ],
        output_name="step1",
    )
    assert build_step_qsql(step) == "select from target where sym in `AAPL`MSFT, qty>100"


def test_build_raw_mode_returns_raw():
    step = Step(server="orders", table="target", mode="raw",
                filters=[], raw_qsql="select from x where a=1", output_name="step1")
    assert build_step_qsql(step) == "select from x where a=1"


def test_build_like_operator():
    step = Step(server="s", table="t", mode="form",
                filters=[Filter("sym", "like", "A*", "string")], output_name="step1")
    assert build_step_qsql(step) == 'select from t where sym like "A*"'


def test_build_negated_filter():
    step = Step(server="s", table="t", mode="form",
                filters=[Filter("state", "=", "done", "symbol", negated=True)],
                output_name="step1")
    assert build_step_qsql(step) == "select from t where not state=`done"


import pandas as pd
from kdbmonitor.core.chain import substitute_refs


def test_substitute_symbol_series():
    outputs = {"step1": pd.DataFrame({"sym": ["AAPL", "MSFT", "AAPL"]})}
    q = "select from QATT where sym in {{step1.sym}}"
    assert substitute_refs(q, outputs) == "select from QATT where sym in `AAPL`MSFT"


def test_substitute_number_series_single():
    outputs = {"s1": pd.DataFrame({"id": [7]})}
    assert substitute_refs("select from t where id in {{s1.id}}", outputs) == \
        "select from t where id in enlist 7"


def test_substitute_missing_ref_raises():
    import pytest
    with pytest.raises(KeyError):
        substitute_refs("x {{nope.col}}", {})


from kdbmonitor.core.models import Alert, TriggerCondition, RearmPolicy, Channels
from kdbmonitor.core.client import FakeClient
from kdbmonitor.core.chain import run_chain


def test_run_chain_cross_server():
    orders = FakeClient({"select from target where sym in `AAPL`MSFT":
                         pd.DataFrame({"sym": ["AAPL", "MSFT"]})})
    kdp = FakeClient({"select from QATT where sym in `AAPL`MSFT":
                      pd.DataFrame({"sym": ["AAPL", "MSFT"], "bid": [101.0, 99.0]})})
    clients = {"orders": orders, "kdp": kdp}

    alert = Alert(
        id=1, name="x", enabled=True, poll_interval_secs=30,
        steps=[
            Step(server="orders", table="target", mode="form",
                 filters=[Filter("sym", "in", ["AAPL", "MSFT"], "symbol")], output_name="step1"),
            Step(server="kdp", table="QATT", mode="raw", filters=[],
                 raw_qsql="select from QATT where sym in {{step1.sym}}", output_name="step2"),
        ],
        trigger=TriggerCondition(type="has_rows"),
        channels=Channels(), rearm=RearmPolicy(),
    )

    final = run_chain(alert, client_for=lambda name: clients[name])
    assert list(final["bid"]) == [101.0, 99.0]
    assert kdp.calls == ["select from QATT where sym in `AAPL`MSFT"]
