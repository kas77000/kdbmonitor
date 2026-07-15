# kdbmonitor/core/conditions.py
from __future__ import annotations

import operator

import pandas as pd

from kdbmonitor.core.models import TriggerCondition

_OPS = {
    "=": operator.eq, "<>": operator.ne, "<": operator.lt,
    "<=": operator.le, ">": operator.gt, ">=": operator.ge,
}
_AGGS = {"max": "max", "min": "min", "avg": "mean", "sum": "sum"}


def evaluate(cond: TriggerCondition, df: pd.DataFrame) -> bool:
    n = len(df)
    if cond.type == "no_rows":
        return n == 0
    if cond.type == "has_rows":
        return n > 0
    if cond.type == "row_count_gte":
        return n >= cond.n
    if cond.type == "any_row":
        if n == 0:
            return False
        return bool(_OPS[cond.op](df[cond.column], cond.value).any())
    if cond.type == "all_rows":
        if n == 0:
            return False
        return bool(_OPS[cond.op](df[cond.column], cond.value).all())
    if cond.type == "aggregate":
        if n == 0:
            return False
        agg_val = getattr(df[cond.column], _AGGS[cond.agg])()
        return bool(_OPS[cond.op](agg_val, cond.value))
    raise ValueError(f"unknown condition type: {cond.type}")
