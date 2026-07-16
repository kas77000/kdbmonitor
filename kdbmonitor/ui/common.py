"""Shared UI helpers: status metadata, timer math, and plain-English summaries.

The functions here are deliberately Streamlit-free and pure so they can be
unit-tested without a running app.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from kdbmonitor.core.models import Step, TriggerCondition

# status key -> (label, streamlit badge color, material icon)
STATUS_META: dict[str, tuple[str, str, str]] = {
    "triggered": ("Triggered", "red", ":material/notifications_active:"),
    "armed": ("Armed", "green", ":material/check_circle:"),
    "error": ("Error", "orange", ":material/error:"),
    "disabled": ("Disabled", "gray", ":material/pause_circle:"),
    "pending": ("Pending", "gray", ":material/schedule:"),
}

# Common poll-interval presets: label -> seconds
INTERVAL_PRESETS: dict[str, int] = {
    "5s": 5, "15s": 15, "30s": 30, "1m": 60, "5m": 300, "15m": 900,
}


def make_client_for(store, mgr):
    """Return a resolver: server name -> KDB client, via the connection store."""
    def resolve(server_name: str):
        conn = store.get_connection_by_name(server_name)
        if conn is None:
            raise RuntimeError(f"unknown server '{server_name}'")
        return mgr.get(conn)
    return resolve


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def is_due(last_ts: Optional[str], interval_secs: int, now: datetime) -> bool:
    """True if an alert last evaluated at `last_ts` is due again by `now`."""
    if not last_ts:
        return True
    return (now - _parse(last_ts)).total_seconds() >= interval_secs


def secs_until_due(last_ts: Optional[str], interval_secs: int, now: datetime) -> int:
    """Whole seconds until the next evaluation is due (0 if due now)."""
    if not last_ts:
        return 0
    remaining = interval_secs - (now - _parse(last_ts)).total_seconds()
    return max(0, int(remaining))


def humanize_secs(secs: int) -> str:
    """Compact duration label, e.g. 5 -> '5s', 90 -> '1m 30s', 300 -> '5m'."""
    if secs < 60:
        return f"{secs}s"
    minutes, seconds = divmod(secs, 60)
    return f"{minutes}m" if seconds == 0 else f"{minutes}m {seconds}s"


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
    where = ""
    if step.mode == "raw":
        return f"{step.server} · raw qSQL"
    if step.filters:
        where = " where " + ", ".join(
            f"{'not ' if f.negated else ''}{f.column} {f.op} {f.value}"
            for f in step.filters
        )
    return f"{step.server} · {step.table}{where}"
