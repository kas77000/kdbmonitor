"""Shared UI helpers: status metadata, timer math, and plain-English summaries.

The functions here are deliberately Streamlit-free and pure so they can be
unit-tested without a running app.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

# Re-exported from core so the UI keeps a single import site; the definitions
# live in core.summaries because the report builder needs them too.
from kdbmonitor.core.summaries import condition_summary, step_summary  # noqa: F401

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


def should_capture_result(retention: str, triggered: bool, prev_triggered: bool) -> bool:
    """Whether the Monitor should (over)write the stored result this check.

    Data is captured only on a triggered check; armed/error checks keep whatever
    was last captured. In 'snapshot' mode we freeze at the trigger moment (the
    rising edge), so a sustained trigger isn't overwritten each tick; in 'latest'
    mode every triggered check refreshes the stored rows.
    """
    if not triggered:
        return False
    if retention == "snapshot":
        return not prev_triggered
    return True


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


