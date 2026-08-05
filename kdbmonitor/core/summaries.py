"""Plain-English descriptions of triggers and steps.

Pure and UI-independent so both the Streamlit layer and the report builder can
share one source of truth for how an alert is described to a human.
"""
from __future__ import annotations

from kdbmonitor.core.models import (
    STEP_CACHE_PRESETS, Channels, Step, TriggerCondition,
)

# Short forms of the delivery names, for a one-line summary of an alert. The
# full labels live in models.DELIVERY_LABELS, which is what the Builder shows.
_DELIVERY_SHORT: tuple[tuple[str, str], ...] = (
    ("in_app", "in-app"), ("sound", "sound"), ("browser", "notification"),
    ("focus", "window to front"), ("popup", "result pop-up"),
)


def channels_summary(channels: Channels) -> str:
    """'in-app · sound · result pop-up · 2 emails' — how a fired alert lands.

    'nothing' is a real answer: an alert with every delivery off still records
    its runs and still shows as triggered on the Monitor, it just doesn't come
    looking for you.
    """
    parts = [label for attr, label in _DELIVERY_SHORT
             if getattr(channels, attr, False)]
    if channels.email_to:
        n = len(channels.email_to)
        parts.append(f"{n} email{'' if n == 1 else 's'}")
    if channels.webhook_urls:
        n = len(channels.webhook_urls)
        parts.append(f"{n} webhook{'' if n == 1 else 's'}")
    return " · ".join(parts) if parts else "nothing"


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
    if kind == "window":
        op = p.get("op") or "?"
        # row_number counts rows rather than reading one, so there is no
        # source column to name.
        of = "" if op == "row_number" else f" of {p.get('column') or '?'}"
        by = p.get("partition_by") or []
        return (f"window: {op}{of}"
                f"{' by ' + ', '.join(by) if by else ''} → {p.get('as') or '?'}")
    if kind == "timezone":
        source = p.get("from_column") or p.get("from_zone") or "?"
        summary = (f"convert {p.get('column') or '?'} from {source} to "
                   f"{p.get('to') or '?'} as {p.get('as') or '?'}")
        if p.get("day_offset_as"):
            summary += f" (+ {p['day_offset_as']})"
        return summary
    return kind


def cache_summary(secs: int) -> str:
    """'cached 1 hour' — how long a step's rows stand before it queries again.

    Empty for a step that queries every check, which is what most do: a summary
    should say what is unusual about a step, and going to the server is not.
    """
    if not secs:
        return ""
    label = next((k for k, v in STEP_CACHE_PRESETS.items() if v == secs and v),
                 f"{secs}s")
    return f"cached {label.lower()}"


def step_summary(step: Step) -> str:
    """One-line description of a single chain step."""
    if step.mode == "raw":
        head = f"{step.server} · raw qSQL"
    else:
        where = ""
        if step.filters:
            where = " where " + ", ".join(
                f"{'not ' if f.negated else ''}{f.column} {f.op} {f.value}"
                for f in step.filters
            )
        head = f"{step.server} · {step.table}{where}"
    held = cache_summary(getattr(step, "cache_secs", 0))
    return f"{head} · {held}" if held else head
