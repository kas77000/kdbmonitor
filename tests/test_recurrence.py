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


def test_the_zone_is_resolved_once_per_walk_not_once_per_minute():
    """zones.to_iana scans the whole tz database. Resolving it inside the
    per-minute predicate made walking a single day take nineteen seconds —
    a page that hangs and a tick that never finishes."""
    from kdbmonitor.core import recurrence as rec_mod

    calls = []
    real = rec_mod.zones.to_iana

    def counting(name):
        calls.append(name)
        return real(name)

    rec_mod.zones.to_iana = counting
    try:
        # A whole day of minutes in a named zone: ~1,260 predicate evaluations.
        next_run(Recurrence(times=["07:00"], tz="Europe/London"),
                 _at(2026, 8, 13, 9, 0))
    finally:
        rec_mod.zones.to_iana = real

    assert len(calls) <= 2


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
