# kdbmonitor/ui/dashboards.py
"""The Dashboards page: gallery, tab strip, live view, PDF export.

The tab strip is st.pills rather than st.tabs on purpose — st.tabs executes every
tab's body on each rerun, which under a refresh timer would fire every
dashboard's queries at KDB continuously. Pills keep exactly one dashboard live.
"""
from __future__ import annotations

from datetime import date, datetime

import streamlit as st

from kdbmonitor.core.dashboard_models import Dashboard
from kdbmonitor.core.dashpdf import (
    dashboard_page_png_bytes, dashboard_to_pdf_bytes, page_count, pdf_filename,
)
from kdbmonitor.core.dataset import run_datasets
from kdbmonitor.core.plotmodel import build_plot_model
from kdbmonitor.core.portability import (
    export_dashboards_json, import_dashboards_json,
)
from kdbmonitor.core.render_plotly import figure
from kdbmonitor.core.timectx import PRESET_LABELS, PRESETS, resolve

NATIVE_KINDS = {"kpi", "table", "text", "error"}   # drawn by Streamlit, not plotly
DPI = 96

REFRESH_OPTIONS = {"Off": 0, "5s": 5, "10s": 10, "15s": 15, "30s": 30,
                   "1m": 60, "5m": 300, "15m": 900}

TIME_OPTIONS = {"Real-time": None, **{PRESET_LABELS[p]: p for p in PRESETS},
                "Custom range…": "custom"}


def spec_for_option(label: str) -> dict:
    preset = TIME_OPTIONS.get(label)
    if preset is None:
        return {"mode": "realtime"}
    if preset == "custom":
        return {"mode": "historical",
                "range": {"kind": "absolute", "from": "", "to": ""}}
    return {"mode": "historical", "range": {"kind": "preset", "name": preset}}


def option_for_spec(spec: dict) -> str:
    if (spec or {}).get("mode") != "historical":
        return "Real-time"
    rng = spec.get("range") or {}
    if rng.get("kind") == "preset" and rng.get("name") in PRESET_LABELS:
        return PRESET_LABELS[rng["name"]]
    return "Custom range…"


def row_height_px(height_in: float) -> int:
    return int(round(height_in * DPI))


ROW_PX = 35          # Streamlit's data-editor row height, header included


def table_height(n_rows: int, allotted_px: int) -> "int | str":
    """Height for st.dataframe: a pixel cap, or "content" to fit the rows.

    Forcing the row's printed height on a short table pads it with blank filler
    rows, which reads as missing data. Only constrain the height when the table
    is actually taller than its slot and therefore needs to scroll.

    Returns "content" rather than None — st.dataframe rejects None, accepting
    only a positive int, "stretch" or "content".
    """
    natural = ROW_PX * (n_rows + 1) + 3
    return allotted_px if natural > allotted_px else "content"


# --- widget rendering ------------------------------------------------------

def render_widget(pm, height_px: int, key: str) -> None:
    """One widget on screen. Charts are interactive; the rest are native."""
    if pm.kind == "error":
        st.error(f"{pm.title or 'Widget'}: {pm.error}", icon=":material/error:")
        return
    if pm.kind == "kpi":
        st.metric(pm.title, pm.value, help=pm.caption or None)
        return
    if pm.kind == "text":
        if pm.title:
            st.markdown(f"**{pm.title}**")
        st.markdown(pm.text)
        return
    if pm.kind == "table":
        if pm.title:
            st.markdown(f"**{pm.title}**")
        st.dataframe({c: [r[i] for r in pm.rows] for i, c in enumerate(pm.columns)},
                     use_container_width=True, hide_index=True,
                     height=table_height(len(pm.rows), height_px))
        return
    st.plotly_chart(figure(pm), use_container_width=True, key=key)


def render_rows(dashboard: Dashboard, results: dict, key_prefix: str = "v") -> None:
    if not dashboard.rows:
        st.info("This dashboard has no widgets yet — open Edit to add some.",
                icon=":material/dashboard:")
        return
    for r_i, row in enumerate(dashboard.rows):
        if not row.widgets:
            continue
        cols = st.columns([max(w.width, 0.01) for w in row.widgets],
                          vertical_alignment="top")
        for c_i, widget in enumerate(row.widgets):
            with cols[c_i]:
                render_widget(build_plot_model(widget, results),
                              row_height_px(row.height_in),
                              key=f"{key_prefix}_{r_i}_{c_i}")


# --- state helpers ---------------------------------------------------------

def frames_key(dashboard_id: int) -> str:
    return f"dash_frames_{dashboard_id}"


def refresh(store, mgr, dashboard: Dashboard) -> dict:
    """Run every dataset and cache the frames, stamped with when they were taken."""
    payload = {"results": run_datasets(dashboard, store, mgr, date.today()),
               "as_of": datetime.now(),
               "rt": resolve(dashboard.time_context, date.today())}
    st.session_state[frames_key(dashboard.id)] = payload
    return payload


def _active_id(store) -> int | None:
    """The open dashboard, from the URL so it is bookmarkable."""
    raw = st.query_params.get("dash")
    ids = [d.id for d in store.list_dashboards()]
    if raw and str(raw).isdigit() and int(raw) in ids:
        return int(raw)
    return None


def _open(dashboard_id: int) -> None:
    st.query_params["dash"] = str(dashboard_id)
    st.rerun()


# --- gallery ---------------------------------------------------------------

def _render_gallery(store) -> None:
    st.subheader(":material/dashboard: Dashboards")
    head = st.columns([6, 1.6], vertical_alignment="center")
    head[0].caption("Saved views built from KDB queries. Open one to watch it "
                    "live, or export the current state as a PDF.")
    if head[1].button("New dashboard", icon=":material/add:", type="primary",
                      use_container_width=True):
        new_id = store.add_dashboard(Dashboard(id=None, name="New dashboard"))
        st.session_state["dash_edit_id"] = new_id
        st.session_state["dash_mode"] = "edit"
        st.session_state.pop("dash_draft", None)
        st.rerun()

    saved = store.list_dashboards()
    if not saved:
        st.info("No dashboards yet. Create one to get started.",
                icon=":material/dashboard:")
    for d in saved:
        with st.container(border=True):
            c = st.columns([3, 2.4, 1, 1, 1.2, 0.8], vertical_alignment="center")
            c[0].markdown(f"**{d.name}**"
                          + (f"<br>:gray[{d.description}]" if d.description else ""),
                          unsafe_allow_html=True)
            envs = sorted({ds.env for ds in d.datasets if ds.env})
            n_widgets = sum(len(r.widgets) for r in d.rows)
            c[1].markdown(f":gray[{len(d.datasets)} dataset(s) · {n_widgets} widget(s)]"
                          + (f"<br>:gray[env: {', '.join(envs)}]" if envs else ""),
                          unsafe_allow_html=True)
            if c[2].button("Open", key=f"open_{d.id}", icon=":material/open_in_new:",
                           use_container_width=True):
                _open(d.id)
            if c[3].button("Edit", key=f"edit_{d.id}", icon=":material/edit:",
                           use_container_width=True):
                st.session_state["dash_edit_id"] = d.id
                st.session_state["dash_mode"] = "edit"
                st.session_state.pop("dash_draft", None)
                st.rerun()
            if c[4].button("Duplicate", key=f"dup_{d.id}",
                           icon=":material/content_copy:", use_container_width=True):
                copy = store.get_dashboard(d.id)
                copy.id = None
                copy.name = f"{copy.name} (copy)"
                store.add_dashboard(copy)
                st.toast(f"Duplicated '{d.name}'", icon=":material/check:")
                st.rerun()
            with c[5].popover("", icon=":material/delete:"):
                st.warning(f"Delete '{d.name}'?")
                if st.button("Confirm", key=f"delok_{d.id}", type="primary"):
                    store.delete_dashboard(d.id)
                    st.rerun()

    st.divider()
    io_cols = st.columns([1.6, 3], vertical_alignment="center")
    io_cols[0].download_button(
        "Export all", data=export_dashboards_json(saved),
        file_name="kdbmonitor_dashboards.json", mime="application/json",
        icon=":material/download:", use_container_width=True, disabled=not saved)

    uploaded = io_cols[1].file_uploader("Import dashboards", type=["json"],
                                        label_visibility="collapsed")
    if uploaded is not None:
        try:
            incoming = import_dashboards_json(uploaded.getvalue().decode("utf-8"))
        except ValueError as exc:
            st.error(str(exc), icon=":material/error:")
        else:
            existing = {d.name for d in saved}
            for d in incoming:
                if d.name in existing:
                    d.name = f"{d.name} (imported)"
                store.add_dashboard(d)
            st.toast(f"Imported {len(incoming)} dashboard(s)", icon=":material/check:")
            st.rerun()


# --- view ------------------------------------------------------------------

def _render_period(store, dashboard: Dashboard, payload: dict | None) -> None:
    bar = st.columns([2.2, 1.6, 1.6, 2.6], vertical_alignment="bottom")

    options = list(TIME_OPTIONS)
    option = bar[0].selectbox("Period", options,
                              index=options.index(option_for_spec(dashboard.time_context)),
                              key=f"tc_{dashboard.id}")
    spec = spec_for_option(option)

    if option == "Custom range…":
        existing = dashboard.time_context.get("range") or {}
        d1 = bar[1].date_input(
            "From", value=date.fromisoformat(existing["from"])
            if existing.get("from") else date.today(), key=f"tcf_{dashboard.id}")
        d2 = bar[2].date_input(
            "To", value=date.fromisoformat(existing["to"])
            if existing.get("to") else date.today(), key=f"tct_{dashboard.id}")
        spec = {"mode": "historical",
                "range": {"kind": "absolute", "from": d1.isoformat(),
                          "to": d2.isoformat()}}

    if spec != dashboard.time_context:
        dashboard.time_context = spec
        store.update_dashboard(dashboard)
        st.session_state.pop(frames_key(dashboard.id), None)
        st.session_state.pop(f"pdf_{dashboard.id}", None)
        st.rerun()

    if payload:
        bar[3].markdown(f":gray[{payload['rt'].label}]<br>"
                        f":gray[updated {payload['as_of']:%H:%M:%S}]",
                        unsafe_allow_html=True)


def _render_view(store, mgr, dashboard: Dashboard) -> None:
    names = {d.name: d.id for d in store.list_dashboards()}
    picked = st.pills("Dashboards", list(names), default=dashboard.name,
                      label_visibility="collapsed", key="dash_pills")
    if picked and names[picked] != dashboard.id:
        _open(names[picked])

    top = st.columns([4, 1.3, 1.2, 1.3], vertical_alignment="bottom")
    top[0].subheader(dashboard.name)

    labels = list(REFRESH_OPTIONS)
    current = next((k for k, v in REFRESH_OPTIONS.items()
                    if v == dashboard.refresh_secs), "15s")
    chosen = top[1].selectbox("Refresh", labels, index=labels.index(current),
                              key=f"rf_{dashboard.id}")
    if REFRESH_OPTIONS[chosen] != dashboard.refresh_secs:
        dashboard.refresh_secs = REFRESH_OPTIONS[chosen]
        store.update_dashboard(dashboard)
        st.rerun()

    if top[2].button("Edit", icon=":material/edit:", use_container_width=True):
        st.session_state["dash_edit_id"] = dashboard.id
        st.session_state["dash_mode"] = "edit"
        st.session_state.pop("dash_draft", None)
        st.rerun()
    if top[3].button("Gallery", icon=":material/grid_view:", use_container_width=True):
        st.query_params.pop("dash", None)
        st.rerun()

    _render_period(store, dashboard, st.session_state.get(frames_key(dashboard.id)))

    @st.fragment(run_every=dashboard.refresh_secs or None)
    def _live() -> None:
        data = refresh(store, mgr, dashboard)
        render_rows(dashboard, data["results"])

    _live()
    _render_export(dashboard)


def _render_export(dashboard: Dashboard) -> None:
    payload = st.session_state.get(frames_key(dashboard.id))
    st.divider()
    e = st.columns([1.6, 1.5, 1.5, 3], vertical_alignment="center")

    if e[0].button("Generate PDF", icon=":material/picture_as_pdf:", type="primary",
                   use_container_width=True, disabled=not payload):
        st.session_state[f"pdf_{dashboard.id}"] = dashboard_to_pdf_bytes(
            dashboard, payload["results"], payload["rt"], payload["as_of"])

    if e[1].button("Preview page", icon=":material/preview:",
                   use_container_width=True, disabled=not payload):
        st.session_state[f"pdfpreview_{dashboard.id}"] = dashboard_page_png_bytes(
            dashboard, payload["results"], payload["rt"], payload["as_of"])

    data = st.session_state.get(f"pdf_{dashboard.id}")
    if data and payload:
        e[2].download_button("Download", data=data,
                             file_name=pdf_filename(dashboard, payload["as_of"]),
                             mime="application/pdf", icon=":material/download:",
                             use_container_width=True)
        e[3].caption("The PDF renders the numbers currently on screen — it does "
                     "not re-query.")

    preview = st.session_state.get(f"pdfpreview_{dashboard.id}")
    if preview:
        pages = page_count(dashboard)
        with st.expander(f"Printed page 1 of {pages}", expanded=True):
            st.image(preview, use_container_width=True)


# --- entry point -----------------------------------------------------------

def render(store, mgr) -> None:
    if st.session_state.get("dash_mode") == "edit":
        from kdbmonitor.ui import dashboard_editor
        dashboard_editor.render(store, mgr)
        return

    active = _active_id(store)
    if active is None:
        _render_gallery(store)
        return

    dashboard = store.get_dashboard(active)
    if dashboard is None:
        st.query_params.pop("dash", None)
        st.rerun()
    _render_view(store, mgr, dashboard)
