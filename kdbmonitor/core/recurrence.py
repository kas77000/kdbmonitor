"""When a report job fires.

Two ways of saying it — a list of times with a list of weekdays, or a cron
expression — and one question underneath both: is this minute a firing minute?
Everything else here is a walk over that question, which is why a picker and a
cron cannot disagree about what a schedule means, and why :func:`describe`
cannot describe something other than what will happen.

Everything is pure and timezone-aware. Times are written the way the person
setting them says them ("07:00") and are read in the job's own zone through
:mod:`kdbmonitor.core.zones`, while the walk itself steps in UTC — an hour that
does not exist locally is simply never produced, and one that happens twice is
produced twice, both of which are the right answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from kdbmonitor.core import zones
from kdbmonitor.core.models import Schedule
from kdbmonitor.core.schedule import days_label, parse_hhmm

MODES: tuple[str, ...] = ("times", "cron")

# How far ahead next_run looks before giving up. A recurrence that fires on one
# date a year needs a year; anything that finds nothing in one has no next run
# worth waiting for, and saying so beats walking forever.
_HORIZON_DAYS = 366

# How far back runs_between will look, however wide the window it is handed.
# A job disabled for two years and switched on again would otherwise walk a
# million minutes to answer a question the grace window (§7.1) throws away.
MAX_LOOKBACK_DAYS = 7


@dataclass
class Recurrence:
    """When a job runs.

    ``days`` is empty for every day, and uses 0=Mon..6=Sun like the rest of
    this codebase — *not* cron's 0=Sun, which is converted once in
    :func:`parse_cron` and nowhere else.
    """
    mode: str = "times"                                    # one of MODES
    times: list[str] = field(default_factory=lambda: ["07:00"])
    days: list[int] = field(default_factory=list)          # 0=Mon..6=Sun
    cron: str = ""                                         # mode="cron" only
    tz: str = "local"                                      # any spelling zones takes


def _tz(rec: Recurrence):
    name = (rec.tz or "local").strip()
    if name.lower() == "local":
        name = zones.local_zone()
    return zones.tzinfo_for(name)


def zone_label(rec: Recurrence) -> str:
    """What to print beside the times, e.g. 'Europe/London'."""
    name = (rec.tz or "local").strip()
    if name.lower() == "local":
        name = zones.local_zone()
    try:
        return zones.to_iana(name)
    except ValueError:
        return name


def _fire_times(rec: Recurrence) -> set[time]:
    """The times that parse. One that does not is dropped rather than raised:
    a hand-edited job should cost itself its schedule, not take the scheduler
    down with it. :func:`validate` is where a person is told."""
    out: set[time] = set()
    for text in rec.times or []:
        try:
            out.add(parse_hhmm(text))
        except ValueError:
            continue
    return out


@dataclass(frozen=True)
class _Plan:
    """A recurrence with its zone resolved and its cron parsed, once.

    Resolving the zone costs a scan of the whole tz database — ``to_iana``
    asks ``available_timezones()`` — and parsing the cron is not free either.
    Doing both inside the per-minute predicate made walking a single day take
    nineteen seconds, which is a page that hangs and a tick that never
    finishes. Everything the walk needs is settled here instead, and the walk
    only compares numbers.
    """
    tz: object
    times: frozenset             # of datetime.time
    days: frozenset              # of int, empty = every day
    cron: Optional[CronSpec]
    never: bool = False          # unreadable: fires nothing, validate() says why


def _plan(rec: Recurrence) -> _Plan:
    try:
        tz = _tz(rec)
    except ValueError:
        # An unreadable zone fires nothing rather than firing at the wrong
        # time, which is what silently falling back to UTC would do.
        return _Plan(timezone.utc, frozenset(), frozenset(), None, never=True)

    if rec.mode == "cron":
        try:
            return _Plan(tz, frozenset(), frozenset(), parse_cron(rec.cron))
        except ValueError:
            return _Plan(tz, frozenset(), frozenset(), None, never=True)

    return _Plan(tz, frozenset(_fire_times(rec)),
                 frozenset(rec.days or []), None)


def _fires(plan: _Plan, moment: datetime) -> bool:
    """The per-minute predicate. Nothing here parses or resolves anything."""
    if plan.never:
        return False
    local = moment.astimezone(plan.tz)
    if plan.cron is not None:
        return _cron_matches(plan.cron, local)
    if time(local.hour, local.minute) not in plan.times:
        return False
    return not plan.days or local.weekday() in plan.days


def fires_at(rec: Recurrence, moment: datetime) -> bool:
    """Whether ``moment`` (an aware datetime) is a firing minute."""
    return _fires(_plan(rec), moment)


# --- cron -------------------------------------------------------------------
#
# Five fields, and no dependency for them. A cron *library* earns its keep by
# computing next-fire times; that part is answered here by walking minutes over
# fires_at, which leaves only "does this field match this number" — a few dozen
# lines that can be read in one sitting and tested exhaustively.

_FIELD_NAMES = ("minute", "hour", "day-of-month", "month", "day-of-week")
_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


@dataclass(frozen=True)
class CronSpec:
    minutes: frozenset[int]
    hours: frozenset[int]
    doms: frozenset[int]
    months: frozenset[int]
    dows: frozenset[int]          # 0=Mon..6=Sun, already converted
    dom_restricted: bool
    dow_restricted: bool


def _cron_int(text: str, lo: int, hi: int, name: str) -> int:
    if not text.isdigit():
        raise ValueError(f"{name}: {text!r} is not a number")
    value = int(text)
    if not lo <= value <= hi:
        raise ValueError(f"{name}: {value} is outside {lo}-{hi}")
    return value


def _cron_field(text: str, lo: int, hi: int, name: str) -> set[int]:
    """One field as the set of values it allows."""
    out: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"{name}: empty element in {text!r}")
        step = 1
        if "/" in part:
            part, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) < 1:
                raise ValueError(f"{name}: bad step in {text!r}")
            step = int(step_text)
            part = part or "*"
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, _, b = part.partition("-")
            start = _cron_int(a, lo, hi, name)
            end = _cron_int(b, lo, hi, name)
            if start > end:
                raise ValueError(f"{name}: {part!r} starts after it ends")
        else:
            start = end = _cron_int(part, lo, hi, name)
        out.update(range(start, end + 1, step))
    if not out:
        raise ValueError(f"{name}: {text!r} matches nothing")
    return out


def parse_cron(expr: str) -> CronSpec:
    """A 5-field cron expression. Raises ValueError on anything it cannot read.

    Rejecting at parse time is the whole point: an expression that quietly
    matches nothing is indistinguishable, at 07:00, from a scheduler that is
    broken. :func:`validate` calls this so the page refuses the save.
    """
    fields = (expr or "").split()
    if len(fields) != 5:
        raise ValueError(
            f"expected 5 fields (minute hour day-of-month month day-of-week), "
            f"got {len(fields)}")

    sets = [_cron_field(text, lo, hi, name)
            for text, (lo, hi), name in zip(fields, _FIELD_RANGES, _FIELD_NAMES)]

    # Cron counts weekdays from Sunday and allows 7 for it as well as 0; this
    # codebase counts from Monday. Converted here, once, so nothing downstream
    # has to remember which convention it is holding.
    dows = {0 if d == 7 else d for d in sets[4]}
    dows = {(d + 6) % 7 for d in dows}

    return CronSpec(
        minutes=frozenset(sets[0]), hours=frozenset(sets[1]),
        doms=frozenset(sets[2]), months=frozenset(sets[3]),
        dows=frozenset(dows),
        dom_restricted=fields[2].strip() != "*",
        dow_restricted=fields[4].strip() != "*")


def _cron_matches(spec: CronSpec, local: datetime) -> bool:
    if local.minute not in spec.minutes or local.hour not in spec.hours:
        return False
    if local.month not in spec.months:
        return False
    dom_ok = local.day in spec.doms
    dow_ok = local.weekday() in spec.dows
    # Vixie cron's rule: with both day fields restricted they are OR-ed, not
    # AND-ed. "0 0 1 * 1" means the 1st *and* every Monday, which is what
    # somebody writing it expects and what every other cron does.
    if spec.dom_restricted and spec.dow_restricted:
        return dom_ok or dow_ok
    return dom_ok and dow_ok


# --- walking ----------------------------------------------------------------
#
# Both of these step a minute at a time, in UTC, asking fires_at. Stepping in
# UTC rather than in local time is what makes daylight saving fall out
# correctly instead of needing a case: a local time that does not exist is
# never produced by any instant, and one that happens twice is produced twice.

def next_run(rec: Recurrence, after: datetime) -> Optional[datetime]:
    """The first firing minute strictly after ``after``, or None within a year.

    Strictly after, or a job asked at the moment it fired would be told to fire
    again immediately, forever.
    """
    plan = _plan(rec)
    if plan.never:
        return None
    cursor = (after.astimezone(timezone.utc).replace(second=0, microsecond=0)
              + timedelta(minutes=1))
    for _ in range(_HORIZON_DAYS * 24 * 60):
        if _fires(plan, cursor):
            return cursor
        cursor += timedelta(minutes=1)
    return None


def runs_between(rec: Recurrence, start: datetime, end: datetime) -> list[datetime]:
    """Every firing minute in ``(start, end]``, oldest first.

    The window is clamped to :data:`MAX_LOOKBACK_DAYS` before ``end``. A job
    left disabled for a year would otherwise be asked to walk half a million
    minutes on the tick it is switched back on, to produce fire times the grace
    window (spec §7.1) discards immediately.
    """
    plan = _plan(rec)
    if plan.never:
        return []

    end = end.astimezone(timezone.utc).replace(second=0, microsecond=0)
    floor = end - timedelta(days=MAX_LOOKBACK_DAYS)
    cursor = start.astimezone(timezone.utc).replace(second=0, microsecond=0)
    cursor = max(cursor, floor) + timedelta(minutes=1)

    out: list[datetime] = []
    while cursor <= end:
        if _fires(plan, cursor):
            out.append(cursor)
        cursor += timedelta(minutes=1)
    return out


# --- saying it out loud ------------------------------------------------------

def describe(rec: Recurrence) -> str:
    """Plain-English summary, for the list on the Schedules page.

    Built from the same fields fires_at reads, so a description cannot promise
    something the schedule will not do.
    """
    if rec.mode == "cron":
        return f"cron '{(rec.cron or '').strip()}' ({zone_label(rec)})"
    times = " & ".join(rec.times or []) or "no time set"
    # days_label already turns [0,1,2,3,4] into "Mon-Fri" for alert schedules;
    # a second copy of that formatting would eventually disagree with it.
    return f"{times} {days_label(Schedule(days=list(rec.days)))} ({zone_label(rec)})"


def validate(rec: Recurrence) -> list[str]:
    """Human-readable problems; empty when the recurrence is usable.

    The page shows these before saving. A recurrence that matches nothing looks
    exactly like a scheduler that is not running, and the difference is only
    discoverable at 07:00 the next morning.
    """
    problems: list[str] = []
    if rec.mode not in MODES:
        problems.append(f"Unknown mode {rec.mode!r} (expected one of {', '.join(MODES)}).")
        return problems

    if rec.mode == "cron":
        try:
            parse_cron(rec.cron)
        except ValueError as exc:
            problems.append(f"Cron expression: {exc}")
    else:
        if not (rec.times or []):
            problems.append("No run time set — add at least one.")
        for text in rec.times or []:
            try:
                parse_hhmm(text)
            except ValueError as exc:
                problems.append(str(exc))
        for d in rec.days or []:
            if d not in range(7):
                problems.append(f"Unknown day {d} (expected 0=Mon .. 6=Sun).")

    if rec.tz and rec.tz.strip().lower() != "local":
        try:
            zones.to_iana(rec.tz)
        except ValueError as exc:
            problems.append(str(exc))
    return problems
