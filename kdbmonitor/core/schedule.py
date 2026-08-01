"""When an alert is allowed to run.

An alert that only means something inside a window — a 16:30 mark, the last
fifteen minutes before a cutoff, the hour a feed is supposed to be quiet —
spends the rest of the day reporting things its author never wanted to hear
about. This module answers one question, ``is_active``, and the two that make
the answer legible on screen: when does it open, when does it close.

Everything here is pure and timezone-aware. Times are written the way the
person watching says them ("16:30", "17:45-18:00") and are read in the
schedule's own zone, while the engine works entirely in UTC — the conversion
happens here so nowhere else has to think about it. Zone names go through
:mod:`kdbmonitor.core.zones`, so a Windows display name, an IANA id, an
abbreviation or a bare UTC offset are all accepted, and DST is computed at the
date in question rather than assumed.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Iterator, Optional

from kdbmonitor.core.models import Schedule, Window
from kdbmonitor.core import zones

_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# How far ahead next_open looks. A schedule that runs on one weekday needs a
# week; anything that finds nothing in nine days has no windows worth waiting
# for (an empty day list, say), and saying "no next window" beats scanning on.
_HORIZON_DAYS = 9


def parse_hhmm(text: str) -> time:
    """'16:30' -> time(16, 30). Raises ValueError on anything else."""
    m = _HHMM_RE.match((text or "").strip())
    if not m:
        raise ValueError(f"not a time of day: {text!r} (expected HH:MM)")
    return time(int(m.group(1)), int(m.group(2)))


def validate(schedule: Schedule) -> list[str]:
    """Human-readable problems with a schedule; empty when it is usable.

    The Builder shows these before saving, because a schedule that silently
    matches nothing looks exactly like an alert that never fires.
    """
    problems: list[str] = []
    if schedule.mode != "windows":
        return problems
    if not schedule.windows:
        problems.append("Active windows are on but no window is set.")
    for i, w in enumerate(schedule.windows, start=1):
        try:
            start = parse_hhmm(w.start)
            end = parse_hhmm(w.end)
        except ValueError as exc:
            problems.append(f"Window {i}: {exc}")
            continue
        if start == end:
            problems.append(f"Window {i}: start and end are both {w.start} — "
                            "give it some width (16:30-16:31 for a single moment).")
    for d in schedule.days:
        if d not in range(7):
            problems.append(f"Unknown day {d} (expected 0=Mon .. 6=Sun).")
    if schedule.tz:
        try:
            zones.to_iana(schedule.tz)
        except ValueError as exc:
            problems.append(str(exc))
    return problems


def _tz(schedule: Schedule):
    """The schedule's tzinfo, falling back to the machine's own zone."""
    name = (schedule.tz or "local").strip()
    if name.lower() == "local":
        name = zones.local_zone()
    return zones.tzinfo_for(name)


def zone_label(schedule: Schedule) -> str:
    """What to print beside the times, e.g. 'Europe/London' or 'UTC+05:30'."""
    name = (schedule.tz or "local").strip()
    if name.lower() == "local":
        name = zones.local_zone()
    try:
        return zones.to_iana(name)
    except ValueError:
        return name


def _intervals(schedule: Schedule, anchor: date, tz,
               back_days: int = 1, forward_days: int = _HORIZON_DAYS
               ) -> Iterator[tuple[datetime, datetime]]:
    """Every (start, end) the schedule opens around ``anchor``, in ``tz``.

    Starts one day early because a window that crosses midnight is still open
    on the far side of it: at 00:30 the interval that matters began yesterday.
    A window is filtered by the weekday it *starts* on, so "Friday 22:00-02:00"
    runs into Saturday morning rather than being cut at midnight.
    """
    for offset in range(-back_days, forward_days + 1):
        day = anchor + timedelta(days=offset)
        if schedule.days and day.weekday() not in schedule.days:
            continue
        for w in schedule.windows:
            try:
                start_t, end_t = parse_hhmm(w.start), parse_hhmm(w.end)
            except ValueError:
                continue                      # a broken window opens nothing
            if start_t == end_t:
                continue
            start = datetime.combine(day, start_t, tzinfo=tz)
            end_day = day if end_t > start_t else day + timedelta(days=1)
            # Built from a calendar date rather than start + duration, so a
            # window spanning a DST change keeps its wall-clock end.
            end = datetime.combine(end_day, end_t, tzinfo=tz)
            yield start, end


def _sorted_intervals(schedule: Schedule, now: datetime, tz) -> list[tuple[datetime, datetime]]:
    return sorted(_intervals(schedule, now.date(), tz))


def is_active(schedule: Optional[Schedule], now: datetime) -> bool:
    """Whether the alert may run at ``now`` (an aware datetime, usually UTC)."""
    if schedule is None or schedule.mode != "windows":
        return True
    if not schedule.windows:
        # Windows switched on with none configured: the alert would never run,
        # which is a setup mistake, not an instruction to go silent. validate()
        # says so in the Builder; here it keeps running.
        return True
    tz = _tz(schedule)
    local = now.astimezone(tz)
    return any(s <= local < e for s, e in _intervals(schedule, local.date(), tz))


def current_close(schedule: Optional[Schedule], now: datetime) -> Optional[datetime]:
    """When the window covering ``now`` closes, or None if none does."""
    if schedule is None or schedule.mode != "windows" or not schedule.windows:
        return None
    tz = _tz(schedule)
    local = now.astimezone(tz)
    ends = [e for s, e in _intervals(schedule, local.date(), tz) if s <= local < e]
    return min(ends) if ends else None


def next_open(schedule: Optional[Schedule], now: datetime) -> Optional[datetime]:
    """The next moment the alert starts running, or None if it always is /
    if nothing opens within the horizon."""
    if schedule is None or schedule.mode != "windows" or not schedule.windows:
        return None
    tz = _tz(schedule)
    local = now.astimezone(tz)
    starts = [s for s, _ in _sorted_intervals(schedule, local, tz) if s > local]
    return starts[0] if starts else None


def days_label(schedule: Schedule) -> str:
    """'Mon-Fri', 'Mon, Wed', 'every day' — the days part of a summary."""
    days = sorted(set(schedule.days))
    if not days or len(days) == 7:
        return "every day"
    if days == [0, 1, 2, 3, 4]:
        return "Mon-Fri"
    if days == [5, 6]:
        return "weekends"
    # A contiguous run reads better as a range than as a list.
    if len(days) > 2 and days == list(range(days[0], days[-1] + 1)):
        return f"{DAY_NAMES[days[0]]}-{DAY_NAMES[days[-1]]}"
    return ", ".join(DAY_NAMES[d] for d in days)


def schedule_summary(schedule: Optional[Schedule]) -> str:
    """Plain-English description of when an alert runs."""
    if schedule is None or schedule.mode != "windows" or not schedule.windows:
        return "always"
    spans = " & ".join(f"{w.start}-{w.end}" for w in schedule.windows)
    return f"{spans} {days_label(schedule)} ({zone_label(schedule)})"
