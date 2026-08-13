# Report Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A separate Python process that runs a KdbMonitor dashboard on a schedule, renders the existing PDF, and mails it to a recipient list — started, stopped and configured from the app.

**Architecture:** Two processes sharing one SQLite file and no memory. `kdbmonitor/core/` gains pure modules for recurrence, the job model, running one job, and mail; `kdbmonitor/scheduler/` is a thin process around them holding a DB-backed lock so exactly one runs; `kdbmonitor/ui/schedules.py` edits jobs and drives the process. The PDF comes from the existing `dashpdf.dashboard_to_pdf_bytes` — nothing about rendering is written twice.

**Tech Stack:** Python 3.11+, SQLite (stdlib `sqlite3`), `smtplib` + `email.message` (stdlib), Streamlit, pandas, matplotlib (`Agg`), pytest.

**Spec:** `docs/superpowers/specs/2026-08-13-report-scheduler-design.md` — read §5.1 and §6.1 before Task 5.

## Global Constraints

- **No new third-party dependencies.** Cron parsing, timezone handling and mail are stdlib or already vendored. Do not add `croniter`, `APScheduler`, `pypdf` or similar.
- **`kdbmonitor/core/` must never import Streamlit.** It is what makes the scheduler possible. `kdbmonitor/scheduler/` must not import it either.
- **Python 3.11+**, `from __future__ import annotations` at the top of every new module, matching the existing files.
- **All timestamps stored as ISO-8601 UTC, `YYYY-MM-DDTHH:MM:SSZ`, exactly 20 characters.** The lock's staleness check is a string comparison on the first 20 characters and breaks if the format varies.
- **Weekdays are `0=Mon .. 6=Sun`** everywhere in this codebase (Python's `date.weekday()`), matching `core/schedule.py`. Standard cron uses `0=Sun`; the conversion happens once, in `parse_cron`, and nowhere else.
- **Any dataset failing fails the whole run.** No PDF is rendered and the recipients receive nothing (spec §5.1).
- **Failure detail goes only to `scheduler_admin_email`.** Never to a job's recipients (spec §6.1).
- Comments explain *why*, not *what*, matching the density of the surrounding code. Follow the existing house style: prose docstrings that name the failure a piece of code prevents.
- Run the full suite with `python -m pytest` from the repo root. It must stay green at every commit.

---

## File Structure

**Created:**

| File | Responsibility |
| --- | --- |
| `kdbmonitor/core/recurrence.py` | When a job fires. Pure, no I/O. Both picker and cron modes answer `fires_at`. |
| `kdbmonitor/core/reportjob.py` | The `ReportJob` dataclass, its JSON round-trip, validation, and template rendering. |
| `kdbmonitor/core/reportrun.py` | Runs one job once: resolve → query → render → send. Knows nothing of clocks or retries. |
| `kdbmonitor/scheduler/__init__.py` | Package marker. |
| `kdbmonitor/scheduler/supervisor.py` | The lock, the heartbeat, the desired state, and spawning. |
| `kdbmonitor/scheduler/loop.py` | The process: what is due, retries, recording, sleeping. |
| `kdbmonitor/scheduler/__main__.py` | `python -m kdbmonitor.scheduler`. Sets the `Agg` backend and logging. |
| `kdbmonitor/ui/schedules.py` | The Schedules page. |
| `run_scheduler.bat` | Starting it by hand on Windows. |
| `tests/test_recurrence.py`, `tests/test_reportjob.py`, `tests/test_report_storage.py`, `tests/test_report_mail.py`, `tests/test_reportrun.py`, `tests/test_scheduler_lock.py`, `tests/test_scheduler_loop.py`, `tests/test_schedules_page.py` | |

**Modified:**

| File | Change |
| --- | --- |
| `kdbmonitor/core/storage.py` | WAL pragmas in `__init__`; `report_jobs` + `report_job_runs` tables and their accessors. |
| `kdbmonitor/core/notifiers.py` | `parse_address_list`, `send_report_email`. Existing `send_email` untouched. |
| `kdbmonitor/ui/admin.py` | Admin-notification addresses, SMTP caption, Send test email. |
| `app.py` | Register the Schedules page; autostart the scheduler; sidebar badge. |
| `tests/test_all_pages_render.py` | Add `schedules` to `PAGES`. |

**Task order:** 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10. Tasks 1 and 4 have no dependencies on each other and could run in parallel; everything else is sequential.

---

## Task 1: Recurrence

Pure module, no I/O, largest test body. Nothing else can be built until "when does this fire" is settled.

**Files:**
- Create: `kdbmonitor/core/recurrence.py`
- Test: `tests/test_recurrence.py`

**Interfaces:**
- Consumes: `core.zones.to_iana`, `core.zones.tzinfo_for`, `core.zones.local_zone`; `core.schedule.parse_hhmm`, `core.schedule.days_label`; `core.models.Schedule` (only to reuse `days_label`).
- Produces:
  - `Recurrence(mode: str = "times", times: list[str], days: list[int], cron: str, tz: str)` — dataclass
  - `MODES: tuple[str, ...] = ("times", "cron")`
  - `parse_cron(expr: str) -> CronSpec` — raises `ValueError`
  - `fires_at(rec: Recurrence, moment: datetime) -> bool` — `moment` must be timezone-aware
  - `next_run(rec: Recurrence, after: datetime) -> Optional[datetime]` — aware UTC
  - `runs_between(rec: Recurrence, start: datetime, end: datetime) -> list[datetime]` — aware UTC, `(start, end]`
  - `describe(rec: Recurrence) -> str`
  - `validate(rec: Recurrence) -> list[str]`
  - `MAX_LOOKBACK_DAYS: int = 7`

- [ ] **Step 1: Write the failing tests for the picker mode**

Create `tests/test_recurrence.py`:

```python
"""When a report job fires.

Both modes answer one question — is this minute a firing minute — so these
tests drive `fires_at` hard and treat `next_run` and `runs_between` as the
thin walks over it that they are.
"""
from datetime import datetime, timedelta, timezone

import pytest

from kdbmonitor.core.recurrence import (
    MAX_LOOKBACK_DAYS, Recurrence, describe, fires_at, next_run, parse_cron,
    runs_between, validate,
)

UTC = timezone.utc


def _at(y, m, d, hh, mm) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


# --- picker mode -------------------------------------------------------------

def test_a_time_fires_on_its_minute_and_no_other():
    rec = Recurrence(times=["07:00"], tz="UTC")
    assert fires_at(rec, _at(2026, 8, 13, 7, 0))
    assert not fires_at(rec, _at(2026, 8, 13, 6, 59))
    assert not fires_at(rec, _at(2026, 8, 13, 7, 1))


def test_several_times_all_fire():
    rec = Recurrence(times=["07:00", "16:35"], tz="UTC")
    assert fires_at(rec, _at(2026, 8, 13, 7, 0))
    assert fires_at(rec, _at(2026, 8, 13, 16, 35))


def test_a_time_typed_without_its_leading_zero_still_fires():
    """parse_hhmm accepts '7:00'; the picker is not the only way in."""
    rec = Recurrence(times=["7:00"], tz="UTC")
    assert fires_at(rec, _at(2026, 8, 13, 7, 0))


def test_an_empty_day_list_means_every_day():
    rec = Recurrence(times=["07:00"], days=[], tz="UTC")
    # 2026-08-15 is a Saturday, 2026-08-17 a Monday.
    assert fires_at(rec, _at(2026, 8, 15, 7, 0))
    assert fires_at(rec, _at(2026, 8, 17, 7, 0))


def test_weekdays_exclude_the_weekend():
    rec = Recurrence(times=["07:00"], days=[0, 1, 2, 3, 4], tz="UTC")
    assert fires_at(rec, _at(2026, 8, 17, 7, 0))       # Monday
    assert not fires_at(rec, _at(2026, 8, 15, 7, 0))   # Saturday


def test_the_time_is_read_in_the_recurrence_own_zone():
    """07:00 in London is 06:00 UTC in August, not 07:00 UTC."""
    rec = Recurrence(times=["07:00"], tz="Europe/London")
    assert fires_at(rec, _at(2026, 8, 13, 6, 0))
    assert not fires_at(rec, _at(2026, 8, 13, 7, 0))


def test_a_broken_time_string_fires_nothing_rather_than_raising():
    """A hand-edited bundle must not take the whole scheduler down."""
    rec = Recurrence(times=["not a time"], tz="UTC")
    assert not fires_at(rec, _at(2026, 8, 13, 7, 0))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_recurrence.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kdbmonitor.core.recurrence'`

- [ ] **Step 3: Write the module as far as the picker mode**

Create `kdbmonitor/core/recurrence.py`:

```python
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


def fires_at(rec: Recurrence, moment: datetime) -> bool:
    """Whether ``moment`` (an aware datetime) is a firing minute."""
    local = moment.astimezone(_tz(rec))
    if rec.mode == "cron":
        try:
            spec = parse_cron(rec.cron)
        except ValueError:
            return False          # an unparseable expression fires nothing
        return _cron_matches(spec, local)
    if time(local.hour, local.minute) not in _fire_times(rec):
        return False
    return not rec.days or local.weekday() in rec.days
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_recurrence.py -q`
Expected: FAIL still — `parse_cron` and `_cron_matches` are referenced but not defined. That is deliberate; Step 5 adds them. If you prefer a green bar here, stub them as `raise ValueError` / `return False` and replace in Step 7.

- [ ] **Step 5: Write the failing cron tests**

Append to `tests/test_recurrence.py`:

```python
# --- cron -------------------------------------------------------------------

def _cron(expr: str) -> Recurrence:
    return Recurrence(mode="cron", cron=expr, tz="UTC")


def test_cron_fires_on_the_minute_it_names():
    rec = _cron("0 7 * * *")
    assert fires_at(rec, _at(2026, 8, 13, 7, 0))
    assert not fires_at(rec, _at(2026, 8, 13, 8, 0))


def test_cron_lists_and_ranges():
    rec = _cron("0 7,16 * * 1-5")
    assert fires_at(rec, _at(2026, 8, 17, 7, 0))       # Monday
    assert fires_at(rec, _at(2026, 8, 17, 16, 0))
    assert not fires_at(rec, _at(2026, 8, 15, 7, 0))   # Saturday


def test_cron_steps():
    rec = _cron("*/15 * * * *")
    for minute in (0, 15, 30, 45):
        assert fires_at(rec, _at(2026, 8, 13, 9, minute))
    assert not fires_at(rec, _at(2026, 8, 13, 9, 7))


def test_cron_sunday_is_written_zero_or_seven_and_means_python_six():
    """Cron counts weekdays from Sunday; this codebase counts from Monday.
    2026-08-16 is a Sunday."""
    for expr in ("0 7 * * 0", "0 7 * * 7"):
        assert fires_at(_cron(expr), _at(2026, 8, 16, 7, 0))
        assert not fires_at(_cron(expr), _at(2026, 8, 17, 7, 0))   # Monday


def test_cron_monday_is_one():
    assert fires_at(_cron("0 7 * * 1"), _at(2026, 8, 17, 7, 0))


def test_cron_day_of_month_and_day_of_week_are_ored_when_both_are_set():
    """Vixie cron's rule: with both restricted, either match fires. Anyone who
    has written '0 0 1 * 1' expecting 'the 1st, and Mondays' is right."""
    rec = _cron("0 7 1 * 1")
    assert fires_at(rec, _at(2026, 8, 1, 7, 0))     # the 1st, a Saturday
    assert fires_at(rec, _at(2026, 8, 17, 7, 0))    # a Monday, not the 1st
    assert not fires_at(rec, _at(2026, 8, 18, 7, 0))


def test_cron_day_of_month_alone_is_anded_with_nothing():
    rec = _cron("0 7 1 * *")
    assert fires_at(rec, _at(2026, 8, 1, 7, 0))
    assert not fires_at(rec, _at(2026, 8, 2, 7, 0))


@pytest.mark.parametrize("expr", [
    "", "0 7 * *", "0 7 * * * *", "60 7 * * *", "0 24 * * *",
    "0 7 0 * *", "0 7 * 13 *", "a 7 * * *", "0 7 * * 8", "5-1 7 * * *",
    "*/0 * * * *", "0 7 * * ,",
])
def test_malformed_cron_is_rejected_by_the_parser(expr):
    with pytest.raises(ValueError):
        parse_cron(expr)


def test_a_bad_cron_expression_fires_nothing_rather_than_raising():
    assert not fires_at(_cron("nonsense"), _at(2026, 8, 13, 7, 0))
```

- [ ] **Step 6: Run them to verify they fail**

Run: `python -m pytest tests/test_recurrence.py -q -k cron`
Expected: FAIL — `NameError: name 'parse_cron' is not defined`

- [ ] **Step 7: Implement cron**

Append to `kdbmonitor/core/recurrence.py`:

```python
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
```

- [ ] **Step 8: Run the cron tests to verify they pass**

Run: `python -m pytest tests/test_recurrence.py -q`
Expected: PASS (all picker and cron tests)

- [ ] **Step 9: Commit**

```bash
git add kdbmonitor/core/recurrence.py tests/test_recurrence.py
git commit -m "feat: one question under two ways of saying when"
```

- [ ] **Step 10: Write the failing tests for the walks, DST, describe and validate**

Append to `tests/test_recurrence.py`:

```python
# --- walking ----------------------------------------------------------------

def test_next_run_finds_tomorrow_morning():
    rec = Recurrence(times=["07:00"], tz="UTC")
    assert next_run(rec, _at(2026, 8, 13, 9, 0)) == _at(2026, 8, 14, 7, 0)


def test_next_run_is_strictly_after_its_argument():
    """Asked at exactly a firing minute, the answer is the *next* one — or a
    job would re-fire forever on the minute it just ran."""
    rec = Recurrence(times=["07:00"], tz="UTC")
    assert next_run(rec, _at(2026, 8, 13, 7, 0)) == _at(2026, 8, 14, 7, 0)


def test_next_run_crosses_a_year_end():
    rec = Recurrence(times=["07:00"], days=[], tz="UTC")
    assert next_run(rec, _at(2026, 12, 31, 9, 0)) == _at(2027, 1, 1, 7, 0)


def test_next_run_gives_up_rather_than_walking_forever():
    """29 February on a Monday is rarer than the horizon."""
    rec = Recurrence(mode="cron", cron="0 7 30 2 *", tz="UTC")
    assert next_run(rec, _at(2026, 8, 13, 9, 0)) is None


def test_next_run_returns_utc_whatever_the_zone():
    rec = Recurrence(times=["07:00"], tz="Europe/London")
    got = next_run(rec, _at(2026, 8, 13, 9, 0))
    assert got == _at(2026, 8, 14, 6, 0)
    assert got.tzinfo == timezone.utc


def test_runs_between_lists_every_missed_fire_in_order():
    rec = Recurrence(times=["07:00", "16:35"], tz="UTC")
    got = runs_between(rec, _at(2026, 8, 13, 0, 0), _at(2026, 8, 14, 12, 0))
    assert got == [_at(2026, 8, 13, 7, 0), _at(2026, 8, 13, 16, 35),
                   _at(2026, 8, 14, 7, 0)]


def test_runs_between_excludes_its_start_and_includes_its_end():
    rec = Recurrence(times=["07:00"], tz="UTC")
    assert runs_between(rec, _at(2026, 8, 13, 7, 0), _at(2026, 8, 14, 7, 0)) \
        == [_at(2026, 8, 14, 7, 0)]


def test_runs_between_is_empty_when_nothing_fired():
    rec = Recurrence(times=["07:00"], tz="UTC")
    assert runs_between(rec, _at(2026, 8, 13, 8, 0), _at(2026, 8, 13, 9, 0)) == []


def test_runs_between_clamps_how_far_back_it_will_look():
    """A job switched on after a year off must not walk a year of minutes."""
    rec = Recurrence(times=["07:00"], tz="UTC")
    end = _at(2026, 8, 13, 12, 0)
    got = runs_between(rec, end - timedelta(days=365), end)
    assert len(got) <= MAX_LOOKBACK_DAYS + 1
    assert got[-1] == _at(2026, 8, 13, 7, 0)


# --- daylight saving --------------------------------------------------------

def test_a_time_that_does_not_exist_locally_does_not_fire():
    """Europe/London springs forward at 01:00 on 2026-03-29: there is no
    01:30 that morning, so a job set for it silently skips the day."""
    rec = Recurrence(times=["01:30"], tz="Europe/London")
    day = [_at(2026, 3, 29, h, m) for h in range(0, 4) for m in (0, 30)]
    assert not any(fires_at(rec, moment) for moment in day)


def test_a_time_that_happens_twice_locally_is_reported_twice():
    """Europe/London falls back at 02:00 on 2026-10-25, so 01:30 comes round
    twice. Both are real instants; the loop takes the most recent, which is
    how one report rather than two goes out."""
    rec = Recurrence(times=["01:30"], tz="Europe/London")
    got = runs_between(rec, _at(2026, 10, 25, 0, 0), _at(2026, 10, 25, 3, 0))
    assert len(got) == 2


# --- saying it out loud ------------------------------------------------------

def test_describe_a_weekday_morning():
    rec = Recurrence(times=["07:00"], days=[0, 1, 2, 3, 4], tz="Europe/London")
    assert describe(rec) == "07:00 Mon-Fri (Europe/London)"


def test_describe_several_times_every_day():
    rec = Recurrence(times=["07:00", "16:35"], days=[], tz="UTC")
    assert describe(rec) == "07:00 & 16:35 every day (UTC)"


def test_describe_a_cron_job_quotes_it():
    rec = Recurrence(mode="cron", cron="0 7 * * 1-5", tz="UTC")
    assert describe(rec) == "cron '0 7 * * 1-5' (UTC)"


def test_validate_is_quiet_about_a_good_recurrence():
    assert validate(Recurrence(times=["07:00"], days=[0], tz="UTC")) == []


def test_validate_names_every_problem_it_finds():
    rec = Recurrence(times=["nope"], days=[9], tz="Mars/Olympus")
    problems = validate(rec)
    assert len(problems) == 3
    assert any("nope" in p for p in problems)
    assert any("9" in p for p in problems)
    assert any("Mars/Olympus" in p for p in problems)


def test_validate_objects_to_a_schedule_with_no_times_at_all():
    assert validate(Recurrence(times=[], tz="UTC"))


def test_validate_objects_to_unparseable_cron():
    problems = validate(Recurrence(mode="cron", cron="0 7 * *", tz="UTC"))
    assert len(problems) == 1
    assert "5 fields" in problems[0]


def test_validate_objects_to_an_unknown_mode():
    assert validate(Recurrence(mode="whenever", tz="UTC"))
```

- [ ] **Step 11: Run them to verify they fail**

Run: `python -m pytest tests/test_recurrence.py -q`
Expected: FAIL — `ImportError: cannot import name 'next_run'`

- [ ] **Step 12: Implement the walks, describe and validate**

Append to `kdbmonitor/core/recurrence.py`:

```python
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
    cursor = (after.astimezone(timezone.utc).replace(second=0, microsecond=0)
              + timedelta(minutes=1))
    for _ in range(_HORIZON_DAYS * 24 * 60):
        if fires_at(rec, cursor):
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
    end = end.astimezone(timezone.utc).replace(second=0, microsecond=0)
    floor = end - timedelta(days=MAX_LOOKBACK_DAYS)
    cursor = start.astimezone(timezone.utc).replace(second=0, microsecond=0)
    cursor = max(cursor, floor) + timedelta(minutes=1)

    out: list[datetime] = []
    while cursor <= end:
        if fires_at(rec, cursor):
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
```

- [ ] **Step 13: Run the whole file to verify it passes**

Run: `python -m pytest tests/test_recurrence.py -q`
Expected: PASS

If `test_describe_a_weekday_morning` fails on the zone label, check `zones.to_iana("Europe/London")` returns `"Europe/London"` — it does; the failure would mean `zone_label` is falling through to the `local` branch.

- [ ] **Step 14: Run the full suite and commit**

Run: `python -m pytest -q`
Expected: PASS, no regressions.

```bash
git add kdbmonitor/core/recurrence.py tests/test_recurrence.py
git commit -m "feat: a walk over the minutes, so daylight saving needs no case"
```

---

## Task 2: The job model

**Files:**
- Create: `kdbmonitor/core/reportjob.py`
- Test: `tests/test_reportjob.py`

**Interfaces:**
- Consumes: `core.recurrence.Recurrence`, `core.recurrence.validate as validate_recurrence`, `core.recurrence.describe`.
- Produces:
  - `ReportJob(id, name, dashboard_id, enabled=True, recurrence=Recurrence(), period=None, parameters={}, recipients=[], subject="{dashboard} — {date}", body="", retries=3, retry_delay_secs=120, grace_minutes=60, last_fired_at=None)`
  - `job_to_dict(job) -> dict`, `job_from_dict(d) -> ReportJob`
  - `job_to_json(job) -> str`, `job_from_json(raw) -> ReportJob`
  - `render_text(template: str, *, job: str, dashboard: str, when: datetime) -> str`
  - `validate_job(job: ReportJob) -> list[str]`
  - `DEFAULT_SUBJECT: str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reportjob.py`:

```python
"""The scheduled-report job: what it holds, and what it refuses to be saved as."""
from datetime import datetime, timezone

from kdbmonitor.core.recurrence import Recurrence
from kdbmonitor.core.reportjob import (
    ReportJob, job_from_dict, job_from_json, job_to_json, render_text,
    validate_job,
)

WHEN = datetime(2026, 8, 14, 7, 0, tzinfo=timezone.utc)


def _job(**kw) -> ReportJob:
    base = dict(id=None, name="Morning OMS report", dashboard_id=3,
                recipients=["desk@corp.local"])
    base.update(kw)
    return ReportJob(**base)


def test_a_new_job_runs_every_weekday_morning_and_is_enabled():
    job = _job()
    assert job.enabled
    assert job.recurrence.times == ["07:00"]
    assert (job.retries, job.retry_delay_secs, job.grace_minutes) == (3, 120, 60)


def test_a_job_uses_the_dashboard_own_period_until_it_is_told_otherwise():
    assert _job().period is None


def test_every_field_survives_a_save_and_a_load():
    job = _job(
        recurrence=Recurrence(mode="cron", cron="0 7 * * 1-5", tz="Europe/London"),
        period={"mode": "historical", "range": {"kind": "preset", "name": "yesterday"}},
        parameters={"region": "EMEA"},
        recipients=["a@x.com", "b@x.com"], subject="S", body="B",
        retries=5, retry_delay_secs=30, grace_minutes=15,
        last_fired_at="2026-08-13T07:00:00Z")
    back = job_from_json(job_to_json(job))
    assert back == job


def test_a_job_stored_before_a_field_existed_still_loads():
    """Read back a payload with almost nothing in it: an older or hand-edited
    bundle should cost itself its defaults, not the whole page."""
    job = job_from_dict({"name": "Old", "dashboard_id": 1})
    assert job.recurrence.times == ["07:00"]
    assert job.recipients == []
    assert job.enabled


def test_placeholders_are_filled_in():
    got = render_text("{dashboard} — {date} {time} ({job})", job="J",
                      dashboard="OMS Overview", when=WHEN)
    assert got == "OMS Overview — 2026-08-14 07:00 (J)"


def test_an_unknown_placeholder_is_left_alone_rather_than_raising():
    """A typo in a subject line must not stop a report going out."""
    got = render_text("{dashboard} {oops}", job="J", dashboard="D", when=WHEN)
    assert got == "D {oops}"


def test_validate_is_quiet_about_a_good_job():
    assert validate_job(_job()) == []


def test_validate_wants_a_name_a_dashboard_and_somebody_to_send_to():
    problems = validate_job(_job(name="  ", dashboard_id=0, recipients=[]))
    assert len(problems) == 3


def test_validate_objects_to_an_address_without_an_at_sign():
    problems = validate_job(_job(recipients=["desk@corp.local", "nope"]))
    assert len(problems) == 1
    assert "nope" in problems[0]


def test_validate_passes_on_what_the_recurrence_says():
    problems = validate_job(_job(recurrence=Recurrence(times=["nope"])))
    assert any("nope" in p for p in problems)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_reportjob.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kdbmonitor.core.reportjob'`

- [ ] **Step 3: Implement the module**

Create `kdbmonitor/core/reportjob.py`:

```python
"""A scheduled report: which dashboard, when, and to whom.

The job owns the *deviation* from the dashboard and nothing else. Its period is
None until somebody overrides it, and its parameters hold only the values it
changes — so adding a parameter to a dashboard cannot break the jobs already
pointing at it, and a dashboard's own defaults keep working without being
copied anywhere.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

from kdbmonitor.core.recurrence import Recurrence
from kdbmonitor.core.recurrence import validate as validate_recurrence

DEFAULT_SUBJECT = "{dashboard} — {date}"


@dataclass
class ReportJob:
    id: Optional[int]
    name: str
    dashboard_id: int
    enabled: bool = True

    recurrence: Recurrence = field(default_factory=Recurrence)

    # None = whatever the dashboard has saved. A stored spec of the same shape
    # the dashboard uses, so core.timectx.resolve reads both and a job cannot
    # express a period a dashboard could not.
    period: Optional[dict] = None
    # Only what this job overrides; everything else falls back to the
    # parameter's own default.
    parameters: dict = field(default_factory=dict)

    recipients: list[str] = field(default_factory=list)
    subject: str = DEFAULT_SUBJECT
    body: str = ""

    retries: int = 3
    retry_delay_secs: int = 120
    grace_minutes: int = 60

    # The last *fire time* run, never the wall-clock end of the run: a slow run
    # recorded by its finish would silently swallow the fire time after it.
    last_fired_at: Optional[str] = None       # ISO-8601 UTC


def _int(value, default: int) -> int:
    """A stored number, or the default. Reading a job must not raise — a
    hand-edited bundle should cost one field its value, not the page its
    contents."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def recurrence_from_dict(d: Optional[dict]) -> Recurrence:
    d = dict(d or {})
    return Recurrence(
        mode=d.get("mode", "times") or "times",
        times=[str(t) for t in (d.get("times") or ["07:00"])],
        days=[int(x) for x in (d.get("days") or [])],
        cron=str(d.get("cron", "") or ""),
        tz=d.get("tz", "local") or "local")


def job_to_dict(job: ReportJob) -> dict:
    return asdict(job)


def job_from_dict(d: dict) -> ReportJob:
    d = dict(d or {})
    return ReportJob(
        id=d.get("id"),
        name=d.get("name", ""),
        dashboard_id=_int(d.get("dashboard_id"), 0),
        enabled=bool(d.get("enabled", True)),
        recurrence=recurrence_from_dict(d.get("recurrence")),
        period=d.get("period") or None,
        parameters=dict(d.get("parameters") or {}),
        recipients=[str(r) for r in (d.get("recipients") or [])],
        subject=d.get("subject", DEFAULT_SUBJECT),
        body=d.get("body", "") or "",
        retries=_int(d.get("retries"), 3),
        retry_delay_secs=_int(d.get("retry_delay_secs"), 120),
        grace_minutes=_int(d.get("grace_minutes"), 60),
        last_fired_at=d.get("last_fired_at"))


def job_to_json(job: ReportJob) -> str:
    return json.dumps(job_to_dict(job))


def job_from_json(raw: str) -> ReportJob:
    return job_from_dict(json.loads(raw))


def render_text(template: str, *, job: str, dashboard: str,
                when: datetime) -> str:
    """A subject or body with its placeholders filled in.

    Plain replacement rather than ``str.format``: an unknown placeholder is
    left as written instead of raising. A typo in a subject line is a cosmetic
    problem, and it must not be the reason a report does not go out.
    """
    values = {"job": job, "dashboard": dashboard,
              "date": f"{when:%Y-%m-%d}", "time": f"{when:%H:%M}"}
    out = template or ""
    for name, value in values.items():
        out = out.replace("{" + name + "}", value)
    return out


def validate_job(job: ReportJob) -> list[str]:
    """Human-readable problems with a job; empty when it is usable."""
    problems: list[str] = []
    if not (job.name or "").strip():
        problems.append("Give the job a name.")
    if not job.dashboard_id:
        problems.append("Choose a dashboard.")
    if not job.recipients:
        problems.append("Add at least one recipient.")
    for address in job.recipients:
        if "@" not in address:
            problems.append(f"{address!r} is not an email address.")
    problems.extend(validate_recurrence(job.recurrence))
    return problems
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_reportjob.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/reportjob.py tests/test_reportjob.py
git commit -m "feat: a job holds what it changes about a dashboard, and nothing else"
```

---

## Task 3: Storage — tables, accessors, WAL

**Files:**
- Modify: `kdbmonitor/core/storage.py` (`__init__` ~line 40, `init_db` ~line 44, new accessor block before `get_setting`)
- Test: `tests/test_report_storage.py`

**Interfaces:**
- Consumes: `core.reportjob.ReportJob`, `job_to_json`, `job_from_json`.
- Produces, on `Storage`:
  - `add_report_job(job) -> int`
  - `list_report_jobs() -> list[ReportJob]`
  - `get_report_job(jid) -> Optional[ReportJob]`
  - `update_report_job(job) -> None`
  - `delete_report_job(jid) -> None`
  - `set_report_job_enabled(jid, enabled) -> None`
  - `set_report_job_fired(jid, fired_at: datetime) -> None`
  - `record_report_run(job_id, fired_for: datetime, started_at: datetime, finished_at: Optional[datetime], status: str, attempts: int = 1, pages: Optional[int] = None, recipients: Optional[int] = None, error: Optional[str] = None, detail: Optional[list[dict]] = None) -> int`
  - `list_report_runs(job_id, limit=20) -> list[dict]` — newest first; `detail` is the parsed list
  - `latest_report_run(job_id) -> Optional[dict]`
  - `REPORT_RUN_RETENTION: int = 200` (module constant)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_report_storage.py`:

```python
"""Report jobs and their history in SQLite."""
from datetime import datetime, timezone

import pytest

from kdbmonitor.core.recurrence import Recurrence
from kdbmonitor.core.reportjob import ReportJob
from kdbmonitor.core.storage import REPORT_RUN_RETENTION, Storage

FIRED = datetime(2026, 8, 13, 7, 0, tzinfo=timezone.utc)


@pytest.fixture()
def store() -> Storage:
    s = Storage(":memory:")
    s.init_db()
    return s


def _job(**kw) -> ReportJob:
    base = dict(id=None, name="Morning OMS report", dashboard_id=3,
                recipients=["desk@corp.local"])
    base.update(kw)
    return ReportJob(**base)


def test_a_saved_job_comes_back_whole(store):
    jid = store.add_report_job(_job(
        recurrence=Recurrence(times=["07:00", "16:35"], days=[0, 4], tz="UTC"),
        parameters={"region": "EMEA"}))
    back = store.get_report_job(jid)
    assert back.id == jid
    assert back.name == "Morning OMS report"
    assert back.recurrence.times == ["07:00", "16:35"]
    assert back.parameters == {"region": "EMEA"}


def test_a_new_job_starts_from_now_rather_than_from_the_epoch(store):
    """A null last_fired_at would make the first tick ask what it missed since
    the beginning of time — and then send it."""
    jid = store.add_report_job(_job())
    assert store.get_report_job(jid).last_fired_at is not None


def test_jobs_are_listed_by_name(store):
    store.add_report_job(_job(name="Zulu"))
    store.add_report_job(_job(name="alpha"))
    assert [j.name for j in store.list_report_jobs()] == ["alpha", "Zulu"]


def test_a_job_can_be_edited(store):
    jid = store.add_report_job(_job())
    job = store.get_report_job(jid)
    job.name = "Renamed"
    job.recipients = ["other@corp.local"]
    store.update_report_job(job)
    back = store.get_report_job(jid)
    assert (back.name, back.recipients) == ("Renamed", ["other@corp.local"])


def test_a_job_can_be_switched_off_without_being_deleted(store):
    jid = store.add_report_job(_job())
    store.set_report_job_enabled(jid, False)
    assert store.get_report_job(jid).enabled is False


def test_a_deleted_job_is_gone(store):
    jid = store.add_report_job(_job())
    store.delete_report_job(jid)
    assert store.get_report_job(jid) is None
    assert store.list_report_jobs() == []


def test_the_fire_time_is_recorded_as_utc_text(store):
    jid = store.add_report_job(_job())
    store.set_report_job_fired(jid, FIRED)
    assert store.get_report_job(jid).last_fired_at == "2026-08-13T07:00:00Z"


def test_a_run_is_recorded_and_read_back(store):
    jid = store.add_report_job(_job())
    store.record_report_run(jid, FIRED, FIRED, FIRED, "ok", attempts=1,
                            pages=2, recipients=3)
    run = store.latest_report_run(jid)
    assert run["status"] == "ok"
    assert (run["pages"], run["recipients"], run["attempts"]) == (2, 3, 1)
    assert run["fired_for"] == "2026-08-13T07:00:00Z"


def test_a_failed_run_keeps_every_dataset_that_broke(store):
    """The page must be able to show exactly what the admin was mailed."""
    jid = store.add_report_job(_job())
    detail = [{"name": "open_orders", "error": "connection refused",
               "qsql": "select from orders"},
              {"name": "fills", "error": "`type error", "qsql": "select from fills"}]
    store.record_report_run(jid, FIRED, FIRED, FIRED, "failed", attempts=3,
                            error="2 of 5 datasets failed", detail=detail)
    run = store.latest_report_run(jid)
    assert run["detail"] == detail
    assert run["attempts"] == 3


def test_a_run_with_no_detail_reads_back_as_an_empty_list(store):
    jid = store.add_report_job(_job())
    store.record_report_run(jid, FIRED, FIRED, FIRED, "missed")
    assert store.latest_report_run(jid)["detail"] == []


def test_runs_are_listed_newest_first(store):
    jid = store.add_report_job(_job())
    for day in (11, 12, 13):
        when = FIRED.replace(day=day)
        store.record_report_run(jid, when, when, when, "ok")
    got = [r["fired_for"] for r in store.list_report_runs(jid)]
    assert got == ["2026-08-13T07:00:00Z", "2026-08-12T07:00:00Z",
                   "2026-08-11T07:00:00Z"]


def test_history_is_trimmed_so_it_cannot_grow_forever(store):
    jid = store.add_report_job(_job())
    for minute in range(REPORT_RUN_RETENTION + 10):
        when = datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc)
        store.record_report_run(jid, when.replace(minute=minute % 60,
                                                  hour=minute // 60),
                                when, when, "ok")
    assert len(store.list_report_runs(jid, limit=1000)) == REPORT_RUN_RETENTION


def test_one_job_history_is_not_trimmed_by_another(store):
    a = store.add_report_job(_job(name="A"))
    b = store.add_report_job(_job(name="B"))
    store.record_report_run(a, FIRED, FIRED, FIRED, "ok")
    for minute in range(REPORT_RUN_RETENTION + 5):
        when = datetime(2026, 8, 13, minute // 60, minute % 60, tzinfo=timezone.utc)
        store.record_report_run(b, when, when, when, "ok")
    assert len(store.list_report_runs(a)) == 1


def test_deleting_a_job_takes_its_history_with_it(store):
    jid = store.add_report_job(_job())
    store.record_report_run(jid, FIRED, FIRED, FIRED, "ok")
    store.delete_report_job(jid)
    assert store.list_report_runs(jid) == []


def test_the_database_is_in_wal_so_two_processes_can_use_it(tmp_path):
    """The scheduler writes while the app reads. Default journal mode makes
    that 'database is locked'."""
    s = Storage(str(tmp_path / "k.db"))
    s.init_db()
    mode = s.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_an_older_database_gains_the_new_tables_on_open(tmp_path):
    path = str(tmp_path / "k.db")
    Storage(path).init_db()
    reopened = Storage(path)
    reopened.init_db()
    assert reopened.list_report_jobs() == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_report_storage.py -q`
Expected: FAIL — `ImportError: cannot import name 'REPORT_RUN_RETENTION'`

- [ ] **Step 3: Add the WAL pragmas**

In `kdbmonitor/core/storage.py`, replace `Storage.__init__`:

```python
class Storage:
    def __init__(self, path: str = "kdbmonitor.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # Two processes share this file: the app and the report scheduler. In
        # the default rollback journal the second one to write meets "database
        # is locked", so WAL (one writer, concurrent readers) is a requirement
        # of the scheduler rather than tidying. The busy timeout covers the
        # moments they collide anyway.
        #
        # ":memory:" databases keep whatever mode they are given; the pragma is
        # harmless there, and tests that assert WAL use a real file.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
```

- [ ] **Step 4: Add the tables**

In `init_db`'s `executescript`, immediately before the `CREATE TABLE IF NOT EXISTS settings` block, insert:

```sql
            CREATE TABLE IF NOT EXISTS report_jobs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL,
                enabled       INTEGER NOT NULL DEFAULT 1,
                dashboard_id  INTEGER NOT NULL,
                spec_json     TEXT NOT NULL DEFAULT '{}',
                last_fired_at TEXT,
                created_at    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS report_job_runs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id      INTEGER NOT NULL,
                fired_for   TEXT NOT NULL,
                started_at  TEXT NOT NULL,
                finished_at TEXT,
                status      TEXT NOT NULL,
                attempts    INTEGER NOT NULL DEFAULT 1,
                pages       INTEGER,
                recipients  INTEGER,
                error       TEXT,
                detail_json TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_report_job_runs_job
                ON report_job_runs (job_id, fired_for DESC);
```

- [ ] **Step 5: Add the accessors**

Add the module constant near the top of `storage.py`, after the imports:

```python
# How much history one job keeps. Enough to see a pattern of morning failures,
# bounded so a job running every fifteen minutes cannot fill the database.
REPORT_RUN_RETENTION = 200
```

Add the import at the top:

```python
from kdbmonitor.core.reportjob import ReportJob, job_from_json, job_to_json
```

Insert this block immediately before `def get_setting`:

```python
    # --- scheduled reports ---
    @staticmethod
    def _stamp(when: datetime) -> str:
        """An aware datetime as the 20-character UTC text everything stores.

        One spelling throughout: the scheduler's lock compares timestamps as
        strings, and a second format would make a fresh lock look stale.
        """
        return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def add_report_job(self, job: ReportJob) -> int:
        # A new job starts from now, not from null: runs_between would
        # otherwise be asked what it missed since the epoch, and told to send
        # all of it.
        now = self._stamp(datetime.now(timezone.utc))
        job.last_fired_at = job.last_fired_at or now
        cur = self.conn.execute(
            "INSERT INTO report_jobs(name, enabled, dashboard_id, spec_json, "
            "last_fired_at, created_at) VALUES (?,?,?,?,?,?)",
            (job.name, 1 if job.enabled else 0, job.dashboard_id,
             job_to_json(job), job.last_fired_at, now))
        self.conn.commit()
        return cur.lastrowid

    def _row_to_report_job(self, r: sqlite3.Row) -> ReportJob:
        job = job_from_json(r["spec_json"])
        # The columns are authoritative for everything also held in a column:
        # they are what the scheduler updates on a run, and re-serialising the
        # payload on every fire would rewrite the whole job to store a date.
        job.id = r["id"]
        job.enabled = bool(r["enabled"])
        job.dashboard_id = r["dashboard_id"]
        job.last_fired_at = r["last_fired_at"]
        return job

    def list_report_jobs(self) -> list[ReportJob]:
        rows = self.conn.execute(
            "SELECT * FROM report_jobs ORDER BY name COLLATE NOCASE").fetchall()
        return [self._row_to_report_job(r) for r in rows]

    def get_report_job(self, jid: int) -> Optional[ReportJob]:
        r = self.conn.execute(
            "SELECT * FROM report_jobs WHERE id=?", (jid,)).fetchone()
        return self._row_to_report_job(r) if r else None

    def update_report_job(self, job: ReportJob) -> None:
        self.conn.execute(
            "UPDATE report_jobs SET name=?, enabled=?, dashboard_id=?, "
            "spec_json=? WHERE id=?",
            (job.name, 1 if job.enabled else 0, job.dashboard_id,
             job_to_json(job), job.id))
        self.conn.commit()

    def delete_report_job(self, jid: int) -> None:
        self.conn.execute("DELETE FROM report_jobs WHERE id=?", (jid,))
        self.conn.execute("DELETE FROM report_job_runs WHERE job_id=?", (jid,))
        self.conn.commit()

    def set_report_job_enabled(self, jid: int, enabled: bool) -> None:
        self.conn.execute("UPDATE report_jobs SET enabled=? WHERE id=?",
                          (1 if enabled else 0, jid))
        self.conn.commit()

    def set_report_job_fired(self, jid: int, fired_at: datetime) -> None:
        """Record the *fire time* just run — never the moment it finished.

        A slow run stamped by its end would put last_fired_at past the next
        fire time, and that report would never be sent at all.
        """
        self.conn.execute("UPDATE report_jobs SET last_fired_at=? WHERE id=?",
                          (self._stamp(fired_at), jid))
        self.conn.commit()

    def record_report_run(self, job_id: int, fired_for: datetime,
                          started_at: datetime,
                          finished_at: Optional[datetime] = None,
                          status: str = "ok", attempts: int = 1,
                          pages: Optional[int] = None,
                          recipients: Optional[int] = None,
                          error: Optional[str] = None,
                          detail: Optional[list[dict]] = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO report_job_runs(job_id, fired_for, started_at, "
            "finished_at, status, attempts, pages, recipients, error, "
            "detail_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (job_id, self._stamp(fired_for), self._stamp(started_at),
             self._stamp(finished_at) if finished_at else None,
             status, attempts, pages, recipients, error,
             json.dumps(detail) if detail else None))
        # Trimmed per job rather than globally: a report running every fifteen
        # minutes must not push a daily one's only failure out of its history.
        self.conn.execute(
            "DELETE FROM report_job_runs WHERE job_id=? AND id NOT IN ("
            "  SELECT id FROM report_job_runs WHERE job_id=? "
            "  ORDER BY id DESC LIMIT ?)",
            (job_id, job_id, REPORT_RUN_RETENTION))
        self.conn.commit()
        return cur.lastrowid

    @staticmethod
    def _row_to_report_run(r: sqlite3.Row) -> dict:
        run = dict(r)
        run["detail"] = json.loads(run.pop("detail_json") or "[]")
        return run

    def list_report_runs(self, job_id: int, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM report_job_runs WHERE job_id=? "
            "ORDER BY id DESC LIMIT ?", (job_id, limit)).fetchall()
        return [self._row_to_report_run(r) for r in rows]

    def latest_report_run(self, job_id: int) -> Optional[dict]:
        runs = self.list_report_runs(job_id, limit=1)
        return runs[0] if runs else None
```

Check the imports at the top of `storage.py` — it already imports `json` and `sqlite3` and `Optional`. Add `from datetime import datetime, timezone` if not present.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_report_storage.py -q`
Expected: PASS

If `test_a_saved_job_comes_back_whole` fails with a circular import, note that `reportjob.py` must not import `storage.py` — it does not.

- [ ] **Step 7: Run the full suite and commit**

Run: `python -m pytest -q`
Expected: PASS. The WAL change touches every test that builds a `Storage`; if anything fails here it is the pragma, not the new tables.

```bash
git add kdbmonitor/core/storage.py tests/test_report_storage.py
git commit -m "feat: two processes on one file, and somewhere to keep the jobs"
```

---

## Task 4: Mail

**Files:**
- Modify: `kdbmonitor/core/notifiers.py`
- Test: `tests/test_report_mail.py`

**Interfaces:**
- Produces:
  - `parse_address_list(text: Optional[str]) -> list[str]`
  - `send_report_email(smtp_host, smtp_port, sender, to: list[str], subject: str, body: str, attachments: Sequence[tuple[str, bytes, str]] = ()) -> None`
- `send_email` is untouched — the alert path has no reason to change.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_report_mail.py`:

```python
"""Mailing a report, and reading the admin list somebody typed."""
import smtplib
from email import message_from_string

import pytest

from kdbmonitor.core.notifiers import parse_address_list, send_report_email


class _FakeSMTP:
    """Records what would have been sent. Constructed as a context manager,
    the way smtplib.SMTP is used."""
    sent: list[dict] = []

    def __init__(self, host, port):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def send_message(self, msg, from_addr=None, to_addrs=None):
        type(self).sent.append({"host": self.host, "port": self.port,
                                "msg": msg, "from": from_addr, "to": to_addrs})


@pytest.fixture()
def sent(monkeypatch):
    _FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    return _FakeSMTP.sent


# --- the admin list ----------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("a@x.com", ["a@x.com"]),
    ("a@x.com,b@x.com", ["a@x.com", "b@x.com"]),
    ("a@x.com; b@x.com", ["a@x.com", "b@x.com"]),
    ("a@x.com; b@x.com, c@x.com", ["a@x.com", "b@x.com", "c@x.com"]),
    ("  a@x.com ,  b@x.com  ", ["a@x.com", "b@x.com"]),
    ("a@x.com,", ["a@x.com"]),
    ("a@x.com,,b@x.com", ["a@x.com", "b@x.com"]),
    ("", []),
    (None, []),
    ("   ", []),
])
def test_an_address_list_is_read_however_it_was_pasted(text, expected):
    """Outlook puts semicolons on the clipboard. Refusing a pasted list for
    its separator is being wrong about something that does not matter."""
    assert parse_address_list(text) == expected


# --- sending -----------------------------------------------------------------

def test_a_report_goes_out_with_its_pdf_attached(sent):
    send_report_email("smtp.corp.local", 25, "kdbmonitor@corp.local",
                      ["desk@corp.local", "risk@corp.local"],
                      "OMS Overview — 2026-08-14", "Attached.",
                      [("oms_overview_2026-08-14_0700.pdf", b"%PDF-1.4 fake",
                        "application/pdf")])
    assert len(sent) == 1
    call = sent[0]
    assert (call["host"], call["port"]) == ("smtp.corp.local", 25)
    assert call["to"] == ["desk@corp.local", "risk@corp.local"]
    assert call["from"] == "kdbmonitor@corp.local"

    msg = call["msg"]
    assert msg["Subject"] == "OMS Overview — 2026-08-14"
    assert msg["To"] == "desk@corp.local, risk@corp.local"

    parts = list(msg.iter_attachments())
    assert len(parts) == 1
    assert parts[0].get_filename() == "oms_overview_2026-08-14_0700.pdf"
    assert parts[0].get_content_type() == "application/pdf"
    assert parts[0].get_payload(decode=True) == b"%PDF-1.4 fake"


def test_the_body_is_readable_text(sent):
    send_report_email("h", 25, "s@x.com", ["a@x.com"], "S", "The body.")
    assert "The body." in sent[0]["msg"].get_content()


def test_a_notice_with_no_attachment_is_a_plain_message(sent):
    send_report_email("h", 25, "s@x.com", ["admin@x.com"], "[FAILED] J", "why")
    assert list(sent[0]["msg"].iter_attachments()) == []


def test_several_addresses_are_one_message_not_one_each(sent):
    """They are colleagues working the same failure; seeing who else was told,
    and being able to reply to all of them, is the point."""
    send_report_email("h", 25, "s@x.com",
                      ["a@x.com", "b@x.com", "c@x.com"], "S", "B")
    assert len(sent) == 1
    assert sent[0]["to"] == ["a@x.com", "b@x.com", "c@x.com"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_report_mail.py -q`
Expected: FAIL — `ImportError: cannot import name 'parse_address_list'`

- [ ] **Step 3: Implement**

In `kdbmonitor/core/notifiers.py`, add to the imports:

```python
import re
from email.message import EmailMessage
from typing import Callable, Optional, Sequence
```

Append to the module, after `send_email`:

```python
_SEPARATORS = re.compile(r"[,;]")


def parse_address_list(text: Optional[str]) -> list[str]:
    """Addresses from one typed field.

    Comma or semicolon, because Outlook puts semicolons on the clipboard and
    refusing a pasted list for its separator is being wrong about something
    that does not matter. Blank entries and a trailing separator are dropped.
    """
    if not text:
        return []
    return [part.strip() for part in _SEPARATORS.split(text) if part.strip()]


def send_report_email(smtp_host: str, smtp_port: int, sender: str,
                      to: list[str], subject: str, body: str,
                      attachments: Sequence[tuple[str, bytes, str]] = ()
                      ) -> None:
    """A message that can carry a PDF.

    ``EmailMessage`` rather than the ``MIMEText`` :func:`send_email` uses,
    which cannot hold an attachment. The alert path keeps that function
    unchanged — it has no attachment to carry and no reason to be touched.

    ``attachments`` are ``(filename, data, mime_type)``.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg.set_content(body or "")

    for filename, data, mime in attachments:
        maintype, _, subtype = mime.partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream",
                           filename=filename)

    with smtplib.SMTP(smtp_host, smtp_port) as s:
        # Recipients passed explicitly rather than left to the header: one
        # envelope for the whole list, so three admins get one conversation
        # and can reply to each other rather than three separate threads.
        s.send_message(msg, from_addr=sender, to_addrs=list(to))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_report_mail.py tests/test_notifiers.py -q`
Expected: PASS — including the existing `test_notifiers.py`, which must be unaffected.

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/core/notifiers.py tests/test_report_mail.py
git commit -m "feat: a message that can carry the report, and a list however it was pasted"
```

---

## Task 5: Running one job

The heart. **Read spec §5.1 and §6.1 before starting.** This module knows nothing about clocks, retries or whether anything is due — it takes a job and runs it now.

**Files:**
- Create: `kdbmonitor/core/reportrun.py`
- Test: `tests/test_reportrun.py`

**Interfaces:**
- Consumes: `core.dataset.run_datasets`, `core.dashpdf.dashboard_to_pdf_bytes`, `core.dashpdf.page_count`, `core.dashpdf.pdf_filename`, `core.timectx.resolve`, `core.timectx.coerce_spec`, `core.notifiers.send_report_email`, `core.notifiers.parse_address_list`, `core.reportjob.render_text`, `core.storage.Storage`.
- Produces:
  - `DatasetFailure(name: str, error: str, qsql: str = "")` — with `as_dict() -> dict`
  - `RunOutcome(ok: bool, pages: int = 0, bytes_sent: int = 0, recipients: int = 0, failures: list[DatasetFailure] = [], error: Optional[str] = None)`
  - `collect_failures(results: dict) -> list[DatasetFailure]`
  - `chosen_values(dashboard, job) -> dict`
  - `run_job(job, store, mgr, now: datetime, send=None) -> RunOutcome`
  - `failure_notice(job, dashboard_name: str, due: datetime, attempts: int, outcome: RunOutcome) -> tuple[str, str]`
  - `send_failure_notice(job, store, dashboard_name, due, attempts, outcome, send=None) -> bool`
  - `ADMIN_EMAIL_KEY: str = "scheduler_admin_email"`

- [ ] **Step 1: Write the failing tests for the happy path and the inputs**

Create `tests/test_reportrun.py`:

```python
"""Running one scheduled report, end to end, against the demo mock.

The PDF is the one the Download button produces — these tests exist to hold
the wiring around it: which period, which parameter values, and the rule that
any dataset failing means nothing is sent.
"""
from datetime import date, datetime, timezone

import pytest

from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.dashboard_models import (
    Dashboard, Dataset, Parameter, Row, Widget,
)
from kdbmonitor.core.mock import demo_connection_specs
from kdbmonitor.core.reportjob import ReportJob
from kdbmonitor.core.reportrun import (
    RunOutcome, chosen_values, collect_failures, failure_notice, run_job,
    send_failure_notice,
)
from kdbmonitor.core.storage import Storage

NOW = datetime(2026, 8, 14, 7, 0, tzinfo=timezone.utc)


class _Sent:
    """Stands in for notifiers.send_report_email."""
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, host, port, sender, to, subject, body, attachments=()):
        self.calls.append({"host": host, "port": port, "sender": sender,
                           "to": list(to), "subject": subject, "body": body,
                           "attachments": list(attachments)})


def _dashboard(**kw) -> Dashboard:
    base = dict(
        id=None, name="OMS Overview", description="", refresh_secs=0,
        datasets=[Dataset(name="orders", env="orders", table="target")],
        rows=[Row(height_in=2.0, widgets=[
            Widget(type="table", dataset="orders", title="Orders", width=1.0)])])
    base.update(kw)
    return Dashboard(**base)


def _env(dashboard: Dashboard = None):
    store = Storage(":memory:")
    store.init_db()
    for spec in demo_connection_specs():
        store.add_connection(spec)
    store.set_setting("smtp_host", "smtp.corp.local")
    store.set_setting("smtp_port", "25")
    store.set_setting("smtp_sender", "kdbmonitor@corp.local")
    did = store.add_dashboard(dashboard or _dashboard())
    return store, ConnectionManager(), did


def _job(dashboard_id: int, **kw) -> ReportJob:
    base = dict(id=1, name="Morning OMS report", dashboard_id=dashboard_id,
                recipients=["desk@corp.local", "risk@corp.local"])
    base.update(kw)
    return ReportJob(**base)


# --- the report itself -------------------------------------------------------

def test_a_good_job_sends_one_pdf_to_everyone_on_the_list():
    store, mgr, did = _env()
    send = _Sent()
    outcome = run_job(_job(did), store, mgr, NOW, send=send)

    assert outcome.ok, outcome.error
    assert outcome.recipients == 2
    assert outcome.pages >= 1
    assert len(send.calls) == 1

    call = send.calls[0]
    assert call["to"] == ["desk@corp.local", "risk@corp.local"]
    assert call["host"] == "smtp.corp.local"
    filename, data, mime = call["attachments"][0]
    assert filename.startswith("oms_overview_2026-08-14")
    assert filename.endswith(".pdf")
    assert data[:4] == b"%PDF"
    assert mime == "application/pdf"


def test_the_subject_has_its_placeholders_filled_in():
    store, mgr, did = _env()
    send = _Sent()
    run_job(_job(did, subject="{dashboard} — {date} {time}"), store, mgr, NOW,
            send=send)
    assert send.calls[0]["subject"] == "OMS Overview — 2026-08-14 07:00"


def test_a_job_with_no_body_still_says_something_useful():
    store, mgr, did = _env()
    send = _Sent()
    run_job(_job(did, body=""), store, mgr, NOW, send=send)
    assert send.calls[0]["body"].strip()


# --- what the job overrides --------------------------------------------------

def test_a_period_override_reaches_the_query_instead_of_the_dashboard_own():
    """The dashboard is saved real-time; the job asks for yesterday. What runs
    must be yesterday, or a morning report of overnight activity is empty."""
    store, mgr, did = _env()
    seen = {}

    def spy_run_datasets(dashboard, store_, mgr_, today, **kw):
        seen["time_context"] = dashboard.time_context
        return {}

    import kdbmonitor.core.reportrun as reportrun
    original = reportrun.run_datasets
    reportrun.run_datasets = spy_run_datasets
    try:
        run_job(_job(did, period={"mode": "historical",
                                  "range": {"kind": "preset", "name": "yesterday"}}),
                store, mgr, NOW, send=_Sent())
    finally:
        reportrun.run_datasets = original

    assert seen["time_context"]["mode"] == "historical"
    assert seen["time_context"]["range"]["name"] == "yesterday"


def test_without_an_override_the_dashboard_own_period_is_used():
    dash = _dashboard(time_context={"mode": "historical",
                                    "range": {"kind": "preset", "name": "mtd"}})
    store, mgr, did = _env(dash)
    job = _job(did)
    assert job.period is None
    loaded = store.get_dashboard(did)
    assert chosen_values(loaded, job) == {}


def test_a_parameter_override_wins_and_the_rest_keep_their_defaults():
    dash = _dashboard(parameters=[Parameter(name="region", default="APAC"),
                                  Parameter(name="desk", default="EQ")])
    store, mgr, did = _env(dash)
    loaded = store.get_dashboard(did)
    got = chosen_values(loaded, _job(did, parameters={"region": "EMEA"}))
    assert got == {"region": "EMEA", "desk": "EQ"}


def test_an_override_for_a_parameter_that_no_longer_exists_is_ignored():
    """A dashboard can lose a parameter after a job named it. That must not
    reach the query as a value nothing will substitute."""
    dash = _dashboard(parameters=[Parameter(name="region", default="APAC")])
    store, mgr, did = _env(dash)
    loaded = store.get_dashboard(did)
    got = chosen_values(loaded, _job(did, parameters={"region": "EMEA",
                                                      "gone": "x"}))
    assert got == {"region": "EMEA"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_reportrun.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kdbmonitor.core.reportrun'`

- [ ] **Step 3: Write the failing tests for the failure rule and the notice**

Append to `tests/test_reportrun.py`:

```python
# --- the rule: any dataset failing fails the run (spec §5.1) ------------------

class _Result:
    """A stand-in for DatasetResult, holding only what collect_failures reads."""
    def __init__(self, name, df=None, error=None, qsql="", waiting=False):
        self.name, self.df, self.error = name, df, error
        self.qsql, self.waiting = qsql, waiting


def test_a_dataset_in_error_is_collected_with_its_query():
    results = {"orders": _Result("orders", df=object()),
               "fills": _Result("fills", error="`type error",
                                qsql="select from fills")}
    failures = collect_failures(results)
    assert [f.name for f in failures] == ["fills"]
    assert failures[0].error == "`type error"
    assert failures[0].qsql == "select from fills"


def test_a_dataset_still_waiting_counts_as_a_failure_too():
    """Waiting distinguishes 'no upload yet' from 'broken' for a reader. For a
    report the frame is absent either way, and the page would print without it."""
    results = {"uploaded": _Result("uploaded", waiting=True, error=None)}
    failures = collect_failures(results)
    assert len(failures) == 1
    assert "waiting" in failures[0].error.lower()


def test_every_broken_dataset_is_named_not_just_the_first():
    """One notice, every problem — or the admin fixes one at 07:05 and finds
    the next at 07:00 tomorrow."""
    results = {"a": _Result("a", error="boom"), "b": _Result("b", df=object()),
               "c": _Result("c", error="bang")}
    assert [f.name for f in collect_failures(results)] == ["a", "c"]


def test_a_run_with_a_broken_dataset_sends_nothing_at_all(monkeypatch):
    store, mgr, did = _env()
    send = _Sent()

    import kdbmonitor.core.reportrun as reportrun
    monkeypatch.setattr(reportrun, "run_datasets", lambda *a, **k: {
        "orders": _Result("orders", error="handle: connection refused",
                          qsql="select from orders")})

    outcome = run_job(_job(did), store, mgr, NOW, send=send)

    assert outcome.ok is False
    assert send.calls == []                      # the recipients hear nothing
    assert [f.name for f in outcome.failures] == ["orders"]
    assert "1 of 1" in outcome.error


def test_a_run_where_everything_works_sends_exactly_once():
    store, mgr, did = _env()
    send = _Sent()
    assert run_job(_job(did), store, mgr, NOW, send=send).ok
    assert len(send.calls) == 1


# --- refusals that never reach a query ---------------------------------------

def test_a_deleted_dashboard_fails_the_job_by_name():
    store, mgr, did = _env()
    store.delete_dashboard(did)
    outcome = run_job(_job(did), store, mgr, NOW, send=_Sent())
    assert outcome.ok is False
    assert "no longer exists" in outcome.error


def test_a_file_dashboard_is_refused_with_the_reason():
    """There is no browser at 07:00 and no upload to read."""
    store, mgr, did = _env(_dashboard(source="file"))
    outcome = run_job(_job(did), store, mgr, NOW, send=_Sent())
    assert outcome.ok is False
    assert "upload" in outcome.error.lower()


def test_a_job_with_no_recipients_fails_rather_than_sending_to_nobody():
    store, mgr, did = _env()
    outcome = run_job(_job(did, recipients=[]), store, mgr, NOW, send=_Sent())
    assert outcome.ok is False
    assert "recipient" in outcome.error.lower()


def test_unconfigured_smtp_fails_the_run_with_a_message_rather_than_raising():
    store, mgr, did = _env()
    store.set_setting("smtp_host", "")
    outcome = run_job(_job(did), store, mgr, NOW, send=_Sent())
    assert outcome.ok is False
    assert "smtp" in outcome.error.lower()


# --- the admin notice (spec §6.1) --------------------------------------------

def _failed() -> RunOutcome:
    from kdbmonitor.core.reportrun import DatasetFailure
    return RunOutcome(ok=False, error="2 of 5 datasets failed", failures=[
        DatasetFailure("open_orders", "handle: connection refused",
                       "select from orders where date=2026.08.13"),
        DatasetFailure("fills", "`type error", "select sum size by sym from fills")])


def test_the_notice_names_the_job_the_time_and_every_broken_dataset():
    subject, body = failure_notice(_job(1), "OMS Overview", NOW, 3, _failed())
    assert "FAILED" in subject
    assert "Morning OMS report" in subject
    for expected in ("OMS Overview", "2026-08-14", "07:00", "open_orders",
                     "connection refused", "fills", "`type error",
                     "select sum size by sym from fills"):
        assert expected in body, expected
    assert "3" in body            # attempts


def test_the_notice_goes_to_the_admins_and_to_nobody_on_the_recipient_list():
    store, mgr, did = _env()
    store.set_setting("scheduler_admin_email",
                      "kdb-support@corp.local; alice@corp.local")
    send = _Sent()
    assert send_failure_notice(_job(did), store, "OMS Overview", NOW, 3,
                               _failed(), send=send)

    assert len(send.calls) == 1
    to = send.calls[0]["to"]
    assert to == ["kdb-support@corp.local", "alice@corp.local"]
    assert "desk@corp.local" not in to
    assert "risk@corp.local" not in to


def test_the_notice_carries_no_attachment():
    store, mgr, did = _env()
    store.set_setting("scheduler_admin_email", "admin@corp.local")
    send = _Sent()
    send_failure_notice(_job(did), store, "OMS Overview", NOW, 3, _failed(),
                        send=send)
    assert send.calls[0]["attachments"] == []


def test_with_no_admin_address_nothing_is_sent_and_it_says_so():
    store, mgr, did = _env()
    send = _Sent()
    assert send_failure_notice(_job(did), store, "OMS", NOW, 3, _failed(),
                               send=send) is False
    assert send.calls == []
```

- [ ] **Step 4: Run them to verify they fail**

Run: `python -m pytest tests/test_reportrun.py -q`
Expected: FAIL — module still missing.

- [ ] **Step 5: Implement the module**

Create `kdbmonitor/core/reportrun.py`:

```python
"""Running one scheduled report, once.

Deliberately ignorant of clocks. This module is handed a job and a moment and
does the whole of the work — resolve, query, render, send — and knows nothing
about what is due, what to retry, or what to write down afterwards. That line
is what makes the report path testable without waiting for a clock and the
timing path testable without a KDB server.

The PDF comes from :func:`kdbmonitor.core.dashpdf.dashboard_to_pdf_bytes`, the
same function the Download button calls. Nothing about rendering is written a
second time here: a report that arrives by mail has to be the report you would
have downloaded by hand, and the only way to guarantee that is to not have a
second implementation to drift from.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable, Optional

from kdbmonitor.core import notifiers, timectx
from kdbmonitor.core.dashpdf import (
    dashboard_to_pdf_bytes, page_count, pdf_filename,
)
from kdbmonitor.core.dataset import run_datasets
from kdbmonitor.core.reportjob import ReportJob, render_text

ADMIN_EMAIL_KEY = "scheduler_admin_email"

_DEFAULT_BODY = ("The attached report was produced by KdbMonitor.\n"
                 "\n{dashboard} — {date} {time}\n")


@dataclass
class DatasetFailure:
    name: str
    error: str
    qsql: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "error": self.error, "qsql": self.qsql}


@dataclass
class RunOutcome:
    ok: bool
    pages: int = 0
    bytes_sent: int = 0
    recipients: int = 0
    failures: list[DatasetFailure] = field(default_factory=list)
    error: Optional[str] = None


def collect_failures(results: dict) -> list[DatasetFailure]:
    """Every dataset that produced no frame, with the query that was sent.

    ``waiting`` is included. It exists to tell a reader "no upload yet" apart
    from "broken", which is a distinction worth drawing on screen — but a
    report has no frame either way, and would print the panel empty.
    """
    out: list[DatasetFailure] = []
    for name, res in (results or {}).items():
        if getattr(res, "df", None) is not None:
            continue
        error = getattr(res, "error", None)
        if not error:
            error = ("waiting for an upload that a scheduled run cannot make"
                     if getattr(res, "waiting", False) else "produced no frame")
        out.append(DatasetFailure(name=name, error=str(error),
                                  qsql=getattr(res, "qsql", "") or ""))
    return out


def chosen_values(dashboard, job: ReportJob) -> dict:
    """The parameter values this run uses: the dashboard's defaults, overlaid
    with what the job overrides.

    An override naming a parameter the dashboard no longer declares is dropped
    rather than passed on — a job can outlive the parameter it was written for,
    and a value nothing will substitute is not worth sending to a server.
    """
    values = {p.name: p.default for p in (dashboard.parameters or [])}
    for name, value in (job.parameters or {}).items():
        if name in values:
            values[name] = value
    return values


def run_job(job: ReportJob, store, mgr, now: datetime,
            send: Optional[Callable] = None) -> RunOutcome:
    """Run ``job`` now and mail the result. Never raises for an expected
    failure — the caller records what came back."""
    send = send or notifiers.send_report_email

    dashboard = store.get_dashboard(job.dashboard_id)
    if dashboard is None:
        return RunOutcome(ok=False, error=(
            f"the dashboard this job points at (id {job.dashboard_id}) "
            "no longer exists"))
    if getattr(dashboard, "source", "kdb") == "file":
        return RunOutcome(ok=False, error=(
            f"'{dashboard.name}' reads from an uploaded file, and a scheduled "
            "run has no browser to take the upload from"))
    if not job.recipients:
        return RunOutcome(ok=False, error="the job has no recipients")

    smtp_host = (store.get_setting("smtp_host", "") or "").strip()
    if not smtp_host:
        return RunOutcome(ok=False, error=(
            "SMTP is not configured — set the host in Admin > Email (SMTP)"))

    # The job's period, or the dashboard's own. Held to what the dashboard
    # offers by the same coerce_spec the Dashboards page applies, so a job
    # asking for a historical period from a real-time-only dashboard lands
    # where the screen would rather than querying a server that is not there.
    spec = timectx.coerce_spec(job.period or dashboard.time_context,
                               dashboard.periods)
    # run_datasets resolves the dashboard's own time_context, so the override
    # has to be *on* the dashboard it is handed. get_dashboard builds a fresh
    # object per call, but replace() says so rather than relying on it.
    dashboard = replace(dashboard, time_context=spec)
    rt = timectx.resolve(spec, now.date())
    chosen = chosen_values(dashboard, job)

    results = run_datasets(dashboard, store, mgr, now.date(), chosen=chosen)

    # Spec §5.1: one dataset in error means no report is sent. A PDF in an
    # inbox is read detached from the app, forwarded on, and quoted days later,
    # by which time nobody knows the panel showing zero was a dead connection
    # rather than a quiet morning. Wrong and plausible is worse than absent.
    failures = collect_failures(results)
    if failures:
        return RunOutcome(
            ok=False, failures=failures,
            error=f"{len(failures)} of {len(results)} datasets failed")

    pdf = dashboard_to_pdf_bytes(dashboard, results, rt, now, chosen)
    subject = render_text(job.subject, job=job.name, dashboard=dashboard.name,
                          when=now)
    body = render_text(job.body or _DEFAULT_BODY, job=job.name,
                       dashboard=dashboard.name, when=now)

    send(smtp_host, int(store.get_setting("smtp_port", "25") or 25),
         store.get_setting("smtp_sender", "") or "", job.recipients,
         subject, body,
         [(pdf_filename(dashboard, now), pdf, "application/pdf")])

    return RunOutcome(ok=True, pages=page_count(dashboard, results, chosen),
                      bytes_sent=len(pdf), recipients=len(job.recipients))


# --- telling the admin (spec §6.1) ------------------------------------------
#
# The recipients are never written to. They asked for a PDF at 07:00; a mail
# telling them a q handle refused a connection asks them to act on something
# they cannot act on, and a list that sometimes carries reports and sometimes
# carries apologies gets filtered. The people who can do something are the
# people who run KdbMonitor, and they are the only ones told.

def failure_notice(job: ReportJob, dashboard_name: str, due: datetime,
                   attempts: int, outcome: RunOutcome) -> tuple[str, str]:
    """Subject and body of the notice. Composed here, beside the run that
    produced it, so the page can show the same words from the same source."""
    subject = f"[KdbMonitor] FAILED — {job.name}"

    lines = [f"Job:        {job.name}",
             f"Dashboard:  {dashboard_name}",
             f"Due:        {due:%Y-%m-%d %H:%M} UTC",
             f"Attempts:   {attempts}",
             f"Recipients: {len(job.recipients)} (not sent to)",
             ""]
    if outcome.failures:
        lines.append(f"{outcome.error}:")
        lines.append("")
        for failure in outcome.failures:
            lines.append(f"  {failure.name}")
            lines.append(f"      {failure.error}")
            if failure.qsql:
                # The admin's next action is to run this. Finding it otherwise
                # means opening the dashboard and reconstructing which
                # substitutions produced it.
                lines.append(f"      {failure.qsql}")
            lines.append("")
    else:
        lines.append(outcome.error or "no further detail")
    return subject, "\n".join(lines)


def send_failure_notice(job: ReportJob, store, dashboard_name: str,
                        due: datetime, attempts: int, outcome: RunOutcome,
                        send: Optional[Callable] = None) -> bool:
    """Mail the notice to the admin list. False when there is nobody to tell.

    A missing admin address is a real hole, and it is closed in the app rather
    than by guessing: Admin warns while jobs exist and the field is empty.
    """
    send = send or notifiers.send_report_email
    admins = notifiers.parse_address_list(store.get_setting(ADMIN_EMAIL_KEY, ""))
    smtp_host = (store.get_setting("smtp_host", "") or "").strip()
    if not admins or not smtp_host:
        return False

    subject, body = failure_notice(job, dashboard_name, due, attempts, outcome)
    send(smtp_host, int(store.get_setting("smtp_port", "25") or 25),
         store.get_setting("smtp_sender", "") or "", admins, subject, body)
    return True
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_reportrun.py -q`
Expected: PASS

Two failures are likely and both are real signals, not test bugs:
- `Dashboard has no attribute 'periods'` → check the field name in `core/dashboard_models.py` and use it.
- `replace() got an unexpected keyword` → `Dashboard` is a plain dataclass; if `time_context` is not an init field, set the attribute directly instead.

- [ ] **Step 7: Run the full suite and commit**

Run: `python -m pytest -q`
Expected: PASS

```bash
git add kdbmonitor/core/reportrun.py tests/test_reportrun.py
git commit -m "feat: a report nobody can trust is worse than one that never came"
```

---

## Task 6: The lock and the desired state

**Files:**
- Create: `kdbmonitor/scheduler/__init__.py`, `kdbmonitor/scheduler/supervisor.py`
- Test: `tests/test_scheduler_lock.py`

**Interfaces:**
- Consumes: `Storage.get_setting` / `set_setting` / `conn`.
- Produces:
  - `LOCK_KEY`, `DESIRED_KEY`, `STOP_REQUESTED_KEY`, `SPAWN_ERROR_KEY` — setting names
  - `BEAT_SECS = 15`, `STALE_SECS = 60`, `STOP_GRACE_SECS = 10`
  - `stamp(when: datetime) -> str`
  - `claim(store, now, pid=None, host=None) -> bool`
  - `heartbeat(store, now, pid=None, host=None) -> None`
  - `release(store) -> None`
  - `holder(store) -> Optional[tuple[str, int, str]]` — `(beat_stamp, pid, host)`
  - `desired(store) -> str` — `"running"` | `"stopped"`
  - `request_stop(store, now) -> None`, `request_start(store) -> None`
  - `SchedulerStatus(state, pid, host, beat_age_secs, spawn_error)` — `state` in `running` \| `stopping` \| `not_responding` \| `stale` \| `stopped`
  - `status(store, now) -> SchedulerStatus`
  - `ensure_running(store, now, spawn=None) -> str`
  - `spawn_detached(db_path: Optional[str] = None) -> int` — returns the pid

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scheduler_lock.py`:

```python
"""One scheduler, and only one.

Two schedulers on one database means every recipient gets every report twice.
These tests hold that shut against two tabs loading at once, a UI restarted
while one is already running, and somebody starting one by hand.
"""
from datetime import datetime, timedelta, timezone

import pytest

from kdbmonitor.core.storage import Storage
from kdbmonitor.scheduler import supervisor as sup

NOW = datetime(2026, 8, 13, 7, 0, tzinfo=timezone.utc)


@pytest.fixture()
def store() -> Storage:
    s = Storage(":memory:")
    s.init_db()
    return s


# --- the lock ----------------------------------------------------------------

def test_the_first_process_to_ask_gets_the_lock(store):
    assert sup.claim(store, NOW, pid=100, host="A") is True


def test_the_second_one_does_not(store):
    assert sup.claim(store, NOW, pid=100, host="A") is True
    assert sup.claim(store, NOW, pid=200, host="B") is False


def test_the_holder_is_named(store):
    sup.claim(store, NOW, pid=8412, host="THISBOX")
    beat, pid, host = sup.holder(store)
    assert (pid, host) == (8412, "THISBOX")
    assert beat == "2026-08-13T07:00:00Z"


def test_a_stale_lock_can_be_taken_over(store):
    """A crashed scheduler leaves its lock behind; nothing else would ever run
    again if that were permanent."""
    sup.claim(store, NOW, pid=100, host="A")
    later = NOW + timedelta(seconds=sup.STALE_SECS + 1)
    assert sup.claim(store, later, pid=200, host="B") is True
    assert sup.holder(store)[1] == 200


def test_a_lock_still_being_beaten_cannot_be_taken(store):
    sup.claim(store, NOW, pid=100, host="A")
    almost = NOW + timedelta(seconds=sup.STALE_SECS - 5)
    assert sup.claim(store, almost, pid=200, host="B") is False


def test_a_heartbeat_keeps_the_lock_alive(store):
    sup.claim(store, NOW, pid=100, host="A")
    sup.heartbeat(store, NOW + timedelta(seconds=50), pid=100, host="A")
    # 70s after the original claim, but only 20s after the beat.
    assert sup.claim(store, NOW + timedelta(seconds=70), pid=200, host="B") is False


def test_releasing_hands_it_straight_over(store):
    """A clean stop must not make the next start wait out the stale window."""
    sup.claim(store, NOW, pid=100, host="A")
    sup.release(store)
    assert sup.holder(store) is None
    assert sup.claim(store, NOW, pid=200, host="B") is True


def test_a_timestamp_is_always_twenty_characters(store):
    """The staleness check is substr(value, 1, 20). Any other width breaks it."""
    assert len(sup.stamp(NOW)) == 20
    assert len(sup.stamp(datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc))) == 20


# --- the desired state -------------------------------------------------------

def test_a_fresh_database_wants_a_scheduler_running(store):
    assert sup.desired(store) == "running"


def test_stop_and_start_are_remembered(store):
    sup.request_stop(store, NOW)
    assert sup.desired(store) == "stopped"
    sup.request_start(store)
    assert sup.desired(store) == "running"


# --- status ------------------------------------------------------------------

def test_a_beating_scheduler_reads_as_running(store):
    sup.claim(store, NOW, pid=8412, host="THISBOX")
    st = sup.status(store, NOW + timedelta(seconds=4))
    assert st.state == "running"
    assert (st.pid, st.host) == (8412, "THISBOX")
    assert 3 <= st.beat_age_secs <= 5


def test_nothing_running_and_nothing_wanted_reads_as_stopped(store):
    sup.request_stop(store, NOW)
    assert sup.status(store, NOW).state == "stopped"


def test_nothing_running_but_one_wanted_reads_as_stale(store):
    """The state the UI reconciles by spawning."""
    assert sup.status(store, NOW).state == "stale"


def test_a_scheduler_asked_to_stop_reads_as_stopping_at_first(store):
    sup.claim(store, NOW, pid=1, host="A")
    sup.request_stop(store, NOW)
    assert sup.status(store, NOW + timedelta(seconds=2)).state == "stopping"


def test_one_that_has_not_gone_after_the_grace_is_not_responding(store):
    sup.claim(store, NOW, pid=1, host="A")
    sup.request_stop(store, NOW)
    later = NOW + timedelta(seconds=sup.STOP_GRACE_SECS + 1)
    sup.heartbeat(store, later, pid=1, host="A")     # still alive, still there
    assert sup.status(store, later).state == "not_responding"


# --- autostart ---------------------------------------------------------------

def test_a_stale_scheduler_that_is_wanted_gets_spawned(store):
    calls = []
    assert sup.ensure_running(store, NOW, spawn=lambda: calls.append(1)) == "starting"
    assert calls == [1]


def test_one_already_running_is_left_alone(store):
    sup.claim(store, NOW, pid=1, host="A")
    calls = []
    assert sup.ensure_running(store, NOW, spawn=lambda: calls.append(1)) == "running"
    assert calls == []


def test_one_that_was_stopped_is_not_respawned(store):
    """The bug this design exists to avoid: Stop, then the next page load puts
    it straight back."""
    sup.request_stop(store, NOW)
    calls = []
    assert sup.ensure_running(store, NOW, spawn=lambda: calls.append(1)) == "stopped"
    assert calls == []


def test_the_environment_can_forbid_autostart(store, monkeypatch):
    monkeypatch.setenv("KDBMONITOR_NO_AUTOSTART", "1")
    calls = []
    got = sup.ensure_running(store, NOW, spawn=lambda: calls.append(1))
    assert calls == []
    assert "autostart" in got


def test_a_spawn_that_blows_up_is_reported_rather_than_swallowed(store):
    """A scheduler that silently declines to start is the worst outcome:
    everything looks normal until a report does not arrive."""
    def boom():
        raise OSError("python not found")

    assert sup.ensure_running(store, NOW, spawn=boom) == "failed"
    assert "python not found" in sup.status(store, NOW).spawn_error
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_scheduler_lock.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kdbmonitor.scheduler'`

- [ ] **Step 3: Create the package and implement the supervisor**

Create `kdbmonitor/scheduler/__init__.py`:

```python
"""The report scheduler: a process of its own, sharing only the database.

Nothing in here may import Streamlit. The whole point of the package is that a
report goes out whether or not a browser is open.
"""
```

Create `kdbmonitor/scheduler/supervisor.py`:

```python
"""Is a scheduler running, should one be, and who owns the lock.

Two schedulers on one database means every recipient gets every report twice,
so the answer cannot be "probably not". It is a lock in the settings table,
claimed atomically and refreshed while the owner runs: the value leads with an
ISO-8601 UTC timestamp, which makes "stale" a string comparison and the claim a
single conditional statement that SQLite settles by itself.

Stopping is cooperative and never a kill. Windows has no SIGTERM to send
another process, and killing by a recorded pid risks killing whatever unrelated
process has since inherited it. Writing an intent the owner reads has neither
problem and needs no privileges.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

LOCK_KEY = "scheduler_lock"
DESIRED_KEY = "scheduler_desired"
STOP_REQUESTED_KEY = "scheduler_stop_requested_at"
SPAWN_ERROR_KEY = "scheduler_spawn_error"

# Beat often, expire slowly: three missed beats before another process may take
# over, which tolerates a long query or a paused VM without handing the lock to
# a second scheduler mid-report.
BEAT_SECS = 15
STALE_SECS = 60

# How long a cooperative stop is given before the process is called wedged.
STOP_GRACE_SECS = 10

_STAMP_LEN = 20          # "2026-08-13T07:00:00Z" — the substr() width below


def stamp(when: datetime) -> str:
    """The 20-character UTC text the lock is compared as."""
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unstamp(text: str) -> Optional[datetime]:
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _value(now: datetime, pid: Optional[int], host: Optional[str]) -> str:
    return (f"{stamp(now)}|{pid or os.getpid()}"
            f"|{host or socket.gethostname()}")


def claim(store, now: datetime, pid: Optional[int] = None,
          host: Optional[str] = None) -> bool:
    """Take the lock if it is free or stale. True when this process owns it.

    One statement, settled by SQLite: two processes asking at the same moment
    cannot both be told yes. A loser exits rather than running a second copy.
    """
    cutoff = stamp(now - timedelta(seconds=STALE_SECS))
    before = store.conn.total_changes
    store.conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value "
        f"WHERE substr(settings.value, 1, {_STAMP_LEN}) < ?",
        (LOCK_KEY, _value(now, pid, host), cutoff))
    store.conn.commit()
    return store.conn.total_changes > before


def heartbeat(store, now: datetime, pid: Optional[int] = None,
              host: Optional[str] = None) -> None:
    """Say the owner is still alive. Unconditional — the owner is not racing
    anybody for a lock it already holds."""
    store.set_setting(LOCK_KEY, _value(now, pid, host))


def release(store) -> None:
    """Drop the lock on a clean exit, so a restart is instant rather than
    waiting out the stale window."""
    store.set_setting(LOCK_KEY, "")


def holder(store) -> Optional[tuple[str, int, str]]:
    """``(beat_stamp, pid, host)`` of whoever holds the lock, or None."""
    raw = (store.get_setting(LOCK_KEY, "") or "").strip()
    if not raw:
        return None
    parts = raw.split("|")
    if len(parts) != 3:
        return None
    try:
        return parts[0], int(parts[1]), parts[2]
    except ValueError:
        return None


# --- what the operator wants -------------------------------------------------

def desired(store) -> str:
    """``"running"`` or ``"stopped"``. A database that has never been asked
    wants a scheduler: the ordinary case is that reports should go out."""
    value = (store.get_setting(DESIRED_KEY, "running") or "running").strip()
    return "stopped" if value == "stopped" else "running"


def request_stop(store, now: datetime) -> None:
    store.set_setting(DESIRED_KEY, "stopped")
    store.set_setting(STOP_REQUESTED_KEY, stamp(now))


def request_start(store) -> None:
    store.set_setting(DESIRED_KEY, "running")
    store.set_setting(STOP_REQUESTED_KEY, "")


@dataclass(frozen=True)
class SchedulerStatus:
    state: str                       # running|stopping|not_responding|stale|stopped
    pid: Optional[int] = None
    host: str = ""
    beat_age_secs: Optional[float] = None
    spawn_error: str = ""


def status(store, now: datetime) -> SchedulerStatus:
    """What the page and the sidebar badge report."""
    spawn_error = store.get_setting(SPAWN_ERROR_KEY, "") or ""
    held = holder(store)
    want = desired(store)

    beat = _unstamp(held[0]) if held else None
    age = (now - beat).total_seconds() if beat else None
    alive = age is not None and age < STALE_SECS

    if not alive:
        # Stale means "should be running and is not" — the one state the UI
        # reconciles by spawning.
        return SchedulerStatus(state="stopped" if want == "stopped" else "stale",
                               spawn_error=spawn_error)

    pid, host = held[1], held[2]
    if want == "running":
        return SchedulerStatus("running", pid, host, age, spawn_error)

    asked = _unstamp(store.get_setting(STOP_REQUESTED_KEY, "") or "")
    waited = (now - asked).total_seconds() if asked else 0.0
    state = "not_responding" if waited >= STOP_GRACE_SECS else "stopping"
    return SchedulerStatus(state, pid, host, age, spawn_error)


# --- starting one ------------------------------------------------------------

def spawn_detached(db_path: Optional[str] = None) -> int:
    """Start the scheduler as a process that outlives whoever started it.

    Detached, so closing the terminal that ran Streamlit does not take the
    scheduler with it. The database path is passed explicitly because the child
    must open the one the app is actually using, not the default relative path
    from whatever directory it inherits.
    """
    log_dir = os.environ.get("KDBMONITOR_LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log = open(os.path.join(log_dir, "scheduler.out"), "ab", buffering=0)

    env = dict(os.environ)
    env["KDBMONITOR_DB"] = db_path or env.get("KDBMONITOR_DB", "kdbmonitor.db")
    # Or the child would inherit the suppression and refuse to start itself.
    env.pop("KDBMONITOR_NO_AUTOSTART", None)

    kwargs: dict = {"stdout": log, "stderr": log, "close_fds": True, "env": env}
    if os.name == "nt":
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                   | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen([sys.executable, "-m", "kdbmonitor.scheduler"],
                            **kwargs)
    return proc.pid


def ensure_running(store, now: Optional[datetime] = None,
                   spawn: Optional[Callable[[], object]] = None) -> str:
    """Start a scheduler if one should be running and none is.

    Called once per Streamlit server process. The rule reads the desired state
    rather than only the lock, and that is what makes Stop actually stop: the
    naive version — spawn whenever nothing is running — would put the process
    back the moment after somebody stopped it.
    """
    now = now or datetime.now(timezone.utc)
    if os.environ.get("KDBMONITOR_NO_AUTOSTART"):
        return "autostart disabled"

    state = status(store, now).state
    if state != "stale":
        return state

    try:
        (spawn or spawn_detached)()
    except Exception as exc:            # noqa: BLE001 - reported, never raised
        # A scheduler that silently declines to start is the worst available
        # outcome: everything looks normal until a report does not arrive.
        store.set_setting(SPAWN_ERROR_KEY, str(exc))
        return "failed"

    store.set_setting(SPAWN_ERROR_KEY, "")
    return "starting"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_scheduler_lock.py -q`
Expected: PASS

If `test_the_second_one_does_not` fails, the upsert's `WHERE` is being applied to the insert rather than the update — check the statement is `ON CONFLICT(key) DO UPDATE SET ... WHERE ...` exactly.

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/scheduler/ tests/test_scheduler_lock.py
git commit -m "feat: whoever holds a fresh heartbeat is the scheduler"
```

---

## Task 7: The loop and the entry point

Knows about time and nothing about PDFs. Every dependency that touches the world — the clock, sleeping, running a job, sending a notice — is injected, so the whole task is tested without a clock, a server or a process.

**Files:**
- Create: `kdbmonitor/scheduler/loop.py`, `kdbmonitor/scheduler/__main__.py`, `run_scheduler.bat`
- Test: `tests/test_scheduler_loop.py`

**Interfaces:**
- Consumes: `supervisor.claim/heartbeat/release/desired/holder/BEAT_SECS`, `core.recurrence.runs_between`, `core.reportrun.run_job`, `core.reportrun.send_failure_notice`, `Storage` report accessors.
- Produces:
  - `TICK_SECS: int = 15`
  - `due_fire_time(job, now) -> Optional[datetime]`
  - `is_missed(job, fire, now) -> bool`
  - `run_due(store, mgr, now, run=None, notify=None, sleep=None, stopping=None) -> list[dict]`
  - `serve(store, mgr, now_fn=None, sleep=None, max_ticks=None) -> int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scheduler_loop.py`:

```python
"""The scheduler loop: what is due, what is too late, and what to do when a
run fails.

Nothing here sleeps, connects, or renders. The clock, the sleep, the run and
the notice are all injected — the loop's whole job is deciding, and decisions
are what these tests read.
"""
from datetime import datetime, timedelta, timezone

import pytest

from kdbmonitor.core.recurrence import Recurrence
from kdbmonitor.core.reportjob import ReportJob
from kdbmonitor.core.reportrun import DatasetFailure, RunOutcome
from kdbmonitor.core.storage import Storage
from kdbmonitor.scheduler import loop, supervisor as sup

# 2026-08-13 is a Thursday. 07:00 UTC is the fire time throughout.
FIRE = datetime(2026, 8, 13, 7, 0, tzinfo=timezone.utc)


@pytest.fixture()
def store() -> Storage:
    s = Storage(":memory:")
    s.init_db()
    return s


def _add(store, **kw) -> ReportJob:
    base = dict(id=None, name="Morning OMS report", dashboard_id=1,
                recipients=["desk@corp.local"],
                recurrence=Recurrence(times=["07:00"], tz="UTC"),
                last_fired_at="2026-08-13T06:00:00Z")
    base.update(kw)
    jid = store.add_report_job(ReportJob(**base))
    return store.get_report_job(jid)


class _Runner:
    """An injected run_job. Returns queued outcomes, then repeats the last."""
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes) or [RunOutcome(ok=True, pages=2,
                                                      recipients=1)]
        self.calls: list[datetime] = []

    def __call__(self, job, store, mgr, now, send=None):
        self.calls.append(now)
        return self.outcomes[min(len(self.calls), len(self.outcomes)) - 1]


class _Notifier:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, job, store, dashboard_name, due, attempts, outcome,
                 send=None):
        self.calls.append({"job": job.name, "due": due, "attempts": attempts,
                           "outcome": outcome})
        return True


def _run_due(store, **kw):
    kw.setdefault("run", _Runner())
    kw.setdefault("notify", _Notifier())
    kw.setdefault("sleep", lambda _s: None)
    return loop.run_due(store, mgr=None, now=kw.pop("now", FIRE), **kw)


# --- what is due -------------------------------------------------------------

def test_a_job_due_now_runs_once(store):
    job = _add(store)
    runner = _Runner()
    _run_due(store, run=runner)
    assert len(runner.calls) == 1
    assert store.latest_report_run(job.id)["status"] == "ok"


def test_the_same_minute_asked_twice_does_not_run_it_twice(store):
    """The tick is faster than a minute. A fire time already recorded is
    already done."""
    job = _add(store)
    runner = _Runner()
    _run_due(store, run=runner)
    _run_due(store, run=runner, now=FIRE + timedelta(seconds=20))
    assert len(runner.calls) == 1


def test_a_job_with_nothing_due_does_not_run(store):
    _add(store, last_fired_at="2026-08-13T07:00:00Z")
    runner = _Runner()
    _run_due(store, run=runner, now=FIRE + timedelta(minutes=5))
    assert runner.calls == []


def test_a_disabled_job_never_runs(store):
    job = _add(store)
    store.set_report_job_enabled(job.id, False)
    runner = _Runner()
    _run_due(store, run=runner)
    assert runner.calls == []


def test_the_fire_time_is_recorded_not_the_moment_it_finished(store):
    """A run stamped by its end would push last_fired_at past the next fire
    time, and that report would never be sent at all."""
    job = _add(store)
    _run_due(store, now=FIRE + timedelta(minutes=3))
    assert store.get_report_job(job.id).last_fired_at == "2026-08-13T07:00:00Z"


# --- the grace window --------------------------------------------------------

def test_a_run_inside_the_grace_window_still_goes_out(store):
    job = _add(store, grace_minutes=60)
    runner = _Runner()
    _run_due(store, run=runner, now=FIRE + timedelta(minutes=30))
    assert len(runner.calls) == 1


def test_one_outside_it_is_recorded_missed_and_not_sent(store):
    """The 07:00 report exists because somebody reads it at 07:05. The same
    PDF at 14:00 is worse than its absence."""
    job = _add(store, grace_minutes=60)
    runner = _Runner()
    _run_due(store, run=runner, now=FIRE + timedelta(hours=7))
    assert runner.calls == []
    assert store.latest_report_run(job.id)["status"] == "missed"


def test_a_missed_run_still_moves_the_job_on(store):
    """Or it would be re-evaluated as missed on every tick, forever."""
    job = _add(store, grace_minutes=60)
    _run_due(store, now=FIRE + timedelta(hours=7))
    assert store.get_report_job(job.id).last_fired_at == "2026-08-13T07:00:00Z"


def test_several_missed_fire_times_collapse_into_one_run(store):
    """A report is a snapshot of the present, not a queue of past ones: a
    scheduler down from Friday to Monday sends Monday's report, not three."""
    job = _add(store, last_fired_at="2026-08-10T06:00:00Z")
    runner = _Runner()
    _run_due(store, run=runner, now=FIRE + timedelta(minutes=10))
    assert len(runner.calls) == 1
    assert store.get_report_job(job.id).last_fired_at == "2026-08-13T07:00:00Z"


# --- retries -----------------------------------------------------------------

def test_a_failing_job_is_retried_to_its_limit(store):
    _add(store, retries=3)
    runner = _Runner(RunOutcome(ok=False, error="boom"))
    _run_due(store, run=runner)
    assert len(runner.calls) == 3


def test_one_that_fails_then_succeeds_stops_early_and_says_so(store):
    """Three attempts two minutes apart is what a KDB server still coming up
    at 07:00 needs."""
    job = _add(store, retries=3)
    runner = _Runner(RunOutcome(ok=False, error="boom"),
                     RunOutcome(ok=True, pages=2, recipients=1))
    _run_due(store, run=runner)
    assert len(runner.calls) == 2
    run = store.latest_report_run(job.id)
    assert (run["status"], run["attempts"]) == ("ok", 2)


def test_the_wait_between_attempts_is_taken_in_slices(store):
    """A Stop pressed during a four-minute retry cycle must not look like a
    hang, so the wait checks the desired state between one-second slices."""
    _add(store, retries=2, retry_delay_secs=5)
    slept = []
    _run_due(store, run=_Runner(RunOutcome(ok=False, error="boom")),
             sleep=slept.append)
    assert len(slept) == 5
    assert set(slept) == {1}


def test_a_stop_during_a_retry_wait_abandons_the_remaining_attempts(store):
    job = _add(store, retries=3, retry_delay_secs=5)
    runner = _Runner(RunOutcome(ok=False, error="boom"))
    _run_due(store, run=runner, stopping=lambda: True)
    assert len(runner.calls) == 1
    run = store.latest_report_run(job.id)
    assert (run["status"], run["attempts"]) == ("failed", 1)


# --- failure is recorded and the admin is told -------------------------------

def test_an_exhausted_job_records_the_failure_with_every_broken_dataset(store):
    job = _add(store, retries=2)
    outcome = RunOutcome(ok=False, error="2 of 5 datasets failed", failures=[
        DatasetFailure("open_orders", "connection refused", "select from o"),
        DatasetFailure("fills", "`type error", "select from f")])
    _run_due(store, run=_Runner(outcome))

    run = store.latest_report_run(job.id)
    assert run["status"] == "failed"
    assert run["error"] == "2 of 5 datasets failed"
    assert [d["name"] for d in run["detail"]] == ["open_orders", "fills"]
    assert run["detail"][0]["qsql"] == "select from o"


def test_the_admin_is_told_once_for_a_run_of_three_attempts(store):
    _add(store, retries=3)
    notifier = _Notifier()
    _run_due(store, run=_Runner(RunOutcome(ok=False, error="boom")),
             notify=notifier)
    assert len(notifier.calls) == 1
    assert notifier.calls[0]["attempts"] == 3


def test_a_run_that_worked_tells_nobody(store):
    _add(store)
    notifier = _Notifier()
    _run_due(store, notify=notifier)
    assert notifier.calls == []


def test_a_missed_run_is_not_a_failure_to_mail_about(store):
    _add(store, grace_minutes=60)
    notifier = _Notifier()
    _run_due(store, notify=notifier, now=FIRE + timedelta(hours=7))
    assert notifier.calls == []


# --- serving -----------------------------------------------------------------

def test_serve_refuses_to_start_when_somebody_else_holds_the_lock(store):
    sup.claim(store, FIRE, pid=999, host="OTHERBOX")
    assert loop.serve(store, mgr=None, now_fn=lambda: FIRE,
                      sleep=lambda _s: None, max_ticks=1) == 0
    assert sup.holder(store)[1] == 999          # not taken


def test_serve_beats_while_it_runs_and_releases_when_it_stops(store):
    ticks = iter([FIRE + timedelta(seconds=n) for n in range(0, 600, 15)])
    loop.serve(store, mgr=None, now_fn=lambda: next(ticks),
               sleep=lambda _s: None, max_ticks=3)
    assert sup.holder(store) is None            # released on the way out


def test_serve_stops_when_the_desired_state_says_so(store):
    sup.request_stop(store, FIRE)
    ticks = iter([FIRE + timedelta(seconds=n) for n in range(0, 600, 15)])
    loop.serve(store, mgr=None, now_fn=lambda: next(ticks),
               sleep=lambda _s: None, max_ticks=50)
    assert sup.holder(store) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_scheduler_loop.py -q`
Expected: FAIL — `ImportError: cannot import name 'loop'`

- [ ] **Step 3: Implement the loop**

Create `kdbmonitor/scheduler/loop.py`:

```python
"""The scheduler process: what is due, what is too late, and what to retry.

Knows about time and nothing about PDFs. Everything that touches the world —
the clock, sleeping, running a job, telling the admin — is a parameter, which
is what lets the whole of this be tested without a clock, a server or a
process, and what keeps the decisions legible: this file is a list of them.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from kdbmonitor.core.recurrence import runs_between
from kdbmonitor.core.reportjob import ReportJob
from kdbmonitor.core.reportrun import RunOutcome, run_job, send_failure_notice
from kdbmonitor.scheduler import supervisor

log = logging.getLogger("kdbmonitor.scheduler")

TICK_SECS = 15

# The wait between retries, and the tick itself, are taken in slices this long
# so a Stop lands in about a second rather than at the end of a four-minute
# retry cycle. Anything longer and a Stop button looks like a hang.
_SLICE_SECS = 1


def _parse(text: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def due_fire_time(job: ReportJob, now: datetime) -> Optional[datetime]:
    """The single fire time to run for ``job``, or None.

    The most recent of what was missed, never all of them. A report is a
    snapshot of the present, not a queue of past ones — a scheduler down from
    Friday to Monday should send Monday's report, not three of them.
    """
    since = _parse(job.last_fired_at) or now
    fires = runs_between(job.recurrence, since, now)
    return fires[-1] if fires else None


def is_missed(job: ReportJob, fire: datetime, now: datetime) -> bool:
    """Whether ``fire`` is too old to be worth sending.

    The 07:00 report exists because somebody reads it at 07:05. The same PDF
    arriving at 14:00, headed with a period the reader has to work out for
    themselves, is worse than its absence — and the absence is visible on the
    page, which is where a missed run belongs.
    """
    return (now - fire) > timedelta(minutes=max(job.grace_minutes, 0))


def _wait(seconds: int, sleep: Callable[[int], None],
          stopping: Callable[[], bool]) -> bool:
    """Sleep in slices, checking for a stop. False when asked to stop."""
    remaining = max(int(seconds), 0)
    while remaining > 0:
        if stopping():
            return False
        sleep(_SLICE_SECS)
        remaining -= _SLICE_SECS
    return not stopping()


def _attempt(job: ReportJob, store, mgr, now: datetime, fire: datetime,
             run: Callable, notify: Callable, sleep: Callable,
             stopping: Callable[[], bool]) -> dict:
    """Run one job, retrying, and write down what happened."""
    started = now
    attempts = 0
    outcome = RunOutcome(ok=False, error="never ran")

    for attempt in range(1, max(job.retries, 1) + 1):
        attempts = attempt
        try:
            outcome = run(job, store, mgr, now)
        except Exception as exc:            # noqa: BLE001
            # A crash in one job must not take the scheduler down: the other
            # jobs on this tick have done nothing wrong.
            log.exception("job %r raised", job.name)
            outcome = RunOutcome(ok=False, error=f"{type(exc).__name__}: {exc}")
        if outcome.ok:
            break
        if attempt < max(job.retries, 1):
            if not _wait(job.retry_delay_secs, sleep, stopping):
                break                        # a Stop abandons what is left

    status = "ok" if outcome.ok else "failed"
    store.record_report_run(
        job.id, fire, started, datetime.now(timezone.utc), status,
        attempts=attempts,
        pages=outcome.pages or None,
        recipients=outcome.recipients or None,
        error=None if outcome.ok else outcome.error,
        detail=[f.as_dict() for f in outcome.failures] or None)
    store.set_report_job_fired(job.id, fire)

    if not outcome.ok:
        dashboard = store.get_dashboard(job.dashboard_id)
        name = dashboard.name if dashboard else f"dashboard {job.dashboard_id}"
        # Once per failed run, not once per attempt.
        notify(job, store, name, fire, attempts, outcome)

    log.info("%s: %s (fire %s, %d attempt(s))", job.name, status,
             fire.isoformat(), attempts)
    return {"job_id": job.id, "status": status, "attempts": attempts}


def run_due(store, mgr, now: datetime, run: Optional[Callable] = None,
            notify: Optional[Callable] = None,
            sleep: Optional[Callable] = None,
            stopping: Optional[Callable[[], bool]] = None) -> list[dict]:
    """Run everything due at ``now``. Returns one record per job touched.

    Jobs run one at a time, in name order. At the scale this is built for — a
    handful of reports a day — that is simpler than concurrency and easier to
    reason about when something is late.
    """
    run = run or run_job
    notify = notify or send_failure_notice
    sleep = sleep or __import__("time").sleep
    stopping = stopping or (lambda: False)

    done: list[dict] = []
    for job in store.list_report_jobs():
        if not job.enabled:
            continue
        fire = due_fire_time(job, now)
        if fire is None:
            continue

        if is_missed(job, fire, now):
            store.record_report_run(
                job.id, fire, now, now, "missed",
                error=f"the scheduler was not running at {fire:%Y-%m-%d %H:%M} UTC")
            # Moved on regardless, or it is re-evaluated as missed forever.
            store.set_report_job_fired(job.id, fire)
            done.append({"job_id": job.id, "status": "missed", "attempts": 0})
            continue

        done.append(_attempt(job, store, mgr, now, fire, run, notify, sleep,
                             stopping))
        if stopping():
            break
    return done


def serve(store, mgr, now_fn: Optional[Callable[[], datetime]] = None,
          sleep: Optional[Callable] = None,
          max_ticks: Optional[int] = None) -> int:
    """Hold the lock and run due jobs until asked to stop. Returns an exit code.

    Losing the claim is a normal outcome, not an error: it means a scheduler is
    already running, which is exactly what the lock is for.
    """
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    sleep = sleep or __import__("time").sleep

    if not supervisor.claim(store, now_fn()):
        held = supervisor.holder(store)
        where = f"pid {held[1]} on {held[2]}" if held else "another process"
        log.info("a scheduler is already running (%s) — exiting", where)
        return 0

    def stopping() -> bool:
        return supervisor.desired(store) == "stopped"

    ticks = 0
    try:
        while max_ticks is None or ticks < max_ticks:
            ticks += 1
            now = now_fn()
            supervisor.heartbeat(store, now)
            if stopping():
                log.info("asked to stop")
                break
            run_due(store, mgr, now, sleep=sleep, stopping=stopping)
            if not _wait(TICK_SECS, sleep, stopping):
                log.info("asked to stop")
                break
    finally:
        # Released rather than left to go stale, so a restart is instant.
        supervisor.release(store)
    return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_scheduler_loop.py -q`
Expected: PASS

If `test_the_wait_between_attempts_is_taken_in_slices` reports 10 sleeps rather than 5, the wait is running after the final attempt — it must only run *between* attempts.

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/scheduler/loop.py tests/test_scheduler_loop.py
git commit -m "feat: the most recent fire time, once, and only if it is still worth sending"
```

- [ ] **Step 6: Write the entry point**

Create `kdbmonitor/scheduler/__main__.py`:

```python
"""python -m kdbmonitor.scheduler

The process that sends the reports. Started by the app when one should be
running and none is, or by hand with run_scheduler.bat — neither path is
privileged, because the lock in the database is what decides who runs.
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler


def _configure_logging() -> str:
    """Console and a rotating file. The file is not optional: a detached
    process has no console to write to, and without it the answer to "why did
    nothing arrive" is unavailable."""
    log_dir = os.environ.get("KDBMONITOR_LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "scheduler.log")

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    rotating = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5,
                                   encoding="utf-8")
    rotating.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)

    root = logging.getLogger("kdbmonitor")
    root.setLevel(logging.INFO)
    root.handlers = [rotating, console]
    return path


def main() -> int:
    # Before anything imports pyplot. core.dashpdf imports it lazily, which is
    # what makes setting the backend here sufficient — and necessary, since a
    # detached process has no display to draw on.
    import matplotlib
    matplotlib.use("Agg")

    from kdbmonitor.core.client import ConnectionManager
    from kdbmonitor.core.storage import Storage
    from kdbmonitor.scheduler import loop

    path = _configure_logging()
    db = os.environ.get("KDBMONITOR_DB", "kdbmonitor.db")

    log = logging.getLogger("kdbmonitor.scheduler")
    log.info("starting: db=%s log=%s pid=%s", db, path, os.getpid())

    store = Storage(db)
    store.init_db()
    try:
        return loop.serve(store, ConnectionManager())
    except KeyboardInterrupt:
        # Ctrl-C is how somebody stops one they started by hand. serve()
        # releases the lock in its finally, so the next start is instant.
        log.info("interrupted")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Create `run_scheduler.bat` at the repo root:

```bat
@echo off
REM Start the KdbMonitor report scheduler in its own window.
REM
REM Not required: the app starts one automatically when a scheduler should be
REM running and none is. This is for running it by hand, or for dropping in
REM Startup on a machine that does not keep the app open. Either way the lock
REM in the database decides who actually runs, so starting a second one is
REM safe -- it will find the lock held and exit.
cd /d "%~dp0"
if "%KDBMONITOR_DB%"=="" set KDBMONITOR_DB=kdbmonitor.db
python -m kdbmonitor.scheduler
pause
```

- [ ] **Step 7: Check the entry point starts and stops cleanly**

Run:

```bash
python -c "from kdbmonitor.scheduler.__main__ import main; print('imports fine')"
```
Expected: `imports fine` — this proves the `Agg` import ordering does not raise.

Then a real short run against a scratch database:

```bash
KDBMONITOR_DB=/tmp/sched_check.db KDBMONITOR_LOG_DIR=/tmp/sched_logs python -c "
import os
from datetime import datetime, timezone
from kdbmonitor.core.storage import Storage
from kdbmonitor.scheduler import loop, supervisor
s = Storage(os.environ['KDBMONITOR_DB']); s.init_db()
print('exit', loop.serve(s, None, sleep=lambda _n: None, max_ticks=2))
print('lock after exit:', supervisor.holder(s))
"
```
Expected: `exit 0` and `lock after exit: None`.

On PowerShell, set the variables with `$env:KDBMONITOR_DB="..."` on their own lines first.

- [ ] **Step 8: Run the full suite and commit**

Run: `python -m pytest -q`
Expected: PASS

```bash
git add kdbmonitor/scheduler/__main__.py run_scheduler.bat
git commit -m "feat: a process with no display, a log it can actually write to, and a way to start it"
```

---

## Task 8: Admin — the admin address list and a test email

**Files:**
- Modify: `kdbmonitor/ui/admin.py` (the SMTP block, ~lines 404–418)
- Test: `tests/test_admin_notifications.py`

**Interfaces:**
- Consumes: `core.notifiers.parse_address_list`, `core.notifiers.send_report_email`, `core.reportrun.ADMIN_EMAIL_KEY`.
- Produces: `admin_problems(text: str) -> list[str]` in `kdbmonitor/ui/admin.py` — pure, so the validation is testable without Streamlit.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_notifications.py`:

```python
"""Where failure notices go, and refusing to save an address that isn't one."""
import pytest

from kdbmonitor.core.reportjob import ReportJob
from kdbmonitor.core.reportrun import ADMIN_EMAIL_KEY
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui.admin import admin_problems


def test_a_good_list_has_no_problems():
    assert admin_problems("kdb-support@corp.local; alice@corp.local") == []


def test_an_empty_list_is_allowed_here():
    """Not every install has jobs yet. The warning about an empty list belongs
    beside the jobs that need it, not on a field somebody has not reached."""
    assert admin_problems("") == []


def test_an_entry_without_an_at_sign_is_named():
    problems = admin_problems("alice@corp.local, nope, bob@corp.local")
    assert len(problems) == 1
    assert "nope" in problems[0]


def test_the_setting_key_is_the_one_reportrun_reads():
    """Two names for one setting is how a notice silently goes nowhere."""
    store = Storage(":memory:")
    store.init_db()
    store.set_setting(ADMIN_EMAIL_KEY, "admin@corp.local")
    assert store.get_setting("scheduler_admin_email") == "admin@corp.local"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_admin_notifications.py -q`
Expected: FAIL — `ImportError: cannot import name 'admin_problems'`

- [ ] **Step 3: Implement**

In `kdbmonitor/ui/admin.py`, add to the imports:

```python
from kdbmonitor.core.notifiers import parse_address_list, send_report_email
from kdbmonitor.core.reportrun import ADMIN_EMAIL_KEY
```

Add near the other module-level helpers, after `_fmt_ts`:

```python
def admin_problems(text: str) -> list[str]:
    """Problems with the admin-notification list; empty when it is usable.

    Pure, so the rule can be tested without building a page. An empty list is
    fine here — the warning about having no admin while report jobs exist
    belongs beside those jobs, not on a field nobody has reached yet.
    """
    return [f"{address!r} is not an email address."
            for address in parse_address_list(text) if "@" not in address]
```

Replace the SMTP block (currently `st.markdown("**Email (SMTP)**")` through the `st.toast`) with:

```python
    # ---- SMTP ------------------------------------------------------------- #
    st.markdown("**Email (SMTP)**")
    with form_area().container(border=True):
        st.caption("Used by alerts that select the email channel, and by "
                   "scheduled reports.")
        s = st.columns([2, 1, 2], vertical_alignment="bottom")
        host = s[0].text_input("SMTP host", value=store.get_setting("smtp_host", ""))
        port = int(s[1].number_input("Port", 1, 65535,
                                     int(store.get_setting("smtp_port", "25"))))
        sender = s[2].text_input("From address", value=store.get_setting("smtp_sender", ""))

        b = st.columns([1, 1, 3])
        if b[0].button("Save SMTP settings", icon=":material/save:"):
            store.set_setting("smtp_host", host)
            store.set_setting("smtp_port", str(port))
            store.set_setting("smtp_sender", sender)
            st.toast("Saved SMTP settings", icon=":material/check:")

        # Verifying a relay by waiting for tomorrow's 07:00 is not a reasonable
        # way to find out that the port is wrong.
        test_to = st.text_input("Send a test email to",
                                key="smtp_test_to", placeholder="you@corp.local")
        if b[1].button("Send test email", icon=":material/outgoing_mail:",
                       disabled=not (host and test_to)):
            try:
                send_report_email(host, port, sender, parse_address_list(test_to),
                                  "KdbMonitor test email",
                                  "If you are reading this, SMTP is configured.")
                st.success(f"Sent to {test_to}.")
            except Exception as exc:      # noqa: BLE001 - the message is the point
                st.error(f"Could not send: {exc}")

    # ---- Admin notifications --------------------------------------------- #
    st.markdown("**Admin notifications**")
    with form_area().container(border=True):
        st.caption("Where a failed scheduled report is reported. The people "
                   "expecting the report are never told it failed — they get "
                   "the report or they get nothing.")
        admins_text = st.text_input(
            "Admin email address(es)",
            value=store.get_setting(ADMIN_EMAIL_KEY, "") or "",
            placeholder="kdb-support@corp.local; alice@corp.local",
            help="Comma or semicolon separated.")

        parsed = parse_address_list(admins_text)
        problems = admin_problems(admins_text)
        if problems:
            for problem in problems:
                st.warning(problem)
        elif parsed:
            # Printed back as it will actually be used, so a stray separator is
            # visible before it costs a notice rather than after.
            st.caption(f"→ {len(parsed)} address(es): {', '.join(parsed)}")

        if st.button("Save admin notifications", icon=":material/save:",
                     disabled=bool(problems)):
            store.set_setting(ADMIN_EMAIL_KEY, admins_text)
            st.toast("Saved admin notifications", icon=":material/check:")

        if not parsed and store.list_report_jobs():
            st.warning("There are scheduled reports but no admin address — a "
                       "failed run will be silent by mail.",
                       icon=":material/warning:")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_admin_notifications.py tests/test_admin_render.py -q`
Expected: PASS — including the existing Admin render test, which walks this page.

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/ui/admin.py tests/test_admin_notifications.py
git commit -m "feat: where a failed report is reported, and a way to test the relay"
```

---

## Task 9: The Schedules page

**Files:**
- Create: `kdbmonitor/ui/schedules.py`
- Test: `tests/test_schedules_page.py`

**Interfaces:**
- Consumes: everything from Tasks 1–7.
- Produces:
  - `render(store, mgr) -> None`
  - `status_line(st_: SchedulerStatus) -> str` — pure, the badge text
  - `schedulable_dashboards(store) -> list` — kdb-sourced only
  - `job_summary(store, job) -> str` — the one-line last-run summary
  - `BADGE: dict[str, str]` — state → sidebar badge markdown

- [ ] **Step 1: Write the failing tests for the pure parts**

Create `tests/test_schedules_page.py`:

```python
"""The Schedules page: what it offers, and what it says about a run."""
from datetime import datetime, timedelta, timezone

import pytest
from streamlit.testing.v1 import AppTest

from kdbmonitor.core.dashboard_models import Dashboard, Dataset, Row, Widget
from kdbmonitor.core.recurrence import Recurrence
from kdbmonitor.core.reportjob import ReportJob
from kdbmonitor.core.storage import Storage
from kdbmonitor.scheduler import supervisor as sup
from kdbmonitor.ui.schedules import (
    BADGE, job_summary, schedulable_dashboards, status_line,
)

NOW = datetime(2026, 8, 13, 7, 0, tzinfo=timezone.utc)


@pytest.fixture()
def store() -> Storage:
    s = Storage(":memory:")
    s.init_db()
    return s


def _dashboard(name="OMS Overview", source="kdb") -> Dashboard:
    return Dashboard(
        id=None, name=name, description="", refresh_secs=0, source=source,
        datasets=[Dataset(name="orders", env="orders", table="target")],
        rows=[Row(height_in=2.0, widgets=[
            Widget(type="table", dataset="orders", title="Orders", width=1.0)])])


def _job(store, dashboard_id, **kw) -> ReportJob:
    base = dict(id=None, name="Morning OMS report", dashboard_id=dashboard_id,
                recipients=["desk@corp.local"],
                recurrence=Recurrence(times=["07:00"], days=[0, 1, 2, 3, 4],
                                      tz="Europe/London"))
    base.update(kw)
    return store.get_report_job(store.add_report_job(ReportJob(**base)))


# --- what can be scheduled ---------------------------------------------------

def test_a_file_dashboard_is_not_offered(store):
    """There is no browser at 07:00 to take an upload from."""
    store.add_dashboard(_dashboard("From KDB", source="kdb"))
    store.add_dashboard(_dashboard("From a spreadsheet", source="file"))
    assert [d.name for d in schedulable_dashboards(store)] == ["From KDB"]


def test_with_no_kdb_dashboards_the_list_is_empty_rather_than_wrong(store):
    store.add_dashboard(_dashboard("From a spreadsheet", source="file"))
    assert schedulable_dashboards(store) == []


# --- the status line ---------------------------------------------------------

def test_a_running_scheduler_says_where_it_is_and_how_fresh(store):
    sup.claim(store, NOW, pid=8412, host="THISBOX")
    line = status_line(sup.status(store, NOW + timedelta(seconds=4)))
    assert "running" in line
    assert "8412" in line and "THISBOX" in line
    assert "4s" in line


def test_a_stopped_scheduler_says_so_without_a_pid(store):
    sup.request_stop(store, NOW)
    line = status_line(sup.status(store, NOW))
    assert "stopped" in line.lower()
    assert "pid" not in line.lower()


def test_a_wedged_one_names_the_process_to_go_and_find(store):
    sup.claim(store, NOW, pid=8412, host="THISBOX")
    sup.request_stop(store, NOW)
    later = NOW + timedelta(seconds=sup.STOP_GRACE_SECS + 1)
    sup.heartbeat(store, later, pid=8412, host="THISBOX")
    line = status_line(sup.status(store, later))
    assert "not responding" in line.lower()
    assert "8412" in line


def test_every_state_has_a_badge():
    for state in ("running", "stopping", "not_responding", "stale", "stopped"):
        assert state in BADGE


# --- what a job's last run says ---------------------------------------------

def test_a_job_that_has_never_run_says_so(store):
    did = store.add_dashboard(_dashboard())
    assert "never" in job_summary(store, _job(store, did)).lower()


def test_a_good_run_reports_pages_and_recipients(store):
    did = store.add_dashboard(_dashboard())
    job = _job(store, did)
    store.record_report_run(job.id, NOW, NOW, NOW, "ok", pages=2, recipients=3)
    summary = job_summary(store, job)
    assert "2 page" in summary and "3 recipient" in summary


def test_a_failed_run_reports_the_datasets_and_the_attempts(store):
    did = store.add_dashboard(_dashboard())
    job = _job(store, did)
    store.record_report_run(
        job.id, NOW, NOW, NOW, "failed", attempts=3,
        error="2 of 5 datasets failed",
        detail=[{"name": "a", "error": "boom", "qsql": "q"},
                {"name": "b", "error": "bang", "qsql": "q"}])
    summary = job_summary(store, job)
    assert "2 dataset" in summary
    assert "3 attempt" in summary


def test_a_missed_run_says_it_was_missed(store):
    did = store.add_dashboard(_dashboard())
    job = _job(store, did)
    store.record_report_run(job.id, NOW, NOW, NOW, "missed")
    assert "missed" in job_summary(store, job).lower()


# --- the page renders --------------------------------------------------------
#
# Driven the way tests/test_all_pages_render.py drives every other page: a
# generated script that calls render() directly, rather than app.py. It is
# faster, it isolates the page from the shell, and it cannot start a scheduler.

def _script(db_path: str) -> str:
    return f'''
import streamlit as st
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import schedules

store = Storage(r"{db_path}")
store.init_db()
schedules.render(store, ConnectionManager())
'''


def _render(tmp_path, populate=None) -> AppTest:
    db = str(tmp_path / "k.db")
    s = Storage(db)
    s.init_db()
    if populate:
        populate(s)
    at = AppTest.from_string(_script(db), default_timeout=30)
    return at.run()


def test_the_page_renders_with_nothing_configured(tmp_path):
    assert not _render(tmp_path).exception


def test_the_page_renders_with_a_job_and_a_failed_run(tmp_path):
    def populate(s):
        did = s.add_dashboard(_dashboard())
        job = _job(s, did)
        s.record_report_run(job.id, NOW, NOW, NOW, "failed", attempts=3,
                            error="1 of 1 datasets failed",
                            detail=[{"name": "orders", "error": "refused",
                                     "qsql": "select from orders"}])

    assert not _render(tmp_path, populate).exception


def test_the_page_renders_when_only_file_dashboards_exist(tmp_path):
    """The state that has no dashboard to offer must still draw a page."""
    def populate(s):
        s.add_dashboard(_dashboard("From a spreadsheet", source="file"))

    assert not _render(tmp_path, populate).exception


def test_the_page_renders_while_a_scheduler_holds_the_lock(tmp_path):
    def populate(s):
        sup.claim(s, datetime.now(timezone.utc), pid=1, host="ELSEWHERE")

    assert not _render(tmp_path, populate).exception
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_schedules_page.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kdbmonitor.ui.schedules'`

- [ ] **Step 3: Implement the page**

Create `kdbmonitor/ui/schedules.py`:

```python
"""Scheduled reports: the jobs, and the process that runs them.

The process controls sit at the top because that is the question somebody
arrives with — a report did not come, is the thing running? The jobs are
below, one expander each, and a failed run opens to exactly what the admin was
mailed: an admin told at 07:00 and an admin who comes looking at 09:00 must not
be given two different accounts of one failure.
"""
from __future__ import annotations

import os
import socket
import time
from datetime import datetime, timezone

import streamlit as st

from kdbmonitor.core import recurrence as rec_mod
from kdbmonitor.core import timectx
from kdbmonitor.core.notifiers import parse_address_list
from kdbmonitor.core.recurrence import Recurrence
from kdbmonitor.core.reportjob import (
    DEFAULT_SUBJECT, ReportJob, validate_job,
)
from kdbmonitor.core.reportrun import run_job
from kdbmonitor.core.schedule import DAY_NAMES
from kdbmonitor.core.zones import iana_names
from kdbmonitor.scheduler import supervisor as sup
from kdbmonitor.ui.common import form_area, pluralize

BADGE = {
    "running": ":green-badge[:material/schedule_send: Scheduler running]",
    "stopping": ":orange-badge[:material/schedule_send: Scheduler stopping]",
    "not_responding": ":red-badge[:material/error: Scheduler not responding]",
    "stale": ":orange-badge[:material/schedule_send: Scheduler starting]",
    "stopped": ":gray-badge[:material/schedule_send: Scheduler stopped]",
}

_STATUS_ICON = {"ok": "✓", "failed": "✗", "missed": "—"}


def schedulable_dashboards(store) -> list:
    """The dashboards a job may point at.

    A file dashboard reads from a spreadsheet somebody uploads in the browser.
    At 07:00 there is no browser and no upload, so offering one would only
    accept a job that mails an empty page every morning.
    """
    return [d for d in store.list_dashboards()
            if getattr(d, "source", "kdb") != "file"]


def status_line(st_: sup.SchedulerStatus) -> str:
    """One line saying what the process is doing, and where to find it."""
    if st_.state == "stopped":
        return "Stopped — no scheduler is running, and none is wanted."
    if st_.state == "stale":
        return "Starting — no scheduler is running yet."
    where = f"pid {st_.pid} on {st_.host}"
    age = f"beat {int(st_.beat_age_secs or 0)}s ago"
    if st_.state == "running":
        return f"Running — {where}, {age}."
    if st_.state == "stopping":
        return f"Stopping — waiting for {where} to finish and exit."
    return (f"Not responding — {where} was asked to stop and has not. "
            "It may be mid-report, or wedged.")


def job_summary(store, job: ReportJob) -> str:
    """The last run, in one line, for the collapsed job header."""
    run = store.latest_report_run(job.id)
    if not run:
        return "never run"

    icon = _STATUS_ICON.get(run["status"], "?")
    when = (run["fired_for"] or "").replace("T", " ").rstrip("Z")
    if run["status"] == "ok":
        return (f"{icon} {when} · {pluralize(run['pages'] or 0, 'page')} · "
                f"{pluralize(run['recipients'] or 0, 'recipient')}")
    if run["status"] == "missed":
        return f"{icon} {when} · missed — the scheduler was not running"
    broken = len(run.get("detail") or [])
    detail = (pluralize(broken, "dataset") + " failed" if broken
              else (run["error"] or "failed"))
    return f"{icon} {when} · {detail} · {pluralize(run['attempts'], 'attempt')}"


# --- the process ------------------------------------------------------------

def _render_process(store) -> None:
    now = datetime.now(timezone.utc)
    state = sup.status(store, now)

    with form_area().container(border=True):
        st.markdown(BADGE.get(state.state, BADGE["stopped"]))
        st.caption(status_line(state))
        if state.spawn_error:
            st.error(f"The last attempt to start it failed: {state.spawn_error}")

        cols = st.columns([1, 1, 1, 3])
        if state.state in ("running", "stopping", "not_responding"):
            if cols[0].button("Stop", icon=":material/stop:"):
                sup.request_stop(store, datetime.now(timezone.utc))
                st.rerun()
            if cols[1].button("Restart", icon=":material/restart_alt:"):
                _restart(store)
                st.rerun()
        else:
            if cols[0].button("Start", icon=":material/play_arrow:",
                              type="primary"):
                sup.request_start(store)
                sup.ensure_running(store, datetime.now(timezone.utc))
                st.rerun()

        # Only where a graceful stop has already failed, and only on the
        # machine holding the lock: taskkill cannot reach another box, and
        # offering a button that cannot work is a way of lying about what
        # happened.
        if state.state == "not_responding" and _is_this_machine(state.host):
            if cols[2].button("Force stop", icon=":material/dangerous:",
                              type="secondary"):
                _force_stop(state.pid)
                st.rerun()

        log_dir = os.environ.get("KDBMONITOR_LOG_DIR", "logs")
        st.caption(f"Log: {os.path.join(log_dir, 'scheduler.log')}")


def _is_this_machine(host: str) -> bool:
    return (host or "").lower() == socket.gethostname().lower()


def _force_stop(pid: int) -> None:
    """Last resort, after a cooperative stop has already been given its grace."""
    import signal
    import subprocess
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           check=False, capture_output=True)
        else:
            os.kill(pid, signal.SIGKILL)
    except Exception as exc:                # noqa: BLE001
        st.error(f"Could not stop pid {pid}: {exc}")


def _restart(store) -> None:
    """Stop, wait for the lock to clear, start. What you need after changing
    the SMTP host or pulling new code."""
    sup.request_stop(store, datetime.now(timezone.utc))
    for _ in range(sup.STOP_GRACE_SECS * 2):
        if sup.holder(store) is None:
            break
        time.sleep(0.5)
    sup.request_start(store)
    sup.ensure_running(store, datetime.now(timezone.utc))
```

- [ ] **Step 4: Add the job list and editor**

Append to `kdbmonitor/ui/schedules.py`:

```python
# --- the jobs ---------------------------------------------------------------

_PERIOD_INHERIT = "(the dashboard's own)"


def _period_options() -> list[str]:
    return [_PERIOD_INHERIT, "Real-time"] + [timectx.PRESET_LABELS[p]
                                             for p in timectx.PRESETS]


def _period_spec(label: str):
    """The chosen label as a stored spec, or None to inherit."""
    if label == _PERIOD_INHERIT:
        return None
    if label == "Real-time":
        return {"mode": "realtime"}
    for name in timectx.PRESETS:
        if timectx.PRESET_LABELS[name] == label:
            return {"mode": "historical",
                    "range": {"kind": "preset", "name": name}}
    return None


def _period_label(spec) -> str:
    if not spec:
        return _PERIOD_INHERIT
    if spec.get("mode") != "historical":
        return "Real-time"
    name = (spec.get("range") or {}).get("name", "")
    return timectx.PRESET_LABELS.get(name, _PERIOD_INHERIT)


def _render_editor(store, mgr, job: ReportJob, dashboards: list,
                   key: str) -> None:
    names = [d.name for d in dashboards]
    index = next((i for i, d in enumerate(dashboards) if d.id == job.dashboard_id), 0)

    job.name = st.text_input("Name", value=job.name, key=f"{key}_name")
    chosen_name = st.selectbox("Dashboard", names, index=index,
                               key=f"{key}_dash") if names else None
    if chosen_name:
        job.dashboard_id = next(d.id for d in dashboards if d.name == chosen_name)

    # --- when ---
    st.markdown("**When**")
    advanced = st.toggle("Advanced (cron)", value=job.recurrence.mode == "cron",
                         key=f"{key}_adv")
    job.recurrence.mode = "cron" if advanced else "times"
    if advanced:
        job.recurrence.cron = st.text_input(
            "Cron expression", value=job.recurrence.cron or "0 7 * * 1-5",
            key=f"{key}_cron",
            help="minute hour day-of-month month day-of-week. Sunday is 0 or 7.")
    else:
        times = st.text_input("Run at", value=", ".join(job.recurrence.times),
                              key=f"{key}_times",
                              help="HH:MM, comma separated — e.g. 07:00, 16:35")
        job.recurrence.times = [t.strip() for t in times.split(",") if t.strip()]
        picked = st.multiselect(
            "On days", options=list(range(7)),
            default=job.recurrence.days or list(range(5)),
            format_func=lambda d: DAY_NAMES[d], key=f"{key}_days")
        job.recurrence.days = sorted(picked)

    zone_options = ["local"] + iana_names()
    zone_index = zone_options.index(job.recurrence.tz) \
        if job.recurrence.tz in zone_options else 0
    job.recurrence.tz = st.selectbox("Timezone", zone_options, index=zone_index,
                                     key=f"{key}_tz")

    # --- what ---
    st.markdown("**What**")
    options = _period_options()
    current = _period_label(job.period)
    job.period = _period_spec(st.selectbox(
        "Period", options, index=options.index(current) if current in options else 0,
        key=f"{key}_period",
        help="A relative period is resolved against the day the job runs, so "
             "'Yesterday' means yesterday every morning."))

    dashboard = store.get_dashboard(job.dashboard_id)
    for parameter in (dashboard.parameters if dashboard else []):
        value = st.text_input(
            f"Parameter · {parameter.name}",
            value=job.parameters.get(parameter.name, ""),
            placeholder=f"(default: {parameter.default})",
            key=f"{key}_p_{parameter.name}")
        if value.strip():
            job.parameters[parameter.name] = value.strip()
        else:
            job.parameters.pop(parameter.name, None)

    # --- to whom ---
    st.markdown("**To whom**")
    job.recipients = parse_address_list(st.text_input(
        "Recipients", value=", ".join(job.recipients), key=f"{key}_to",
        help="Comma or semicolon separated."))
    job.subject = st.text_input("Subject", value=job.subject or DEFAULT_SUBJECT,
                                key=f"{key}_subject",
                                help="{dashboard} {date} {time} {job}")
    job.body = st.text_area("Body", value=job.body, key=f"{key}_body",
                            placeholder="(a default is used when this is empty)")

    problems = validate_job(job)
    for problem in problems:
        st.warning(problem)

    if not problems and job.recurrence:
        upcoming = rec_mod.next_run(job.recurrence, datetime.now(timezone.utc))
        st.caption(f"{rec_mod.describe(job.recurrence)} · next run "
                   f"{upcoming:%a %d %b %Y, %H:%M} UTC" if upcoming
                   else f"{rec_mod.describe(job.recurrence)} · no next run")

    actions = st.columns([1, 1, 1, 2])
    if actions[0].button("Save", icon=":material/save:", key=f"{key}_save",
                         type="primary", disabled=bool(problems)):
        if job.id is None:
            store.add_report_job(job)
        else:
            store.update_report_job(job)
        st.session_state.pop("_new_report_job", None)
        st.rerun()

    # Without this the first honest test of a new job is tomorrow at 07:00,
    # and it either works or produces a failure nobody sees until they look.
    if job.id is not None and actions[1].button(
            "Send now", icon=":material/send:", key=f"{key}_now"):
        with st.spinner("Running…"):
            outcome = run_job(job, store, mgr, datetime.now(timezone.utc))
        if outcome.ok:
            st.success(f"Sent {pluralize(outcome.pages, 'page')} to "
                       f"{pluralize(outcome.recipients, 'recipient')}.")
        else:
            st.error(outcome.error or "failed")
            for failure in outcome.failures:
                st.code(f"{failure.name}: {failure.error}\n{failure.qsql}")

    if job.id is not None and actions[2].button(
            "Delete", icon=":material/delete:", key=f"{key}_del"):
        store.delete_report_job(job.id)
        st.rerun()


def _render_history(store, job: ReportJob) -> None:
    runs = store.list_report_runs(job.id, limit=20)
    if not runs:
        st.caption("No runs yet.")
        return
    for run in runs:
        icon = _STATUS_ICON.get(run["status"], "?")
        when = (run["fired_for"] or "").replace("T", " ").rstrip("Z")
        st.markdown(f"{icon} **{when}** · {run['status']} · "
                    f"{pluralize(run['attempts'], 'attempt')}")
        if run["error"]:
            st.caption(run["error"])
        # The same detail the admin was mailed, from the same source: two
        # accounts of one failure is how an hour goes missing.
        for failure in run.get("detail") or []:
            st.code(f"{failure['name']}: {failure['error']}\n"
                    f"{failure.get('qsql', '')}".strip())


def render(store, mgr) -> None:
    st.title(":material/schedule_send: Schedules")
    st.caption("Dashboards run on a clock and mailed as PDF.")

    _render_process(store)

    dashboards = schedulable_dashboards(store)
    if not dashboards:
        st.info("No dashboard can be scheduled yet. A scheduled report runs "
                "its queries with no browser open, so a dashboard reading an "
                "uploaded file cannot be one.", icon=":material/info:")
        return

    st.markdown("**Jobs**")
    jobs = store.list_report_jobs()
    for job in jobs:
        mark = "✓" if job.enabled else "✗"
        with st.expander(f"{mark}  {job.name} — "
                         f"{rec_mod.describe(job.recurrence)}"):
            st.caption(f"Last run: {job_summary(store, job)}")
            enabled = st.toggle("Enabled", value=job.enabled,
                                key=f"job{job.id}_on")
            if enabled != job.enabled:
                store.set_report_job_enabled(job.id, enabled)
                st.rerun()
            editor, history = st.tabs(["Edit", "History"])
            with editor:
                _render_editor(store, mgr, job, dashboards, f"job{job.id}")
            with history:
                _render_history(store, job)

    if st.button("New schedule", icon=":material/add:"):
        st.session_state["_new_report_job"] = True
    if st.session_state.get("_new_report_job"):
        with st.container(border=True):
            st.markdown("**New schedule**")
            fresh = ReportJob(id=None, name="", dashboard_id=dashboards[0].id,
                              recurrence=Recurrence())
            _render_editor(store, mgr, fresh, dashboards, "jobnew")
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_schedules_page.py -q`
Expected: PASS

Two things to check if they fail:
- `pluralize` — confirm the signature at `kdbmonitor/ui/common.py:128`. It is `pluralize(n, word)` returning `"2 pages"`. If it returns only the suffix, wrap it.
- The render tests use `AppTest.from_string`. If the installed Streamlit lacks it, fall back to writing the script to `tmp_path` and using `AppTest.from_file`, as `tests/test_static_render.py` does.

- [ ] **Step 6: Commit**

```bash
git add kdbmonitor/ui/schedules.py tests/test_schedules_page.py
git commit -m "feat: a page for the jobs, and for the process that runs them"
```

---

## Task 10: Wiring — navigation, autostart, sidebar badge

**Files:**
- Modify: `app.py`, `tests/test_all_pages_render.py`
- Test: the render tests in `tests/test_schedules_page.py` from Task 9 now run

**Interfaces:**
- Consumes: `ui.schedules.render`, `ui.schedules.BADGE`, `scheduler.supervisor.ensure_running`, `scheduler.supervisor.status`.

- [ ] **Step 1: Add the page, the autostart and the badge**

In `app.py`, extend the `kdbmonitor.ui` import to include `schedules`, and add after `get_manager()`:

```python
@st.cache_resource
def start_scheduler():
    """Start the report scheduler if one should be running and none is.

    In a cache_resource so it happens once per Streamlit server process —
    which is exactly "when the UI launches", not on every rerun and not once
    per browser session. The spawned process is detached and outlives this
    one; the lock in the database is what stops a second copy running, so
    calling this from several sessions is safe.
    """
    from kdbmonitor.scheduler import supervisor
    return supervisor.ensure_running(get_store())


start_scheduler()
```

In the sidebar block, after the existing monitoring badge lines, add:

```python
    from datetime import datetime, timezone

    from kdbmonitor.scheduler import supervisor as _sup
    from kdbmonitor.ui.schedules import BADGE as _SCHED_BADGE

    _sched = _sup.status(store, datetime.now(timezone.utc))
    st.markdown(_SCHED_BADGE.get(_sched.state, _SCHED_BADGE["stopped"]))
```

Add the page and register it in the navigation:

```python
def schedules_page():
    schedules.render(store, mgr)


schedules_pg = st.Page(schedules_page, title="Schedules", url_path="schedules",
                       icon=":material/schedule_send:")
```

and change the `st.navigation` call to:

```python
nav = st.navigation([monitor_pg, builder_pg, dashboards_pg, reports_pg,
                     schedules_pg, admin_pg, result_pg])
```

- [ ] **Step 2: Stop any test from ever spawning a scheduler**

`tests/test_app_smoke.py` runs the real `app.py` through `AppTest`, so from Step 1 onward it would start a detached scheduler against whatever database the developer's shell points at — on every test run, in CI as well. Close that globally.

Create `tests/conftest.py`:

```python
"""Test-wide guards.

Nothing in the suite may start a real scheduler. `app.py` starts one when the
UI launches, and tests/test_app_smoke.py launches the UI — without this, a test
run leaves detached processes behind, pointed at whatever KDBMONITOR_DB the
developer's shell happens to name.
"""
import pytest


@pytest.fixture(autouse=True)
def no_scheduler_spawn(monkeypatch):
    monkeypatch.setenv("KDBMONITOR_NO_AUTOSTART", "1")
```

Tests that want to exercise `ensure_running` still can — `tests/test_scheduler_lock.py` passes its own `spawn` callable and asserts on that, never on a real process, except for `test_the_environment_can_forbid_autostart`, which sets the variable itself and is unaffected by having it already set.

- [ ] **Step 3: Add the page to the existing render sweep**

In `tests/test_all_pages_render.py`, add `schedules` to `PAGES`:

```python
PAGES = ["monitor", "builder", "dashboards", "reports", "schedules", "admin",
         "result"]
```

That file drives pages through its own `_script()` helper rather than through `app.py`, so also extend the import line and the dispatch chain inside it. Change:

```python
from kdbmonitor.ui import admin, builder, dashboards, monitor, reports, result
```

to:

```python
from kdbmonitor.ui import (
    admin, builder, dashboards, monitor, reports, result, schedules,
)
```

and add a branch before the final `else`:

```python
elif page == "schedules":
    schedules.render(store, mgr)
```

- [ ] **Step 4: Run the render tests**

Run: `python -m pytest tests/test_schedules_page.py tests/test_all_pages_render.py tests/test_app_smoke.py -q`
Expected: PASS

If a test hangs or leaves a process behind, `ensure_running` is reading the store before it checks the environment variable — the check must come first.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Drive it once for real**

```bash
python -m pytest -q && streamlit run app.py
```

Check by hand, in order:
1. Admin → Email (SMTP) has a **Send test email** button, and **Admin notifications** echoes a semicolon-separated list back as a parsed one.
2. Schedules shows `Scheduler running` with a pid within a few seconds of the app starting, and the sidebar badge agrees.
3. Create a job against a demo dashboard, press **Send now**, and confirm the outcome message names pages and recipients.
4. Press **Stop**. The badge goes grey within a couple of seconds and *stays* grey across a page reload — this is the check that the desired state is beating autostart.
5. Press **Start**, then close Streamlit entirely and confirm in Task Manager that `python.exe` is still running.

- [ ] **Step 7: Commit**

```bash
git add app.py tests/conftest.py tests/test_all_pages_render.py
git commit -m "feat: the app starts the scheduler it needs, and says whether it is there"
```

---

## Self-review notes

Checked against the spec:

- §4 job model, §4.1 recurrence, §4.2 cron → Tasks 1, 2
- §5 running a job, §5.1 the failure rule, §5.2 file dashboards → Task 5
- §6 mail, §6.1 admin notices and the address list → Tasks 4, 5, 8
- §7 the process, §7.1 grace, §7.2 retries, §7.3 logging → Task 7
- §8 the lock, §9 start/stop/restart, §9.1 spawning → Tasks 6, 9, 10
- §10 storage, §10.1 WAL → Task 3
- §11 the page → Tasks 9, 10
- §12 testing → distributed across every task; each test named in the spec has a home

**Known deviations from the spec, both deliberate:**

1. **`RunOutcome.pages` costs a second pagination.** `dashboard_to_pdf_bytes` does not return its page count, so `run_job` calls `page_count` after it. That re-paginates and re-formats every table. For a handful of reports a day it is seconds and no extra queries; if it ever matters, the fix is for `dashpdf` to return the count rather than for the scheduler to guess it.
2. **`supervisor.status` has five states, not the three named in §8.** `stopping` and `not_responding` were needed to make §9's Force-stop rule expressible; they are refinements of "the lock is fresh but the desired state is stopped", not new concepts.

