# tests/test_conditions.py
import pandas as pd
from kdbmonitor.core.models import TriggerCondition
from kdbmonitor.core.conditions import evaluate

EMPTY = pd.DataFrame({"bid": []})
ROWS = pd.DataFrame({"bid": [101.0, 99.0, 98.0]})


def test_no_rows_and_has_rows():
    assert evaluate(TriggerCondition(type="no_rows"), EMPTY) is True
    assert evaluate(TriggerCondition(type="no_rows"), ROWS) is False
    assert evaluate(TriggerCondition(type="has_rows"), ROWS) is True


def test_row_count_gte():
    assert evaluate(TriggerCondition(type="row_count_gte", n=3), ROWS) is True
    assert evaluate(TriggerCondition(type="row_count_gte", n=4), ROWS) is False


def test_any_row():
    assert evaluate(TriggerCondition(type="any_row", column="bid", op=">", value=100), ROWS) is True
    assert evaluate(TriggerCondition(type="any_row", column="bid", op=">", value=200), ROWS) is False


def test_all_rows():
    assert evaluate(TriggerCondition(type="all_rows", column="bid", op=">", value=90), ROWS) is True
    assert evaluate(TriggerCondition(type="all_rows", column="bid", op=">", value=100), ROWS) is False
    assert evaluate(TriggerCondition(type="all_rows", column="bid", op=">", value=90), EMPTY) is False


def test_aggregate():
    assert evaluate(TriggerCondition(type="aggregate", agg="max", column="bid", op=">", value=100), ROWS) is True
    assert evaluate(TriggerCondition(type="aggregate", agg="avg", column="bid", op="<", value=50), ROWS) is False
