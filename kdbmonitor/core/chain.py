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
