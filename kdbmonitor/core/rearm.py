# kdbmonitor/core/rearm.py
from __future__ import annotations

from datetime import datetime
from typing import Optional

from kdbmonitor.core.models import RearmPolicy


def should_notify(prev_triggered: bool, prev_notified_at: Optional[datetime],
                  curr_triggered: bool, policy: RearmPolicy, now: datetime) -> bool:
    if not curr_triggered:
        return False
    if policy.mode == "every_tick":
        return True
    if policy.mode == "transition":
        return not prev_triggered
    if policy.mode == "cooldown":
        if not prev_triggered or prev_notified_at is None:
            return True
        return (now - prev_notified_at).total_seconds() >= policy.cooldown_secs
    raise ValueError(f"unknown rearm mode: {policy.mode}")
