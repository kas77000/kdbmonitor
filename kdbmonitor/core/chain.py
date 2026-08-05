# kdbmonitor/core/chain.py
from __future__ import annotations

from kdbmonitor.core.models import Step
from kdbmonitor.core.qfmt import format_q_value, format_q_list, q_table


def filter_clause(f) -> str:
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
    clauses = ", ".join(filter_clause(f) for f in step.filters)
    return f"{base} where {clauses}"


import re
import pandas as pd

_REF = re.compile(r"\{\{(\w+)\.(\w+)\}\}")

# The whole of an earlier result, rather than one column of it. Prefixed like
# {{param:name}} and {{conn:ENV}} rather than written as a bare {{name}},
# which could not be told apart from {{date_from}} or from a typo — and a
# reference nobody can distinguish from a mistake is one that cannot be checked.
_TABLE_REF = re.compile(r"\{\{table:(\w+)\}\}")


def _infer_value_type(series: pd.Series) -> str:
    return "number" if pd.api.types.is_numeric_dtype(series) else "symbol"


def substitute_refs(qsql: str, outputs: dict) -> str:
    """Fill in every reference to an earlier result.

    Two forms, and the difference is what you are going to do with it.
    ``{{name.column}}`` is that column's distinct values as a q list, for a
    ``where`` clause — the original form, and still the one to reach for when
    the earlier result is there to narrow this query down.

    ``{{table:name}}`` is the whole result as a q table, for a query that has
    to *join* against it rather than filter by it: an uploaded file of order
    ids matched row for row against the OMS, where one column's values could
    only ever have asked "is it one of these" and never "and what did it say
    beside it". It carries every row and column, so a large result makes a
    large query — the same bargain the column form already offers, one column
    at a time.
    """
    def column(m: re.Match) -> str:
        name, col = m.group(1), m.group(2)
        if name not in outputs:
            raise KeyError(f"unknown step reference: {name}")
        df = outputs[name]
        if col not in df.columns:
            raise KeyError(f"step '{name}' has no column '{col}'")
        series = df[col]
        distinct = list(dict.fromkeys(series.tolist()))  # preserve order, dedupe
        return format_q_list(distinct, _infer_value_type(series))

    def table(m: re.Match) -> str:
        name = m.group(1)
        if name not in outputs:
            raise KeyError(f"unknown step reference: {name}")
        # Parenthesised, so it can stand where a table name stands: 'select
        # from (flip ...)' parses, 'select from flip ...' does not.
        return "(" + q_table(outputs[name]) + ")"

    return _TABLE_REF.sub(table, _REF.sub(column, qsql))


from datetime import datetime
from typing import Callable, Optional
from kdbmonitor.core.models import Alert
from kdbmonitor.core.qcache import QueryCache


def step_frame(step, qsql: str, client_for: Callable[[str], object],
               cache: Optional[QueryCache] = None,
               now: Optional[datetime] = None) -> pd.DataFrame:
    """One step's rows: held ones while its TTL says they still stand, else sent.

    A step with ``cache_secs`` of 0 — every step, until somebody says otherwise
    — goes to the server, which is what an alert has always done. The key is the
    resolved query and the server it goes to, so a step whose text depends on an
    earlier result stops matching the moment that result changes, TTL or no TTL.
    """
    ttl = getattr(step, "cache_secs", 0) or 0
    holding = cache if (ttl and cache is not None) else None
    key = (step.server, qsql)
    if holding is not None:
        held = holding.get(key, now=now, ttl=ttl)
        if held is not None:
            return held.df
    df = client_for(step.server).query(qsql)
    if holding is not None:
        holding.put(key, df, now)
    return df


def run_chain(alert: Alert, client_for: Callable[[str], object],
              cache: Optional[QueryCache] = None,
              now: Optional[datetime] = None) -> pd.DataFrame:
    """The chain's final result. ``cache`` holds the rows of steps that ask for
    it (see :func:`step_frame`); without one every step queries, as before."""
    outputs: dict[str, pd.DataFrame] = {}
    final: pd.DataFrame = pd.DataFrame()
    for step in alert.steps:
        qsql = substitute_refs(build_step_qsql(step), outputs)
        final = step_frame(step, qsql, client_for, cache, now)
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

    No cache, deliberately, whatever a step's TTL says: Preview is somebody
    asking for this query to be run, and answering it from a held frame would
    show them what the alert last saw rather than what the server says now.
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
