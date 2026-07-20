"""Plain-English descriptions of triggers and steps.

Pure and UI-independent so both the Streamlit layer and the report builder can
share one source of truth for how an alert is described to a human.
"""
from __future__ import annotations

from kdbmonitor.core.models import Step, TriggerCondition


def condition_summary(trigger: TriggerCondition) -> str:
    """Plain-English description of when a trigger fires."""
    t = trigger.type
    if t == "no_rows":
        return "the final query returns no rows"
    if t == "has_rows":
        return "the final query returns at least one row"
    if t == "row_count_gte":
        return f"the final query returns at least {trigger.n} rows"
    if t == "any_row":
        return f"at least one row has {trigger.column} {trigger.op} {trigger.value}"
    if t == "all_rows":
        return f"every row has {trigger.column} {trigger.op} {trigger.value}"
    if t == "aggregate":
        return f"{trigger.agg}({trigger.column}) {trigger.op} {trigger.value}"
    return t


def step_summary(step: Step) -> str:
    """One-line description of a single chain step."""
    if step.mode == "raw":
        return f"{step.server} · raw qSQL"
    where = ""
    if step.filters:
        where = " where " + ", ".join(
            f"{'not ' if f.negated else ''}{f.column} {f.op} {f.value}"
            for f in step.filters
        )
    return f"{step.server} · {step.table}{where}"
