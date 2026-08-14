"""The SMTP settings typed in Admin are the ones a scheduled report is sent with.

Two halves that must agree on three setting names, written by a page and read
by a process that never share memory. A rename on either side would not fail a
test, would not fail a run, and would simply stop the reports arriving — so the
contract is pinned here, end to end: type it into the page, then watch where
the socket is opened.
"""
from datetime import datetime, timezone

import smtplib

import pytest
from streamlit.testing.v1 import AppTest

from kdbmonitor.core.dashboard_models import Dashboard, Dataset, Row, Widget
from kdbmonitor.core.mock import demo_connection_specs
from kdbmonitor.core.reportjob import ReportJob
from kdbmonitor.core.reportrun import RunOutcome, run_job, send_failure_notice
from kdbmonitor.core.storage import Storage
from kdbmonitor.core.client import ConnectionManager

NOW = datetime(2026, 8, 14, 7, 0, tzinfo=timezone.utc)

ADMIN_SCRIPT = '''
import streamlit as st
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import admin

store = Storage(r"{db}")
store.init_db()
admin.render(store, ConnectionManager())
'''


class _Recorder:
    """Stands in for smtplib.SMTP and remembers where it was pointed."""
    opened: list[tuple[str, int]] = []

    def __init__(self, host, port):
        type(self).opened.append((host, port))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def send_message(self, msg, from_addr=None, to_addrs=None):
        type(self).sent = {"from": from_addr, "to": to_addrs, "msg": msg}


@pytest.fixture()
def recorder(monkeypatch):
    _Recorder.opened = []
    _Recorder.sent = {}
    monkeypatch.setattr(smtplib, "SMTP", _Recorder)
    return _Recorder


def _save_smtp_in_admin(db: str, host: str, port: int, sender: str) -> None:
    """Type the settings into the real Admin page and press Save.

    Addressed by key, not by label: the page carries two boxes labelled "Port"
    — a KDB server's and the relay's — and picking by label silently drove the
    wrong one.
    """
    at = AppTest.from_string(ADMIN_SCRIPT.format(db=db), default_timeout=60).run()

    at.text_input(key="smtp_host").set_value(host)
    at.number_input(key="smtp_port").set_value(port)
    at.text_input(key="smtp_sender").set_value(sender)
    at.run()

    save = next(b for b in at.button if b.label == "Save SMTP settings")
    save.click()
    at.run()
    assert not at.exception


def test_the_two_port_boxes_on_the_admin_page_are_distinguishable(tmp_path):
    """A KDB server's port and the relay's sit on one page under one label.
    Keys are what stop a caller reaching the wrong one."""
    db = str(tmp_path / "k.db")
    Storage(db).init_db()
    at = AppTest.from_string(ADMIN_SCRIPT.format(db=db), default_timeout=60).run()

    ports = [e for e in at.number_input if e.label == "Port"]
    assert len(ports) == 2
    assert {e.key for e in ports} == {"ac_port", "smtp_port"}


def _dashboard() -> Dashboard:
    return Dashboard(
        id=None, name="OMS Overview", description="", refresh_secs=0,
        datasets=[Dataset(name="orders", env="orders", table="target")],
        rows=[Row(height_in=2.0, widgets=[
            Widget(type="table", dataset="orders", title="Orders", width=1.0)])])


def test_what_admin_saves_is_what_the_report_is_sent_with(tmp_path, recorder):
    db = str(tmp_path / "k.db")
    store = Storage(db)
    store.init_db()
    for spec in demo_connection_specs():
        store.add_connection(spec)

    # Deliberately not the default port: a hardcoded 25 would pass otherwise.
    _save_smtp_in_admin(db, "smtp.corp.local", 2525, "kdbmonitor@corp.local")

    store = Storage(db)                     # reopened, as the scheduler does
    assert store.get_setting("smtp_host") == "smtp.corp.local"
    assert store.get_setting("smtp_port") == "2525"
    assert store.get_setting("smtp_sender") == "kdbmonitor@corp.local"

    did = store.add_dashboard(_dashboard())
    job = ReportJob(id=1, name="Morning OMS report", dashboard_id=did,
                    recipients=["desk@corp.local"])

    outcome = run_job(job, store, ConnectionManager(), NOW)

    assert outcome.ok, outcome.error
    assert recorder.opened == [("smtp.corp.local", 2525)]
    assert recorder.sent["from"] == "kdbmonitor@corp.local"
    assert recorder.sent["to"] == ["desk@corp.local"]

    # and the PDF really is attached
    attachments = list(recorder.sent["msg"].iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_content_type() == "application/pdf"
    assert attachments[0].get_payload(decode=True)[:4] == b"%PDF"


def test_the_admin_failure_notice_uses_the_same_relay(tmp_path, recorder):
    """One relay for both, or a failing report fails silently in a second way."""
    db = str(tmp_path / "k.db")
    Storage(db).init_db()
    _save_smtp_in_admin(db, "smtp.corp.local", 2525, "kdbmonitor@corp.local")

    store = Storage(db)
    store.set_setting("scheduler_admin_email", "kdb-support@corp.local")
    job = ReportJob(id=1, name="J", dashboard_id=1,
                    recipients=["desk@corp.local"])

    assert send_failure_notice(job, store, "D", NOW, 3,
                               RunOutcome(ok=False, error="boom"))
    assert recorder.opened == [("smtp.corp.local", 2525)]
    assert recorder.sent["to"] == ["kdb-support@corp.local"]
