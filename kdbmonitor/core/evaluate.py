# kdbmonitor/core/evaluate.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from kdbmonitor.core.models import Alert
from kdbmonitor.core.chain import run_chain
from kdbmonitor.core.conditions import evaluate as eval_condition
from kdbmonitor.core.rearm import should_notify


@dataclass
class EvalResult:
    status: str          # armed | triggered | error
    triggered: bool
    notify: bool
    row_count: Optional[int]
    message: str


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(ts) if ts else None


def evaluate_alert(alert: Alert, client_for: Callable[[str], object],
                   prev_run: Optional[dict], now: datetime) -> EvalResult:
    try:
        df = run_chain(alert, client_for)
    except Exception as exc:  # noqa: BLE001 - surface any query/connection error
        return EvalResult(status="error", triggered=False, notify=False,
                          row_count=None, message=f"{alert.name}: error - {exc}")

    triggered = eval_condition(alert.trigger, df)
    prev_triggered = bool(prev_run["triggered"]) if prev_run else False
    prev_notified_at = None
    if prev_run and prev_run.get("notified") and prev_run.get("ts"):
        prev_notified_at = _parse_ts(prev_run["ts"])

    notify = should_notify(prev_triggered, prev_notified_at, triggered, alert.rearm, now)
    status = "triggered" if triggered else "armed"
    message = (f"{alert.name}: TRIGGERED ({len(df)} rows)" if triggered
               else f"{alert.name}: armed ({len(df)} rows)")
    return EvalResult(status=status, triggered=triggered, notify=notify,
                      row_count=len(df), message=message)
