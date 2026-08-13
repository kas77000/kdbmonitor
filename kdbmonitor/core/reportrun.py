"""Running one scheduled report, once.

Deliberately ignorant of clocks. This module is handed a job and a moment and
does the whole of the work — resolve, query, render, send — and knows nothing
about what is due, what to retry, or what to write down afterwards. That line
is what makes the report path testable without waiting for a clock and the
timing path testable without a KDB server.

The PDF comes from :func:`kdbmonitor.core.dashpdf.dashboard_to_pdf_bytes`, the
same function the Download button calls. Nothing about rendering is written a
second time here: a report that arrives by mail has to be the report you would
have downloaded by hand, and the only way to guarantee that is to not have a
second implementation to drift from.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable, Optional

from kdbmonitor.core import notifiers, timectx
from kdbmonitor.core.dashpdf import (
    dashboard_to_pdf_bytes, page_count, pdf_filename,
)
from kdbmonitor.core.dataset import run_datasets
from kdbmonitor.core.reportjob import ReportJob, render_text

ADMIN_EMAIL_KEY = "scheduler_admin_email"

_DEFAULT_BODY = ("The attached report was produced by KdbMonitor.\n"
                 "\n{dashboard} — {date} {time}\n")


@dataclass
class DatasetFailure:
    name: str
    error: str
    qsql: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "error": self.error, "qsql": self.qsql}


@dataclass
class RunOutcome:
    ok: bool
    pages: int = 0
    bytes_sent: int = 0
    recipients: int = 0
    failures: list[DatasetFailure] = field(default_factory=list)
    error: Optional[str] = None


def collect_failures(results: dict) -> list[DatasetFailure]:
    """Every dataset that produced no frame, with the query that was sent.

    ``waiting`` is included. It exists to tell a reader "no upload yet" apart
    from "broken", which is a distinction worth drawing on screen — but a
    report has no frame either way, and would print the panel empty.
    """
    out: list[DatasetFailure] = []
    for name, res in (results or {}).items():
        if getattr(res, "df", None) is not None:
            continue
        error = getattr(res, "error", None)
        if not error:
            error = ("waiting for an upload that a scheduled run cannot make"
                     if getattr(res, "waiting", False) else "produced no frame")
        out.append(DatasetFailure(name=name, error=str(error),
                                  qsql=getattr(res, "qsql", "") or ""))
    return out


def chosen_values(dashboard, job: ReportJob) -> dict:
    """The parameter values this run uses: the dashboard's defaults, overlaid
    with what the job overrides.

    An override naming a parameter the dashboard no longer declares is dropped
    rather than passed on — a job can outlive the parameter it was written for,
    and a value nothing will substitute is not worth sending to a server.
    """
    values = {p.name: p.default for p in (dashboard.parameters or [])}
    for name, value in (job.parameters or {}).items():
        if name in values:
            values[name] = value
    return values


def run_job(job: ReportJob, store, mgr, now: datetime,
            send: Optional[Callable] = None) -> RunOutcome:
    """Run ``job`` now and mail the result. Never raises for an expected
    failure — the caller records what came back."""
    send = send or notifiers.send_report_email

    dashboard = store.get_dashboard(job.dashboard_id)
    if dashboard is None:
        return RunOutcome(ok=False, error=(
            f"the dashboard this job points at (id {job.dashboard_id}) "
            "no longer exists"))
    if getattr(dashboard, "source", "kdb") == "file":
        return RunOutcome(ok=False, error=(
            f"'{dashboard.name}' reads from an uploaded file, and a scheduled "
            "run has no browser to take the upload from"))
    if not job.recipients:
        return RunOutcome(ok=False, error="the job has no recipients")

    smtp_host = (store.get_setting("smtp_host", "") or "").strip()
    if not smtp_host:
        return RunOutcome(ok=False, error=(
            "SMTP is not configured — set the host in Admin > Email (SMTP)"))

    # The job's period, or the dashboard's own. Held to what the dashboard
    # offers by the same coerce_spec the Dashboards page applies, so a job
    # asking for a historical period from a real-time-only dashboard lands
    # where the screen would rather than querying a server that is not there.
    spec = timectx.coerce_spec(job.period or dashboard.time_context,
                               dashboard.periods)
    # run_datasets resolves the dashboard's own time_context, so the override
    # has to be *on* the dashboard it is handed. get_dashboard builds a fresh
    # object per call, but replace() says so rather than relying on it.
    dashboard = replace(dashboard, time_context=spec)
    rt = timectx.resolve(spec, now.date())
    chosen = chosen_values(dashboard, job)

    results = run_datasets(dashboard, store, mgr, now.date(), chosen=chosen)

    # Spec §5.1: one dataset in error means no report is sent. A PDF in an
    # inbox is read detached from the app, forwarded on, and quoted days later,
    # by which time nobody knows the panel showing zero was a dead connection
    # rather than a quiet morning. Wrong and plausible is worse than absent.
    failures = collect_failures(results)
    if failures:
        return RunOutcome(
            ok=False, failures=failures,
            error=f"{len(failures)} of {len(results)} datasets failed")

    pdf = dashboard_to_pdf_bytes(dashboard, results, rt, now, chosen)
    subject = render_text(job.subject, job=job.name, dashboard=dashboard.name,
                          when=now)
    body = render_text(job.body or _DEFAULT_BODY, job=job.name,
                       dashboard=dashboard.name, when=now)

    send(smtp_host, int(store.get_setting("smtp_port", "25") or 25),
         store.get_setting("smtp_sender", "") or "", job.recipients,
         subject, body,
         [(pdf_filename(dashboard, now), pdf, "application/pdf")])

    return RunOutcome(ok=True, pages=page_count(dashboard, results, chosen),
                      bytes_sent=len(pdf), recipients=len(job.recipients))


# --- telling the admin (spec §6.1) ------------------------------------------
#
# The recipients are never written to. They asked for a PDF at 07:00; a mail
# telling them a q handle refused a connection asks them to act on something
# they cannot act on, and a list that sometimes carries reports and sometimes
# carries apologies gets filtered. The people who can do something are the
# people who run KdbMonitor, and they are the only ones told.

def failure_notice(job: ReportJob, dashboard_name: str, due: datetime,
                   attempts: int, outcome: RunOutcome) -> tuple[str, str]:
    """Subject and body of the notice. Composed here, beside the run that
    produced it, so the page can show the same words from the same source."""
    subject = f"[KdbMonitor] FAILED — {job.name}"

    lines = [f"Job:        {job.name}",
             f"Dashboard:  {dashboard_name}",
             f"Due:        {due:%Y-%m-%d %H:%M} UTC",
             f"Attempts:   {attempts}",
             f"Recipients: {len(job.recipients)} (not sent to)",
             ""]
    if outcome.failures:
        lines.append(f"{outcome.error}:")
        lines.append("")
        for failure in outcome.failures:
            lines.append(f"  {failure.name}")
            lines.append(f"      {failure.error}")
            if failure.qsql:
                # The admin's next action is to run this. Finding it otherwise
                # means opening the dashboard and reconstructing which
                # substitutions produced it.
                lines.append(f"      {failure.qsql}")
            lines.append("")
    else:
        lines.append(outcome.error or "no further detail")
    return subject, "\n".join(lines)


def send_failure_notice(job: ReportJob, store, dashboard_name: str,
                        due: datetime, attempts: int, outcome: RunOutcome,
                        send: Optional[Callable] = None) -> bool:
    """Mail the notice to the admin list. False when there is nobody to tell.

    A missing admin address is a real hole, and it is closed in the app rather
    than by guessing: Admin warns while jobs exist and the field is empty.
    """
    send = send or notifiers.send_report_email
    admins = notifiers.parse_address_list(store.get_setting(ADMIN_EMAIL_KEY, ""))
    smtp_host = (store.get_setting("smtp_host", "") or "").strip()
    if not admins or not smtp_host:
        return False

    subject, body = failure_notice(job, dashboard_name, due, attempts, outcome)
    send(smtp_host, int(store.get_setting("smtp_port", "25") or 25),
         store.get_setting("smtp_sender", "") or "", admins, subject, body)
    return True
