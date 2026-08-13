"""Running one scheduled report, end to end, against the demo mock.

The PDF is the one the Download button produces — these tests exist to hold
the wiring around it: which period, which parameter values, and the rule that
any dataset failing means nothing is sent.
"""
from datetime import datetime, timezone

from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.dashboard_models import (
    Dashboard, Dataset, Parameter, Row, Widget,
)
from kdbmonitor.core.mock import demo_connection_specs
from kdbmonitor.core.reportjob import ReportJob
from kdbmonitor.core.reportrun import (
    DatasetFailure, RunOutcome, chosen_values, collect_failures,
    failure_notice, run_job, send_failure_notice,
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

def test_a_period_override_reaches_the_query_instead_of_the_dashboard_own(
        monkeypatch):
    """The dashboard is saved real-time; the job asks for yesterday. What runs
    must be yesterday, or a morning report of overnight activity is empty."""
    store, mgr, did = _env()
    seen = {}

    import kdbmonitor.core.reportrun as reportrun

    def spy(dashboard, store_, mgr_, today, **kw):
        seen["time_context"] = dashboard.time_context
        return {}

    monkeypatch.setattr(reportrun, "run_datasets", spy)
    run_job(_job(did, period={"mode": "historical",
                              "range": {"kind": "preset", "name": "yesterday"}}),
            store, mgr, NOW, send=_Sent())

    assert seen["time_context"]["mode"] == "historical"
    assert seen["time_context"]["range"]["name"] == "yesterday"


def test_without_an_override_the_dashboard_own_period_is_used(monkeypatch):
    store, mgr, did = _env(_dashboard(
        periods="historical",
        time_context={"mode": "historical",
                      "range": {"kind": "preset", "name": "mtd"}}))
    seen = {}

    import kdbmonitor.core.reportrun as reportrun

    def spy(dashboard, store_, mgr_, today, **kw):
        seen["time_context"] = dashboard.time_context
        return {}

    monkeypatch.setattr(reportrun, "run_datasets", spy)
    run_job(_job(did), store, mgr, NOW, send=_Sent())
    assert seen["time_context"]["range"]["name"] == "mtd"


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


def test_one_broken_of_several_still_sends_nothing(monkeypatch):
    store, mgr, did = _env()
    send = _Sent()

    import kdbmonitor.core.reportrun as reportrun
    monkeypatch.setattr(reportrun, "run_datasets", lambda *a, **k: {
        "a": _Result("a", df=object()),
        "b": _Result("b", error="boom", qsql="q"),
        "c": _Result("c", df=object())})

    outcome = run_job(_job(did), store, mgr, NOW, send=send)
    assert outcome.ok is False
    assert send.calls == []
    assert "1 of 3" in outcome.error


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
