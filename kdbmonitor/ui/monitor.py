# kdbmonitor/ui/monitor.py
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from kdbmonitor.core import schedule as sched
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.ui import engine, layout as L
from kdbmonitor.ui.common import (
    STATUS_META, INTERVAL_PRESETS, secs_until_due, humanize_secs,
    condition_summary, group_label, sort_group_names, pluralize,
    schedule_summary,
)


def _today_strip(store) -> None:
    """Durable per-day totals, derived from the persisted run log so they don't
    reset when the app is restarted. Additive to the live 'now' KPIs below."""
    today = datetime.now(timezone.utc).date().isoformat()
    d = store.daily_stats(today)
    # A real section heading. This was gray bold body text, the same size and
    # weight as everything under it, so the page had no ladder to read down.
    L.section("Today", level=5,
              description=f"{today} UTC — cumulative, survives restarts.")
    # No "Armed today": a count of checks that found nothing is the day doing
    # what it should, and it crowded out the three totals worth reading.
    L.kpi_row([
        ("Triggered today", d["triggered_events"], "red",
         pluralize(d["triggered_alerts"], "alert")),
        ("Errors today", d["error_events"], "orange",
         pluralize(d["error_alerts"], "alert")),
        ("Notifications today", d["notifications"], None,
         f"{d['total_checks']} checks run"),
    ])


def render(store, mgr: ConnectionManager) -> None:
    if st.session_state.pop("_open_result", False) and "_nav_pages" in st.session_state:
        st.switch_page(st.session_state["_nav_pages"]["result"])

    # --- monitoring controls (state persists in the DB, so it keeps running on
    #     other tabs and auto-resumes after a restart; the loop itself lives in
    #     the app shell — see app.py / ui/engine.py) --------------------------- #
    running = engine.monitoring_on(store)
    gran = engine.granularity_label(store)

    # Whether monitoring is on is the page's headline fact, so it goes in the
    # header where a page-level state belongs. The badge used to float in a
    # third column of the control row, aligned neither to the controls on its
    # left nor to the content's right edge — some 250px short of both.
    L.page_header(
        "Live monitor", icon=":material/monitoring:",
        subtitle=("Checks run on every tab and resume after a restart. "
                  + ("Monitoring is on." if running else "Monitoring is paused.")))

    # Two controls, each at its own width, on one baseline: the toggle now
    # carries a label above it like the segmented control beside it, instead of
    # one label to the right and one above, 16px apart.
    ctl = L.toolbar(L.FIELD_NARROW, L.FIELD_WIDE)
    new_running = L.switch(
        ctl[0], "Monitoring", running,
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
            active = sched.is_active(a.schedule, now)
            if not a.enabled:
                display.append({"a": a, "status": "disabled", "rows": None,
                                "checked": None, "next": None})
            elif not active:
                # Enabled but outside its hours: parked, not pending and not
                # broken. Say when it wakes up rather than when it last ran.
                display.append({"a": a, "status": "off_hours", "rows": None,
                                "checked": latest["ts"] if latest else None,
                                "next": None,
                                "opens": sched.next_open(a.schedule, now)})
            elif latest is None or latest["status"] == "off_hours":
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
        n_off = sum(1 for d in display if d["status"] == "off_hours")
        L.section("Now", level=5, description="Current state of each alert.")
        # The Off-hours tile only appears once something is actually parked;
        # a desk with no scheduled alerts shouldn't carry a column of zeroes.
        tiles = [("Alerts", len(display), None, None),
                 ("Armed", n_armed, "green", None),
                 ("Triggered", n_trig, "red", None),
                 ("Errors", n_err, "orange", None)]
        if n_off:
            tiles.append(("Off-hours", n_off, None, "outside their windows"))
        # Both tile rows fill the content column, so the three above and the
        # four or five here start and finish on the same two edges. They used to
        # be st.columns(3) over st.columns(5), which shared only a left margin.
        L.kpi_row(tiles)

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
            with L.list_row(spans=L.ROW_SPANS_DETAIL) as row:
                row[0].badge(label, icon=icon, color=color)
                # A red "NEW" badge stays next to an alert that has fired until
                # the user opens it with View (persisted, so it survives restarts).
                new_flag = " :red-badge[:material/notifications_active: NEW]" if unseen else ""
                # The window is already spelled out beside a parked alert; only
                # a running one needs reminding that it has hours at all.
                hours = ("" if a.schedule.mode != "windows" or d["status"] == "off_hours"
                         else f" · :material/schedule: {schedule_summary(a.schedule)}")
                row[1].markdown(
                    f"**{a.name}**{new_flag}  \n"
                    f":gray[Triggers when {condition_summary(a.trigger)}{hours}]")
                if d["rows"] is None:
                    row[2].markdown(":gray[—]")
                else:
                    row[2].markdown(f"`{d['rows']}` rows")
                if d["status"] == "disabled":
                    row[3].caption("disabled")
                elif d["status"] == "off_hours":
                    opens = d.get("opens")
                    when = (f"opens in {humanize_secs(int((opens - now).total_seconds()))}"
                            if opens else "no window ahead")
                    row[3].caption(f":material/bedtime: {when} · runs "
                                   f"{schedule_summary(a.schedule)}")
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
            # A filter is a toolbar control, not content: four short chips do not
            # need the full content column, and stretching them there is what put
            # the help icon 800px from its own label.
            chosen = L.toolbar(L.FIELD_WIDE)[0].multiselect(
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
