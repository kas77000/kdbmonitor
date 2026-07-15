# tests/test_rearm.py
from datetime import datetime
from kdbmonitor.core.models import RearmPolicy
from kdbmonitor.core.rearm import should_notify


def test_not_triggered_never_notifies():
    assert should_notify(prev_triggered=False, prev_notified_at=None,
                         curr_triggered=False, policy=RearmPolicy("transition"),
                         now=datetime(2026, 7, 15, 10, 0, 0)) is False


def test_transition_only_on_rising_edge():
    p = RearmPolicy("transition")
    now = datetime(2026, 7, 15, 10, 0, 0)
    assert should_notify(False, None, True, p, now) is True   # rising edge
    assert should_notify(True, now, True, p, now) is False    # still triggered


def test_every_tick():
    p = RearmPolicy("every_tick")
    now = datetime(2026, 7, 15, 10, 0, 0)
    assert should_notify(True, now, True, p, now) is True


def test_cooldown():
    p = RearmPolicy("cooldown", cooldown_secs=900)
    t0 = datetime(2026, 7, 15, 10, 0, 0)
    t_soon = datetime(2026, 7, 15, 10, 10, 0)   # 600s later
    t_late = datetime(2026, 7, 15, 10, 20, 0)   # 1200s later
    assert should_notify(False, None, True, p, t0) is True    # first trigger
    assert should_notify(True, t0, True, p, t_soon) is False  # within cooldown
    assert should_notify(True, t0, True, p, t_late) is True   # cooldown elapsed
