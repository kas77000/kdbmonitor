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
    mode: str = "transition"     # transition | cooldown | every_tick
    cooldown_secs: int = 0


@dataclass
class Channels:
    in_app: bool = True
    sound: bool = True
    email_to: list[str] = field(default_factory=list)
    webhook_urls: list[str] = field(default_factory=list)


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


@dataclass
class Connection:
    id: Optional[int]
    name: str
    host: str
    port: int
    schema: dict[str, list[str]] = field(default_factory=dict)  # table -> columns
    last_introspected_at: Optional[str] = None


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
            )
            for s in d["steps"]
        ],
        trigger=TriggerCondition(**d["trigger"]),
        channels=Channels(**d["channels"]),
        rearm=RearmPolicy(**d["rearm"]),
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
    )
