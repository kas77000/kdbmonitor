"""An alert's active hours.

A check that only means something between 17:45 and 18:00 reports false
positives for the other 23 hours of the day, so the answer is to stop running
it outside its window rather than to filter what it says afterwards. Times are
written in the watcher's own zone; the engine works in UTC — every test here
crosses that boundary on purpose.
"""
from datetime import datetime, timezone

import pytest

from kdbmonitor.core.models import Schedule, Window
from kdbmonitor.core.schedule import (
    current_close, is_active, next_open, parse_hhmm, schedule_summary, validate,
)


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def _sched(*spans, days=(), tz="UTC") -> Schedule:
    return Schedule(mode="windows",
                    windows=[Window(start=s, end=e) for s, e in spans],
                    days=list(days), tz=tz)


# --- no schedule at all ------------------------------------------------------

def test_an_alert_without_a_schedule_always_runs():
    assert is_active(Schedule(), _utc("2026-08-01 03:00")) is True
    assert is_active(None, _utc("2026-08-01 03:00")) is True


def test_windows_turned_on_with_none_set_still_runs():
    """A setup half-finished is a mistake, not an instruction to go quiet —
    the Builder complains about it rather than the alert disappearing."""
    empty = Schedule(mode="windows", windows=[])
    assert is_active(empty, _utc("2026-08-01 03:00")) is True
    assert validate(empty)


# --- a single window ---------------------------------------------------------

def test_inside_the_window_it_runs():
    assert is_active(_sched(("16:30", "17:00")), _utc("2026-08-03 16:45"))


def test_a_minute_before_it_opens_it_does_not():
    assert not is_active(_sched(("16:30", "17:00")), _utc("2026-08-03 16:29"))


def test_the_end_is_exclusive_so_two_windows_never_overlap():
    sched = _sched(("16:30", "17:00"))
    assert is_active(sched, _utc("2026-08-03 16:59:59"))
    assert not is_active(sched, _utc("2026-08-03 17:00"))


def test_a_single_moment_is_a_window_one_minute_wide():
    """'alert me at 16:30' — what the Builder's 'At a moment' button writes."""
    sched = _sched(("16:30", "16:31"))
    assert is_active(sched, _utc("2026-08-03 16:30:30"))
    assert not is_active(sched, _utc("2026-08-03 16:31:30"))


def test_several_windows_add_up():
    sched = _sched(("16:30", "16:31"), ("17:45", "18:00"))
    assert is_active(sched, _utc("2026-08-03 16:30"))
    assert is_active(sched, _utc("2026-08-03 17:50"))
    assert not is_active(sched, _utc("2026-08-03 17:00"))


# --- midnight ----------------------------------------------------------------

def test_a_window_that_crosses_midnight_is_one_window():
    sched = _sched(("22:00", "02:00"))
    assert is_active(sched, _utc("2026-08-03 23:30"))
    assert is_active(sched, _utc("2026-08-04 01:30"))
    assert not is_active(sched, _utc("2026-08-04 03:00"))


def test_a_crossing_window_belongs_to_the_day_it_starts_on():
    """Friday 22:00-02:00 runs into Saturday morning; it is not cut at
    midnight, and Saturday's own 22:00 does not open because Saturday is not
    one of the days."""
    friday_nights = _sched(("22:00", "02:00"), days=(4,))
    assert is_active(friday_nights, _utc("2026-08-07 23:00"))     # Friday
    assert is_active(friday_nights, _utc("2026-08-08 01:00"))     # into Saturday
    assert not is_active(friday_nights, _utc("2026-08-08 23:00"))  # Saturday night


# --- days --------------------------------------------------------------------

def test_weekdays_only_stays_shut_at_the_weekend():
    weekdays = _sched(("09:00", "17:00"), days=(0, 1, 2, 3, 4))
    assert is_active(weekdays, _utc("2026-08-07 12:00"))          # Friday
    assert not is_active(weekdays, _utc("2026-08-08 12:00"))      # Saturday


def test_no_days_named_means_every_day():
    every = _sched(("09:00", "17:00"))
    assert is_active(every, _utc("2026-08-08 12:00"))             # Saturday


# --- timezones ---------------------------------------------------------------

def test_the_window_is_read_in_its_own_zone():
    """16:30 in Mumbai is 11:00 UTC, and the engine only ever holds UTC."""
    sched = _sched(("16:30", "17:00"), tz="Asia/Kolkata")
    assert is_active(sched, _utc("2026-08-03 11:00"))
    assert not is_active(sched, _utc("2026-08-03 16:30"))


def test_a_windows_zone_name_works_as_well_as_an_iana_one():
    assert is_active(_sched(("16:30", "17:00"), tz="India Standard Time"),
                     _utc("2026-08-03 11:00"))


def test_a_bare_utc_offset_works_too():
    assert is_active(_sched(("16:30", "17:00"), tz="UTC+05:30"),
                     _utc("2026-08-03 11:00"))


def test_daylight_saving_is_applied_on_the_day_not_assumed():
    """09:00 in London is 08:00 UTC in August and 09:00 UTC in January. A
    fixed offset taken once would have the window an hour out for half the
    year."""
    london = _sched(("09:00", "10:00"), tz="Europe/London")
    assert is_active(london, _utc("2026-08-03 08:30"))            # BST
    assert not is_active(london, _utc("2026-08-03 09:30"))
    assert is_active(london, _utc("2026-01-05 09:30"))            # GMT
    assert not is_active(london, _utc("2026-01-05 08:30"))


# --- what the Monitor prints -------------------------------------------------

def test_next_open_is_the_next_time_it_wakes_up():
    sched = _sched(("16:30", "17:00"))
    assert next_open(sched, _utc("2026-08-03 12:00")) == _utc("2026-08-03 16:30")


def test_next_open_rolls_to_tomorrow_once_today_is_over():
    sched = _sched(("16:30", "17:00"))
    assert next_open(sched, _utc("2026-08-03 18:00")) == _utc("2026-08-04 16:30")


def test_next_open_skips_the_days_the_alert_does_not_run():
    weekdays = _sched(("09:00", "17:00"), days=(0, 1, 2, 3, 4))
    # Saturday afternoon -> Monday morning.
    assert next_open(weekdays, _utc("2026-08-08 12:00")) == _utc("2026-08-10 09:00")


def test_an_always_on_alert_has_no_next_open():
    assert next_open(Schedule(), _utc("2026-08-03 12:00")) is None


def test_current_close_is_the_end_of_the_window_now_running():
    sched = _sched(("16:30", "17:00"))
    assert current_close(sched, _utc("2026-08-03 16:45")) == _utc("2026-08-03 17:00")
    assert current_close(sched, _utc("2026-08-03 18:00")) is None


def test_the_summary_says_when_in_words():
    sched = _sched(("16:30", "16:31"), ("17:45", "18:00"),
                   days=(0, 1, 2, 3, 4), tz="Europe/London")
    assert schedule_summary(sched) == (
        "16:30-16:31 & 17:45-18:00 Mon-Fri (Europe/London)")
    assert schedule_summary(Schedule()) == "always"


def test_the_summary_names_a_weekend_and_a_scattered_week():
    assert "weekends" in schedule_summary(_sched(("09:00", "10:00"), days=(5, 6)))
    assert "Mon, Wed" in schedule_summary(_sched(("09:00", "10:00"), days=(0, 2)))


# --- validation --------------------------------------------------------------

def test_parse_hhmm_takes_a_clock_and_nothing_else():
    assert parse_hhmm("16:30").hour == 16
    assert parse_hhmm("9:05").minute == 5
    for bad in ("", "1630", "24:00", "16:60", "half four", None):
        with pytest.raises(ValueError):
            parse_hhmm(bad)


def test_a_zero_width_window_is_reported_rather_than_never_firing():
    problems = validate(_sched(("16:30", "16:30")))
    assert problems and "16:30" in problems[0]


def test_a_time_that_is_not_a_time_is_reported():
    assert validate(_sched(("half four", "17:00")))


def test_an_unknown_timezone_is_reported():
    assert validate(_sched(("16:30", "17:00"), tz="Middle Earth"))


def test_a_good_schedule_has_nothing_to_report():
    assert validate(_sched(("16:30", "17:00"), days=(0, 4),
                           tz="Asia/Kolkata")) == []


# --- what the engine does with a closed window -------------------------------

def _stored_alert(store, schedule):
    from kdbmonitor.core.models import (
        Alert, Channels, RearmPolicy, Step, TriggerCondition)
    alert = Alert(id=None, name="EOD check", enabled=True, poll_interval_secs=30,
                  steps=[Step(server="s", table="t", mode="form")],
                  trigger=TriggerCondition(type="has_rows"),
                  channels=Channels(), rearm=RearmPolicy(), schedule=schedule)
    alert.id = store.add_alert(alert)
    return alert


@pytest.fixture
def store(tmp_path):
    from kdbmonitor.core.storage import Storage
    s = Storage(str(tmp_path / "sched.db"))
    s.init_db()
    return s


def test_a_closed_window_parks_the_alert_rather_than_leaving_it_triggered(store):
    """A trigger still standing at 18:00 would be the 'previous' state at
    tomorrow's open, and a transition re-arm only fires on a rising edge — so
    the alert would go quiet for good. Parking it disarms it."""
    from kdbmonitor.ui.engine import _park_off_hours

    alert = _stored_alert(store, _sched(("17:45", "18:00")))
    store.record_run(alert.id, ts="2026-08-03T17:50:00+00:00", status="triggered",
                     triggered=True, notified=True, row_count=4, message="m")

    _park_off_hours(store, alert, store.latest_run(alert.id),
                    _utc("2026-08-03 18:00"))
    latest = store.latest_run(alert.id)
    assert latest["status"] == "off_hours" and not latest["triggered"]


def test_parking_is_recorded_once_and_not_on_every_tick(store):
    from kdbmonitor.ui.engine import _park_off_hours

    alert = _stored_alert(store, _sched(("17:45", "18:00")))
    for minute in range(5):
        now = _utc(f"2026-08-03 18:0{minute}")
        _park_off_hours(store, alert, store.latest_run(alert.id), now)
    assert len(store.list_runs(alert.id)) == 1


def test_an_alert_that_never_ran_is_parked_too(store):
    """Otherwise it reads as Pending all night, which is a check that has not
    happened yet rather than one that is deliberately not happening."""
    from kdbmonitor.ui.engine import _park_off_hours

    alert = _stored_alert(store, _sched(("17:45", "18:00")))
    _park_off_hours(store, alert, None, _utc("2026-08-03 09:00"))
    assert store.latest_run(alert.id)["status"] == "off_hours"


def test_the_monitor_renders_a_parked_alert(store, tmp_path):
    """'off_hours' is a status the status table has to know about; it used to
    be a KeyError waiting for the first scheduled alert."""
    from streamlit.testing.v1 import AppTest

    _stored_alert(store, _sched(("17:45", "18:00"), tz="Europe/London"))
    db = str(tmp_path / "sched.db").replace("\\", "\\\\")
    at = AppTest.from_string(f'''
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import monitor
store = Storage(r"{db}")
store.init_db()
monitor.render(store, ConnectionManager())
''', default_timeout=60).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    printed = " ".join(str(e.value) for e in list(at.markdown) + list(at.caption))
    assert "Off-hours" in printed
    assert "17:45-18:00" in printed and "Europe/London" in printed
