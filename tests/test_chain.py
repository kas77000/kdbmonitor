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
