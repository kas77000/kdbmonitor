# kdbmonitor/ui/monitor.py
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.ui import engine
from kdbmonitor.ui.common import (
    STATUS_META, INTERVAL_PRESETS, secs_until_due, humanize_secs,
    condition_summary, group_label, sort_group_names, pluralize,
)


def _kpi(col, label: str, value: int, color: str | None = None,
         caption: str | None = None) -> None:
    """Metric-style tile whose number turns `color` when it's non-zero."""
    with col.container(border=True):
        st.caption(label)
        if color and value:
            st.markdown(f"### :{color}[{value}]")
        else:
            st.markdown(f"### {value}")
        if caption:
            st.caption(caption)


def _today_strip(store) -> None:
    """Durable per-day totals, derived from the persisted run log so they don't
    reset when the app is restarted. Additive to the live 'now' KPIs below."""
    today = datetime.now(timezone.utc).date().isoformat()
    d = store.daily_stats(today)
    st.markdown(f":gray[**Today** · {today} UTC — cumulative, survives restarts]")
    k = st.columns(4)
    _kpi(k[0], "Triggered today", d["triggered_events"], color="red",
         caption=f"{pluralize(d['triggered_alerts'], 'alert')}")
    _kpi(k[1], "Armed today", d["armed_events"], color="green",
         caption=f"{pluralize(d['armed_alerts'], 'alert')}")
    _kpi(k[2], "Errors today", d["error_events"], color="orange",
         caption=f"{pluralize(d['error_alerts'], 'alert')}")
    _kpi(k[3], "Notifications today", d["notifications"],
         caption=f"{d['total_checks']} checks run")


def render(store, mgr: ConnectionManager) -> None:
    if st.session_state.pop("_open_result", False) and "_nav_pages" in st.session_state:
        st.switch_page(st.session_state["_nav_pages"]["result"])

    st.subheader(":material/monitoring: Live monitor")

    # --- monitoring controls (state persists in the DB, so it keeps running on
    #     other tabs and auto-resumes after a restart; the loop itself lives in
    #     the app shell — see app.py / ui/engine.py) --------------------------- #
    running = engine.monitoring_on(store)
    gran = engine.granularity_label(store)

    ctl = st.columns([1.4, 2.4, 1.2], vertical_alignment="center")
    new_running = ctl[0].toggle(
        "Monitoring", value=running,
        help="Checks keep running on every tab and after a restart while this is on.")
    if new_running != running:
        engine.set_monitoring(store, new_running)
        st.rerun()
    new_gran = ctl[1].segmented_control(
        "Check granularity", list(INTERVAL_PRESETS.keys()), default=gran,
        help="How often the loop wakes. Each alert still checks on its own interval.")
    if new_gran and new_gran != gran:
        engine.set_granularity(store, new_gran)
        st.rerun()
    ctl[2].markdown(
        (":green-badge[:material/sensors: Active]" if running
         else ":gray-badge[:material/sensors_off: Paused]")
    )

    tick = INTERVAL_PRESETS.get(gran, 15)

    alerts = store.list_alerts()
    if not alerts:
        st.info("No alerts yet. Create one in the Builder.", icon=":material/build:")
        return

    @st.fragment(run_every=tick if running else None)
    def _view() -> None:
        # Display only: the app-shell engine does the evaluating/recording; here
        # we just read the latest persisted state so a page rerun never fires an
        # alert on its own.
        now = datetime.now(timezone.utc)
        display = []
        for a in store.list_alerts():
            latest = store.latest_run(a.id)
            if not a.enabled:
                display.append({"a": a, "status": "disabled", "rows": None,
                                "checked": None, "next": None})
            elif latest is None:
                display.append({"a": a, "status": "pending", "rows": None,
                                "checked": None, "next": 0})
            else:
                display.append({
                    "a": a, "status": latest["status"], "rows": latest["row_count"],
                    "checked": latest["ts"],
                    "next": secs_until_due(latest["ts"], a.poll_interval_secs, now)})

        # Durable per-day totals first, then the live 'now' snapshot KPIs.
        _today_strip(store)

        n_trig = sum(1 for d in display if d["status"] == "triggered")
        n_err = sum(1 for d in display if d["status"] == "error")
        n_armed = sum(1 for d in display if d["status"] == "armed")
        st.markdown(":gray[**Now** — current state of each alert]")
        k = st.columns(4)
        _kpi(k[0], "Alerts", len(display))
        _kpi(k[1], "Armed", n_armed, color="green")
        _kpi(k[2], "Triggered", n_trig, color="red")
        _kpi(k[3], "Errors", n_err, color="orange")

        # Active triggers surfaced first
        for d in display:
            if d["status"] == "triggered":
                st.error(f"**{d['a'].name}** — {condition_summary(d['a'].trigger)}  "
                         f"·  {pluralize(d['rows'], 'row')}",
                         icon=":material/notifications_active:")

        # Per-alert status rows, bucketed by group
        last_results = st.session_state.get("last_results", {})

        def _render_row(d) -> None:
            a = d["a"]
            label, color, icon = STATUS_META[d["status"]]
            unseen = store.has_unseen_trigger(a.id)
            with st.container(border=True):
                row = st.columns([1.4, 3.4, 1, 2.1, 1.1], vertical_alignment="center")
                row[0].badge(label, icon=icon, color=color)
                # A red "NEW" badge stays next to an alert that has fired until
                # the user opens it with View (persisted, so it survives restarts).
                new_flag = " :red-badge[:material/notifications_active: NEW]" if unseen else ""
                row[1].markdown(
                    f"**{a.name}**{new_flag}  \n"
                    f":gray[Triggers when {condition_summary(a.trigger)}]")
                if d["rows"] is None:
                    row[2].markdown(":gray[—]")
                else:
                    row[2].markdown(f"`{d['rows']}` rows")
                if d["status"] == "disabled":
                    row[3].caption("disabled")
                elif d["status"] == "pending":
                    row[3].caption("awaiting first check")
                else:
                    nxt = d["next"] if isinstance(d["next"], int) else a.poll_interval_secs
                    row[3].caption(f":material/schedule: next check in {humanize_secs(nxt)} "
                                   f"· every {humanize_secs(a.poll_interval_secs)}")

                stored = last_results.get(a.id)
                has_result = (stored is not None and stored.get("df") is not None)
                # Show View whenever there is something to open OR an unread
                # trigger to clear; the result page falls back to the stored
                # daily snapshot when the in-session result is gone.
                if has_result or unseen:
                    btn_type = "primary" if unseen else "secondary"
                    if row[4].button("View", key=f"view_{a.id}",
                                     icon=":material/open_in_full:", type=btn_type,
                                     help="Open the result and clear the NEW flag"):
                        store.mark_triggers_seen(a.id)
                        st.session_state["result_alert_id"] = a.id
                        st.session_state["_open_result"] = True
                        st.rerun()
                else:
                    row[4].caption("—")

        buckets: dict[str, list] = {}
        for d in display:
            buckets.setdefault(group_label(d["a"]), []).append(d)
        ordered = sort_group_names(buckets.keys())
        grouped_view = len(buckets) > 1 or set(buckets) != {"Ungrouped"}

        if grouped_view:
            chosen = st.multiselect(
                "Filter groups", ordered, default=ordered, key="mon_group_filter",
                help="Show only the chosen groups. Collapse a group with its ▸ header.")
            visible = chosen or ordered            # empty selection = show all
            for gname in ordered:
                if gname not in visible:
                    continue
                items = buckets[gname]
                n_t = sum(1 for d in items if d["status"] == "triggered")
                n_e = sum(1 for d in items if d["status"] == "error")
                lbl = f"**{gname}** · {len(items)}"
                if n_t:
                    lbl += f" · :red[{n_t} triggered]"
                if n_e:
                    lbl += f" · :orange[{n_e} error]"
                with st.expander(lbl, expanded=True, icon=":material/folder:"):
                    for d in items:
                        _render_row(d)
        else:
            for d in display:
                _render_row(d)

        st.caption(f"Last refresh: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC"
                   + ("" if running else " · monitoring paused"))

    _view()
