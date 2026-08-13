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
    assert "running" in line.lower()
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


def test_the_new_schedule_editor_renders(tmp_path):
    """It draws only behind a session flag, so nothing else reaches it — and
    it is the one editor built from a job that has never been saved."""
    db = str(tmp_path / "k.db")
    s = Storage(db)
    s.init_db()
    s.add_dashboard(_dashboard())
    at = AppTest.from_string(_script(db), default_timeout=30)
    at.session_state["_new_report_job"] = True
    at.run()
    assert not at.exception


def test_the_new_editor_and_an_existing_one_do_not_collide(tmp_path):
    """Two editors on one page: every widget key must still be unique."""
    db = str(tmp_path / "k.db")
    s = Storage(db)
    s.init_db()
    did = s.add_dashboard(_dashboard())
    _job(s, did)
    at = AppTest.from_string(_script(db), default_timeout=30)
    at.session_state["_new_report_job"] = True
    at.run()
    assert not at.exception


def test_the_page_renders_a_cron_job(tmp_path):
    def populate(s):
        did = s.add_dashboard(_dashboard())
        _job(s, did, recurrence=Recurrence(mode="cron", cron="0 7 * * 1-5",
                                           tz="UTC"))

    assert not _render(tmp_path, populate).exception
