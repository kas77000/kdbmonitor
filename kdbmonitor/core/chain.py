# kdbmonitor/core/chain.py
from __future__ import annotations

from kdbmonitor.core.models import Step
from kdbmonitor.core.qfmt import format_q_value, format_q_list


def _filter_clause(f) -> str:
    if f.op == "in":
        clause = f"{f.column} in {format_q_list(f.value, f.value_type)}"
    elif f.op == "like":
        clause = f"{f.column} like {format_q_value(f.value, 'string')}"
    else:
        clause = f"{f.column}{f.op}{format_q_value(f.value, f.value_type)}"
    return f"not {clause}" if f.negated else clause


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


from typing import Callable
from kdbmonitor.core.models import Alert


def run_chain(alert: Alert, client_for: Callable[[str], object]) -> pd.DataFrame:
    outputs: dict[str, pd.DataFrame] = {}
    final: pd.DataFrame = pd.DataFrame()
    for step in alert.steps:
        qsql = substitute_refs(build_step_qsql(step), outputs)
        final = client_for(step.server).query(qsql)
        outputs[step.output_name] = final
    return final


from dataclasses import dataclass
from typing import Optional


@dataclass
class StepResult:
    index: int                       # 0-based position in the chain
    server: str
    qsql: str                        # the resolved query that was (or would be) run
    df: Optional[pd.DataFrame]       # result rows, or None if this step failed
    error: Optional[str]             # error message, or None on success


def preview_chain(alert: Alert, client_for: Callable[[str], object]) -> list[StepResult]:
    """Run the chain for inspection, capturing each step's query and rows.

    Unlike run_chain, this never raises: a failing step is recorded with its
    error message and the chain stops there. Use it to preview/investigate an
    alert without recording a run or sending notifications.
    """
    outputs: dict[str, pd.DataFrame] = {}
    results: list[StepResult] = []
    for i, step in enumerate(alert.steps):
        try:
            qsql = substitute_refs(build_step_qsql(step), outputs)
        except Exception as exc:  # noqa: BLE001 - surface reference errors
            results.append(StepResult(i, step.server, "", None, f"reference error: {exc}"))
            break
        try:
            df = client_for(step.server).query(qsql)
        except Exception as exc:  # noqa: BLE001 - surface query/connection errors
            results.append(StepResult(i, step.server, qsql, None, str(exc)))
            break
        outputs[step.output_name] = df
        results.append(StepResult(i, step.server, qsql, df, None))
    return results
