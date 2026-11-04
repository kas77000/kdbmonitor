"""The all-clear mail: saying out loud that an alert did not trigger.

A silent inbox means either "nothing is wrong" or "nothing is checking". This
mail says the first. It goes to the alert's own email recipients, either with
every clean check or once per interval — and an interval mail certifies the
whole interval: one trigger or error in it and no mail is sent for it.

Pure apart from :func:`after_check`, which is the engine's one call and takes
the store and the mailer as parameters so it can be tested without either a
browser or an SMTP server.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from kdbmonitor.core import zones
from kdbmonitor.core.models import SUCCESS_MAIL_INTERVALS, Alert, SuccessMail

DEFAULT_SUBJECT = "KdbMonitor: {alert} all clear"
DEFAULT_BODY = ("{alert} did not trigger between {since} and {until}.\n"
                "Clean checks: {checks}. Rows in the last check: {rows}.")
# A single check has no period to speak of.
DEFAULT_BODY_EVERY_CHECK = ("{alert} did not trigger at {until}.\n"
                            "Rows returned: {rows}.")

# What the Builder lists beside the fields, and what render() fills.
PLACEHOLDERS: dict[str, str] = {
    "alert": "the alert's name",
    "group": "its group (blank when ungrouped)",
    "checks": "how many clean checks the mail covers",
    "since": "start of the period",
    "until": "end of the period",
    "rows": "rows the last check returned",
}

_TIME_FMT = "%Y-%m-%d %H:%M"

# (to, subject, body) -> None; raises on failure.
SendFn = Callable[[list[str], str, str], None]


@dataclass
class SuccessState:
    """The interval currently being counted."""
    period_start: datetime
    clean_checks: int = 0
    dirty: bool = False


@dataclass
class Period:
    """A stretch of checks that was all clear, and is worth a mail."""
    since: datetime
    until: datetime
    checks: int


def step(cfg: SuccessMail, state: Optional[SuccessState], now: datetime,
         clean: bool) -> tuple[Optional[SuccessState], Optional[Period]]:
    """Count one check. Returns the state to keep and the period to mail, if any.

    ``clean`` is "the condition was not met and the check did not error".
    The interval's cadence is fixed: a period closes once it is ``interval``
    old whatever happened in it, and a new one opens at the same moment — so an
    hour that saw a trigger has no mail, and the next hour starts clean.
    """
    if cfg.mode == "every_check":
        return None, (Period(since=now, until=now, checks=1) if clean else None)

    if state is None:
        state = SuccessState(period_start=now)
    if clean:
        state.clean_checks += 1
    else:
        state.dirty = True

    if now - state.period_start < timedelta(seconds=max(cfg.interval_secs, 1)):
        return state, None

    period = None
    if state.clean_checks > 0 and not state.dirty:
        period = Period(since=state.period_start, until=now,
                        checks=state.clean_checks)
    return SuccessState(period_start=now), period


class _Keep(dict):
    """Leaves an unknown ``{name}`` exactly as it was typed."""
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _fill(template: str, values: dict) -> str:
    try:
        return template.format_map(_Keep(values))
    except (ValueError, IndexError, AttributeError, KeyError):
        # A stray brace, '{0}', '{alert.x}': send the text as typed rather
        # than not send at all.
        return template


def _zone(alert: Alert):
    name = (alert.schedule.tz or "local").strip()
    if name.lower() == "local":
        name = zones.local_zone()
    return zones.tzinfo_for(name)


def render(alert: Alert, period: Period,
           rows: Optional[int]) -> tuple[str, str]:
    """The subject and body for one period, defaults where the user left none."""
    tz = _zone(alert)
    values = {
        "alert": alert.name,
        "group": alert.group or "",
        "checks": period.checks,
        "since": period.since.astimezone(tz).strftime(_TIME_FMT),
        "until": period.until.astimezone(tz).strftime(_TIME_FMT),
        "rows": "?" if rows is None else rows,
    }
    cfg = alert.success
    subject = _fill(cfg.subject.strip() or DEFAULT_SUBJECT, values)
    default = (DEFAULT_BODY_EVERY_CHECK if cfg.mode == "every_check"
               else DEFAULT_BODY)
    body = _fill(cfg.body if cfg.body.strip() else default, values)
    # A header cannot carry a newline; a subject pasted from somewhere can.
    return " ".join(subject.split()), body


def describe(cfg: SuccessMail) -> str:
    """'all-clear mail every 1 hour' — for the one-line summaries."""
    if cfg.mode == "every_check":
        return "all-clear mail every check"
    for label, secs in SUCCESS_MAIL_INTERVALS.items():
        if secs == cfg.interval_secs:
            return f"all-clear mail every {label}"
    return f"all-clear mail every {cfg.interval_secs}s"


def _parse(text: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def after_check(store, alert: Alert, now: datetime, clean: bool,
                rows: Optional[int], send: Optional[SendFn]) -> Optional[str]:
    """Count a check that has just run, and mail if a period closed clean.

    Returns an error message when a send failed, None otherwise — it never
    raises, because a mail server being down must not stop the alerts that
    come after this one. The period moves on either way: a broken server
    costs one message per period, not a retry every tick.
    """
    cfg = alert.success
    if not (cfg.enabled and alert.channels.email_to) or alert.id is None:
        return None

    saved = store.get_success_state(alert.id)
    state = None
    if saved is not None:
        start = _parse(saved["period_start"])
        if start is not None:
            state = SuccessState(period_start=start,
                                 clean_checks=saved["clean_checks"],
                                 dirty=saved["dirty"])

    state, period = step(cfg, state, now, clean)
    if state is not None:
        store.set_success_state(alert.id, state.period_start.isoformat(),
                                state.clean_checks, state.dirty)

    if period is None or send is None:
        return None
    subject, body = render(alert, period, rows)
    try:
        send(list(alert.channels.email_to), subject, body)
    except Exception as exc:  # noqa: BLE001 - any SMTP/network failure
        return f"{alert.name}: all-clear mail failed - {exc}"
    return None
