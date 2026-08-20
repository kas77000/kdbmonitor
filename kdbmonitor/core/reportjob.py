"""A scheduled report: which dashboard, when, and to whom.

The job owns the *deviation* from the dashboard and nothing else. Its period is
None until somebody overrides it, and its parameters hold only the values it
changes — so adding a parameter to a dashboard cannot break the jobs already
pointing at it, and a dashboard's own defaults keep working without being
copied anywhere.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

from kdbmonitor.core.recurrence import Recurrence
from kdbmonitor.core.recurrence import validate as validate_recurrence

DEFAULT_SUBJECT = "{dashboard} — {date}"


@dataclass
class ReportJob:
    id: Optional[int]
    name: str
    dashboard_id: int
    enabled: bool = True

    recurrence: Recurrence = field(default_factory=Recurrence)

    # None = whatever the dashboard has saved. A stored spec of the same shape
    # the dashboard uses, so core.timectx.resolve reads both and a job cannot
    # express a period a dashboard could not.
    period: Optional[dict] = None
    # Only what this job overrides; everything else falls back to the
    # parameter's own default.
    parameters: dict = field(default_factory=dict)

    recipients: list[str] = field(default_factory=list)
    subject: str = DEFAULT_SUBJECT
    body: str = ""

    # Also file the PDF under the archive folder set in Admin. An extra
    # destination, never an alternative one: the mail goes either way.
    archive: bool = False

    retries: int = 3
    retry_delay_secs: int = 120
    grace_minutes: int = 60

    # The last *fire time* run, never the wall-clock end of the run: a slow run
    # recorded by its finish would silently swallow the fire time after it.
    last_fired_at: Optional[str] = None       # ISO-8601 UTC


def _int(value, default: int) -> int:
    """A stored number, or the default. Reading a job must not raise — a
    hand-edited bundle should cost one field its value, not the page its
    contents."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def recurrence_from_dict(d: Optional[dict]) -> Recurrence:
    d = dict(d or {})
    return Recurrence(
        mode=d.get("mode", "times") or "times",
        times=[str(t) for t in (d.get("times") or ["07:00"])],
        days=[int(x) for x in (d.get("days") or [])],
        cron=str(d.get("cron", "") or ""),
        tz=d.get("tz", "local") or "local")


def job_to_dict(job: ReportJob) -> dict:
    return asdict(job)


def job_from_dict(d: dict) -> ReportJob:
    d = dict(d or {})
    return ReportJob(
        id=d.get("id"),
        name=d.get("name", ""),
        dashboard_id=_int(d.get("dashboard_id"), 0),
        enabled=bool(d.get("enabled", True)),
        recurrence=recurrence_from_dict(d.get("recurrence")),
        period=d.get("period") or None,
        parameters=dict(d.get("parameters") or {}),
        recipients=[str(r) for r in (d.get("recipients") or [])],
        subject=d.get("subject", DEFAULT_SUBJECT),
        body=d.get("body", "") or "",
        archive=bool(d.get("archive", False)),
        retries=_int(d.get("retries"), 3),
        retry_delay_secs=_int(d.get("retry_delay_secs"), 120),
        grace_minutes=_int(d.get("grace_minutes"), 60),
        last_fired_at=d.get("last_fired_at"))


def job_to_json(job: ReportJob) -> str:
    return json.dumps(job_to_dict(job))


def job_from_json(raw: str) -> ReportJob:
    return job_from_dict(json.loads(raw))


def render_text(template: str, *, job: str, dashboard: str,
                when: datetime) -> str:
    """A subject or body with its placeholders filled in.

    Plain replacement rather than ``str.format``: an unknown placeholder is
    left as written instead of raising. A typo in a subject line is a cosmetic
    problem, and it must not be the reason a report does not go out.
    """
    values = {"job": job, "dashboard": dashboard,
              "date": f"{when:%Y-%m-%d}", "time": f"{when:%H:%M}"}
    out = template or ""
    for name, value in values.items():
        out = out.replace("{" + name + "}", value)
    return out


def validate_job(job: ReportJob, *, archive_root: str = "") -> list[str]:
    """Human-readable problems with a job; empty when it is usable.

    ``archive_root`` is the folder Admin has configured. A job asking to be
    archived in an app that has nowhere to archive it is genuinely unsaveable,
    so the rule lives here with the others rather than on the page — which is
    also how the page gets its Save disabled for free.
    """
    problems: list[str] = []
    if not (job.name or "").strip():
        problems.append("Give the job a name.")
    if not job.dashboard_id:
        problems.append("Choose a dashboard.")
    if not job.recipients:
        problems.append("Add at least one recipient.")
    for address in job.recipients:
        if "@" not in address:
            problems.append(f"{address!r} is not an email address.")
    if job.archive and not (archive_root or "").strip():
        problems.append("Turn off saving a copy, or set a report archive "
                        "folder in Admin > Report archive.")
    problems.extend(validate_recurrence(job.recurrence))
    return problems
