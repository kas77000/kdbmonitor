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
from kdbmonitor.core.reportjob import DEFAULT_SUBJECT, ReportJob, validate_job
from kdbmonitor.core.reportrun import run_job
from kdbmonitor.core.schedule import DAY_NAMES
from kdbmonitor.core.zones import iana_names
from kdbmonitor.scheduler import supervisor as sup
from kdbmonitor.ui import layout as L
from kdbmonitor.ui.common import pluralize

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


def status_line(state: sup.SchedulerStatus) -> str:
    """One line saying what the process is doing, and where to find it."""
    if state.state == "stopped":
        return "Stopped — no scheduler is running, and none is wanted."
    if state.state == "stale":
        return "Starting — no scheduler is running yet."
    where = f"pid {state.pid} on {state.host}"
    age = f"beat {int(state.beat_age_secs or 0)}s ago"
    if state.state == "running":
        return f"Running — {where}, {age}."
    if state.state == "stopping":
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


def _render_process(store) -> None:
    now = datetime.now(timezone.utc)
    state = sup.status(store, now)

    # Full width, like every other card on every other page. It used to be the
    # only narrow block on this page and sat directly above a full-width job
    # list, so the two ended 390px apart with nothing to explain the step.
    with L.card():
        st.markdown(BADGE.get(state.state, BADGE["stopped"]))
        st.caption(status_line(state))
        if state.spawn_error:
            st.error(f"The last attempt to start it failed: {state.spawn_error}")

        cols = L.grid(L.BUTTON, L.BUTTON, L.BUTTON)
        if state.state in ("running", "stopping", "not_responding"):
            if cols[0].button("Stop", icon=":material/stop:", key="sched_stop"):
                sup.request_stop(store, datetime.now(timezone.utc))
                st.rerun()
            if cols[1].button("Restart", icon=":material/restart_alt:",
                              key="sched_restart"):
                _restart(store)
                st.rerun()
        else:
            if cols[0].button("Start", icon=":material/play_arrow:",
                              type="primary", key="sched_start"):
                sup.request_start(store)
                sup.ensure_running(store, datetime.now(timezone.utc))
                st.rerun()

        # Only where a graceful stop has already failed, and only on the
        # machine holding the lock: taskkill cannot reach another box, and
        # offering a button that cannot work is a way of lying about what
        # happened.
        if state.state == "not_responding" and _is_this_machine(state.host):
            if cols[2].button("Force stop", icon=":material/dangerous:",
                              key="sched_force"):
                _force_stop(state.pid)
                st.rerun()

        log_dir = os.environ.get("KDBMONITOR_LOG_DIR", "logs")
        st.caption(f"Log: {os.path.join(log_dir, 'scheduler.log')}")


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
    index = next((i for i, d in enumerate(dashboards)
                  if d.id == job.dashboard_id), 0)

    top = L.field_row(2)
    job.name = top[0].text_input("Name", value=job.name, key=f"{key}_name")
    chosen_name = top[1].selectbox("Dashboard", names, index=index,
                                   key=f"{key}_dash")
    if chosen_name:
        job.dashboard_id = next(d.id for d in dashboards
                                if d.name == chosen_name)

    # --- when ---
    L.section("When", level=5)
    advanced = st.toggle("Advanced (cron)", value=job.recurrence.mode == "cron",
                         key=f"{key}_adv")
    job.recurrence.mode = "cron" if advanced else "times"
    if advanced:
        when = L.grid(L.FIELD_WIDE, L.FIELD)
        job.recurrence.cron = when[0].text_input(
            "Cron expression", value=job.recurrence.cron or "0 7 * * 1-5",
            key=f"{key}_cron",
            help="minute hour day-of-month month day-of-week. Sunday is 0 or 7.")
    else:
        when = L.grid(L.FIELD, L.FIELD_WIDE, L.FIELD)
        times = when[0].text_input("Run at", value=", ".join(job.recurrence.times),
                                   key=f"{key}_times",
                                   help="HH:MM, comma separated — e.g. 07:00, 16:35")
        job.recurrence.times = [t.strip() for t in times.split(",") if t.strip()]
        picked = when[1].multiselect(
            "On days", options=list(range(7)),
            default=job.recurrence.days or list(range(5)),
            format_func=lambda d: DAY_NAMES[d], key=f"{key}_days")
        job.recurrence.days = sorted(picked)

    zone_options = ["local"] + iana_names()
    zone_index = (zone_options.index(job.recurrence.tz)
                  if job.recurrence.tz in zone_options else 0)
    job.recurrence.tz = when[-1].selectbox("Timezone", zone_options,
                                           index=zone_index, key=f"{key}_tz")

    # --- what ---
    L.section("What", level=5)
    options = _period_options()
    current = _period_label(job.period)
    what = L.field_row(2)
    job.period = _period_spec(what[0].selectbox(
        "Period", options,
        index=options.index(current) if current in options else 0,
        key=f"{key}_period",
        help="A relative period is resolved against the day the job runs, so "
             "'Yesterday' means yesterday every morning."))

    dashboard = store.get_dashboard(job.dashboard_id)
    params = list(dashboard.parameters) if dashboard else []
    pcols = L.field_row(min(len(params), 3)) if params else []
    for i, parameter in enumerate(params):
        value = pcols[i % 3].text_input(
            f"Parameter · {parameter.name}",
            value=job.parameters.get(parameter.name, ""),
            placeholder=f"(default: {parameter.default})",
            key=f"{key}_p_{parameter.name}")
        if value.strip():
            job.parameters[parameter.name] = value.strip()
        else:
            job.parameters.pop(parameter.name, None)

    # --- to whom ---
    L.section("To whom", level=5)
    to = L.grid(L.FIELD_WIDE, L.FIELD_WIDE)
    job.recipients = parse_address_list(to[0].text_input(
        "Recipients", value=", ".join(job.recipients), key=f"{key}_to",
        help="Comma or semicolon separated."))
    job.subject = to[1].text_input("Subject", value=job.subject or DEFAULT_SUBJECT,
                                   key=f"{key}_subject",
                                   help="{dashboard} {date} {time} {job}")
    job.body = L.grid(L.FIELD_WIDE * 2)[0].text_area(
        "Body", value=job.body, key=f"{key}_body",
        placeholder="(a default is used when this is empty)")

    problems = validate_job(job)
    for problem in problems:
        st.warning(problem)

    if not problems:
        upcoming = rec_mod.next_run(job.recurrence, datetime.now(timezone.utc))
        summary = rec_mod.describe(job.recurrence)
        st.caption(f"{summary} · next run {upcoming:%a %d %b %Y, %H:%M} UTC"
                   if upcoming else f"{summary} · no next run")

    # Submit sits bottom-left of the form it commits — the rule the whole app
    # now follows for a form's own actions.
    actions = L.grid(L.BUTTON, L.BUTTON, L.BUTTON)
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
    # An h2 like every other page. This was the app's only `st.title` — an h1 at
    # 28px against everyone else's 16px, so moving between Monitor and Schedules
    # changed the title size for no reason a reader could name.
    #
    # "New schedule" moves to the header. It used to sit bottom-left *after* the
    # job list, so the way to create one was found by scrolling past everything
    # that already existed.
    act = L.page_header("Schedules", icon=":material/schedule_send:", actions=1,
                        subtitle="Dashboards run on a clock and mailed as PDF.")
    if act[0].button("New schedule", icon=":material/add:", key="sched_new",
                     type="primary", use_container_width=True):
        st.session_state["_new_report_job"] = True

    L.section("Scheduler process",
              description="The report jobs below only run while this is up.")
    _render_process(store)

    dashboards = schedulable_dashboards(store)
    if not dashboards:
        st.info("No dashboard can be scheduled yet. A scheduled report runs "
                "its queries with no browser open, so a dashboard reading an "
                "uploaded file cannot be one.", icon=":material/info:")
        return

    jobs = store.list_report_jobs()
    L.section(f"Jobs ({len(jobs)})")

    if st.session_state.get("_new_report_job"):
        with L.card():
            L.section("New schedule", level=5)
            fresh = ReportJob(id=None, name="", dashboard_id=dashboards[0].id,
                              recurrence=Recurrence())
            _render_editor(store, mgr, fresh, dashboards, "jobnew")

    for job in jobs:
        # The status of a job is said the way status is said everywhere else in
        # this app — a coloured badge with a material icon — rather than in the
        # ASCII tick and cross this page had invented for itself.
        icon = (":material/check_circle:" if job.enabled
                else ":material/pause_circle:")
        with st.expander(f"**{job.name}** · {rec_mod.describe(job.recurrence)}",
                         icon=icon):
            head = L.grid(L.GRID - L.ACTION_SPAN, L.ACTION_SPAN, align="center",
                          pad=False)
            head[0].markdown(
                (":green-badge[:material/check_circle: Enabled]" if job.enabled
                 else ":gray-badge[:material/pause_circle: Disabled]")
                + f"  ·  :gray[Last run: {job_summary(store, job)}]")
            enabled = head[1].toggle("Enabled", value=job.enabled,
                                     key=f"job{job.id}_on",
                                     label_visibility="collapsed")
            if enabled != job.enabled:
                store.set_report_job_enabled(job.id, enabled)
                st.rerun()
            editor, history = st.tabs(["Edit", "History"])
            with editor:
                _render_editor(store, mgr, job, dashboards, f"job{job.id}")
            with history:
                _render_history(store, job)
