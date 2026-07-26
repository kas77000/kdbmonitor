"""Dashboard definitions: datasets (the data) and rows of widgets (the layout).

Kept separate from ``core.models`` — alerts and dashboards are different entities
that happen to share ``Filter``. Serialised whole as JSON, the same way alerts
are, so adding a field never needs a schema migration.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Optional

from kdbmonitor.core.models import Filter


def _default_time_context() -> dict:
    return {"mode": "realtime"}


@dataclass
class Transform:
    kind: str                    # derive | filter | groupby | sort | limit | rename
    params: dict = field(default_factory=dict)


@dataclass
class Dataset:
    name: str                    # referenced by widgets and by {{name.column}}
    env: str                     # logical environment, not a connection name
    time_mode: str = "inherit"   # inherit | realtime | custom
    time_context: Optional[dict] = None    # only when time_mode == "custom"
    mode: str = "guided"         # guided | raw
    table: str = ""              # guided only
    filters: list[Filter] = field(default_factory=list)      # guided only
    raw_qsql: Optional[str] = None                            # raw only
    transforms: list[Transform] = field(default_factory=list)
    max_rows: int = 5000


@dataclass
class Widget:
    type: str                    # kpi|table|bar|line|scatter|hist|box|heatmap|pie|text
    dataset: str
    title: str = ""
    spec: dict = field(default_factory=dict)
    width: float = 1.0           # relative weight within its row


@dataclass
class Row:
    widgets: list[Widget] = field(default_factory=list)
    height_in: float = 2.5       # printed height; screen height derives from it


@dataclass
class Dashboard:
    id: Optional[int]
    name: str
    description: str = ""
    refresh_secs: int = 15
    time_context: dict = field(default_factory=_default_time_context)
    datasets: list[Dataset] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)


def dashboard_to_dict(d: Dashboard) -> dict:
    return asdict(d)


def _dataset_from_dict(d: dict) -> Dataset:
    return Dataset(
        name=d["name"],
        env=d.get("env", ""),
        time_mode=d.get("time_mode", "inherit"),
        time_context=d.get("time_context"),
        mode=d.get("mode", "guided"),
        table=d.get("table", ""),
        filters=[Filter(**f) for f in d.get("filters", [])],
        raw_qsql=d.get("raw_qsql"),
        transforms=[Transform(kind=t["kind"], params=t.get("params", {}))
                    for t in d.get("transforms", [])],
        max_rows=d.get("max_rows", 5000),
    )


def _row_from_dict(d: dict) -> Row:
    return Row(
        widgets=[Widget(type=w["type"], dataset=w["dataset"],
                        title=w.get("title", ""), spec=w.get("spec", {}),
                        width=w.get("width", 1.0))
                 for w in d.get("widgets", [])],
        height_in=d.get("height_in", 2.5),
    )


def dashboard_from_dict(d: dict) -> Dashboard:
    return Dashboard(
        id=d.get("id"),
        name=d["name"],
        description=d.get("description", ""),
        refresh_secs=d.get("refresh_secs", 15),
        time_context=d.get("time_context") or _default_time_context(),
        datasets=[_dataset_from_dict(x) for x in d.get("datasets", [])],
        rows=[_row_from_dict(x) for x in d.get("rows", [])],
    )


def dashboard_to_json(d: Dashboard) -> str:
    return json.dumps(dashboard_to_dict(d))


def dashboard_from_json(raw: str) -> Dashboard:
    return dashboard_from_dict(json.loads(raw))
