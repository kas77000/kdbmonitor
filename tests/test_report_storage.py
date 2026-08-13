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
        when = datetime(2026, 8, 13, minute // 60, minute % 60,
                        tzinfo=timezone.utc)
        store.record_report_run(jid, when, when, when, "ok")
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
