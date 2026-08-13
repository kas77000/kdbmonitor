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
