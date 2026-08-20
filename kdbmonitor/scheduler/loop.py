"""The scheduler process: what is due, what is too late, and what to retry.

Knows about time and nothing about PDFs. Everything that touches the world —
the clock, sleeping, running a job, telling the admin — is a parameter, which
is what lets the whole of this be tested without a clock, a server or a
process, and what keeps the decisions legible: this file is a list of them.
"""
from __future__ import annotations

import logging
import time as _time
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
    limit = max(job.retries, 1)

    for attempt in range(1, limit + 1):
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
        if attempt < limit:
            if not _wait(job.retry_delay_secs, sleep, stopping):
                break                        # a Stop abandons what is left

    status = "ok" if outcome.ok else "failed"
    store.record_report_run(
        job.id, fire, started, datetime.now(timezone.utc), status,
        attempts=attempts,
        pages=outcome.pages or None,
        recipients=outcome.recipients or None,
        error=None if outcome.ok else outcome.error,
        detail=[f.as_dict() for f in outcome.failures] or None,
        archived_path=outcome.archived_path)
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
    sleep = sleep or _time.sleep
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
                error=f"the scheduler was not running at "
                      f"{fire:%Y-%m-%d %H:%M} UTC")
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
    sleep = sleep or _time.sleep

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
