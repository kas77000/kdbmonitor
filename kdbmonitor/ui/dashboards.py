# kdbmonitor/ui/dashboards.py
"""The Dashboards page: gallery, tab strip, live view, PDF export.

The tab strip is st.pills rather than st.tabs on purpose — st.tabs executes every
tab's body on each rerun, which under a refresh timer would fire every
dashboard's queries at KDB continuously. Pills keep exactly one dashboard live.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import streamlit as st

from kdbmonitor.core.dashboard_models import Dashboard, FileShape
from kdbmonitor.core.dashpdf import (
    LANDSCAPE, dashboard_page_png_bytes, dashboard_to_pdf_bytes, pdf_filename,
    report_plan,
)
from kdbmonitor.core.dataset import run_datasets
from kdbmonitor.core.plotmodel import build_plot_model
from kdbmonitor.core.portability import (
    export_dashboards_json, import_dashboards_json,
)
from kdbmonitor.core.render_plotly import figure
from kdbmonitor.core.timectx import (
    PRESET_LABELS, PRESETS, coerce_spec, resolve,
)
from kdbmonitor.ui import parameters

NATIVE_KINDS = {"kpi", "table", "text", "error"}   # drawn by Streamlit, not plotly
DPI = 96

REFRESH_OPTIONS = {"Off": 0, "5s": 5, "10s": 10, "15s": 15, "30s": 30,
                   "1m": 60, "5m": 300, "15m": 900}

TIME_OPTIONS = {"Real-time": None, **{PRESET_LABELS[p]: p for p in PRESETS},
                "Custom range…": "custom"}


def time_options(periods: str) -> dict:
    """The periods this dashboard offers, in picker order.

    Every option but "Real-time" resolves to the historical server, so a
    historical-only dashboard keeps the whole list of ranges and loses only the
    one that would ask for a live server it does not have.
    """
    if periods == "realtime":
        return {"Real-time": None}
    if periods == "historical":
        return {k: v for k, v in TIME_OPTIONS.items() if k != "Real-time"}
    return dict(TIME_OPTIONS)


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


def dashboard_filename(dashboard: Dashboard) -> str:
    """Filename for a single-dashboard export."""
    slug = re.sub(r"[^a-z0-9]+", "_", dashboard.name.lower()).strip("_")
    return f"{slug or 'dashboard'}.json"


def pending_uploads(uploads, processed_ids) -> list:
    """Uploads not yet imported.

    st.file_uploader keeps returning the same file on every rerun, so importing
    whatever it holds — and then rerunning — duplicates the dashboard endlessly.
    Each upload carries a stable ``file_id``; import once per id.
    """
    return [u for u in (uploads or [])
            if getattr(u, "file_id", None) not in processed_ids]


def unique_dashboard_name(name: str, taken) -> str:
    """A name that does not collide with ``taken``.

    Imports never overwrite: re-importing the same file gives you a second copy
    to compare against, rather than silently replacing the one you have.
    """
    taken = set(taken)
    if name not in taken:
        return name
    candidate = f"{name} (imported)"
    i = 2
    while candidate in taken:
        candidate = f"{name} (imported {i})"
        i += 1
    return candidate


ROW_PX = 35          # Streamlit's data-editor row height, header included


def table_height(n_rows: int, allotted_px: int) -> int:
    """Height in pixels for st.dataframe: what the rows need, capped by the slot.

    Forcing the row's printed height on a short table pads it with blank filler
    rows, which reads as missing data. So a table that fits gets exactly the
    height its rows come to, and only one taller than its slot is constrained —
    there the cap is what makes it scroll rather than lose rows.

    A pixel count rather than st.dataframe's "content", which Streamlit only
    learned in 1.46: this is the same number, arrived at here instead of there,
    and every version takes it. An empty table keeps a row's worth of room, so
    its empty state has somewhere to print.
    """
    return min(ROW_PX * (max(n_rows, 1) + 1) + 3, allotted_px)


# --- widget rendering ------------------------------------------------------

def render_widget(pm, height_px: int, key: str, waiting: bool = False) -> None:
    """One widget on screen. Charts are interactive; the rest are native.

    ``waiting`` softens the error panel for a file dataset with nothing
    uploaded yet: "waiting for your export" is an instruction, not a fault, and
    a red panel saying the same thing would read as one.
    """
    if pm.kind == "error":
        if waiting:
            st.info(f"{pm.title or 'Widget'}: {pm.error}",
                    icon=":material/upload_file:")
        else:
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
                waiting = getattr(results.get(widget.dataset), "waiting", False)
                render_widget(build_plot_model(widget, results),
                              row_height_px(row.height_in),
                              key=f"{key_prefix}_{r_i}_{c_i}",
                              waiting=waiting)


# --- state helpers ---------------------------------------------------------

def frames_key(dashboard_id: int) -> str:
    return f"dash_frames_{dashboard_id}"


def is_due(as_of: datetime, refresh_secs: int,
           now: datetime | None = None) -> bool:
    """Whether frames taken at ``as_of`` have aged past the refresh interval.

    Refresh 'Off' never comes due: the numbers then change only when asked for.
    A half-second of slack keeps the timer from missing its own beat — it fires
    *on* the interval, and insisting on the full span would push every other
    tick to the one after.
    """
    if not refresh_secs:
        return False
    now = now or datetime.now()
    return (now - as_of).total_seconds() >= refresh_secs - 0.5


def comes_due(dashboard: Dashboard, as_of: datetime,
              now: datetime | None = None) -> bool:
    """Whether this dashboard's frames should be taken again.

    A file dashboard never does. Its frame changes when somebody uploads a
    different file and at no other moment, so a tick could only discard the
    cached printed pages to arrive back at the numbers already on screen — the
    same waste :func:`refresh` already records having fixed for query
    dashboards, arrived at from the other direction.
    """
    if dashboard.source == "file":
        return False
    return is_due(as_of, dashboard.refresh_secs, now)


def refresh(store, mgr, dashboard: Dashboard,
            uploads: dict | None = None,
            chosen: dict | None = None) -> dict:
    """The dashboard's frames, re-querying only when they are actually due.

    A rerun is not a reason to re-query. Every button on the page reruns the
    whole script, and running the datasets again each time restamped the frames
    — which hit KDB for numbers nobody asked to change and, because the printed
    pages are cached against that stamp, threw away the rendered PDF preview on
    every page turn. Frames are taken again on the interval, or when the
    Refresh button drops them. A file dashboard never comes due on its own — see
    :func:`comes_due` — so for it this only ever runs once per uploaded file,
    when Refresh drops the cache after :func:`_render_uploads` sees a new one.

    ``chosen`` is the reader's parameter picks. They never change whether a
    re-fetch is due — a parameter reaches a transform, not a query — so a
    picker change reaches this function only through :func:`drop_derived`,
    which forces the re-run that applies it.
    """
    cached = st.session_state.get(frames_key(dashboard.id))
    if cached and not comes_due(dashboard, cached["as_of"]):
        return cached

    # Here rather than only in the picker: a PDF export and an interval refresh
    # both reach this without anyone having touched the period control. A file
    # dashboard has no period to coerce — it has no environment for coerce_spec
    # to check against.
    if dashboard.source != "file":
        dashboard.time_context = coerce_spec(dashboard.time_context,
                                             dashboard.periods)
    payload = {"results": run_datasets(dashboard, store, mgr, date.today(),
                                       uploads=uploads, chosen=chosen),
               "as_of": datetime.now(),
               "rt": resolve(dashboard.time_context, date.today())}
    st.session_state[frames_key(dashboard.id)] = payload
    return payload


def force_refresh(dashboard_id: int) -> None:
    """Drop the frames so the next pass re-queries, and the PDF built from them.

    Used by every Refresh control: dropping the state is the whole action, so
    the button cannot disagree with what the fragment then does.
    """
    st.session_state.pop(frames_key(dashboard_id), None)
    st.session_state.pop(f"pdf_{dashboard_id}", None)
    st.rerun()


def drop_derived(dashboard_id) -> None:
    """Re-run the transforms, keeping whatever was fetched.

    A parameter never reaches a query or a file's shape, so the frames on hand
    are still the right frames — only the shaping of them has changed. Dropping
    the fetched ones too would ask a reader to upload their file again for
    having picked a different instrument.
    """
    st.session_state.pop(frames_key(dashboard_id), None)
    st.session_state.pop(f"pdf_{dashboard_id}", None)
    st.session_state.pop(f"pdfpages_{dashboard_id}", None)


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


def back_to_gallery() -> None:
    """Leave the open dashboard and return to the list.

    Clearing ``?dash`` is the only way back: Streamlit's sidebar page link keeps
    existing query params, so clicking 'Dashboards' in the nav while a dashboard
    is open lands on that same dashboard again. Hence a prominent in-page
    control rather than relying on the nav.
    """
    st.query_params.pop("dash", None)
    st.session_state.pop("dash_pills", None)
    st.rerun()


# --- gallery ---------------------------------------------------------------

def _render_gallery(store) -> None:
    st.subheader(":material/dashboard: Dashboards")
    head = st.columns([6, 1.6], vertical_alignment="center")
    head[0].caption("Saved views built from KDB queries. Open one to watch it "
                    "live, or export the current state as a PDF.")
    if head[1].button("New dashboard", icon=":material/add:", type="primary"):
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
            c = st.columns([2.8, 2.2, 1, 1, 1.2, 1.1, 0.8],
                           vertical_alignment="center")
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
            c[5].download_button(
                "Export", key=f"exp_{d.id}",
                data=export_dashboards_json([d]),
                file_name=dashboard_filename(d), mime="application/json",
                icon=":material/download:", use_container_width=True,
                help="Download this dashboard as JSON")
            with c[6].popover("", icon=":material/delete:"):
                st.warning(f"Delete '{d.name}'?")
                if st.button("Confirm", key=f"delok_{d.id}", type="primary"):
                    store.delete_dashboard(d.id)
                    st.rerun()

    st.divider()
    st.markdown("**Share dashboards**")
    st.caption("Dashboards travel as JSON. They reference *environments* by "
               "name, not servers, so a file lands cleanly on any machine whose "
               "Admin has the same environment names.")

    io_cols = st.columns([1.8, 4.2], vertical_alignment="top")
    with io_cols[0]:
        st.download_button(
            f"Export all ({len(saved)})", data=export_dashboards_json(saved),
            file_name="kdbmonitor_dashboards.json", mime="application/json",
            icon=":material/download:", disabled=not saved,
            help="Every dashboard in one file. Use a card's Export button for "
                 "just one.")

    with io_cols[1]:
        uploaded = st.file_uploader(
            "Import dashboards from a .json file", type=["json"],
            accept_multiple_files=True, key="dash_import",
            help="A name that already exists is suffixed rather than "
                 "overwritten, so an import never destroys your work.")

    processed = st.session_state.setdefault("dash_imported_ids", set())
    fresh = pending_uploads(uploaded, processed)
    imported_any = False

    for upload in fresh:
        # Mark first: a file that fails to parse must not be retried on every
        # rerun, or its error banner never goes away.
        processed.add(getattr(upload, "file_id", None))
        try:
            incoming = import_dashboards_json(upload.getvalue().decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            st.error(f"{upload.name}: {exc}", icon=":material/error:")
            continue
        if not incoming:
            st.warning(f"{upload.name} contains no dashboards.",
                       icon=":material/warning:")
            continue

        existing = {d.name for d in store.list_dashboards()}
        for d in incoming:
            d.name = unique_dashboard_name(d.name, existing)
            existing.add(d.name)
            store.add_dashboard(d)
        st.toast(f"Imported {len(incoming)} dashboard(s) from {upload.name}",
                 icon=":material/check:")
        imported_any = True

    if imported_any:
        st.rerun()


# --- view ------------------------------------------------------------------

def _render_period(store, dashboard: Dashboard, payload: dict | None) -> None:
    bar = st.columns([2.2, 1.6, 1.6, 2.6], vertical_alignment="bottom")

    # Held to what the dashboard offers before the picker is drawn, so a period
    # stored before it was declared one-sided cannot select a missing option.
    dashboard.time_context = coerce_spec(dashboard.time_context, dashboard.periods)
    options = list(time_options(dashboard.periods))
    if len(options) == 1:
        bar[0].markdown(f"**Period**<br>:gray[{options[0]} only]",
                        unsafe_allow_html=True)
        option = options[0]
    else:
        option = bar[0].selectbox(
            "Period", options, key=f"tc_{dashboard.id}",
            index=options.index(option_for_spec(dashboard.time_context)))
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
        force_refresh(dashboard.id)      # a new period is new data by definition

    if payload:
        bar[3].markdown(f":gray[{payload['rt'].label}]<br>"
                        f":gray[updated {payload['as_of']:%H:%M:%S}]",
                        unsafe_allow_html=True)


def uploads_key(dashboard_id: int) -> str:
    return f"dash_uploads_{dashboard_id}"


def _render_uploads(dashboard: Dashboard) -> dict:
    """One upload box per file dataset, and the frames they produced.

    The file is read and checked here, the moment it is dropped in, rather than
    on the way through the dashboard: a refusal belongs beside the thing that
    caused it rather than arriving later as a red panel some distance away, and
    reading it once means it is not re-read on every rerun.

    A refusal leaves the previous frame standing. Somebody comparing two exports
    should not lose the one that worked by trying one that does not.
    """
    from kdbmonitor.core.filesource import load

    held = st.session_state.setdefault(uploads_key(dashboard.id), {})
    datasets = [ds for ds in dashboard.datasets if ds.source == "file"]
    if not datasets:
        return {}

    with st.container(border=True):
        for ds in datasets:
            upload = st.file_uploader(
                ds.file_label or f"File for '{ds.name}'", type=["csv"],
                key=f"up_{dashboard.id}_{ds.name}")
            if upload is None:
                continue
            # A rerun is not a new file. Every button on the page reruns the
            # script and hands the same upload back; reading it again would
            # re-parse it and drop the printed pages built from it.
            token = (upload.name, upload.size)
            if held.get(ds.name, {}).get("token") == token:
                continue
            out = load(upload.getvalue(), ds.shape or FileShape())
            if out.problems:
                for problem in out.problems:
                    st.error(problem.message, icon=":material/error:")
                continue
            for note in out.notes:
                st.caption(f":gray[{note}]")
            st.success(f"{ds.name}: {len(out.df):,} row(s) read.",
                       icon=":material/check:")
            held[ds.name] = {"token": token, "df": out.df}
            force_refresh(dashboard.id)

    return {name: kept["df"] for name, kept in held.items()}


def _render_view(store, mgr, dashboard: Dashboard) -> None:
    # The way back lives here, first and on its own line, because the sidebar
    # nav cannot get you out (it keeps ?dash).
    nav = st.columns([1.15, 6.85], vertical_alignment="center")
    # Sized to its content — a full-width button here reads as a banner, not a
    # back control.
    if nav[0].button("All dashboards", icon=":material/arrow_back:",
                     help="Back to the dashboard list"):
        back_to_gallery()

    names = {d.name: d.id for d in store.list_dashboards()}
    with nav[1]:
        picked = st.pills("Dashboards", list(names), default=dashboard.name,
                          label_visibility="collapsed", key="dash_pills")
    if picked and names[picked] != dashboard.id:
        _open(names[picked])

    top = st.columns([3.5, 1.3, 1.2, 1.0], vertical_alignment="bottom")
    top[0].subheader(dashboard.name)

    if dashboard.source != "file":
        # A control that does nothing is worse than no control: a file
        # dashboard's numbers change only when a new file arrives, never on a
        # clock, so it gets no auto-refresh picker at all.
        labels = list(REFRESH_OPTIONS)
        current = next((k for k, v in REFRESH_OPTIONS.items()
                        if v == dashboard.refresh_secs), "15s")
        chosen = top[1].selectbox("Auto-refresh", labels,
                                  index=labels.index(current),
                                  key=f"rf_{dashboard.id}")
        if REFRESH_OPTIONS[chosen] != dashboard.refresh_secs:
            dashboard.refresh_secs = REFRESH_OPTIONS[chosen]
            store.update_dashboard(dashboard)
            st.rerun()

    # The numbers now hold still between interval ticks, so there has to be a
    # way to ask for new ones without waiting for the next one. For a file
    # dashboard this re-runs the pipeline against the frame already held,
    # rather than asking for the file again.
    if top[2].button("Refresh", icon=":material/refresh:",
                     key=f"rf_now_{dashboard.id}",
                     help="Run every dataset again now"):
        force_refresh(dashboard.id)

    if top[3].button("Edit", icon=":material/edit:"):
        st.session_state["dash_edit_id"] = dashboard.id
        st.session_state["dash_mode"] = "edit"
        st.session_state.pop("dash_draft", None)
        st.rerun()

    if dashboard.source != "file":
        _render_period(store, dashboard,
                       st.session_state.get(frames_key(dashboard.id)))

    payload = st.session_state.get(frames_key(dashboard.id))
    choices: dict = {}
    if payload:
        for res in payload["results"].values():
            choices.update(getattr(res, "choices", None) or {})
    parameters.render(dashboard, choices,
                      on_change=lambda: drop_derived(dashboard.id))

    uploads = _render_uploads(dashboard)

    @st.fragment(run_every=None if dashboard.source == "file"
                 else (dashboard.refresh_secs or None))
    def _live() -> None:
        data = refresh(store, mgr, dashboard, uploads,
                       chosen=parameters.chosen_values(dashboard))
        render_rows(dashboard, data["results"])

    _live()
    _render_export(dashboard)


def _render_export(dashboard: Dashboard) -> None:
    payload = st.session_state.get(frames_key(dashboard.id))
    st.divider()
    # Counted against the rows on screen: a table longer than its slot carries
    # on over further pages, so only the data knows how long the report is.
    sheet, pages = report_plan(dashboard,
                               payload["results"] if payload else None)
    # Why the page turned, said once and only where it did: a report that comes
    # out sideways with no explanation reads as a bug.
    turned = (" — turned landscape to fit a table's columns"
              if sheet is LANDSCAPE and dashboard.orientation == "auto" else "")
    st.caption(f":material/picture_as_pdf: This dashboard prints on "
               f"**{pages}** A4 {sheet.orientation} page(s){turned}. A table "
               f"longer than its row continues onto the next one, so the count "
               f"follows the data.")
    e = st.columns([1.6, 1.5, 1.5, 3], vertical_alignment="center")

    if e[0].button("Generate PDF", icon=":material/picture_as_pdf:", type="primary",
                   use_container_width=True, disabled=not payload):
        st.session_state[f"pdf_{dashboard.id}"] = dashboard_to_pdf_bytes(
            dashboard, payload["results"], payload["rt"], payload["as_of"])

    # One button both ways: opened, the only thing you want from it is to get
    # the page back, and a preview with no way out is a preview you regret.
    preview_flag = f"pdfpreview_on_{dashboard.id}"
    showing = bool(st.session_state.get(preview_flag))
    if e[1].button("Hide preview" if showing
                   else ("Preview pages" if pages > 1 else "Preview page"),
                   icon=":material/visibility_off:" if showing
                   else ":material/preview:",
                   use_container_width=True, disabled=not payload):
        st.session_state[preview_flag] = not showing
        st.rerun()

    data = st.session_state.get(f"pdf_{dashboard.id}")
    if data and payload:
        e[2].download_button("Download", data=data,
                             file_name=pdf_filename(dashboard, payload["as_of"]),
                             mime="application/pdf", icon=":material/download:",
                             use_container_width=True)
        e[3].caption("The PDF renders the numbers currently on screen — it does "
                     "not re-query.")

    if st.session_state.get(preview_flag) and payload:
        _render_pdf_preview(dashboard, payload, pages)


def _render_pdf_preview(dashboard: Dashboard, payload: dict, pages: int) -> None:
    """The printed pages, one at a time — every page, not just the first.

    Shown plainly rather than inside an expander: collapsing it left a stub
    across the page that still had to be scrolled past. Close puts the
    dashboard back, and the button that opened it says Hide while it is up.

    Turning a page never redraws the report: the pages are cached against the
    frames they were drawn from, and those only change on the refresh interval
    or when Refresh is pressed.
    """
    # The slider's own key IS the current page. Giving the buttons a separate
    # state let the two disagree: the title said page 2 while the slider still
    # read 1, because a keyed widget ignores `value=` after its first render.
    page_key = f"pv_page_{dashboard.id}"
    page_no = st.session_state.get(page_key, 1)
    if not isinstance(page_no, int) or not 1 <= page_no <= pages:
        page_no = 1
        st.session_state[page_key] = 1

    head = st.columns([4.2, 1.3, 1.2], vertical_alignment="center")
    head[0].markdown(f"**Printed page {page_no} of {pages}** "
                     f":gray[— the {payload['as_of']:%H:%M:%S} frames]")
    # Paging holds the report still on purpose, so the way to newer numbers is
    # here rather than implied by turning a page.
    if head[1].button("Refresh", icon=":material/refresh:",
                      key=f"pv_refresh_{dashboard.id}",
                      help="Re-query and draw the pages again"):
        force_refresh(dashboard.id)
    if head[2].button("Close", icon=":material/close:",
                      key=f"pv_close_{dashboard.id}"):
        st.session_state[f"pdfpreview_on_{dashboard.id}"] = False
        st.rerun()

    with st.container(border=True):
        if pages > 1:
            nav = st.columns([1, 1, 5], vertical_alignment="center")
            if nav[0].button("Previous", icon=":material/chevron_left:",
                             use_container_width=True, disabled=page_no <= 1,
                             key=f"pv_prev_{dashboard.id}"):
                st.session_state[page_key] = page_no - 1
                st.rerun()
            if nav[1].button("Next", icon=":material/chevron_right:",
                             use_container_width=True, disabled=page_no >= pages,
                             key=f"pv_next_{dashboard.id}"):
                st.session_state[page_key] = page_no + 1
                st.rerun()
            # Jumping straight to a page beats stepping through a long report.
            # Keyed on page_key, so moving it and the buttons drive one value.
            page_no = nav[2].select_slider(
                "Page", options=list(range(1, pages + 1)), key=page_key,
                label_visibility="collapsed")

        # Rendering a page costs ~0.3s, so cache per (page, as_of): paging back
        # and forth never re-renders, while frames taken on the interval — or by
        # Refresh — invalidate every page at once.
        cache = st.session_state.setdefault(f"pdfpages_{dashboard.id}", {})
        stamp = payload["as_of"]
        if cache.get("as_of") != stamp:
            cache = {"as_of": stamp, "pages": {}}
            st.session_state[f"pdfpages_{dashboard.id}"] = cache
        if page_no not in cache["pages"]:
            cache["pages"][page_no] = dashboard_page_png_bytes(
                dashboard, payload["results"], payload["rt"], stamp,
                page_no=page_no)

        st.image(cache["pages"][page_no], use_container_width=True)


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
