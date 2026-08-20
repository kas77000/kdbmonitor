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
    now = kw.pop("now", FIRE)
    return loop.run_due(store, None, now, **kw)


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
    _add(store)
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
    _add(store, grace_minutes=60)
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


def test_a_job_that_raises_does_not_take_the_scheduler_down(store):
    """The other jobs on this tick have done nothing wrong."""
    job = _add(store, retries=1)

    def explode(*a, **k):
        raise RuntimeError("pykx exploded")

    _run_due(store, run=explode)
    run = store.latest_report_run(job.id)
    assert run["status"] == "failed"
    assert "pykx exploded" in run["error"]


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
    assert loop.serve(store, None, now_fn=lambda: FIRE,
                      sleep=lambda _s: None, max_ticks=1) == 0
    assert sup.holder(store)[1] == 999          # not taken


def test_serve_beats_while_it_runs_and_releases_when_it_stops(store):
    ticks = iter([FIRE + timedelta(seconds=n) for n in range(0, 600, 15)])
    loop.serve(store, None, now_fn=lambda: next(ticks),
               sleep=lambda _s: None, max_ticks=3)
    assert sup.holder(store) is None            # released on the way out


def test_serve_stops_when_the_desired_state_says_so(store):
    sup.request_stop(store, FIRE)
    ticks = iter([FIRE + timedelta(seconds=n) for n in range(0, 600, 15)])
    loop.serve(store, None, now_fn=lambda: next(ticks),
               sleep=lambda _s: None, max_ticks=50)
    assert sup.holder(store) is None


# --- the report archive ------------------------------------------------------

def test_a_run_that_filed_a_copy_records_where_it_went(store):
    """The path is how the History tab answers 'where did Tuesday's go'."""
    job = _add(store, archive=True)
    archived = r"\nas\reports\oms\oms_20260813_070000.pdf"
    runner = _Runner(RunOutcome(ok=True, pages=2, recipients=1,
                                archived_path=archived))

    loop.run_due(store, None, FIRE, run=runner, notify=_Notifier(),
                 sleep=lambda s: None)

    assert store.latest_report_run(job.id)["archived_path"] == archived


def test_a_run_that_filed_nothing_records_no_path(store):
    job = _add(store)
    loop.run_due(store, None, FIRE, run=_Runner(), notify=_Notifier(),
                 sleep=lambda s: None)
    assert store.latest_report_run(job.id)["archived_path"] is None
