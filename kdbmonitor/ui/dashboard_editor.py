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
from kdbmonitor.core.dataset import run_datasets
from kdbmonitor.core.models import Filter
from kdbmonitor.core.plotmodel import build_plot_model
from kdbmonitor.core.timectx import PRESET_LABELS, PRESETS, has_date_constraint, resolve
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
    "dataset with `{{name.column}}`."
)

_REF = re.compile(r"\{\{(\w+)\.(\w+)\}\}")


# --- pure helpers (unit-tested) -------------------------------------------

def unique_name(base: str, taken: list[str]) -> str:
    if base not in taken:
        return base
    i = 2
    while f"{base}_{i}" in taken:
        i += 1
    return f"{base}_{i}"


def dataset_columns(ds: Dataset, conn) -> list[str]:
    """The columns a dataset is expected to produce, so widget forms can offer a
    picker instead of a free-text box. Raw datasets are unpredictable."""
    if ds is None or ds.mode == "raw" or conn is None:
        return []
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


# Spec fields a widget cannot render without. Anything not listed is optional.
REQUIRED_SPEC: dict[str, tuple[str, ...]] = {
    "kpi": ("column", "agg"),
    "table": (),
    "text": ("markdown",),
    "bar": ("x", "y"),
    "line": ("x", "y"),
    "scatter": ("x", "y"),
    "hist": ("x",),
    "box": ("y",),
    "heatmap": ("rows", "cols", "value"),
    "pie": ("by", "value"),
}


def _blank(value) -> bool:
    """A field the user has not actually filled in."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


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

        if ds.env not in envs:
            problems.append(f"Dataset '{ds.name}' uses unknown environment "
                            f"'{ds.env}'.")
        elif rt.mode == "historical" and envs[ds.env]["historical"] is None:
            problems.append(f"Dataset '{ds.name}': environment '{ds.env}' has no "
                            f"historical server — add one in Admin.")

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

            missing = [f for f in REQUIRED_SPEC.get(w.type, ())
                       if _blank(w.spec.get(f))]
            if missing:
                problems.append(f"{label} is missing "
                                f"{', '.join(repr(m) for m in missing)}.")

            # Catch a column that stopped existing — e.g. a group-by was changed
            # after the widget was bound to one of its outputs.
            ds = by_name[w.dataset]
            known = dataset_columns(ds, _connection_for(store, ds))
            if known:
                for field in REQUIRED_SPEC.get(w.type, ()):
                    value = w.spec.get(field)
                    if isinstance(value, str) and value and field != "agg" \
                            and value not in known:
                        problems.append(f"{label}: column '{value}' is not produced "
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
    for key in ("dash_draft", "dash_mode", "dash_edit_id"):
        st.session_state.pop(key, None)


def _connection_for(store, ds: Dataset):
    pair = store.list_environments().get(getattr(ds, "env", "")) or {}
    return pair.get("realtime") or pair.get("historical")


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
        opts = columns or [f.column]
        f.column = c[0].selectbox("Column", opts,
                                  index=opts.index(f.column) if f.column in opts else 0,
                                  key=f"{key}_fc_{i}")
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
            opts = columns or [p.get("source", "")]
            p["source"] = c[2].selectbox("From column", opts, key=f"{key}_ds")
            p["mapping"] = _kv_lines(st.text_area(
                "Suffix = label (one per line, e.g. `.HK = Hong Kong`)",
                value="\n".join(f"{k} = {v}" for k, v in p.get("mapping", {}).items()),
                key=f"{key}_dm", height=90))
            p["default"] = st.text_input("Fallback", value=p.get("default", "Unknown"),
                                         key=f"{key}_dd")

    elif t.kind == "filter":
        c = st.columns([2, 1, 2], vertical_alignment="bottom")
        opts = columns or [p.get("column", "")]
        p["column"] = c[0].selectbox("Column", opts, key=f"{key}_fc")
        p["op"] = c[1].selectbox("Op", ["=", "!=", "<", "<=", ">", ">="],
                                 key=f"{key}_fo")
        p["value"] = _coerce(c[2].text_input("Value", value=str(p.get("value", "")),
                                             key=f"{key}_fv"), "number")

    elif t.kind == "groupby":
        p["keys"] = st.multiselect("Group by", columns,
                                   default=[k for k in p.get("keys", [])
                                            if k in columns], key=f"{key}_gk")
        st.caption("Aggregations")
        for i, a in enumerate(list(p.get("aggs", []))):
            c = st.columns([2, 1.4, 2, 0.6], vertical_alignment="bottom")
            opts = columns or [a["column"]]
            a["column"] = c[0].selectbox("Column", opts,
                                         index=opts.index(a["column"])
                                         if a["column"] in opts else 0,
                                         key=f"{key}_gc_{i}")
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
        p["columns"] = c[0].multiselect("Sort by", columns,
                                        default=[x for x in p.get("columns", [])
                                                 if x in columns], key=f"{key}_sc")
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
        ds.time_mode = head[2].selectbox(
            "Period", modes, index=modes.index(ds.time_mode), key=f"{key}_tm",
            help="inherit = follow the dashboard's period control")
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

        ds.mode = st.radio("Query", ["guided", "raw"], horizontal=True,
                           index=0 if ds.mode == "guided" else 1, key=f"{key}_m")

        if ds.mode == "guided":
            tables = sorted(getattr(conn, "schema", {}) or {})
            ds.table = st.selectbox("Table", tables or [ds.table],
                                    index=tables.index(ds.table)
                                    if ds.table in tables else 0, key=f"{key}_t")
            _filters_form(ds, dataset_columns(ds, conn), key)
        else:
            ds.raw_qsql = st.text_area("q", value=ds.raw_qsql or "", height=160,
                                       help=RAW_HELP, key=f"{key}_q")

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
                _transform_form(t, dataset_columns(upstream, conn), f"{key}_t{i}")

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

    if draft.datasets and st.button("Preview datasets", icon=":material/play_arrow:"):
        for name, res in run_datasets(draft, store, mgr, date.today()).items():
            with st.container(border=True):
                st.markdown(f"**{name}**")
                st.code(res.qsql or "(no query)", language="python")
                if res.error:
                    st.error(res.error, icon=":material/error:")
                else:
                    st.caption(f"{res.row_count} row(s)"
                               + (" — capped" if res.truncated else ""))
                    st.dataframe(res.df, use_container_width=True, height=220)


# --- widget spec forms -----------------------------------------------------

def _pick(container, label: str, columns: list[str], current: str, key: str) -> str:
    options = columns or ([current] if current else [""])
    index = options.index(current) if current in options else 0
    return container.selectbox(label, options, index=index, key=key)


def _widget_form(w: Widget, columns: list[str], key: str) -> None:
    s = w.spec
    if w.type == "kpi":
        c = st.columns([2, 1.4, 1.2, 1.2], vertical_alignment="bottom")
        s["column"] = _pick(c[0], "Column", columns, s.get("column", ""), f"{key}_c")
        s["agg"] = c[1].selectbox("Aggregate", AGG_FUNCS,
                                  index=AGG_FUNCS.index(s.get("agg", "sum")),
                                  key=f"{key}_a")
        s["fmt"] = c[2].text_input("Format", value=s.get("fmt", ",.0f"),
                                   help="Python format spec, e.g. ,.0f or .1f",
                                   key=f"{key}_f")
        s["suffix"] = c[3].text_input("Suffix", value=s.get("suffix", ""),
                                      key=f"{key}_sfx")
        red = st.checkbox("Turn red when above zero", key=f"{key}_thr",
                          value=bool(s.get("thresholds")))
        s["thresholds"] = ([{"op": ">", "value": 0, "color": "critical"}]
                           if red else [])

    elif w.type == "table":
        s["columns"] = st.multiselect("Columns (empty = all)", columns,
                                      default=[c for c in s.get("columns", [])
                                               if c in columns], key=f"{key}_cols")
        hl_opts = ["(none)"] + columns
        current = (s.get("highlight") or [{}])[0].get("column", "(none)")
        hl = st.selectbox("Highlight when above zero", hl_opts,
                          index=hl_opts.index(current) if current in hl_opts else 0,
                          key=f"{key}_hl")
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

    for r_i, row in enumerate(list(draft.rows)):
        with st.container(border=True):
            head = st.columns([3, 1.6, 0.6, 0.6, 0.6], vertical_alignment="bottom")
            head[0].markdown(f"**Row {r_i + 1}** · {len(row.widgets)} widget(s)")
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
                    _widget_form(w, dataset_columns(ds, _connection_for(store, ds)),
                                 f"{key}_spec")

            if st.button("Add widget", icon=":material/add:", key=f"r{r_i}_add",
                         disabled=len(row.widgets) >= 4):
                row.widgets.append(Widget(type="kpi", dataset=names[0], title=""))
                st.rerun()

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

    head = st.columns([3, 3, 1.2, 1.2, 1.2], vertical_alignment="bottom")
    draft.name = head[0].text_input("Dashboard name", value=draft.name)
    draft.description = head[1].text_input("Description", value=draft.description)

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

    if head[4].button("All dashboards", icon=":material/arrow_back:",
                      use_container_width=True,
                      help="Discard nothing — just leave the editor"):
        _close()
        back_to_gallery()

    _render_problems(problems)

    section = st.segmented_control("Section", ["Data", "Layout", "Preview"],
                                   default="Data", key="dash_edit_section")
    st.divider()
    if section == "Layout":
        _render_layout(store, draft)
    elif section == "Preview":
        _render_preview(store, mgr, draft)
    else:
        _render_data(store, mgr, draft)
