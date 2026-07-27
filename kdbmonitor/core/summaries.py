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


def transform_summary(transform) -> str:
    """One-line description of a dataset transform, e.g. 'group by market'.

    Used to label the stages of a dataset's pipeline so a preview reads as the
    steps the user configured rather than as anonymous frames.
    """
    p = transform.params
    kind = transform.kind
    if kind == "derive":
        if p.get("kind", "arithmetic") == "suffix_map":
            return (f"derive {p.get('column') or '?'} from "
                    f"{p.get('source') or '?'}")
        return f"derive {p.get('column') or '?'} = {p.get('expr') or '?'}"
    if kind == "filter":
        return (f"keep rows where {p.get('column') or '?'} "
                f"{p.get('op') or '?'} {p.get('value')}")
    if kind == "groupby":
        keys = ", ".join(p.get("keys") or ["?"])
        aggs = ", ".join(f"{a.get('func')}({a.get('column')}) as {a.get('as')}"
                         for a in p.get("aggs") or []) or "?"
        return f"group by {keys} → {aggs}"
    if kind == "sort":
        columns = ", ".join(p.get("columns") or ["?"])
        return (f"sort by {columns} "
                f"{'ascending' if p.get('ascending', True) else 'descending'}")
    if kind == "limit":
        return f"keep the first {p.get('n', '?')} rows"
    if kind == "rename":
        renames = ", ".join(f"{k} → {v}" for k, v in (p.get("mapping") or {}).items())
        return f"rename {renames or '?'}"
    return kind


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
