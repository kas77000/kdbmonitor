# kdbmonitor/ui/dashboard_editor.py
"""Dashboard editor: datasets (the data) and rows of widgets (the layout).

Session-state driven, following ui/builder.py. The draft lives in
``st.session_state['dash_draft']`` and is written back to the DB only on Save, so
a half-built dataset never reaches the view.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import streamlit as st

from kdbmonitor.core.dashboard_models import (
    Component, Dashboard, Dataset, PARAMETER_KINDS, Parameter, Row, Transform,
    Widget, parameter_from_dict, parameter_to_dict, transform_from_dict,
    row_from_dict, row_to_dict, transform_to_dict, widget_from_dict,
    widget_to_dict,
)
from kdbmonitor.core.dashpdf import (
    ORIENTATION_LABELS, ORIENTATIONS, choose_page, plan_rows,
)
from kdbmonitor.core.dataset import (
    is_marketdata_env, run_datasets, standalone_side, trace_datasets,
)
from kdbmonitor.core.models import KIND_LABELS, Filter
from kdbmonitor.core.parameters import params_in, unresolved_params
from kdbmonitor.core.plotmodel import (
    FIELD_LABELS, build_plot_model, is_blank as _blank, missing_spec_fields,
    referenced_columns,
)
from kdbmonitor.core.qfmt import VALUE_TYPES, is_placeholder, q_date
from kdbmonitor.core.timectx import (
    PERIOD_LABELS, PERIOD_MODES, PRESET_LABELS, PRESETS, coerce_spec,
    has_date_constraint, resolve,
)
from kdbmonitor.core.transform import AGG_FUNCS, Step, check_expression
from kdbmonitor.ui import parameters as ui_parameters
from kdbmonitor.ui.common import form_area
from kdbmonitor.ui.dashboards import back_to_gallery, render_widget, row_height_px

OPS = ["=", "<>", "<", "<=", ">", ">=", "in", "like"]

# One width for every column holding nothing but an icon button. Streamlit draws
# the icon at a fixed size, so a wider column simply pads around it — and the
# same delete button landing a few pixels further right in one card than in the
# card below it is the kind of thing that reads as carelessness without a reader
# being able to name what is wrong. Editors are looked at for hours; the grid
# being predictable is itself an affordance.
ICON_COL = 0.6

# Readable labels for core.qfmt.VALUE_TYPES — the label doubles as a sample of
# what the type is for, since "expression" alone does not say it is the escape
# hatch for a value q has to work out itself.
VALUE_TYPE_LABELS = {
    "symbol": "symbol (`AAPL)",
    "number": "number",
    "string": "string (\"text\")",
    "date": "date (2026-07-30)",
    "time": "time (09:30:00)",
    "expression": "q expression (.z.D-1)",
}
TRANSFORM_KINDS = ["derive", "filter", "groupby", "sort", "limit", "rename"]

# A dataset's own Period control — same three stored values as ds.time_mode
# always has, worded so the choice says what it does rather than naming the
# mechanism ('inherit') a reader has to already know to decode.
PERIOD_MODE_LABELS = {
    "inherit": "Follow the dashboard",
    "realtime": "Always real-time",
    "custom": "A historical range of its own",
}
WIDGET_TYPES = ["kpi", "table", "text", "bar", "line", "scatter", "hist",
                "box", "heatmap", "pie"]
REFERENCE_KINDS = ["constant", "mean", "median", "quantile", "column"]

RAW_HELP = (
    "Raw q. In historical mode you MUST constrain `date` — use "
    "`{{date_from}}` / `{{date_to}}` / `{{date_list}}`. Reference another "
    "dataset with `{{name.column}}`. To query a second KDB process in the same "
    "query, list it under *Also connect* below and `hopen` it via `{{conn:ENV}}`."
)

_REF = re.compile(r"\{\{(\w+)\.(\w+)\}\}")
_CONN_REF = re.compile(r"\{\{conn:([^{}]+)\}\}")

# Formats offered by name, so nobody has to know Python format specs.
# The labels ARE the samples — you pick the output you want to see.
SAMPLE_VALUE = 1234.567
SAMPLE_MOMENT = datetime(2026, 7, 27, 9, 30, 15)

NUMBER_FORMATS: dict[str, str] = {
    "1,235": ",.0f",
    "1,234.6": ",.1f",
    "1,234.57": ",.2f",
    "1235": ".0f",
    "1234.57": ".2f",
    "61.4%  (value is a fraction 0–1)": ".1%",
    "1.23e+03": ".2e",
}

# Dates, timestamps and q time columns. Same idea: the label is what prints.
DATE_FORMATS: dict[str, str] = {
    "2026-07-27": "%Y-%m-%d",
    "27/07/2026": "%d/%m/%Y",
    "27 Jul 2026": "%d %b %Y",
    "2026-07-27 09:30": "%Y-%m-%d %H:%M",
    "2026-07-27 09:30:15": "%Y-%m-%d %H:%M:%S",
    "09:30": "%H:%M",
    "09:30:15": "%H:%M:%S",
}

NO_FORMAT = "No formatting"
CUSTOM_FORMAT = "Custom…"

# One catalogue for the picker: numbers, then dates, then the two escapes.
VALUE_FORMATS: dict[str, str] = {**NUMBER_FORMATS, **DATE_FORMATS, NO_FORMAT: ""}

# A strftime directive is '%' followed by a letter. The numeric '.1%' ends in a
# bare '%', which is why "does it contain a percent sign" is not the test.
_STRFTIME = re.compile(r"%[a-zA-Z]")


def is_date_format(spec: str) -> bool:
    """Whether ``spec`` formats a date/time rather than a number."""
    return bool(_STRFTIME.search(spec or ""))


def _slug(name: str) -> str:
    """A widget-key-safe stand-in for a column name.

    Column names come from KDB and can hold anything; the key only has to be
    stable and unique per column, so unusual characters become their code point.
    """
    return "".join(c if c.isalnum() or c == "_" else f"_{ord(c)}_" for c in str(name))


def format_sample(spec: str, value=None) -> str:
    """What ``spec`` turns a sample value into — or a complaint if it is not a
    usable format spec, so a typo shows up immediately instead of at render.

    The sample follows the kind of spec, since '%Y-%m-%d' says nothing about
    1234.567 and ',.0f' says nothing about a timestamp.
    """
    if not spec:
        return str(SAMPLE_VALUE if value is None else value)
    if value is None:
        value = SAMPLE_MOMENT if is_date_format(spec) else SAMPLE_VALUE
    try:
        formatted = format(value, spec)
    except (TypeError, ValueError):
        return "invalid format"
    # datetime.__format__ hands an unknown spec straight back rather than
    # raising, which would otherwise pass a typo off as a valid format.
    return "invalid format" if formatted == spec else formatted


def is_valid_format(spec: str) -> bool:
    return format_sample(spec) != "invalid format"


def format_label_for(spec: str) -> str:
    """The catalogue entry matching a stored spec, else Custom."""
    for label, value in VALUE_FORMATS.items():
        if value == (spec or ""):
            return label
    return CUSTOM_FORMAT


# --- pure helpers (unit-tested) -------------------------------------------

def unique_name(base: str, taken: list[str]) -> str:
    if base not in taken:
        return base
    i = 2
    while f"{base}_{i}" in taken:
        i += 1
    return f"{base}_{i}"


def table_columns(ds: Dataset, conn) -> list[str]:
    """The columns of the table a guided dataset selects from.

    This is what a *filter* addresses: filters become the query's where clause,
    so they see the table as KDB has it, before any transform reshapes it.
    """
    if ds is None or conn is None or ds.mode == "raw":
        return []
    return list(getattr(conn, "schema", {}).get(ds.table, []))


def dataset_columns(ds: Dataset, conn, learned: list[str] | None = None) -> list[str]:
    """The columns a dataset is expected to produce, so widget forms can offer a
    picker instead of a free-text box.

    A file dataset's columns come from its stored shape rather than a live
    schema, since the shape is the contract that was saved when the file was
    uploaded — needing neither a server nor a run is what lets the dashboard
    be edited again after it has simply been reopened.

    A raw query's columns cannot be known without running it — ``learned`` is
    what a real run returned (the query's own columns, before transforms), which
    the editor remembers from the last preview.

    Even with an unknown starting point the transforms themselves are known: a
    derive names its new column and a group-by replaces the frame outright with
    its keys and aggregations. So a raw dataset that groups by market still
    offers market and every aggregate downstream of it, run or not. The list is
    then a lower bound, never a complete one — callers must leave stored
    settings alone rather than treat "not offered" as "not wanted".
    """
    if ds is None:
        return []
    if ds.source == "file":
        # The shape is the contract and it is stored, so a file dataset's
        # columns are known without a sample, a server or a run — which is what
        # makes a dashboard editable again after it has been reopened. A dataset
        # converted from a query keeps its table name; reading that instead
        # would offer the columns of a table this dataset no longer touches.
        cols = [c.name for c in (ds.shape.columns if ds.shape else [])]
    elif ds.mode == "raw" or conn is None:
        cols = list(learned or [])
    else:
        cols = list(getattr(conn, "schema", {}).get(ds.table, []))
    for t in ds.transforms:
        p = t.params
        if t.kind == "derive" and p.get("column"):
            cols.append(p["column"])
        elif t.kind == "groupby":
            cols = list(p.get("keys", [])) + [a["as"] for a in p.get("aggs", [])]
        elif t.kind == "rename":
            mapping = p.get("mapping", {})
            cols = [mapping.get(c, c) for c in cols]
    return list(dict.fromkeys(cols))


def suffix_length(mapping: dict) -> int:
    """The length every key in a suffix map shares, or 0 when they differ.

    Used to fill in the length for a map built before the length was a setting:
    ``.HK``/``.JP``/``.KS`` are all 3, so the answer is the same either way.
    """
    lengths = {len(k) for k in (mapping or {})}
    return lengths.pop() if len(lengths) == 1 else 0


def with_stored(columns: list[str], stored) -> list[str]:
    """``columns`` plus any stored value it does not contain, order preserved.

    A picker must always be able to show what is configured. Offering only the
    columns we happen to know about would make a selectbox fall back to its
    first option and a multiselect drop the value entirely — silently rewriting
    the dashboard just because someone opened it.
    """
    extra = [stored] if isinstance(stored, str) else list(stored or [])
    return list(dict.fromkeys(list(columns) + [c for c in extra if c]))


def _transform_problems(ds_name: str, index: int, t: Transform) -> list[str]:
    where = f"Dataset '{ds_name}', transform {index} ({t.kind})"
    p, out = t.params, []
    if t.kind == "derive":
        if _blank(p.get("column")):
            out.append(f"{where}: the new column has no name.")
        if p.get("kind", "arithmetic") == "arithmetic":
            if _blank(p.get("expr")):
                out.append(f"{where}: no expression entered.")
            else:
                # Caught here, while the author is still building the
                # dashboard, rather than as a red panel when someone else
                # opens it — the check itself lives with the transform, not
                # the editor, so a bad expression can never reach df.eval.
                try:
                    check_expression(p["expr"])
                except ValueError as exc:
                    out.append(f"{where}: {exc}")
        else:
            if _blank(p.get("source")):
                out.append(f"{where}: no source column chosen.")
            if _blank(p.get("mapping")):
                out.append(f"{where}: no suffix mappings entered.")
            length = int(p.get("length") or 0)
            odd = [k for k in (p.get("mapping") or {}) if len(k) != length]
            if length and odd:
                out.append(f"{where}: the suffix length is {length}, so "
                           f"{', '.join(sorted(odd))} can never match.")
    elif t.kind == "filter":
        if _blank(p.get("column")):
            out.append(f"{where}: no column chosen.")
        if _blank(p.get("value")):
            out.append(f"{where}: no value entered.")
    elif t.kind == "groupby":
        if _blank(p.get("keys")):
            out.append(f"{where}: nothing to group by.")
        if _blank(p.get("aggs")):
            out.append(f"{where}: no aggregations added.")
        for a in p.get("aggs", []):
            if _blank(a.get("column")) or _blank(a.get("as")):
                out.append(f"{where}: an aggregation is missing its column or name.")
                break
    elif t.kind == "sort":
        if _blank(p.get("columns")):
            out.append(f"{where}: no sort columns chosen.")
    elif t.kind == "rename":
        if _blank(p.get("mapping")):
            out.append(f"{where}: no renames entered.")
    return out


# The same three choices as PERIOD_LABELS, worded to sit inside a sentence.
_PERIOD_PHRASE = {"both": "both periods", "realtime": "real-time only",
                  "historical": "historical only"}


def env_serves(pair: dict, mode: str) -> bool:
    """Whether an environment can answer a period at all.

    Market data ignores the period rather than failing it — a dataset on one
    resolves to the same server whatever is asked, so it never stands in the way
    of a dashboard offering both.
    """
    return is_marketdata_env(pair) or pair.get(mode) is not None


def _orientation_picker(container, draft: Dashboard) -> None:
    """The printed-page control, in one place so it can only say one thing.

    Written out once per source it drifted: the file dashboard's copy had lost
    the sentence explaining that only the data knows how wide a column is, so
    two readers of the same setting were told different amounts about it.
    """
    ways = list(ORIENTATIONS)
    draft.orientation = container.selectbox(
        "Printed page", ways,
        index=ways.index(draft.orientation
                         if draft.orientation in ways else "auto"),
        format_func=lambda o: ORIENTATION_LABELS[o],
        help="A wide table has to fit its columns across the page, and text "
             "given too little room collides rather than shrinks. On 'auto' "
             "the report prints portrait until a table cannot be printed "
             "legibly across it, and then the whole report turns. Only the "
             "data can say how wide a column really is, so the decision is "
             "made when the PDF is generated, not here.")


def env_label(env: str, pair: dict) -> str:
    """An environment, named by the servers it actually holds.

    An environment name is a label somebody chose, and a dashboard only ever
    showed that — so a connection registered as EQUITY DATA under an
    environment called MD appeared in the picker as "MD", and picking the right
    one meant remembering which alias stood for which database. The servers are
    the names people know, so the picker says both.

    Where the environment is named after its only server there is nothing to
    add, and repeating it would be noise.
    """
    servers = [conn.name for kind in ("realtime", "historical", "marketdata")
               if (conn := (pair or {}).get(kind)) is not None]
    if not servers or servers == [env]:
        return env
    return f"{env} — {' / '.join(servers)}"


def _periods_problems(draft: Dashboard, envs: dict) -> list[str]:
    """Where what the dashboard offers contradicts an environment's own answer.

    Only environments *declared* one-sided are checked. An environment that
    merely has not got its other server yet is somebody midway through setting
    up, and the per-dataset checks below already say so — against the period
    actually in force, rather than about one nobody has asked for yet. A
    declaration is different: it says the other side is never coming, so a
    dashboard promising to switch to it is wrong today and wrong for good.
    """
    out: list[str] = []
    # Only the datasets that actually follow the dashboard's period are judged
    # against what it offers. One pinned to a period of its own does not care
    # what the control at the top says, and a dashboard reading a live order
    # book beside a historical reference database is an ordinary thing to want
    # — the engine has always run it. Judging every environment against the
    # declaration made that dashboard impossible to *save*, because Save is
    # disabled while any problem is listed, so the editor refused to let out a
    # thing the rest of the app was happy to do.
    inheriting = {ds.env for ds in draft.datasets
                  if ds.env and ds.time_mode == "inherit"}
    for env in sorted(inheriting):
        side = standalone_side(envs.get(env) or {})
        if side is None or draft.periods == side:
            continue
        out.append(
            f"The dashboard offers {_PERIOD_PHRASE[draft.periods]}, but "
            f"environment '{env}' is {KIND_LABELS[side].lower()} only. Set "
            f"Periods offered to '{PERIOD_LABELS[side]}'.")
    return out


def _periods_hint(draft: Dashboard, store) -> str:
    """What the environments this dashboard reads can actually serve."""
    envs = store.list_environments()
    used = sorted({ds.env for ds in draft.datasets if ds.env and ds.env in envs})
    if not used:
        return "Add a dataset and this will say what its environment can serve."

    parts = []
    for env in used:
        pair = envs[env]
        if is_marketdata_env(pair):
            served = "market data · any period"
        elif env_serves(pair, "realtime") and env_serves(pair, "historical"):
            served = "both"
        else:
            side = standalone_side(pair) or (
                "realtime" if env_serves(pair, "realtime") else "historical")
            served = f"{KIND_LABELS[side].lower()} only"
        parts.append(f"`{env}` ({served})")
    return "Reads " + ", ".join(parts) + "."


def _kdb_dataset_problems(ds: Dataset, envs: dict, dashboard_time) -> list[str]:
    """Everything wrong with a KDB-backed dataset.

    Lifted out of ``validate`` whole when file datasets arrived: nearly all of it
    is about servers, periods and q, and wrapping each check in a source test
    would have made a long function longer without making it clearer.
    """
    problems: list[str] = []
    if _blank(ds.env):
        problems.append(f"Dataset '{ds.name}' has no environment selected.")

    if ds.mode == "raw" and _blank(ds.raw_qsql):
        problems.append(f"Dataset '{ds.name}' is set to raw q but the query is "
                        f"empty.")

    for i, f in enumerate(ds.filters, start=1):
        if _blank(f.column):
            problems.append(f"Dataset '{ds.name}', filter {i}: no column chosen.")
        if _blank(f.value):
            problems.append(f"Dataset '{ds.name}', filter {i} on "
                            f"'{f.column}': no value entered.")
        elif f.value_type == "date":
            # A date read as subtraction is exactly the failure q_date exists
            # to rule out (see core.qfmt) — worth catching here, before Save,
            # rather than as a wrong number once the dashboard is running.
            for v in (f.value if isinstance(f.value, list) else [f.value]):
                if is_placeholder(v):
                    continue
                try:
                    q_date(v)
                except ValueError as exc:
                    problems.append(f"Dataset '{ds.name}', filter {i} on "
                                    f"'{f.column}': {exc}")
                    break

    if ds.time_mode == "realtime":
        rt = resolve({"mode": "realtime"}, date.today())
    elif ds.time_mode == "custom":
        rt = resolve(ds.time_context or {"mode": "realtime"}, date.today())
    else:
        rt = dashboard_time

    # Market-data environments hold reference data: no date partitioning, so
    # the period simply does not apply to them.
    market = ds.env in envs and is_marketdata_env(envs[ds.env])
    if market:
        rt = resolve({"mode": "realtime"}, date.today())

    if ds.env not in envs:
        problems.append(f"Dataset '{ds.name}' uses unknown environment "
                        f"'{ds.env}'.")
    elif not market and envs[ds.env][rt.mode] is None:
        # An environment declared one-sided is not waiting for the other
        # half, so say what it does instead of naming a server to add.
        solo = standalone_side(envs[ds.env])
        wanted = "date ranges" if rt.mode == "historical" else "real-time"
        if solo:
            problems.append(
                f"Dataset '{ds.name}': environment '{ds.env}' is "
                f"{KIND_LABELS[solo].lower()} only, so it cannot show "
                f"{wanted}. Give this dataset a period it can answer.")
        else:
            problems.append(f"Dataset '{ds.name}': environment '{ds.env}' has "
                            f"no {KIND_LABELS[rt.mode].lower()} server — add "
                            f"one in Admin.")

    if rt.mode == "historical" and ds.mode == "raw" \
            and not has_date_constraint(ds.raw_qsql or ""):
        problems.append(
            f"Dataset '{ds.name}' is historical but its q never constrains "
            "'date'. Add a date within ({{date_from}};{{date_to}}) clause.")

    if ds.mode == "guided" and not ds.table:
        problems.append(f"Dataset '{ds.name}' has no table selected.")

    return problems


def _file_dataset_problems(ds: Dataset) -> list[str]:
    """Everything wrong with a file-backed dataset, in plain English.

    None of the KDB checks apply: there is no environment, no period and no
    query. What it has instead is a shape, and a shape nothing can be read
    through is the one way it fails before anybody has uploaded anything.
    """
    shape = ds.shape
    if shape is None:
        return [f"Dataset '{ds.name}' has no shape yet — upload a sample file "
                f"and confirm where its table sits."]

    out: list[str] = []
    names = [c.name.strip() for c in shape.columns]
    if not names:
        out.append(f"Dataset '{ds.name}' has no columns — confirm its shape "
                   f"against a sample file.")
    if any(not n for n in names):
        out.append(f"Dataset '{ds.name}' has a column with no name.")
    for name in sorted({n for n in names if n and names.count(n) > 1}):
        out.append(f"Dataset '{ds.name}' has two columns called '{name}'.")
    if shape.data_start <= shape.header_row:
        out.append(f"Dataset '{ds.name}': data starts on or above its header "
                   f"line — the header is line {shape.header_row + 1}.")
    return out


def _parameter_raw_columns(ds: Dataset | None, conn) -> list[str]:
    """What a ``column`` parameter can read from ``ds`` — its frame as fetched,
    before its own transforms run.

    The same frame :func:`~kdbmonitor.core.parameters.choices_for` resolves
    against at run time, so a column that only exists after a transform (say, a
    derive) is correctly still unknown here: the parameter never sees it either.
    A raw query's columns cannot be known without running it, so it answers
    nothing rather than guessing — which is why the caller skips this check
    wherever the list comes back empty.
    """
    if ds is None:
        return []
    if ds.source == "file":
        return [c.name for c in (ds.shape.columns if ds.shape else [])]
    return table_columns(ds, conn)


def _parameter_problems(draft: Dashboard, store) -> list[str]:
    """Everything wrong with this dashboard's parameters, in plain English.

    Covers the definition itself — a name, a usable kind, choices where the
    kind needs them, a default actually on offer — and the two ways a
    parameter misfires quietly rather than loudly: a ``{{param:name}}`` nobody
    declared (caught while the dashboard is still being built, rather than as
    an empty panel afterwards), and a ``column`` parameter reading a dataset
    that has not been fetched yet by the time the parameter is needed — see
    :func:`~kdbmonitor.core.dataset.run_datasets`, which resolves parameters
    against only the raw frames fetched so far.
    """
    problems: list[str] = []
    order = {ds.name: i for i, ds in enumerate(draft.datasets)}
    by_name = {ds.name: ds for ds in draft.datasets}
    seen: list[str] = []

    # Which parameters each dataset's own transforms reference — the ordering
    # rule only cares about transforms, since a widget reads the results only
    # after every dataset has finished running.
    used_in: list[set] = []
    for ds in draft.datasets:
        refs: set = set()
        for t in ds.transforms:
            refs |= params_in(t.params)
        used_in.append(refs)

    for p in draft.parameters:
        name = p.name.strip()
        label = name or p.name or "(unnamed)"
        if not name:
            problems.append("A parameter has no name.")
        elif name in seen:
            problems.append(f"Two parameters are both named '{name}'.")
        seen.append(name)

        if p.kind not in PARAMETER_KINDS:
            problems.append(f"Parameter '{label}' has an unknown kind "
                            f"'{p.kind}'.")
            continue

        if p.kind == "choice":
            if not p.choices:
                problems.append(f"Parameter '{label}' has no choices.")
            elif p.default and p.default not in p.choices:
                problems.append(f"Parameter '{label}': default '{p.default}' "
                                f"is not among its choices.")

        if p.kind == "column":
            ds = by_name.get(p.dataset)
            if ds is None:
                problems.append(f"Parameter '{label}' reads dataset "
                                f"'{p.dataset}', which does not exist.")
                continue

            conn = _connection_for(store, ds)
            columns = _parameter_raw_columns(ds, conn)
            if columns and p.column not in columns:
                problems.append(f"Parameter '{label}' reads column "
                                f"'{p.column}' from dataset '{ds.name}', which "
                                f"does not produce it.")

            my_index = order[ds.name]
            for i, refs in enumerate(used_in):
                if name in refs and my_index > i:
                    problems.append(
                        f"Parameter '{label}' reads dataset '{ds.name}', "
                        f"declared after '{draft.datasets[i].name}' — its "
                        f"choices will not be ready when "
                        f"'{draft.datasets[i].name}' runs.")
                    break

    declared = {p.name.strip() for p in draft.parameters if p.name.strip()}
    for name in sorted(unresolved_params(draft) - declared):
        problems.append(f"'{{{{param:{name}}}}}' is used but no parameter "
                        f"named '{name}' is declared.")

    return problems


def validate(draft: Dashboard, store) -> list[str]:
    """Everything wrong with this dashboard, in plain English. Empty when fine.

    Covers both invalid configurations and inputs simply left unfilled, so the
    editor can warn while you build rather than only when you press Save.
    """
    problems: list[str] = []
    envs = store.list_environments()
    if draft.source != "file":
        # The declaration decides what the stored period may be, so validate
        # against what this dashboard will actually run with rather than what
        # it was left on.
        draft.time_context = coerce_spec(draft.time_context, draft.periods)
        dashboard_time = resolve(draft.time_context, date.today())
        problems += _periods_problems(draft, envs)
    else:
        dashboard_time = resolve({"mode": "realtime"}, date.today())

    problems += _parameter_problems(draft, store)

    if _blank(draft.name):
        problems.append("The dashboard has no name.")
    if not draft.datasets:
        problems.append("No datasets yet — add one in the Data section.")
    elif not any(row.widgets for row in draft.rows):
        problems.append("No widgets yet — add a row and a widget in the Layout "
                        "section.")

    seen: list[str] = []
    for ds in draft.datasets:
        if _blank(ds.name):
            problems.append("A dataset has no name.")
        if ds.name in seen:
            problems.append(f"Duplicate dataset name '{ds.name}'.")

        for i, t in enumerate(ds.transforms, start=1):
            problems += _transform_problems(ds.name, i, t)

        # The dashboard's source governs its datasets. Switching a saved
        # dashboard between sources leaves the old ones behind, and a dataset
        # nobody is going to run is worth saying so about now rather than
        # leaving to show up as an empty panel.
        if ds.source != draft.source:
            reads = ("an uploaded file" if draft.source == "file"
                     else "KDB queries")
            problems.append(
                f"Dataset '{ds.name}' does not match this dashboard, which "
                f"reads {reads}. Delete it, or change the dashboard's source.")
        elif ds.source == "file":
            problems += _file_dataset_problems(ds)
        else:
            problems += _kdb_dataset_problems(ds, envs, dashboard_time)

        # Raw q referencing another dataset or hopening a second environment.
        # These live in the shared loop rather than in _kdb_dataset_problems
        # because they need `seen` to know what was defined above this dataset —
        # but they are still questions about q, so a file dataset is not asked
        # them. It may well be carrying answers: a dataset converted from a
        # query keeps its raw_qsql and extra_connections, and the file editor
        # shows neither, so a complaint about them names a field its owner
        # cannot reach and cannot clear.
        if ds.source != "file":
            for ref, _ in _REF.findall(ds.raw_qsql or ""):
                if ref not in seen:
                    problems.append(f"Dataset '{ds.name}' references '{ref}', which is "
                                    f"not defined above it.")
            for env in ds.extra_connections:
                if env not in envs:
                    problems.append(f"Dataset '{ds.name}' also-connects to unknown "
                                    f"environment '{env}'.")
            for env in _CONN_REF.findall(ds.raw_qsql or ""):
                if env.strip() not in envs:
                    problems.append(f"Dataset '{ds.name}' opens {{{{conn:{env.strip()}}}}}"
                                    f", which is not a known environment.")
        seen.append(ds.name)

    by_name = {ds.name: ds for ds in draft.datasets}
    for i, row in enumerate(draft.rows, start=1):
        if len(row.widgets) > 4:
            problems.append(f"Row {i} has {len(row.widgets)} widgets — a row holds "
                            f"at most 4 widgets.")
        if not row.widgets:
            problems.append(f"Row {i} is empty — add a widget or delete the row.")

        for w in row.widgets:
            label = f"Row {i}: {w.type} '{w.title}'" if w.title else f"Row {i}: {w.type}"
            if w.dataset not in by_name:
                problems.append(f"{label} uses unknown dataset '{w.dataset}'.")
                continue
            if w.width <= 0:
                problems.append(f"{label} has a non-positive width.")

            missing = missing_spec_fields(w)
            if missing:
                named = ", ".join(FIELD_LABELS.get(f, f) for f in missing)
                problems.append(f"{label} has no {named} set.")

            fmt = w.spec.get("fmt")
            if isinstance(fmt, str) and fmt and not is_valid_format(fmt):
                problems.append(f"{label}: '{fmt}' is not a usable number format.")
            for col, col_fmt in (w.spec.get("formats") or {}).items():
                if col_fmt and not is_valid_format(col_fmt):
                    problems.append(f"{label}: number format '{col_fmt}' for column "
                                    f"'{col}' is not usable.")

            # Catch a column that stopped existing — e.g. a group-by was changed
            # after the widget was bound to one of its outputs. Only a dataset
            # whose table schema is known has a *complete* column list; for a raw
            # query we know some of what it produces but never all of it, and
            # guessing there would flag columns that are perfectly real.
            ds = by_name[w.dataset]
            conn = _connection_for(store, ds)
            known = dataset_columns(ds, conn) if table_columns(ds, conn) else []
            if known:
                for column in referenced_columns(w):
                    if column not in known:
                        problems.append(f"{label}: column '{column}' is not produced "
                                        f"by dataset '{ds.name}'.")
    return problems


# --- draft state -----------------------------------------------------------

def _draft(store) -> Dashboard:
    draft = st.session_state.get("dash_draft")
    wanted = st.session_state.get("dash_edit_id")
    if draft is None or draft.id != wanted:
        draft = store.get_dashboard(wanted) or Dashboard(id=None, name="New dashboard")
        st.session_state["dash_draft"] = draft
    return draft


def _close() -> None:
    for key in ("dash_draft", "dash_mode", "dash_edit_id", "dash_learned_cols"):
        st.session_state.pop(key, None)


def _forget(pattern: str) -> None:
    """Drop the widget state stored under keys matching ``pattern``.

    Every control here is keyed by *position* — ``r2w1_ti`` is "row 2, widget
    1's title" — and writes its value straight back into the draft on each
    rerun. Move or delete an element and those positions renumber underneath
    keys that do not: the widget that shuffled up is drawn from the value of
    the one that used to sit there, and writes it into the draft. Deleting a
    row left the survivor holding the deleted row's height; swapping two
    widgets wrote them straight back the way they were, so the move appeared
    not to happen at all.

    Forgetting the keys makes the next pass read from the draft, which is the
    thing that actually changed. Call it for the whole span that renumbers,
    not just the element touched.
    """
    for key in [k for k in list(st.session_state) if re.match(pattern, str(k))]:
        st.session_state.pop(key, None)


# One transform's controls: the kind selectbox (``ds0_tk_2``) and its form,
# which lives under its own index (``ds0_t2_gc_0``). Deliberately no match for
# the dataset's own ``_t`` / ``_tm`` / ``_tc`` keys — those do not renumber.
_TRANSFORM_KEYS = r"%s_t(?:k_|\d)"

# Every row's controls, and every widget's inside them (``r1_h``, ``r1w0_ti``,
# ``r1w0_spec_cols``). A row that moves or goes renumbers all of them.
_ROW_KEYS = r"r\d+"


def learned_columns(name: str) -> list[str]:
    """The columns this dataset's query returned the last time it was run here.

    A raw q query's shape is only knowable by running it, so the editor keeps
    what each preview returned and offers it to the column pickers. Cleared with
    the draft, since it belongs to one editing session.
    """
    return list(st.session_state.get("dash_learned_cols", {}).get(name, []))


def _remember_columns(name: str, columns: list[str]) -> None:
    st.session_state.setdefault("dash_learned_cols", {})[name] = list(columns)


def _connection_for(store, ds: Dataset):
    """Any server in the dataset's environment — used only to read the schema
    for column pickers, so which side it comes from does not matter."""
    pair = store.list_environments().get(getattr(ds, "env", "")) or {}
    return (pair.get("marketdata") or pair.get("realtime")
            or pair.get("historical"))


def _coerce(raw: str, value_type: str):
    """Turn the text a text_input hands back into what the value_type needs.

    Only ``number`` needs this: every other type — including the newer
    ``date``, ``time`` and ``expression`` — is q-formatted from its text as
    typed (see core.qfmt.format_q_value), so passing it through unchanged is
    correct, not an oversight.
    """
    if value_type != "number":
        return raw
    try:
        return float(raw) if "." in raw else int(raw)
    except ValueError:
        return raw


def _kv_lines(text: str) -> dict:
    """Parse 'a = b' lines into a dict, ignoring blanks."""
    return dict((a.strip(), b.strip())
                for a, _, b in (line.partition("=") for line in text.splitlines())
                if a.strip() and b.strip())


def _ref_tokens(draft: Dashboard, index: int, store) -> list[str]:
    """Every ``{{name.column}}`` a dataset at ``index`` could reference.

    One token per column of a dataset declared *above* it — a later dataset's
    frame is never fed forward until it too has run (see
    ``core.dataset.run_datasets``), so referencing one is not a token that
    will ever resolve. Reads the same ``dataset_columns`` a widget's own
    pickers already trust, rather than builder.py's literal ``{{stepN.col}}``,
    since a dashboard's schema is known well enough here to name the column
    for real.
    """
    tokens: list[str] = []
    for ds in draft.datasets[:index]:
        conn = _connection_for(store, ds)
        for col in dataset_columns(ds, conn, learned_columns(ds.name)):
            tokens.append(f"{{{{{ds.name}.{col}}}}}")
    return tokens


def _insert_reference(tokens: list[str], text_key: str, key: str) -> None:
    """The 'Insert reference' control, appending a token to the text widget at
    ``text_key``. Must run *before* that widget is instantiated — writing to
    ``st.session_state[text_key]`` after Streamlit has already built the
    widget for this run raises, which is why this is always called ahead of
    the text input it feeds rather than beside or after it.
    """
    if not tokens:
        return
    rc = st.columns([4, 1], vertical_alignment="bottom")
    token = rc[0].selectbox("Insert reference", tokens, key=f"{key}_refsel")
    if rc[1].button("Insert", key=f"{key}_refins", icon=":material/add:"):
        st.session_state[text_key] = (
            st.session_state.get(text_key, "") + " " + token).strip()
        st.rerun()


# --- dataset section -------------------------------------------------------

def _filters_form(ds: Dataset, columns: list[str], key: str,
                  ref_tokens: list[str] | None = None) -> None:
    st.caption("Filters — combined with AND, sent to KDB as the where clause.")
    for i, f in enumerate(list(ds.filters)):
        c = st.columns([1.7, 1, 1.7, 1.3, 0.8, 0.6, 0.6], vertical_alignment="bottom")
        f.column = _pick(c[0], "Column", columns, f.column, f"{key}_fc_{i}")
        f.op = c[1].selectbox("Op", OPS, index=OPS.index(f.op) if f.op in OPS else 0,
                              key=f"{key}_fo_{i}")

        value_key = f"{key}_fv_{i}"
        # A date picker rather than free text — but only while the value is
        # not already a {{dataset.column}} reference, which a date_input has
        # no way to hold. Column c[5]'s insert control runs before this one so
        # a click there can still set session_state[value_key] safely.
        use_picker = f.value_type == "date" and f.op != "in" \
            and not is_placeholder(f.value)
        if ref_tokens and not use_picker:
            with c[5].popover("", icon=":material/data_object:",
                              help="Insert a {{dataset.column}} reference to "
                                   "an earlier dataset's output"):
                token = st.selectbox("Reference", ref_tokens,
                                     key=f"{key}_frefsel_{i}")
                if st.button("Insert", icon=":material/add:",
                             key=f"{key}_frefins_{i}"):
                    st.session_state[value_key] = (
                        st.session_state.get(value_key, "") + " " + token).strip()
                    st.rerun()

        if use_picker:
            try:
                current = (date.fromisoformat(str(f.value)) if f.value
                          else date.today())
            except ValueError:
                current = date.today()
            raw = c[2].date_input("Value", value=current,
                                  key=value_key).isoformat()
        else:
            raw = c[2].text_input("Value",
                                  value=", ".join(map(str, f.value))
                                  if isinstance(f.value, list) else str(f.value),
                                  key=value_key)
        f.value_type = c[3].selectbox(
            "Type", VALUE_TYPES,
            index=VALUE_TYPES.index(f.value_type) if f.value_type in VALUE_TYPES
            else 0, format_func=lambda v: VALUE_TYPE_LABELS.get(v, v),
            key=f"{key}_ft_{i}")
        f.value = ([v.strip() for v in raw.split(",")] if f.op == "in"
                   else _coerce(raw, f.value_type))
        f.negated = c[4].checkbox("not", value=f.negated, key=f"{key}_fn_{i}")
        if c[6].button("", icon=":material/close:", key=f"{key}_fx_{i}"):
            ds.filters.pop(i)
            _forget(rf"{key}_f")               # every filter below shuffles up
            st.rerun()

    if st.button("Add filter", icon=":material/add:", key=f"{key}_addf"):
        ds.filters.append(Filter(column=columns[0] if columns else "", op="=",
                                 value="", value_type="symbol"))
        st.rerun()


def _transform_form(t: Transform, columns: list[str], key: str) -> None:
    p = t.params
    if t.kind == "derive":
        c = st.columns([1.6, 1.4, 3], vertical_alignment="bottom")
        p["column"] = c[0].text_input("New column", value=p.get("column", ""),
                                      key=f"{key}_dc")
        kinds = ["arithmetic", "suffix_map"]
        p["kind"] = c[1].selectbox("How", kinds,
                                   index=kinds.index(p.get("kind", "arithmetic")),
                                   key=f"{key}_dk")
        if p["kind"] == "arithmetic":
            p["expr"] = c[2].text_input("Expression", value=p.get("expr", ""),
                                        placeholder="100 * executed / size",
                                        key=f"{key}_de")
        else:
            p["source"] = _pick(c[2], "From column", columns,
                                p.get("source", ""), f"{key}_ds")
            p["mapping"] = _kv_lines(st.text_area(
                "Suffix = label (one per line, e.g. `.HK = Hong Kong`)",
                value="\n".join(f"{k} = {v}" for k, v in p.get("mapping", {}).items()),
                key=f"{key}_dm", height=90))
            cc = st.columns([1.4, 2], vertical_alignment="bottom")
            # Default from the map itself, so a map written before this setting
            # existed keeps matching exactly what it matched before.
            current = int(p.get("length") or suffix_length(p["mapping"]) or 3)
            p["length"] = int(cc[0].number_input(
                "Suffix length", 1, 12, current, key=f"{key}_dl",
                help="How many characters at the end of the value make the "
                     "suffix. 3 reads '700.HK' as '.HK'; write the suffixes "
                     "above exactly that long."))
            p["default"] = cc[1].text_input("Fallback",
                                            value=p.get("default", "Unknown"),
                                            key=f"{key}_dd")
            odd = [k for k in p["mapping"] if len(k) != p["length"]]
            if odd:
                them = "it" if len(odd) == 1 else "them"
                st.caption(f":orange[{', '.join(odd)} — not {p['length']} "
                           f"characters, so nothing will match {them}.]")

    elif t.kind == "filter":
        c = st.columns([2, 1, 2], vertical_alignment="bottom")
        p["column"] = _pick(c[0], "Column", columns, p.get("column", ""),
                            f"{key}_fc")
        ops = ["=", "!=", "<", "<=", ">", ">="]
        p["op"] = c[1].selectbox("Op", ops,
                                 index=ops.index(p["op"]) if p.get("op") in ops
                                 else 0, key=f"{key}_fo")
        p["value"] = _coerce(c[2].text_input("Value", value=str(p.get("value", "")),
                                             key=f"{key}_fv"), "number")

    elif t.kind == "groupby":
        p["keys"] = _pick_many(st, "Group by", columns, p.get("keys", []),
                               f"{key}_gk")
        st.caption("Aggregations")
        for i, a in enumerate(list(p.get("aggs", []))):
            c = st.columns([2, 1.4, 2, 0.6], vertical_alignment="bottom")
            a["column"] = _pick(c[0], "Column", columns, a["column"],
                                f"{key}_gc_{i}")
            a["func"] = c[1].selectbox("Func", AGG_FUNCS,
                                       index=AGG_FUNCS.index(a["func"]),
                                       key=f"{key}_gf_{i}")
            a["as"] = c[2].text_input("As", value=a["as"], key=f"{key}_ga_{i}")
            if c[3].button("", icon=":material/close:", key=f"{key}_gx_{i}"):
                p["aggs"].pop(i)
                _forget(rf"{key}_g")           # every aggregation below shuffles up
                st.rerun()
        if st.button("Add aggregation", icon=":material/add:", key=f"{key}_gadd"):
            p.setdefault("aggs", []).append(
                {"column": columns[0] if columns else "", "func": "sum",
                 "as": "value"})
            st.rerun()

    elif t.kind == "sort":
        c = st.columns([3, 1.4], vertical_alignment="bottom")
        p["columns"] = _pick_many(c[0], "Sort by", columns,
                                  p.get("columns", []), f"{key}_sc")
        p["ascending"] = c[1].selectbox(
            "Order", [True, False], index=0 if p.get("ascending", True) else 1,
            format_func=lambda v: "Ascending" if v else "Descending",
            key=f"{key}_sa")

    elif t.kind == "limit":
        p["n"] = int(st.number_input("Keep first N rows", 1, 1_000_000,
                                     int(p.get("n", 100)), key=f"{key}_ln"))

    elif t.kind == "rename":
        p["mapping"] = _kv_lines(st.text_area(
            "Old = New (one per line)",
            value="\n".join(f"{k} = {v}" for k, v in p.get("mapping", {}).items()),
            key=f"{key}_rm", height=90))


# --- the component library -------------------------------------------------
#
# A transform or a widget worth building once is worth keeping. Saving copies
# it out under a name; loading copies it back in. What lands in the draft is an
# ordinary part of that dashboard — edit it, then save it back under the same
# name or a new one. Nothing is linked: changing a dashboard never rewrites the
# library, and saving to the library never reaches into a dashboard.

LIBRARY_KINDS = {"transform": "step", "widget": "widget", "parameter": "parameter"}


def _component_summary(c: Component) -> str:
    """A one-line 'what is in this' for the picker, so a name need not carry
    the whole description."""
    p = c.payload or {}
    if c.kind == "widget":
        return f"{p.get('type', '?')} · {p.get('title') or 'untitled'}"
    if c.kind == "parameter":
        return f"{p.get('kind', '?')} · {p.get('name') or 'unnamed'}"

    kind, params = p.get("kind", "?"), p.get("params", {})
    if kind == "derive":
        source = params.get("source") or params.get("expr") or "?"
        return f"derive `{params.get('column') or '?'}` from {source}"
    if kind == "groupby":
        return f"group by {', '.join(params.get('keys') or []) or '?'}"
    if kind == "sort":
        return f"sort by {', '.join(params.get('columns') or []) or '?'}"
    if kind in ("filter", "rename"):
        return f"{kind} `{params.get('column') or params.get('from') or '?'}`"
    return kind


def _save_to_library(container, store, kind: str, payload: dict, key: str,
                     suggested: str = "") -> None:
    """The 'keep this for next time' popover.

    One control for the name, because saving something new and saving back a
    component you loaded and improved are the same act: type a name that is not
    in the library yet, or pick the one to replace.
    """
    names = [c.name for c in store.list_components(kind)]
    noun = LIBRARY_KINDS[kind]
    with container.popover("", icon=":material/bookmark_add:",
                           help=f"Save this {noun} to the library, to reuse in "
                                f"this or any other dashboard"):
        name = st.selectbox(
            "Save as", names, key=f"{key}_lib_name", accept_new_options=True,
            index=names.index(suggested) if suggested in names else None,
            placeholder="Name it, or pick one to replace",
            help="Type a new name, or choose a saved one to overwrite.")
        name = (name or "").strip()
        if name in names:
            st.caption(f":orange[Replaces the saved {noun} '{name}'.]")
        if st.button("Save to library", icon=":material/save:", disabled=not name,
                     key=f"{key}_lib_save"):
            store.save_component(kind, name, payload)
            st.toast(f"Saved {noun} '{name}'", icon=":material/bookmark_added:")
            st.rerun()


def _load_from_library(container, store, kind: str, key: str,
                       disabled: bool = False) -> Component | None:
    """Pick a saved component to add. Returns it once, on the click."""
    saved = store.list_components(kind)
    noun = LIBRARY_KINDS[kind]
    if not saved:
        return None
    with container.popover("From library", icon=":material/bookmark:",
                           disabled=disabled):
        names = [c.name for c in saved]
        pick = st.selectbox(f"Saved {noun}s", names, index=None, key=f"{key}_pick",
                            placeholder=f"Choose a saved {noun}")
        chosen = next((c for c in saved if c.name == pick), None)
        if chosen:
            st.caption(_component_summary(chosen))
        st.caption(f"A copy is added here — edit it freely, the saved {noun} "
                   f"stays as it is until you save over it.")
        if st.button("Add a copy", icon=":material/add:", key=f"{key}_add",
                     disabled=chosen is None):
            return chosen
    return None


def _render_library(store) -> None:
    saved = store.list_components()
    st.caption("Transforms and widgets you have saved. They belong to no one "
               "dashboard: add a copy from **Data** or **Layout**, and every "
               "dashboard can use the same one.")
    if not saved:
        st.info("Nothing saved yet — use the bookmark button on a transform or "
                "a widget.", icon=":material/bookmark:")
        return

    for c in saved:
        with st.container(border=True):
            cc = st.columns([2.4, 1, 3.4, 1, 0.8], vertical_alignment="bottom")
            cc[0].markdown(f"**{c.name}**")
            cc[1].markdown(f":blue-badge[{LIBRARY_KINDS.get(c.kind, c.kind)}]")
            cc[2].caption(_component_summary(c))
            with cc[3].popover("Rename", icon=":material/edit:"):
                new = st.text_input("New name", value=c.name,
                                    key=f"lib{c.id}_rn").strip()
                taken = new != c.name and store.get_component_by_name(c.kind, new)
                if taken:
                    st.caption(f":orange[Another {LIBRARY_KINDS[c.kind]} is "
                               f"already called '{new}'.]")
                if st.button("Rename", key=f"lib{c.id}_rnb",
                             disabled=not new or bool(taken)):
                    store.rename_component(c.id, new)
                    st.rerun()
            with cc[4].popover("", icon=":material/delete:"):
                st.caption(f"Delete '{c.name}'? Dashboards already using a copy "
                           f"keep it.")
                if st.button("Delete", icon=":material/delete:",
                             key=f"lib{c.id}_delb"):
                    store.delete_component(c.id)
                    st.rerun()


def _dataset_card(store, ds: Dataset, index: int, draft: Dashboard) -> None:
    key = f"ds{index}"
    conn = _connection_for(store, ds)

    subtitle = ((ds.file_label or "an uploaded file") if ds.source == "file"
                else (env_label(ds.env, store.list_environments().get(ds.env)
                                or {}) if ds.env else "no environment"))
    with st.expander(f"**{ds.name}** · {subtitle}", expanded=True):
        if ds.source == "file":
            from kdbmonitor.ui import fileshape

            head = st.columns([3, 1.8, ICON_COL], vertical_alignment="bottom")
            was = ds.name
            ds.name = head[0].text_input("Name", value=ds.name, key=f"{key}_n")
            # The held sample is filed under the dataset's name, and the box
            # above hands back the new one on the very rerun it is typed in.
            # Without moving it, renaming a dataset would blank its grid and its
            # preview as though the sample had never been uploaded.
            fileshape.rename_sample(was, ds.name)
            ds.max_rows = int(head[1].number_input(
                "Max rows", 1, 1_000_000, ds.max_rows, step=100,
                key=f"{key}_mr"))
            if head[2].button("", icon=":material/delete:", key=f"{key}_del"):
                fileshape.forget_sample(ds.name)
                draft.datasets.pop(index)
                _forget(r"ds\d+")              # every card below renumbers
                st.rerun()
            fileshape.render(ds, key=key)
        else:
            head = st.columns([2, 2, 1.8, 1.6, ICON_COL], vertical_alignment="bottom")
            ds.name = head[0].text_input("Name", value=ds.name, key=f"{key}_n")
            pairs = store.list_environments()
            envs = sorted(pairs)
            ds.env = head[1].selectbox(
                "Environment", envs or [ds.env],
                index=envs.index(ds.env) if ds.env in envs else 0,
                key=f"{key}_e",
                format_func=lambda e: env_label(e, pairs.get(e) or {}),
                help="Named by the servers it holds, so you are picking a "
                     "database rather than remembering which environment name "
                     "stands for it.")
            modes = list(PERIOD_MODE_LABELS)
            market = is_marketdata_env(
                store.list_environments().get(ds.env) or {})
            ds.time_mode = head[2].selectbox(
                "Period", modes,
                index=modes.index(ds.time_mode) if ds.time_mode in modes else 0,
                format_func=lambda m: PERIOD_MODE_LABELS[m],
                key=f"{key}_tm", disabled=market,
                help="Market data is not partitioned by date — the period does not "
                     "apply." if market
                     else "'A historical range of its own' is what reads history "
                          "for this one dataset while the rest of the dashboard "
                          "stays live — or the other way around.")
            ds.max_rows = int(head[3].number_input("Max rows", 1, 1_000_000,
                                                   ds.max_rows, step=100, key=f"{key}_mr"))
            if head[4].button("", icon=":material/delete:", key=f"{key}_del"):
                draft.datasets.pop(index)
                _forget(r"ds\d+")                  # every card below renumbers
                st.rerun()

            if ds.time_mode == "custom":
                rng = ((ds.time_context or {}).get("range") or {})
                kinds = ["preset", "absolute"]
                kind = st.selectbox(
                    "Its own period", kinds,
                    index=kinds.index(rng.get("kind"))
                    if rng.get("kind") in kinds else 0,
                    format_func=lambda k: "A preset range" if k == "preset"
                    else "A fixed date range", key=f"{key}_tck")
                if kind == "preset":
                    labels = [PRESET_LABELS[p] for p in PRESETS]
                    current = rng.get("name", "last_30d")
                    chosen = st.selectbox("Range", labels,
                                          index=list(PRESETS).index(current)
                                          if current in PRESETS else 3,
                                          key=f"{key}_tc")
                    ds.time_context = {"mode": "historical",
                                       "range": {"kind": "preset",
                                                 "name": PRESETS[labels.index(chosen)]}}
                else:
                    cc = st.columns(2, vertical_alignment="bottom")
                    d1 = cc[0].date_input(
                        "From", value=date.fromisoformat(rng["from"])
                        if rng.get("from") else date.today(), key=f"{key}_tcf")
                    d2 = cc[1].date_input(
                        "To", value=date.fromisoformat(rng["to"])
                        if rng.get("to") else date.today(), key=f"{key}_tct")
                    ds.time_context = {"mode": "historical",
                                       "range": {"kind": "absolute",
                                                 "from": d1.isoformat(),
                                                 "to": d2.isoformat()}}

            if market:
                st.caption(":violet[Market data] — reference tables, queried the "
                           "same way whatever period the dashboard is showing.")

            ds.mode = st.radio("Query", ["guided", "raw"], horizontal=True,
                               index=0 if ds.mode == "guided" else 1, key=f"{key}_m")

            if ds.mode == "guided":
                tables = sorted(getattr(conn, "schema", {}) or {})
                ds.table = st.selectbox("Table", tables or [ds.table],
                                        index=tables.index(ds.table)
                                        if ds.table in tables else 0, key=f"{key}_t")
                # Filters are the query's where clause, so they address the table
                # as KDB holds it — not the shape the transforms leave behind.
                _filters_form(ds, table_columns(ds, conn), key,
                             ref_tokens=_ref_tokens(draft, index, store))
            else:
                _insert_reference(_ref_tokens(draft, index, store),
                                  f"{key}_q", key)
                ds.raw_qsql = st.text_area("q", value=ds.raw_qsql or "", height=160,
                                           help=RAW_HELP, key=f"{key}_q")
                others = [e for e in sorted(store.list_environments()) if e != ds.env]
                ds.extra_connections = st.multiselect(
                    "Also connect (for hopen)", others,
                    default=[e for e in ds.extra_connections if e in others],
                    key=f"{key}_xc",
                    help="Extra environments this query opens with hopen, so one "
                         "query can span two KDB processes. Reference one in your q "
                         "as {{conn:ENV}} — it becomes that server's `:host:port.")
                if ds.extra_connections:
                    st.caption("Use in your q: "
                               + "  ".join(f"`{{{{conn:{e}}}}}`"
                                           for e in ds.extra_connections))

        st.markdown("**Transforms**")
        for i, t in enumerate(list(ds.transforms)):
            with st.container(border=True):
                c = st.columns([2, 4.4, 0.6, 0.6, 0.6, 0.6],
                               vertical_alignment="bottom")
                t.kind = c[0].selectbox("Kind", TRANSFORM_KINDS,
                                        index=TRANSFORM_KINDS.index(t.kind),
                                        key=f"{key}_tk_{i}")
                if c[2].button("", icon=":material/arrow_upward:",
                               key=f"{key}_tu_{i}", disabled=i == 0):
                    ds.transforms[i - 1], ds.transforms[i] = \
                        ds.transforms[i], ds.transforms[i - 1]
                    _forget(_TRANSFORM_KEYS % key)
                    st.rerun()
                if c[3].button("", icon=":material/arrow_downward:",
                               key=f"{key}_td_{i}",
                               disabled=i == len(ds.transforms) - 1):
                    ds.transforms[i + 1], ds.transforms[i] = \
                        ds.transforms[i], ds.transforms[i + 1]
                    _forget(_TRANSFORM_KEYS % key)
                    st.rerun()
                _save_to_library(c[4], store, "transform", transform_to_dict(t),
                                 f"{key}_t{i}",
                                 suggested=str(t.params.get("column") or ""))
                if c[5].button("", icon=":material/close:", key=f"{key}_tx_{i}"):
                    ds.transforms.pop(i)
                    _forget(_TRANSFORM_KEYS % key)
                    st.rerun()
                # Columns available to a transform are those produced by the
                # ones before it, not the dataset's final shape.
                upstream = Dataset(name=ds.name, env=ds.env, mode=ds.mode,
                                   table=ds.table, transforms=ds.transforms[:i],
                                   source=ds.source, shape=ds.shape)
                _transform_form(t, dataset_columns(upstream, conn,
                                                   learned_columns(ds.name)),
                                f"{key}_t{i}")

        add = st.columns([1.6, 1.6, 4.8], vertical_alignment="bottom")
        if add[0].button("Add transform", icon=":material/add:",
                         key=f"{key}_taddb"):
            ds.transforms.append(Transform(
                kind="derive",
                params={"column": "", "kind": "arithmetic", "expr": ""}))
            st.rerun()
        saved = _load_from_library(add[1], store, "transform", f"{key}_tlib")
        if saved:
            ds.transforms.append(transform_from_dict(saved.payload))
            st.rerun()


def _parameter_card(store, p: Parameter, index: int, draft: Dashboard) -> None:
    """One parameter's definition — what it is called, what kind it is, and
    where a 'column' kind reads its values from.

    Keyed by position (``pm{index}``) like a dataset card, and for the same
    reason: deleting or reordering renumbers every card below it, and
    ``_forget`` clears the stale widget state so the next pass reads from the
    draft rather than from a control that used to sit at that position.
    """
    key = f"pm{index}"
    with st.expander(f"**{p.name or '(unnamed)'}** · {p.kind}", expanded=True):
        head = st.columns([2, 2, 1.6, ICON_COL, ICON_COL],
                          vertical_alignment="bottom")
        was = p.name
        p.name = head[0].text_input("Name", value=p.name, key=f"{key}_n")
        # Session state remembers a reader's pick by name — follow a rename the
        # same way fileshape.rename_sample follows a dataset's, or whoever had
        # already picked something loses that pick the moment it is renamed.
        ui_parameters.rename(draft, was, p.name)
        p.label = head[1].text_input(
            "Label", value=p.label, key=f"{key}_lbl",
            help="Shown on the control. Falls back to the name.")
        p.kind = head[2].selectbox(
            "Kind", PARAMETER_KINDS,
            index=PARAMETER_KINDS.index(p.kind) if p.kind in PARAMETER_KINDS
            else 0, key=f"{key}_k")
        _save_to_library(head[3], store, "parameter", parameter_to_dict(p),
                         key, suggested=p.name)
        if head[4].button("", icon=":material/delete:", key=f"{key}_del"):
            st.session_state.pop(ui_parameters.value_key(draft.id, p.name), None)
            draft.parameters.pop(index)
            _forget(r"pm\d+")              # every card below renumbers
            st.rerun()

        if p.kind == "choice":
            raw = st.text_input(
                "Choices (comma-separated)",
                value=", ".join(p.choices), key=f"{key}_ch")
            p.choices = [c.strip() for c in raw.split(",") if c.strip()]
        elif p.kind == "column":
            names = [ds.name for ds in draft.datasets]
            c = st.columns(2, vertical_alignment="bottom")
            p.dataset = c[0].selectbox(
                "Dataset", names or [p.dataset],
                index=names.index(p.dataset) if p.dataset in names else 0,
                key=f"{key}_ds")
            ds = next((d for d in draft.datasets if d.name == p.dataset), None)
            conn = _connection_for(store, ds) if ds else None
            columns = (dataset_columns(ds, conn, learned_columns(p.dataset))
                      if ds else [])
            p.column = _pick(c[1], "Column", columns, p.column, f"{key}_col")

        p.default = st.text_input(
            "Default", value=p.default, key=f"{key}_def",
            help="What applies until a reader picks something else, and what "
                 "a stale or missing pick falls back to.")


def _render_parameters(store, draft: Dashboard) -> None:
    """Controls the reader of this dashboard will get, defined here.

    A 'column' parameter's picker offers whatever the last preview run's frame
    actually held — that is decided at run time, in ``core.parameters`` — but
    which dataset and which column it reads is a fact about the dashboard,
    settled here while it is built.
    """
    st.markdown("**Parameters**")
    st.caption("Controls the reader sets when viewing this dashboard. A "
               "parameter never reaches a query — only the transforms "
               "downstream of it re-shape when it changes.")
    for i, p in enumerate(list(draft.parameters)):
        _parameter_card(store, p, i, draft)

    add = st.columns([1.6, 1.6, 4.8], vertical_alignment="bottom")
    if add[0].button("Add parameter", icon=":material/add:",
                     key="dash_param_addb"):
        draft.parameters.append(Parameter(
            name=unique_name("param", [p.name for p in draft.parameters])))
        st.rerun()
    saved = _load_from_library(add[1], store, "parameter", "dash_param_lib")
    if saved:
        loaded = parameter_from_dict(saved.payload)
        taken = [p.name for p in draft.parameters]
        if loaded.name in taken:
            loaded.name = unique_name(loaded.name, taken)
        draft.parameters.append(loaded)
        st.rerun()


def _render_data(store, mgr, draft: Dashboard) -> None:
    if draft.source != "file" and not store.list_environments():
        st.warning("No connections yet — add one in Admin first.",
                   icon=":material/warning:")

    for i, ds in enumerate(list(draft.datasets)):
        _dataset_card(store, ds, i, draft)

    if st.button("Add dataset", icon=":material/add:", type="primary"):
        envs = sorted(store.list_environments())
        draft.datasets.append(Dataset(
            name=unique_name("dataset", [d.name for d in draft.datasets]),
            env="" if draft.source == "file" else (envs[0] if envs else ""),
            source=draft.source))
        st.rerun()

    st.divider()
    _render_parameters(store, draft)

    if draft.datasets and st.button("Run and inspect each step",
                                    icon=":material/play_arrow:",
                                    key="dash_preview_run",
                                    help="Send each KDB dataset's query and "
                                         "apply each file dataset's transforms "
                                         "to its held sample, one step at a "
                                         "time, so you can check what every "
                                         "step did."):
        # Rerun rather than render below: the run teaches the editor what a raw
        # query returns, and the column pickers above should show that straight
        # away rather than one interaction later.
        st.session_state["dash_preview_data"] = True
        st.rerun()


PREVIEW_ROWS = 200          # rows shown per step; the counts are of the full frame


def _step_caption(step: Step) -> str:
    """Row count, what it changed, and which columns came and went."""
    bits = [f"{step.rows:,} row(s)"]
    delta = step.row_delta
    if delta:
        bits.append(f"{delta:+,} vs previous step")
    elif delta == 0 and step.index:
        bits.append("same row count")
    if step.added:
        bits.append("+ " + ", ".join(step.added))
    if step.dropped:
        bits.append("− " + ", ".join(step.dropped))
    return " · ".join(bits)


def _render_step(step: Step) -> None:
    marker = ":material/database:" if step.index == 0 else ":material/function:"
    st.markdown(f"{marker} **{step.label}**")
    if step.error:
        st.error(f"This step failed: {step.error}", icon=":material/error:")
        st.caption("The steps above it ran — the frame shown for the previous "
                   "step is exactly what this one was handed.")
        return
    st.caption(_step_caption(step))
    if step.rows:
        st.dataframe(step.df.head(PREVIEW_ROWS), use_container_width=True,
                     height=min(240, 60 + 35 * min(step.rows, 5)))
    else:
        st.caption(":orange[No rows at this step.]")


def run_preview(store, mgr, draft: Dashboard):
    """Every dataset run step by step, so the editor can show what each did.

    A file dataset previews against the sample its shape editor is holding, so
    the frame the author sees is the same one a viewer's upload would produce.
    Every dataset previews against the parameter values currently selected —
    otherwise the author would be building a pipeline against a different
    frame from the one their reader will actually see.
    """
    if not st.session_state.pop("dash_preview_data", False):
        return None
    from kdbmonitor.ui.fileshape import stored_sample
    uploads = {ds.name: stored_sample(ds.name) for ds in draft.datasets
               if ds.source == "file" and stored_sample(ds.name) is not None}
    traces = trace_datasets(draft, store, mgr, date.today(), uploads=uploads,
                            chosen=ui_parameters.chosen_values(draft))
    for name, trace in traces.items():
        # The query's own columns are the only reliable knowledge we have of a
        # raw dataset's shape — keep them for the column pickers.
        if trace.steps:
            _remember_columns(name, trace.steps[0].columns)
    return traces


def _render_dataset_results(traces) -> None:
    """Dataset results for the Data section, stage by stage.

    Rendered outside the bounded form column, because result rows want the
    width. Every transform gets its own frame so you can see which step dropped
    the rows or added the column you were expecting — a whole-pipeline result
    only tells you that something went wrong, not where.
    """
    if not traces:
        return

    for name, trace in traces.items():
        with st.container(border=True):
            st.markdown(f"### {name}")
            with st.expander("Query sent", expanded=not trace.steps):
                st.code(trace.qsql or "(no query)", language="python")

            if trace.error:
                st.error(trace.error, icon=":material/error:")
                continue

            failed = trace.failed_step
            if failed:
                st.warning(f"The pipeline stops at step {failed.index} — later "
                           f"transforms never ran.", icon=":material/warning:")
            for step in trace.steps:
                _render_step(step)
                if step is not trace.steps[-1]:
                    st.divider()


# --- widget spec forms -----------------------------------------------------

COLUMN_HELP = ("Not listed? Type the name — a raw q query's columns are only "
               "known once it has run.")


def _pick(container, label: str, columns: list[str], current: str, key: str) -> str:
    """A column picker that can always show what is already configured, and
    always lets you name a column it has not been told about."""
    options = with_stored(columns, current) or ([current] if current else [""])
    index = options.index(current) if current in options else 0
    return container.selectbox(label, options, index=index, key=key,
                               accept_new_options=True, help=COLUMN_HELP)


def _pick_many(container, label: str, columns: list[str], current, key: str,
               help_text: str = COLUMN_HELP, **kwargs) -> list[str]:
    """The same, for a picker that takes several columns."""
    return container.multiselect(label, with_stored(columns, current),
                                 default=list(current or []), key=key,
                                 accept_new_options=True, help=help_text,
                                 **kwargs)


def _move_column(spec: dict, shown: list[str], i: int, j: int, key: str) -> None:
    """Swap two of a table's columns, as a button callback.

    An empty ``columns`` means 'all of them, in dataset order'; there is no
    order to move within until it is written down, so the first move records
    the list as it stands and moves within that.

    Runs as an ``on_click`` rather than inline, because the multiselect holds
    the selection in its own widget state: it has to be told the new order too,
    and Streamlit only allows that before the widget is drawn.
    """
    order = list(shown)
    order[i], order[j] = order[j], order[i]
    spec["columns"] = order
    st.session_state[key] = order


def _format_picker(container, label: str, current: str, key: str,
                   show_label: bool = True) -> str:
    """Choose a format by its sample output rather than typing a spec.

    Numbers and dates share one list because a column is one or the other and
    the samples say which is which: '1,234.57' and '2026-07-27 09:30' need no
    further explanation.

    ``show_label`` collapses the label for repeated rows — an empty label string
    would still reserve space and leave the help icon floating on its own.
    """
    options = list(VALUE_FORMATS) + [CUSTOM_FORMAT]
    current_label = format_label_for(current)
    chosen = container.selectbox(
        label, options, index=options.index(current_label), key=f"{key}_pick",
        label_visibility="visible" if show_label else "collapsed",
        help=("Pick how the value should read — each option is a sample of what "
              "prints. Date formats apply to date, timestamp and q time "
              "columns.") if show_label else None)
    if chosen != CUSTOM_FORMAT:
        return VALUE_FORMATS[chosen]

    spec = container.text_input(
        "Custom format", value=current, key=f"{key}_custom",
        placeholder=",.0f",
        help="A Python format spec (,.0f or .2%) or a date pattern (%Y-%m-%d).")
    container.caption(f"→ **{format_sample(spec)}**")
    return spec


def _references_bands_form(s: dict, columns: list[str], key: str) -> None:
    """References and bands for a bar/line/scatter widget — a dashed line and
    a shaded span, both optional and both repeatable.

    Matches ``_filters_form``'s shape: one row per item, a close button that
    pops it and forgets the keys below so a deleted row's neighbours redraw
    from the spec instead of from a stale widget position.
    """
    with st.expander("References & bands", expanded=False):
        st.caption("A reference is a dashed line drawn against the chart — a "
                   "target, an average, a pace. It never widens the axis: a "
                   "value far off the chart is named as off-scale rather "
                   "than stretching the real data flat to make room for it.")
        for i, ref in enumerate(list(s.get("references", []))):
            c = st.columns([1.5, 1.8, 1.8, 0.6], vertical_alignment="bottom")
            kind = ref.get("kind", "constant")
            ref["kind"] = c[0].selectbox(
                "Kind", REFERENCE_KINDS,
                index=REFERENCE_KINDS.index(kind)
                if kind in REFERENCE_KINDS else 0,
                key=f"{key}_refk_{i}")
            if ref["kind"] == "column":
                ref["column"] = _pick(c[1], "Column", columns,
                                      ref.get("column", ""), f"{key}_refc_{i}")
            elif ref["kind"] == "quantile":
                ref["value"] = c[1].number_input(
                    "Quantile (0-1)", 0.0, 1.0, float(ref.get("value") or 0.5),
                    step=0.05, key=f"{key}_refv_{i}")
            elif ref["kind"] == "constant":
                ref["value"] = c[1].number_input(
                    "Value", value=float(ref.get("value") or 0.0),
                    key=f"{key}_refv_{i}")
            else:
                c[1].caption("Taken over the whole Y column.")
            ref["label"] = c[2].text_input("Label", value=ref.get("label", ""),
                                           key=f"{key}_refl_{i}")
            if c[3].button("", icon=":material/close:", key=f"{key}_refx_{i}"):
                s["references"].pop(i)
                _forget(rf"{key}_ref")     # every reference below shuffles up
                st.rerun()

        if st.button("Add reference", icon=":material/add:",
                    key=f"{key}_refadd"):
            s.setdefault("references", []).append(
                {"kind": "constant", "value": 0.0, "label": ""})
            st.rerun()

        st.caption("A band shades a span behind the chart — a pre-open "
                   "stretch, a lunch break. Matched against X as it already "
                   "prints, so type the value exactly as it reads on the "
                   "axis; an end not found in the data is dropped rather "
                   "than drawn wrong.")
        for i, band in enumerate(list(s.get("bands", []))):
            c = st.columns([1.6, 1.6, 1.8, 0.6], vertical_alignment="bottom")
            band["from"] = c[0].text_input(
                "From", value=str(band.get("from", "")), key=f"{key}_bandf_{i}")
            band["to"] = c[1].text_input(
                "To", value=str(band.get("to", "")), key=f"{key}_bandt_{i}")
            band["label"] = c[2].text_input(
                "Label", value=band.get("label", ""), key=f"{key}_bandl_{i}")
            if c[3].button("", icon=":material/close:", key=f"{key}_bandx_{i}"):
                s["bands"].pop(i)
                _forget(rf"{key}_band")    # every band below shuffles up
                st.rerun()

        if st.button("Add band", icon=":material/add:", key=f"{key}_bandadd"):
            s.setdefault("bands", []).append({"from": "", "to": "", "label": ""})
            st.rerun()


def _widget_form(w: Widget, columns: list[str], key: str) -> None:
    s = w.spec
    if w.type == "kpi":
        c = st.columns([2, 1.4, 1.2, 1.2], vertical_alignment="bottom")
        s["column"] = _pick(c[0], "Column", columns, s.get("column", ""), f"{key}_c")
        s["agg"] = c[1].selectbox("Aggregate", AGG_FUNCS,
                                  index=AGG_FUNCS.index(s.get("agg", "sum")),
                                  key=f"{key}_a")
        s["fmt"] = _format_picker(c[2], "Number format", s.get("fmt", ",.0f"),
                                  f"{key}_f")
        s["suffix"] = c[3].text_input("Suffix", value=s.get("suffix", ""),
                                      placeholder="%, bps, sh",
                                      help="Appended after the number.",
                                      key=f"{key}_sfx")
        red = st.checkbox("Turn red when above zero", key=f"{key}_thr",
                          value=bool(s.get("thresholds")))
        s["thresholds"] = ([{"op": ">", "value": 0, "color": "critical"}]
                           if red else [])

    elif w.type == "table":
        # Every column, listed, so the job is taking one out rather than
        # remembering what there was to put in — and so the header and format
        # rows below appear straight away instead of after somebody has gone
        # looking for them. A table built before this stored nothing at all and
        # meant "all", which read the same on the page and quite differently
        # here.
        stored = s.get("columns") or list(columns)
        s["columns"] = _pick_many(st, "Columns", columns, stored, f"{key}_cols",
                                  help_text="Every column the dataset produces, "
                                            "in the order they print. Remove "
                                            "the ones this table does not need.")

        shown = s["columns"]
        if shown:
            st.caption("Header text and format, per column, top to bottom in "
                       "the order they print. Leave the header blank to keep "
                       "the column's own name.")
            labels = dict(s.get("labels", {}))
            formats = dict(s.get("formats", {}))
            for i, col in enumerate(shown):
                cc = st.columns([1.6, 2.2, 2.6, ICON_COL, ICON_COL],
                                vertical_alignment="bottom")
                cc[0].markdown(f"`{col}`")
                # Keyed by column name, never by row number: drop a column and
                # every remaining row keeps its own header and format instead of
                # inheriting whatever sat at that position before.
                slot = f"{key}_col_{_slug(col)}"
                labels[col] = cc[1].text_input(
                    "Header", value=labels.get(col, ""), placeholder=col,
                    key=f"{slot}_lbl",
                    label_visibility="visible" if i == 0 else "collapsed")
                formats[col] = _format_picker(
                    cc[2], "Format", formats.get(col, ""), f"{slot}_fmt",
                    show_label=i == 0)
                cc[3].button("", icon=":material/arrow_upward:",
                             key=f"{slot}_up", disabled=i == 0,
                             help="Print this column one place earlier.",
                             on_click=_move_column,
                             args=(s, shown, i, i - 1, f"{key}_cols"))
                cc[4].button("", icon=":material/arrow_downward:",
                             key=f"{slot}_down", disabled=i == len(shown) - 1,
                             help="Print this column one place later.",
                             on_click=_move_column,
                             args=(s, shown, i, i + 1, f"{key}_cols"))
            # Empties go, but a column that is merely not shown keeps what it
            # was given: deselecting a column to try another must not throw its
            # header away, or putting it back means typing it again.
            s["labels"] = {k: v for k, v in labels.items() if v and v.strip()}
            s["formats"] = {k: v for k, v in formats.items() if v}

        current = (s.get("highlight") or [{}])[0].get("column", "(none)")
        hl_opts = ["(none)"] + with_stored(columns, current if current != "(none)"
                                           else "")
        hl = st.selectbox("Highlight when above zero", hl_opts,
                          index=hl_opts.index(current) if current in hl_opts else 0,
                          key=f"{key}_hl", accept_new_options=True,
                          help=COLUMN_HELP)
        s["highlight"] = ([] if hl == "(none)"
                          else [{"column": hl, "op": ">", "value": 0,
                                 "color": "critical"}])

    elif w.type == "text":
        s["markdown"] = st.text_area(
            "Markdown", value=s.get("markdown", ""), height=120, key=f"{key}_md",
            help="Use {{dataset.agg.column}} to inline a number, e.g. "
                 "{{by_market.sum.n_orders}}")

    elif w.type in ("bar", "line", "scatter"):
        c = st.columns([2, 2, 1.6, 1.4], vertical_alignment="bottom")
        s["x"] = _pick(c[0], "X", columns, s.get("x", ""), f"{key}_x")
        current_y = s.get("y") if isinstance(s.get("y"), str) else ""
        s["y"] = _pick(c[1], "Y", columns, current_y, f"{key}_y")
        hue = _pick(c[2], "Split by", ["(none)"] + columns,
                    s.get("hue") or "(none)", f"{key}_h")
        s["hue"] = None if hue == "(none)" else hue
        if w.type == "bar":
            s["orientation"] = c[3].selectbox(
                "Direction", ["v", "h"],
                index=0 if s.get("orientation", "v") == "v" else 1,
                format_func=lambda v: "Vertical" if v == "v" else "Horizontal",
                key=f"{key}_o")
            sorts = [None, "asc", "desc"]
            s["sort"] = st.selectbox("Sort", sorts,
                                     index=sorts.index(s.get("sort"))
                                     if s.get("sort") in sorts else 0,
                                     format_func=lambda v: v or "(source order)",
                                     key=f"{key}_s")
        if w.type == "scatter":
            s["regression"] = c[3].checkbox("Trend line",
                                            value=bool(s.get("regression")),
                                            key=f"{key}_r")

        _references_bands_form(s, columns, key)

    elif w.type == "hist":
        c = st.columns([2, 1.4], vertical_alignment="bottom")
        s["x"] = _pick(c[0], "Value", columns, s.get("x", ""), f"{key}_x")
        s["bins"] = int(c[1].number_input("Bins", 2, 200, int(s.get("bins", 20)),
                                          key=f"{key}_b"))

    elif w.type == "box":
        c = st.columns(2, vertical_alignment="bottom")
        s["x"] = _pick(c[0], "Group by", columns, s.get("x", ""), f"{key}_x")
        s["y"] = _pick(c[1], "Value", columns, s.get("y", ""), f"{key}_y")

    elif w.type == "heatmap":
        c = st.columns([2, 2, 2, 1.4], vertical_alignment="bottom")
        s["rows"] = _pick(c[0], "Rows", columns, s.get("rows", ""), f"{key}_r")
        s["cols"] = _pick(c[1], "Columns", columns, s.get("cols", ""), f"{key}_c")
        s["value"] = _pick(c[2], "Value", columns, s.get("value", ""), f"{key}_v")
        aggs = ["sum", "mean", "count"]
        s["agg"] = c[3].selectbox("Aggregate", aggs,
                                  index=aggs.index(s.get("agg", "sum")),
                                  key=f"{key}_a")

    elif w.type == "pie":
        c = st.columns([2, 2, 1.2], vertical_alignment="bottom")
        s["by"] = _pick(c[0], "Slice by", columns, s.get("by", ""), f"{key}_b")
        s["value"] = _pick(c[1], "Value", columns, s.get("value", ""), f"{key}_v")
        s["donut"] = c[2].checkbox("Donut", value=bool(s.get("donut")),
                                   key=f"{key}_d")


# --- layout section --------------------------------------------------------

def _render_layout(store, draft: Dashboard) -> None:
    if not draft.datasets:
        st.warning("Add a dataset first — widgets read from datasets.",
                   icon=":material/warning:")
        return

    names = [ds.name for ds in draft.datasets]
    by_name = {ds.name: ds for ds in draft.datasets}

    # Which page each row prints on, from the same pagination the PDF uses, so
    # the layout can be arranged here rather than by generating and re-checking.
    # The editor has no results, so 'auto' plans against portrait — the page it
    # would print on today. A dashboard pinned to landscape plans against that.
    # ``chosen`` is passed too, or a dashboard with parameters would show page
    # breaks here that disagree with the real PDF, which has a caption to make
    # room for.
    sheet = choose_page(draft)
    chosen = ui_parameters.chosen_values(draft)
    placements = {p.index: p for p in plan_rows(draft.rows, sheet, chosen)}
    total_pages = max((p.page for p in placements.values()), default=1)
    if draft.rows:
        turns = (" On 'auto' a table too wide for the page turns the whole "
                 "report landscape, which shortens every page and so can add "
                 "more." if draft.orientation == "auto" else "")
        st.caption(f":material/picture_as_pdf: Prints on **{total_pages}** A4 "
                   f"{sheet.orientation} page(s) at least. Row heights are "
                   f"printed inches — reorder or resize rows to change where "
                   f"the page breaks fall. A table with more rows than its slot "
                   f"holds carries on over further pages, which only the data "
                   f"can tell you.{turns}")

    for r_i, row in enumerate(list(draft.rows)):
        placed = placements.get(r_i)
        if placed and placed.starts_page and placed.page > 1:
            st.markdown(f":gray[──────  page break  ·  page {placed.page} "
                        f"starts here  ──────]")

        with st.container(border=True):
            head = st.columns([3, 1.6, 0.6, 0.6, 0.6, 0.6],
                              vertical_alignment="bottom")
            page_badge = (f" :blue-badge[page {placed.page}]" if placed else "")
            head[0].markdown(f"**Row {r_i + 1}**{page_badge} · "
                             f"{len(row.widgets)} widget(s)")
            row.height_in = float(head[1].number_input(
                "Height (in)", 0.4, 9.0, float(row.height_in), step=0.1,
                key=f"r{r_i}_h", help="Printed height on the A4 page."))
            if head[2].button("", icon=":material/arrow_upward:", key=f"r{r_i}_u",
                              disabled=r_i == 0):
                draft.rows[r_i - 1], draft.rows[r_i] = draft.rows[r_i], draft.rows[r_i - 1]
                _forget(_ROW_KEYS)
                st.rerun()
            if head[3].button("", icon=":material/arrow_downward:", key=f"r{r_i}_d",
                              disabled=r_i == len(draft.rows) - 1):
                draft.rows[r_i + 1], draft.rows[r_i] = draft.rows[r_i], draft.rows[r_i + 1]
                _forget(_ROW_KEYS)
                st.rerun()
            if head[4].button("", icon=":material/content_copy:",
                              key=f"r{r_i}_c",
                              help="Copy this row, widgets and all, in below."):
                draft.rows.insert(r_i + 1, row_from_dict(row_to_dict(row)))
                _forget(_ROW_KEYS)
                st.rerun()
            if head[5].button("", icon=":material/delete:", key=f"r{r_i}_x"):
                draft.rows.pop(r_i)
                _forget(_ROW_KEYS)
                st.rerun()

            for w_i, w in enumerate(list(row.widgets)):
                key = f"r{r_i}w{w_i}"
                with st.container(border=True):
                    c = st.columns([1.5, 1.7, 2.0, 0.9, 0.6, 0.6, 0.6, 0.6],
                                   vertical_alignment="bottom")
                    w.type = c[0].selectbox("Type", WIDGET_TYPES,
                                            index=WIDGET_TYPES.index(w.type),
                                            key=f"{key}_t")
                    w.dataset = c[1].selectbox("Dataset", names,
                                               index=names.index(w.dataset)
                                               if w.dataset in names else 0,
                                               key=f"{key}_ds")
                    w.title = c[2].text_input("Title", value=w.title, key=f"{key}_ti")
                    w.width = float(c[3].number_input("Width", 0.2, 8.0,
                                                      float(w.width), step=0.1,
                                                      key=f"{key}_w"))
                    # Both ways, like every other reorderable thing here. With
                    # only a left arrow, moving a widget right meant reaching
                    # for a control on the widget beside it — so shifting the
                    # last of four to the front was three clicks, none of them
                    # on the thing being moved.
                    if c[4].button("", icon=":material/arrow_back:", key=f"{key}_l",
                                   disabled=w_i == 0, help="Move left."):
                        row.widgets[w_i - 1], row.widgets[w_i] = \
                            row.widgets[w_i], row.widgets[w_i - 1]
                        _forget(rf"r{r_i}w\d+")
                        st.rerun()
                    if c[5].button("", icon=":material/arrow_forward:",
                                   key=f"{key}_r",
                                   disabled=w_i == len(row.widgets) - 1,
                                   help="Move right."):
                        row.widgets[w_i + 1], row.widgets[w_i] = \
                            row.widgets[w_i], row.widgets[w_i + 1]
                        _forget(rf"r{r_i}w\d+")
                        st.rerun()
                    if c[6].button("", icon=":material/content_copy:",
                                   key=f"{key}_dup", disabled=len(row.widgets) >= 4,
                                   help="Copy this widget in beside itself. Two "
                                        "tables laid out the same way over "
                                        "different datasets are then one change "
                                        "apart, not built twice."):
                        # Through the dict and back, so the copy shares no spec,
                        # no reference list and no band list with the original —
                        # editing one would otherwise edit both.
                        row.widgets.insert(w_i + 1,
                                           widget_from_dict(widget_to_dict(w)))
                        _forget(rf"r{r_i}w\d+")
                        st.rerun()
                    if c[7].button("", icon=":material/close:", key=f"{key}_del"):
                        row.widgets.pop(w_i)
                        _forget(rf"r{r_i}w\d+")
                        st.rerun()

                    ds = by_name.get(w.dataset)
                    # Namespaced: the card's own controls live under "{key}_*",
                    # so an axis field called "_x" would collide with the remove
                    # button. Keep the spec form in its own key space.
                    _widget_form(w, dataset_columns(ds, _connection_for(store, ds),
                                                    learned_columns(w.dataset)),
                                 f"{key}_spec")
                    # Saving to the library is something done once a widget is
                    # finished, not while it is being arranged — so it sits
                    # under the settings rather than taking a place in the row
                    # of controls, which had grown to eight and was squeezing
                    # the title field it sat beside.
                    lib = st.columns([1.8, 6.2], vertical_alignment="bottom")
                    _save_to_library(lib[0], store, "widget", widget_to_dict(w),
                                     key, suggested=w.title)

            add = st.columns([1.4, 1.6, 5], vertical_alignment="bottom")
            full = len(row.widgets) >= 4
            if add[0].button("Add widget", icon=":material/add:",
                             key=f"r{r_i}_add", disabled=full):
                row.widgets.append(Widget(type="kpi", dataset=names[0], title=""))
                st.rerun()
            saved = _load_from_library(add[1], store, "widget", f"r{r_i}_wlib",
                                       disabled=full)
            if saved:
                w = widget_from_dict(saved.payload)
                # A saved widget names the dataset it was built against, which
                # this dashboard may not have. Keep the name when it fits, and
                # otherwise point it somewhere real rather than at nothing.
                if w.dataset not in names:
                    w.dataset = names[0]
                row.widgets.append(w)
                st.rerun()

        # Room left below this row, so you can see what will still fit before
        # the next page break rather than discovering it in the PDF.
        last_on_page = placed and (r_i + 1 not in placements
                                   or placements[r_i + 1].page != placed.page)
        if last_on_page:
            free = placed.free_after
            if free < 0.5:
                st.caption(f":orange[Page {placed.page} is full — "
                           f"{free:.1f} in left.]")
            else:
                st.caption(f":gray[{free:.1f} in still free on page "
                           f"{placed.page}.]")

    if st.button("Add row", icon=":material/add:", type="primary"):
        draft.rows.append(Row(widgets=[], height_in=2.5))
        st.rerun()


def _render_preview(store, mgr, draft: Dashboard) -> None:
    """The dashboard as its reader will actually see it — widgets and all.

    The parameter controls are drawn here too, live, so the author is picking
    from the same row of controls a reader gets rather than trusting the
    definitions alone. ``choices`` comes from the last Refresh, kept in session
    state across reruns for the same reason the real view keeps it on the
    dashboard's payload: a picker has to offer something before the very first
    click, not just after it.
    """
    if not draft.datasets:
        st.caption("Nothing to preview yet.")
        return

    last = st.session_state.get("dash_preview_last") or {}
    choices: dict = {}
    for res in last.values():
        choices.update(getattr(res, "choices", None) or {})
    ui_parameters.render(draft, choices, on_change=lambda: None)

    if not st.button("Refresh preview", icon=":material/play_arrow:"):
        st.caption("Run the datasets to see the real page.")
        return
    results = run_datasets(draft, store, mgr, date.today(),
                           chosen=ui_parameters.chosen_values(draft))
    st.session_state["dash_preview_last"] = results
    for r_i, row in enumerate(draft.rows):
        if not row.widgets:
            continue
        cols = st.columns([max(w.width, 0.01) for w in row.widgets],
                          vertical_alignment="top")
        for c_i, w in enumerate(row.widgets):
            with cols[c_i]:
                render_widget(build_plot_model(w, results),
                              row_height_px(row.height_in),
                              key=f"prev_{r_i}_{c_i}")


# --- entry point -----------------------------------------------------------

def _render_problems(problems: list[str]) -> None:
    """A standing list of what is unfilled or wrong, shown while you build.

    Expanded by default: a collapsed warning is one a user scrolls past, and the
    whole point is that nobody saves a dashboard with a field they never filled.
    """
    if not problems:
        st.success("Ready to save — every field is filled in.",
                   icon=":material/check_circle:")
        return

    noun = "problem" if len(problems) == 1 else "problems"
    with st.expander(f":red[{len(problems)} {noun} to fix before saving]",
                     expanded=True, icon=":material/error:"):
        for p in problems:
            st.markdown(f":red[·] {p}")


def render(store, mgr) -> None:
    draft = _draft(store)

    with form_area():
        head = st.columns([2.6, 2.6, 1.8, 1.4, 1.2, 1.2],
                          vertical_alignment="bottom")
        draft.name = head[0].text_input("Dashboard name", value=draft.name)
        draft.description = head[1].text_input("Description",
                                               value=draft.description)
        sources = ["kdb", "file"]
        draft.source = head[2].selectbox(
            "Data from", sources,
            index=sources.index(draft.source if draft.source in sources
                                else "kdb"),
            format_func=lambda s: "KDB queries" if s == "kdb"
            else "An uploaded file",
            help="A file dashboard is a template: whoever opens it uploads "
                 "their own file of the shape you profile here, and sees their "
                 "own numbers. It has no environment, no period and no refresh "
                 "interval — those describe a server, and there is none.")

        # One row either way, and the printed-page control keeps its place in
        # both. It used to sit last on a query dashboard and first on a file
        # one, so changing the source made it jump the width of the screen —
        # and it was written out twice, which is how the two copies of its
        # help text had already come to say different things.
        if draft.source != "file":
            p = st.columns([2.4, 4.2, 2.4], vertical_alignment="bottom")
            modes = list(PERIOD_MODES)
            draft.periods = p[0].selectbox(
                "Periods offered", modes,
                index=modes.index(draft.periods if draft.periods in modes else "both"),
                format_func=lambda m: PERIOD_LABELS[m],
                help="Switching period switches server, so 'both' needs every "
                     "environment this reads to have a real-time server and its "
                     "historical twin. A dashboard built over one side alone says "
                     "so here, and is never offered the period it cannot answer.")
            p[1].caption(_periods_hint(draft, store))
            _orientation_picker(p[2], draft)
        else:
            p = st.columns([6.6, 2.4], vertical_alignment="bottom")
            p[0].caption(
                "This dashboard reads an uploaded file, so it has no "
                "environment, no period and no refresh interval — its numbers "
                "change when somebody uploads a different file, and at no "
                "other moment.")
            _orientation_picker(p[1], draft)

    # Validate on every rerun, not just on Save, so a half-filled field is
    # visible while you build rather than only when you try to leave.
    problems = validate(draft, store)

    if head[3].button(f"Save ({len(problems)})" if problems else "Save",
                      icon=":material/save:", type="primary",
                      use_container_width=True, disabled=bool(problems),
                      help="Fix the listed problems first" if problems else None):
        if draft.id:
            store.update_dashboard(draft)
        else:
            draft.id = store.add_dashboard(draft)
        st.toast(f"Saved '{draft.name}'", icon=":material/check:")
        _close()
        st.rerun()

    if head[4].button("Open", icon=":material/open_in_new:",
                      use_container_width=True, disabled=draft.id is None):
        dashboard_id = draft.id
        _close()
        st.query_params["dash"] = str(dashboard_id)
        st.rerun()

    if head[5].button("Back", icon=":material/arrow_back:",
                      use_container_width=True,
                      help="Leave the editor and return to the dashboard list"):
        _close()
        back_to_gallery()

    with form_area():
        _render_problems(problems)

    section = st.segmented_control("Section",
                                   ["Data", "Layout", "Preview", "Library"],
                                   default="Data", key="dash_edit_section")
    st.divider()
    if section == "Preview":
        # The preview IS the dashboard — it must match the real thing's width.
        _render_preview(store, mgr, draft)
    elif section == "Library":
        with form_area():
            _render_library(store)
    elif section == "Layout":
        with form_area():
            _render_layout(store, draft)
    else:
        # Run first, draw second: the forms above the results are built from
        # what the run just learned about each query.
        traces = run_preview(store, mgr, draft)
        with form_area():
            _render_data(store, mgr, draft)
        _render_dataset_results(traces)
