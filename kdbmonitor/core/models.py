from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class Filter:
    column: str
    op: str                      # = <> < <= > >= in like
    value: Any                   # scalar, or list for op == "in"
    value_type: str              # symbol | number | string
    negated: bool = False        # prefix the expression with q 'not'


@dataclass
class Step:
    server: str                  # connection name
    table: str
    mode: str                    # form | raw
    filters: list[Filter] = field(default_factory=list)
    raw_qsql: Optional[str] = None
    output_name: str = "step1"
    # How long this step's rows may be reused before it goes back to the server;
    # 0 (the default) is every tick, as it always was. For the step that fetches
    # what does not change — the basket, the universe, the book mapping — so an
    # alert checking every 15 seconds does not ask for it 240 times an hour.
    #
    # A TTL rather than a plain "fetch once", because an alert runs unattended
    # all day: held forever means it would still be checking yesterday's basket
    # tomorrow morning, and nothing on screen would say so. The cache is keyed
    # by the query as resolved, so a step whose text depends on an earlier one
    # re-fetches when that changes regardless of the TTL. See core.qcache.
    cache_secs: int = 0


# How long a step's rows may be held, as durations somebody would actually pick.
# Label -> seconds, and 0 is "go every time". Here rather than in the Builder
# because the Monitor describes a saved alert in the same words — see
# core.summaries.cache_summary.
STEP_CACHE_PRESETS: dict[str, int] = {
    "Not at all": 0, "1 minute": 60, "15 minutes": 900, "1 hour": 3600,
    "4 hours": 14400, "24 hours": 86400,
}


@dataclass
class TriggerCondition:
    type: str                    # no_rows | has_rows | row_count_gte | any_row | all_rows | aggregate
    column: Optional[str] = None
    op: Optional[str] = None
    value: Any = None
    n: Optional[int] = None      # for row_count_gte
    agg: Optional[str] = None    # max | min | avg | sum (for aggregate)
    value_type: str = "number"   # number | symbol | string (for any_row/all_rows)


@dataclass
class RearmPolicy:
    mode: str = "transition"     # transition | cooldown | every_tick | on_change
    cooldown_secs: int = 0


# How a fired alert reaches the person watching. Each one is independent and
# they add up: an alert can beep, raise the window, open the rows and mail the
# desk, or do exactly one of those. The labels are what the Builder shows.
DELIVERY_KINDS: tuple[str, ...] = ("in_app", "sound", "browser", "focus", "popup")

DELIVERY_LABELS: dict[str, str] = {
    "in_app": "In-app message",
    "sound": "Sound",
    "browser": "Browser notification",
    "focus": "Bring the window to the front",
    "popup": "Pop-up with the result",
    "email": "Email",
    "webhook": "Teams / Slack webhook",
}


@dataclass
class Channels:
    in_app: bool = True
    sound: bool = True
    # A desktop notification from the browser: it arrives on top of whatever
    # the user is doing, including while the tab is in the background. Older
    # saved alerts have no such field and used to get one whenever `in_app`
    # was on, which is why channels_from_dict falls back to it rather than to
    # this default.
    browser: bool = True
    # Pull the browser window forward. A page cannot raise itself unprompted —
    # every browser refuses that — so this is the strongest legal combination:
    # a notification that stays on screen until clicked (clicking it focuses
    # the tab), a flashing tab title, and the taskbar attention that comes with
    # both. See ui/engine.py.
    focus: bool = False
    # A modal in the app window showing the rows that fired it, so the result
    # is in front of the user rather than one navigation away.
    popup: bool = False
    email_to: list[str] = field(default_factory=list)
    webhook_urls: list[str] = field(default_factory=list)


# --- when an alert is allowed to fire -------------------------------------- #
@dataclass
class Window:
    """A time of day range, in the schedule's own timezone.

    ``end`` is exclusive, and an end earlier than the start crosses midnight
    (22:00-02:00 is one window, not a mistake).
    """
    start: str = "09:00"         # HH:MM
    end: str = "17:00"           # HH:MM


@dataclass
class Schedule:
    """The hours an alert is live.

    A check that only means something inside a window — a 16:30 mark, the last
    fifteen minutes before a cutoff — reports false positives for the rest of
    the day if it keeps running. Outside its windows an alert is not evaluated
    at all: no query, no trigger, no notification.
    """
    mode: str = "always"                             # always | windows
    windows: list[Window] = field(default_factory=list)
    days: list[int] = field(default_factory=list)    # 0=Mon..6=Sun; [] = every day
    tz: str = "local"                                # any spelling core.zones takes


@dataclass
class Alert:
    id: Optional[int]
    name: str
    enabled: bool
    poll_interval_secs: int
    steps: list[Step]
    trigger: TriggerCondition
    channels: Channels
    rearm: RearmPolicy
    result_retention: str = "latest"   # latest | snapshot (Monitor Result view)
    group: str = ""                    # optional grouping label ("" = Ungrouped)
    schedule: Schedule = field(default_factory=Schedule)


# The three kinds of KDB server this app talks to.
#   realtime   — today's data, no date column
#   historical — the partitioned HDB; same tables plus a date column
#   marketdata — reference/instrument data (stocks and the like); not
#                partitioned by date, so a dashboard's period does not apply
CONNECTION_KINDS = ("realtime", "historical", "marketdata")

KIND_LABELS = {"realtime": "Real-time", "historical": "Historical",
               "marketdata": "Market data"}


@dataclass
class Connection:
    id: Optional[int]
    name: str
    host: str
    port: int
    schema: dict[str, list[str]] = field(default_factory=dict)  # table -> columns
    last_introspected_at: Optional[str] = None
    kind: str = "realtime"       # one of CONNECTION_KINDS
    # One database and its historical twin — not a desk's whole environment.
    # An env holds a single server per kind, so several databases that a desk
    # would call one environment are registered as several: PROD-ORDERS,
    # PROD-QUOTES, PROD-REF, each pairing its own real-time and historical
    # sides. That is a decision rather than a limitation left lying about
    # (2026-07-31), and it is why a query reaching across databases names the
    # one it wants with {{conn:PROD-QUOTES}} rather than asking an environment
    # to hold them all. Storage.duplicate_slots reports a second server that
    # claims a slot already taken, which used to make it disappear in silence.
    env: str = ""                # logical environment; "" falls back to name
    # This side is the whole environment: there is no counterpart coming, and a
    # missing one is the design rather than a setup half-done. A date-partitioned
    # feed with no live server is historical and nothing else, and saying so is
    # what stops the app asking for the other half for the rest of its life.
    standalone: bool = False


def _int(value, default: int) -> int:
    """A stored number, or the default if it is not one.

    Reading a saved alert must not raise: a bundle can be hand-edited before it
    is imported, and a bad number in it should cost that one field its value
    rather than cost the import its error message.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def channels_from_dict(d: dict) -> Channels:
    """Channels from stored JSON, including alerts saved before this grew.

    An alert written before browser notifications were their own channel gets
    one exactly where it got one then — alongside the in-app message — so
    upgrading the app doesn't start (or stop) notifying anybody.
    """
    d = dict(d or {})
    return Channels(
        in_app=bool(d.get("in_app", True)),
        sound=bool(d.get("sound", True)),
        browser=bool(d.get("browser", d.get("in_app", True))),
        focus=bool(d.get("focus", False)),
        popup=bool(d.get("popup", False)),
        email_to=list(d.get("email_to") or []),
        webhook_urls=list(d.get("webhook_urls") or []),
    )


def schedule_from_dict(d: Optional[dict]) -> Schedule:
    """Schedule from stored JSON; anything older simply runs around the clock."""
    d = dict(d or {})
    return Schedule(
        mode=d.get("mode", "always"),
        windows=[Window(start=w.get("start", "09:00"), end=w.get("end", "17:00"))
                 for w in (d.get("windows") or [])],
        days=[int(x) for x in (d.get("days") or [])],
        tz=d.get("tz", "local") or "local",
    )


def alert_to_dict(alert: Alert) -> dict:
    return asdict(alert)


def alert_from_dict(d: dict) -> Alert:
    return Alert(
        id=d["id"],
        name=d["name"],
        enabled=d["enabled"],
        poll_interval_secs=d["poll_interval_secs"],
        steps=[
            Step(
                server=s["server"], table=s["table"], mode=s["mode"],
                filters=[Filter(**f) for f in s["filters"]],
                raw_qsql=s["raw_qsql"], output_name=s["output_name"],
                # An alert saved before this existed queried every tick, which
                # is what it did — an upgrade starts holding nothing back.
                cache_secs=_int(s.get("cache_secs"), 0),
            )
            for s in d["steps"]
        ],
        trigger=TriggerCondition(**d["trigger"]),
        channels=channels_from_dict(d["channels"]),
        rearm=RearmPolicy(**d["rearm"]),
        result_retention=d.get("result_retention", "latest"),
        group=d.get("group", ""),
        schedule=schedule_from_dict(d.get("schedule")),
    )


def alert_to_json(alert: Alert) -> str:
    return json.dumps(alert_to_dict(alert))


def alert_from_json(raw: str) -> Alert:
    return alert_from_dict(json.loads(raw))


def connection_to_dict(conn: Connection) -> dict:
    return asdict(conn)


def connection_from_dict(d: dict) -> Connection:
    return Connection(
        id=d.get("id"),
        name=d["name"],
        host=d["host"],
        port=d["port"],
        schema=d.get("schema", {}),
        last_introspected_at=d.get("last_introspected_at"),
        kind=d.get("kind", "realtime"),
        env=d.get("env", ""),
        standalone=bool(d.get("standalone", False)),
    )
