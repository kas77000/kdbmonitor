"""Dashboard definitions: datasets (the data) and rows of widgets (the layout).

Kept separate from ``core.models`` — alerts and dashboards are different entities
that happen to share ``Filter``. Serialised whole as JSON, the same way alerts
are, so adding a field never needs a schema migration.
"""
from __future__ import annotations

import copy
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


# What a cell has to say to be read as missing. Matched case-insensitively after
# trimming. Editable per dataset because a column in which "-" is a real
# category would otherwise be silently blanked — quiet data loss, worth a
# control to prevent.
DEFAULT_NULL_MARKERS = ["", "NA", "N/A", "NaN", "NULL", "NONE", "-", "--", "#N/A"]

COLUMN_TYPES = ("date", "number", "integer", "text", "boolean")


@dataclass
class ColumnSpec:
    """One column an uploaded file has to provide."""
    name: str                    # the header text the file must carry
    type: str = "text"           # one of COLUMN_TYPES
    required: bool = True        # a column no widget references need not arrive
    allow_null: bool = True      # false: a blank in this column is a refusal


@dataclass
class NamedCell:
    """A single cell picked out of the file, outside the table.

    Addressed against the raw grid as it sits on disk — the grid the designer
    clicked to create it. Orientation applies to the table region only; if it
    applied here too, turning on vertical headers would move every cell already
    named.
    """
    name: str                    # "Report date"
    row: int = 0                 # 0-based
    col: int = 0                 # 0-based
    type: str = "text"
    allow_null: bool = True


@dataclass
class FileShape:
    """Where the table sits in an uploaded file, and what it must contain.

    Nothing here is ever guessed at run time. The header line is declared, and a
    file whose header is elsewhere is refused rather than searched.
    """
    header_axis: str = "row"     # row = headers across | column = headers down
    header_row: int = 0          # 0-based line carrying the headers
    first_col: int = 0           # 0-based column the table starts at
    data_start: int = 1          # 0-based first data line
    null_markers: list[str] = field(default_factory=lambda:
                                    list(DEFAULT_NULL_MARKERS))
    columns: list[ColumnSpec] = field(default_factory=list)
    cells: list[NamedCell] = field(default_factory=list)


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
    # Extra environments this raw query opens with hopen, so a single query can
    # span two KDB processes (e.g. join OMS orders against a quote server).
    # Referenced in the q as {{conn:ENV}}, replaced with the server's `:host:port.
    extra_connections: list[str] = field(default_factory=list)  # raw only
    transforms: list[Transform] = field(default_factory=list)
    max_rows: int = 5000
    # --- file-backed datasets -------------------------------------------
    # A file dataset ignores env/time_mode/mode/table/filters/raw_qsql above:
    # they describe a server, and there is no server. transforms and max_rows
    # are NOT ignored — they apply to an uploaded frame identically, which is
    # why a file dataset needs no shaping vocabulary of its own.
    source: str = "kdb"          # kdb | file
    shape: Optional[FileShape] = None       # file only
    file_label: str = ""         # the prompt on the upload box


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
    # Which periods this dashboard offers: both (switch between a real-time
    # server and its historical twin), or the one it is built for. Switching is
    # only possible where every environment it reads has both sides, so a
    # dashboard over a historical-only feed says so once here rather than
    # offering a period that resolves to no server.
    periods: str = "both"        # both | realtime | historical
    # Which way up the PDF prints. 'auto' keeps portrait until a table's columns
    # will not fit legibly across it, at which point the whole report turns —
    # the two explicit settings are there for when you would rather decide than
    # be decided for.
    orientation: str = "auto"    # auto | portrait | landscape
    # Where this dashboard's data comes from. A file dashboard has no
    # environment, no period and no refresh interval — see the spec, §8.1.
    source: str = "kdb"          # kdb | file
    time_context: dict = field(default_factory=_default_time_context)
    datasets: list[Dataset] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)


@dataclass
class Component:
    """One saved, reusable piece of a dashboard, kept under a name of its own.

    A transform step or a whole widget, stored as the same dict the dashboard
    itself holds. Loading one copies it into the draft, where it is an ordinary
    part of that dashboard again: edit it freely, and save it back under this
    name or a new one when the edit is worth keeping.
    """
    id: Optional[int]
    kind: str                    # transform | widget
    name: str
    payload: dict = field(default_factory=dict)


def dashboard_to_dict(d: Dashboard) -> dict:
    return asdict(d)


def transform_to_dict(t: Transform) -> dict:
    return asdict(t)


def transform_from_dict(d: dict) -> Transform:
    return Transform(kind=d["kind"], params=copy.deepcopy(d.get("params", {})))


def widget_to_dict(w: Widget) -> dict:
    return asdict(w)


def widget_from_dict(d: dict) -> Widget:
    return Widget(type=d["type"], dataset=d.get("dataset", ""),
                  title=d.get("title", ""),
                  spec=copy.deepcopy(d.get("spec", {})),
                  width=d.get("width", 1.0))


def _column_from_dict(d: dict) -> ColumnSpec:
    return ColumnSpec(name=d.get("name", ""), type=d.get("type", "text"),
                      required=bool(d.get("required", True)),
                      allow_null=bool(d.get("allow_null", True)))


def _cell_from_dict(d: dict) -> NamedCell:
    return NamedCell(name=d.get("name", ""), row=int(d.get("row", 0)),
                     col=int(d.get("col", 0)), type=d.get("type", "text"),
                     allow_null=bool(d.get("allow_null", True)))


def _shape_from_dict(d: Optional[dict]) -> Optional[FileShape]:
    """A stored shape, field by field — never ``FileShape(**d)``.

    Splatting a stored dict would make an old dashboard carrying a field this
    version has since dropped raise on load, which is the one thing reading
    stored data must never do.
    """
    if not d:
        return None
    return FileShape(
        header_axis=d.get("header_axis", "row"),
        header_row=int(d.get("header_row", 0)),
        first_col=int(d.get("first_col", 0)),
        data_start=int(d.get("data_start", 1)),
        null_markers=list(d.get("null_markers") or DEFAULT_NULL_MARKERS),
        columns=[_column_from_dict(c) for c in d.get("columns", [])],
        cells=[_cell_from_dict(c) for c in d.get("cells", [])])


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
        extra_connections=list(d.get("extra_connections", [])),
        transforms=[transform_from_dict(t) for t in d.get("transforms", [])],
        max_rows=d.get("max_rows", 5000),
        source=d.get("source", "kdb"),
        shape=_shape_from_dict(d.get("shape")),
        file_label=d.get("file_label", ""),
    )


def _row_from_dict(d: dict) -> Row:
    return Row(
        widgets=[widget_from_dict(w) for w in d.get("widgets", [])],
        height_in=d.get("height_in", 2.5),
    )


def dashboard_from_dict(d: dict) -> Dashboard:
    return Dashboard(
        id=d.get("id"),
        name=d["name"],
        description=d.get("description", ""),
        refresh_secs=d.get("refresh_secs", 15),
        periods=d.get("periods", "both"),
        orientation=d.get("orientation", "auto"),
        source=d.get("source", "kdb"),
        time_context=d.get("time_context") or _default_time_context(),
        datasets=[_dataset_from_dict(x) for x in d.get("datasets", [])],
        rows=[_row_from_dict(x) for x in d.get("rows", [])],
    )


def dashboard_to_json(d: Dashboard) -> str:
    return json.dumps(dashboard_to_dict(d))


def dashboard_from_json(raw: str) -> Dashboard:
    return dashboard_from_dict(json.loads(raw))
