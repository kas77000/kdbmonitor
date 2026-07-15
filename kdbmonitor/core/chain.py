# kdbmonitor/core/chain.py
from __future__ import annotations

from kdbmonitor.core.models import Step
from kdbmonitor.core.qfmt import format_q_value, format_q_list


def _filter_clause(f) -> str:
    if f.op == "in":
        return f"{f.column} in {format_q_list(f.value, f.value_type)}"
    return f"{f.column}{f.op}{format_q_value(f.value, f.value_type)}"


def build_step_qsql(step: Step) -> str:
    if step.mode == "raw":
        return step.raw_qsql or ""
    base = f"select from {step.table}"
    if not step.filters:
        return base
    clauses = ", ".join(_filter_clause(f) for f in step.filters)
    return f"{base} where {clauses}"


import re
import pandas as pd

_REF = re.compile(r"\{\{(\w+)\.(\w+)\}\}")


def _infer_value_type(series: pd.Series) -> str:
    return "number" if pd.api.types.is_numeric_dtype(series) else "symbol"


def substitute_refs(qsql: str, outputs: dict) -> str:
    def repl(m: re.Match) -> str:
        name, col = m.group(1), m.group(2)
        if name not in outputs:
            raise KeyError(f"unknown step reference: {name}")
        df = outputs[name]
        if col not in df.columns:
            raise KeyError(f"step '{name}' has no column '{col}'")
        series = df[col]
        distinct = list(dict.fromkeys(series.tolist()))  # preserve order, dedupe
        return format_q_list(distinct, _infer_value_type(series))

    return _REF.sub(repl, qsql)
