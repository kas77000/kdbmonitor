# kdbmonitor/ui/dashboard_editor.py
"""Dashboard editor: datasets (the data) and rows of widgets (the layout).

Session-state driven, following ui/builder.py. The draft lives in
``st.session_state['dash_draft']`` and is written back to the DB only on Save, so
a half-built dataset never reaches the view.
"""
from __future__ import annotations

import re
from datetime import date

import streamlit as st

from kdbmonitor.core.dashboard_models import (
    Dashboard, Dataset, Row, Transform, Widget,
)
from kdbmonitor.core.dashpdf import plan_rows
from kdbmonitor.core.dataset import is_marketdata_env, run_datasets, trace_datasets
from kdbmonitor.core.models import Filter
from kdbmonitor.core.plotmodel import (
    FIELD_LABELS, build_plot_model, is_blank as _blank, missing_spec_fields,
    referenced_columns,
)
from kdbmonitor.core.timectx import PRESET_LABELS, PRESETS, has_date_constraint, resolve
from kdbmonitor.core.transform import Step
from kdbmonitor.ui.common import form_area
from kdbmonitor.ui.dashboards import back_to_gallery, render_widget, row_height_px

OPS = ["=", "<>", "<", "<=", ">", ">=", "in", "like"]
VALUE_TYPES = ["symbol", "number", "string"]
AGG_FUNCS = ["count", "nunique", "sum", "mean", "min", "max"]
TRANSFORM_KINDS = ["derive", "filter", "groupby", "sort", "limit", "rename"]
WIDGET_TYPES = ["kpi", "table", "text", "bar", "line", "scatter", "hist",
                "box", "heatmap", "pie"]

RAW_HELP = (
    "Raw q. In historical mode you MUST constrain `date` — use "
    "`{{date_from}}` / `{{date_to}}` / `{{date_list}}`. Reference another "
    "dataset with `{{name.column}}`. To query a second KDB process in the same "
    "query, list it under *Also connect* below and `hopen` it via `{{conn:ENV}}`."
)

_REF = re.compile(r"\{\{(\w+)\.(\w+)\}\}")
_CONN_REF = re.compile(r"\{\{conn:([^{}]+)\}\}")

# Number formats offered by name, so nobody has to know Python format specs.
# The labels ARE the samples — you pick the output you want to see.
SAMPLE_VALUE = 1234.567

NUMBER_FORMATS: dict[str, str] = {
    "1,235": ",.0f",
    "1,234.6": ",.1f",
    "1,234.57": ",.2f",
    "1235": ".0f",
    "1234.57": ".2f",
    "61.4%  (value is a fraction 0–1)": ".1%",
    "1.23e+03": ".2e",
    "No formatting": "",
}
CUSTOM_FORMAT = "Custom…"


def format_sample(spec: str, value: float = SAMPLE_VALUE) -> str:
    """What ``spec`` turns the sample value into — or a complaint if it is not a
    usable format spec, so a typo shows up immediately instead of at render."""
    if not spec:
        return str(value)
    try:
        return format(value, spec)
    except (TypeError, ValueError):
        return "invalid format"


def is_valid_format(spec: str) -> bool:
    return format_sample(spec) != "invalid format"


def format_label_for(spec: str) -> str:
    """The catalogue entry matching a stored spec, else Custom."""
    for label, value in NUMBER_FORMATS.items():
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
    if ds.mode == "raw" or conn is None:
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


def validate(draft: Dashboard, store) -> list[str]:
    """Everything wrong with this dashboard, in plain English. Empty when fine.

    Covers both invalid configurations and inputs simply left unfilled, so the
    editor can warn while you build rather than only when you press Save.
    """
    problems: list[str] = []
    envs = store.list_environments()
    dashboard_time = resolve(draft.time_context, date.today())

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

        for i, t in enumerate(ds.transforms, start=1):
            problems += _transform_problems(ds.name, i, t)

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
        elif rt.mode == "historical" and envs[ds.env]["historical"] is None:
            problems.append(f"Dataset '{ds.name}': environment '{ds.env}' has no "
                            f"historical server — add one in Admin.")
        elif rt.mode == "realtime" and not market \
                and envs[ds.env]["realtime"] is None:
            problems.append(f"Dataset '{ds.name}': environment '{ds.env}' has no "
                            f"real-time server — add one in Admin.")

        if rt.mode == "historical" and ds.mode == "raw" \
                and not has_date_constraint(ds.raw_qsql or ""):
            problems.append(
                f"Dataset '{ds.name}' is historical but its q never constrains "
                "'date'. Add a date within ({{date_from}};{{date_to}}) clause.")

        if ds.mode == "guided" and not ds.table:
            problems.append(f"Dataset '{ds.name}' has no table selected.")

        for ref, _ in _REF.findall(ds.raw_qsql or ""):
            if ref not in seen:
                problems.append(f"Dataset '{ds.name}' references '{ref}', which is "
                                f"not defined above it.")

        # Cross-process connections: every declared extra env, and every
        # {{conn:ENV}} the query hopens, must name a known environment.
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


# --- dataset section -------------------------------------------------------

def _filters_form(ds: Dataset, columns: list[str], key: str) -> None:
    st.caption("Filters — combined with AND, sent to KDB as the where clause.")
    for i, f in enumerate(list(ds.filters)):
        c = st.columns([2, 1.2, 2, 1.4, 1, 0.6], vertical_alignment="bottom")
        f.column = _pick(c[0], "Column", columns, f.column, f"{key}_fc_{i}")
        f.op = c[1].selectbox("Op", OPS, index=OPS.index(f.op) if f.op in OPS else 0,
                              key=f"{key}_fo_{i}")
        raw = c[2].text_input("Value",
                              value=", ".join(map(str, f.value))
                              if isinstance(f.value, list) else str(f.value),
                              key=f"{key}_fv_{i}")
        f.value_type = c[3].selectbox("Type", VALUE_TYPES,
                                      index=VALUE_TYPES.index(f.value_type),
                                      key=f"{key}_ft_{i}")
        f.value = ([v.strip() for v in raw.split(",")] if f.op == "in"
                   else _coerce(raw, f.value_type))
        f.negated = c[4].checkbox("not", value=f.negated, key=f"{key}_fn_{i}")
        if c[5].button("", icon=":material/close:", key=f"{key}_fx_{i}"):
            ds.filters.pop(i)
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


def _dataset_card(store, ds: Dataset, index: int, draft: Dashboard) -> None:
    key = f"ds{index}"
    conn = _connection_for(store, ds)

    with st.expander(f"**{ds.name}** · {ds.env or 'no environment'}", expanded=True):
        head = st.columns([2, 2, 1.8, 1.6, 0.7], vertical_alignment="bottom")
        ds.name = head[0].text_input("Name", value=ds.name, key=f"{key}_n")
        envs = sorted(store.list_environments())
        ds.env = head[1].selectbox("Environment", envs or [ds.env],
                                   index=envs.index(ds.env) if ds.env in envs else 0,
                                   key=f"{key}_e")
        modes = ["inherit", "realtime", "custom"]
        market = is_marketdata_env(
            store.list_environments().get(ds.env) or {})
        ds.time_mode = head[2].selectbox(
            "Period", modes, index=modes.index(ds.time_mode), key=f"{key}_tm",
            disabled=market,
            help="Market data is not partitioned by date — the period does not "
                 "apply." if market
                 else "inherit = follow the dashboard's period control")
        ds.max_rows = int(head[3].number_input("Max rows", 1, 1_000_000,
                                               ds.max_rows, step=100, key=f"{key}_mr"))
        if head[4].button("", icon=":material/delete:", key=f"{key}_del"):
            draft.datasets.pop(index)
            st.rerun()

        if ds.time_mode == "custom":
            labels = [PRESET_LABELS[p] for p in PRESETS]
            current = ((ds.time_context or {}).get("range") or {}).get("name", "last_30d")
            chosen = st.selectbox("Its own period", labels,
                                  index=list(PRESETS).index(current)
                                  if current in PRESETS else 3, key=f"{key}_tc")
            ds.time_context = {"mode": "historical",
                               "range": {"kind": "preset",
                                         "name": PRESETS[labels.index(chosen)]}}

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
            _filters_form(ds, table_columns(ds, conn), key)
        else:
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
                c = st.columns([2, 5, 0.6, 0.6, 0.6], vertical_alignment="bottom")
                t.kind = c[0].selectbox("Kind", TRANSFORM_KINDS,
                                        index=TRANSFORM_KINDS.index(t.kind),
                                        key=f"{key}_tk_{i}")
                if c[2].button("", icon=":material/arrow_upward:",
                               key=f"{key}_tu_{i}", disabled=i == 0):
                    ds.transforms[i - 1], ds.transforms[i] = \
                        ds.transforms[i], ds.transforms[i - 1]
                    st.rerun()
                if c[3].button("", icon=":material/arrow_downward:",
                               key=f"{key}_td_{i}",
                               disabled=i == len(ds.transforms) - 1):
                    ds.transforms[i + 1], ds.transforms[i] = \
                        ds.transforms[i], ds.transforms[i + 1]
                    st.rerun()
                if c[4].button("", icon=":material/close:", key=f"{key}_tx_{i}"):
                    ds.transforms.pop(i)
                    st.rerun()
                # Columns available to a transform are those produced by the
                # ones before it, not the dataset's final shape.
                upstream = Dataset(name=ds.name, env=ds.env, mode=ds.mode,
                                   table=ds.table, transforms=ds.transforms[:i])
                _transform_form(t, dataset_columns(upstream, conn,
                                                   learned_columns(ds.name)),
                                f"{key}_t{i}")

        if st.button("Add transform", icon=":material/add:", key=f"{key}_taddb"):
            ds.transforms.append(Transform(
                kind="derive",
                params={"column": "", "kind": "arithmetic", "expr": ""}))
            st.rerun()


def _render_data(store, mgr, draft: Dashboard) -> None:
    if not store.list_environments():
        st.warning("No connections yet — add one in Admin first.",
                   icon=":material/warning:")

    for i, ds in enumerate(list(draft.datasets)):
        _dataset_card(store, ds, i, draft)

    if st.button("Add dataset", icon=":material/add:", type="primary"):
        envs = sorted(store.list_environments())
        draft.datasets.append(Dataset(
            name=unique_name("dataset", [d.name for d in draft.datasets]),
            env=envs[0] if envs else ""))
        st.rerun()

    if draft.datasets and st.button("Run and inspect each step",
                                    icon=":material/play_arrow:",
                                    key="dash_preview_run",
                                    help="Send each dataset's query, then apply "
                                         "its transforms one at a time so you "
                                         "can check what every step did."):
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
    """Run every dataset stage by stage, if the Data section asked for it.

    Done before the forms are drawn, not after: a run is the only way to learn a
    raw query's columns, and the pickers are built from them.
    """
    if not st.session_state.pop("dash_preview_data", False):
        return None
    traces = trace_datasets(draft, store, mgr, date.today())
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
               **kwargs) -> list[str]:
    """The same, for a picker that takes several columns."""
    return container.multiselect(label, with_stored(columns, current),
                                 default=list(current or []), key=key,
                                 accept_new_options=True, help=COLUMN_HELP,
                                 **kwargs)


def _format_picker(container, label: str, current: str, key: str,
                   show_label: bool = True) -> str:
    """Choose a number format by its sample output rather than typing a spec.

    ``show_label`` collapses the label for repeated rows — an empty label string
    would still reserve space and leave the help icon floating on its own.
    """
    options = list(NUMBER_FORMATS) + [CUSTOM_FORMAT]
    current_label = format_label_for(current)
    chosen = container.selectbox(
        label, options, index=options.index(current_label), key=f"{key}_pick",
        label_visibility="visible" if show_label else "collapsed",
        help=("Pick how the number should read. Each option shows what "
              f"{SAMPLE_VALUE} would look like.") if show_label else None)
    if chosen != CUSTOM_FORMAT:
        return NUMBER_FORMATS[chosen]

    spec = container.text_input(
        "Custom format", value=current, key=f"{key}_custom",
        placeholder=",.0f",
        help="A Python format spec, e.g. ,.0f or .2%")
    container.caption(f"{SAMPLE_VALUE} → **{format_sample(spec)}**")
    return spec


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
        s["columns"] = _pick_many(st, "Columns (empty = all)", columns,
                                  s.get("columns", []), f"{key}_cols")

        shown = s["columns"] or columns
        if shown:
            st.caption("Header text and number format, per column. Leave the "
                       "header blank to keep the column's own name.")
            labels = dict(s.get("labels", {}))
            formats = dict(s.get("formats", {}))
            for i, col in enumerate(shown):
                cc = st.columns([1.6, 2.2, 2.6], vertical_alignment="bottom")
                cc[0].markdown(f"`{col}`")
                labels[col] = cc[1].text_input(
                    "Header", value=labels.get(col, ""), placeholder=col,
                    key=f"{key}_lbl{i}",
                    label_visibility="visible" if i == 0 else "collapsed")
                formats[col] = _format_picker(
                    cc[2], "Format", formats.get(col, ""), f"{key}_fmt{i}",
                    show_label=i == 0)
            # Drop empties so the stored spec stays clean and diffable.
            s["labels"] = {k: v for k, v in labels.items()
                           if k in shown and v.strip()}
            s["formats"] = {k: v for k, v in formats.items() if k in shown and v}

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
    placements = {p.index: p for p in plan_rows(draft.rows)}
    total_pages = max((p.page for p in placements.values()), default=1)
    if draft.rows:
        st.caption(f":material/picture_as_pdf: Prints on **{total_pages}** A4 "
                   f"page(s). Row heights are printed inches — reorder or resize "
                   f"rows to change where the page breaks fall.")

    for r_i, row in enumerate(list(draft.rows)):
        placed = placements.get(r_i)
        if placed and placed.starts_page and placed.page > 1:
            st.markdown(f":gray[──────  page break  ·  page {placed.page} "
                        f"starts here  ──────]")

        with st.container(border=True):
            head = st.columns([3, 1.6, 0.6, 0.6, 0.6], vertical_alignment="bottom")
            page_badge = (f" :blue-badge[page {placed.page}]" if placed else "")
            head[0].markdown(f"**Row {r_i + 1}**{page_badge} · "
                             f"{len(row.widgets)} widget(s)")
            row.height_in = float(head[1].number_input(
                "Height (in)", 0.4, 9.0, float(row.height_in), step=0.1,
                key=f"r{r_i}_h", help="Printed height on the A4 page."))
            if head[2].button("", icon=":material/arrow_upward:", key=f"r{r_i}_u",
                              disabled=r_i == 0):
                draft.rows[r_i - 1], draft.rows[r_i] = draft.rows[r_i], draft.rows[r_i - 1]
                st.rerun()
            if head[3].button("", icon=":material/arrow_downward:", key=f"r{r_i}_d",
                              disabled=r_i == len(draft.rows) - 1):
                draft.rows[r_i + 1], draft.rows[r_i] = draft.rows[r_i], draft.rows[r_i + 1]
                st.rerun()
            if head[4].button("", icon=":material/delete:", key=f"r{r_i}_x"):
                draft.rows.pop(r_i)
                st.rerun()

            for w_i, w in enumerate(list(row.widgets)):
                key = f"r{r_i}w{w_i}"
                with st.container(border=True):
                    c = st.columns([1.6, 1.8, 2.4, 1.1, 0.6, 0.6],
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
                    if c[4].button("", icon=":material/arrow_back:", key=f"{key}_l",
                                   disabled=w_i == 0):
                        row.widgets[w_i - 1], row.widgets[w_i] = \
                            row.widgets[w_i], row.widgets[w_i - 1]
                        st.rerun()
                    if c[5].button("", icon=":material/close:", key=f"{key}_del"):
                        row.widgets.pop(w_i)
                        st.rerun()

                    ds = by_name.get(w.dataset)
                    # Namespaced: the card's own controls live under "{key}_*",
                    # so an axis field called "_x" would collide with the remove
                    # button. Keep the spec form in its own key space.
                    _widget_form(w, dataset_columns(ds, _connection_for(store, ds),
                                                    learned_columns(w.dataset)),
                                 f"{key}_spec")

            if st.button("Add widget", icon=":material/add:", key=f"r{r_i}_add",
                         disabled=len(row.widgets) >= 4):
                row.widgets.append(Widget(type="kpi", dataset=names[0], title=""))
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
    if not draft.datasets:
        st.caption("Nothing to preview yet.")
        return
    if not st.button("Refresh preview", icon=":material/play_arrow:"):
        st.caption("Run the datasets to see the real page.")
        return
    results = run_datasets(draft, store, mgr, date.today())
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
        head = st.columns([3, 3, 1.4, 1.2, 1.2], vertical_alignment="bottom")
        draft.name = head[0].text_input("Dashboard name", value=draft.name)
        draft.description = head[1].text_input("Description",
                                               value=draft.description)

    # Validate on every rerun, not just on Save, so a half-filled field is
    # visible while you build rather than only when you try to leave.
    problems = validate(draft, store)

    if head[2].button(f"Save ({len(problems)})" if problems else "Save",
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

    if head[3].button("Open", icon=":material/open_in_new:",
                      use_container_width=True, disabled=draft.id is None):
        dashboard_id = draft.id
        _close()
        st.query_params["dash"] = str(dashboard_id)
        st.rerun()

    if head[4].button("Back", icon=":material/arrow_back:",
                      use_container_width=True,
                      help="Leave the editor and return to the dashboard list"):
        _close()
        back_to_gallery()

    with form_area():
        _render_problems(problems)

    section = st.segmented_control("Section", ["Data", "Layout", "Preview"],
                                   default="Data", key="dash_edit_section")
    st.divider()
    if section == "Preview":
        # The preview IS the dashboard — it must match the real thing's width.
        _render_preview(store, mgr, draft)
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
