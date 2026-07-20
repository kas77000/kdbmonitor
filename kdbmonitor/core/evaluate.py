# kdbmonitor/core/evaluate.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from kdbmonitor.core.models import Alert
from kdbmonitor.core.chain import run_chain
from kdbmonitor.core.conditions import evaluate as eval_condition
from kdbmonitor.core.fingerprint import result_fingerprint
from kdbmonitor.core.rearm import should_notify


@dataclass
class EvalResult:
    status: str          # armed | triggered | error
    triggered: bool
    notify: bool
    row_count: Optional[int]
    message: str
    df: Optional[object] = None   # final result rows (pandas DataFrame), None on error
    result_hash: str = ""         # content fingerprint (for the 'on_change' re-arm)


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(ts) if ts else None


def evaluate_alert(alert: Alert, client_for: Callable[[str], object],
                   prev_run: Optional[dict], now: datetime,
                   last_notified_ts: Optional[str] = None,
                   last_triggered_hash: Optional[str] = None) -> EvalResult:
    try:
        df = run_chain(alert, client_for)
    except Exception as exc:  # noqa: BLE001 - surface any query/connection error
        return EvalResult(status="error", triggered=False, notify=False,
                          row_count=None, message=f"{alert.name}: error - {exc}")

    condition_met = eval_condition(alert.trigger, df)
    curr_hash = result_fingerprint(df)

    # 'on_change': treat as triggered only on the first trigger, or when this
    # snapshot differs from the previous *triggered* snapshot. Identical repeat
    # results stay armed and don't re-fire.
    if alert.rearm.mode == "on_change":
        is_new = (last_triggered_hash is None) or (curr_hash != last_triggered_hash)
        triggered = condition_met and is_new
    else:
        triggered = condition_met

    prev_triggered = bool(prev_run["triggered"]) if prev_run else False
    prev_notified_at = _parse_ts(last_notified_ts) if last_notified_ts else None
    notify = should_notify(prev_triggered, prev_notified_at, triggered, alert.rearm, now)
    status = "triggered" if triggered else "armed"
    if triggered:
        message = f"{alert.name}: TRIGGERED ({len(df)} rows)"
    elif condition_met and alert.rearm.mode == "on_change":
        message = f"{alert.name}: unchanged ({len(df)} rows)"
    else:
        message = f"{alert.name}: armed ({len(df)} rows)"
    return EvalResult(status=status, triggered=triggered, notify=notify,
                      row_count=len(df), message=message, df=df, result_hash=curr_hash)
