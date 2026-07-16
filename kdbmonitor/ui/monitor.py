# kdbmonitor/ui/monitor.py
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.evaluate import evaluate_alert
from kdbmonitor.core.notifiers import InAppSink, dispatch, send_email, post_webhook
from kdbmonitor.ui.common import (
    STATUS_META, INTERVAL_PRESETS, is_due, secs_until_due, humanize_secs,
    condition_summary, make_client_for,
)


def _email_fn(store):
    smtp_host = store.get_setting("smtp_host", "")
    if not smtp_host:
        return None
    smtp_port = int(store.get_setting("smtp_port", "25"))
    smtp_sender = store.get_setting("smtp_sender", "")
    return lambda to, msg: send_email(
        smtp_host, smtp_port, smtp_sender, to, subject="KdbMonitor alert", body=msg
    )


def render(store, mgr: ConnectionManager) -> None:
    resolve = make_client_for(store, mgr)
    sink: InAppSink = st.session_state.setdefault("in_app_sink", InAppSink())

    st.subheader(":material/monitoring: Live monitor")

    ctl = st.columns([1.4, 2.4, 1.2], vertical_alignment="center")
    running = ctl[0].toggle("Monitoring", value=False, key="mon_running",
                            help="Checks run only while this is on and the page is open.")
    gran_label = ctl[1].segmented_control(
        "Check granularity", list(INTERVAL_PRESETS.keys()), default="15s",
        key="mon_gran", help="How often the loop wakes. Each alert still checks on its own interval.",
    )
    tick = INTERVAL_PRESETS.get(gran_label or "15s", 15)
    ctl[2].markdown(
        (":green-badge[:material/sensors: Active]" if running
         else ":gray-badge[:material/sensors_off: Paused]")
    )

    alerts = store.list_alerts()
    if not alerts:
        st.info("No alerts yet. Create one in the Builder.", icon=":material/build:")
        return

    @st.fragment(run_every=tick if running else None)
    def _tick() -> None:
        now = datetime.now(timezone.utc)
        email_fn = _email_fn(store)
        new_sound = False
        display = []

        for a in store.list_alerts():
            latest = store.latest_run(a.id)
            if not a.enabled:
                display.append({"a": a, "status": "disabled", "rows": None,
                                "checked": None, "next": None})
                continue

            due = is_due(latest["ts"] if latest else None, a.poll_interval_secs, now)
            if due:
                res = evaluate_alert(a, resolve, prev_run=latest, now=now,
                                     last_notified_ts=store.last_notified_at(a.id))
                store.record_run(a.id, ts=now.isoformat(), status=res.status,
                                 triggered=res.triggered, notified=res.notify,
                                 row_count=res.row_count, message=res.message)
                if res.df is not None:
                    st.session_state.setdefault("last_results", {})[a.id] = {
                        "df": res.df, "rows": res.row_count, "when": now,
                        "triggered": res.triggered}
                if res.notify:
                    dispatch(a.channels, res.message, in_app_sink=sink,
                             email_fn=email_fn, webhook_fn=post_webhook)
                    if a.channels.in_app and a.channels.sound:
                        new_sound = True
                display.append({"a": a, "status": res.status, "rows": res.row_count,
                                "checked": now, "next": a.poll_interval_secs})
            else:
                status = latest["status"] if latest else "pending"
                nxt = secs_until_due(latest["ts"] if latest else None,
                                     a.poll_interval_secs, now)
                display.append({"a": a, "status": status,
                                "rows": latest["row_count"] if latest else None,
                                "checked": latest["ts"] if latest else None, "next": nxt})

        # KPI row
        n_trig = sum(1 for d in display if d["status"] == "triggered")
        n_err = sum(1 for d in display if d["status"] == "error")
        n_armed = sum(1 for d in display if d["status"] == "armed")
        k = st.columns(4)
        k[0].metric("Alerts", len(display), border=True)
        k[1].metric("Armed", n_armed, border=True)
        k[2].metric("Triggered", n_trig, border=True)
        k[3].metric("Errors", n_err, border=True)

        # Active triggers surfaced first
        for d in display:
            if d["status"] == "triggered":
                st.error(f"**{d['a'].name}** — {condition_summary(d['a'].trigger)}  "
                         f"·  {d['rows']} row(s)", icon=":material/notifications_active:")
        if new_sound:
            st.markdown(
                "<audio autoplay><source src='https://actions.google.com/sounds/v1/alarms/beep_short.ogg'></audio>",
                unsafe_allow_html=True,
            )

        # Per-alert status rows
        last_results = st.session_state.get("last_results", {})
        for d in display:
            a = d["a"]
            label, color, icon = STATUS_META[d["status"]]
            row = st.columns([1.4, 3.4, 1, 2.1, 1.1], vertical_alignment="center")
            row[0].badge(label, icon=icon, color=color)
            row[1].markdown(f"**{a.name}**  \n:gray[Triggers when {condition_summary(a.trigger)}]")
            rows_txt = "—" if d["rows"] is None else str(d["rows"])
            row[2].markdown(f"`{rows_txt}` rows")
            if d["status"] == "disabled":
                row[3].caption("disabled")
            elif d["status"] == "pending":
                row[3].caption("awaiting first check")
            else:
                nxt = d["next"] if isinstance(d["next"], int) else a.poll_interval_secs
                row[3].caption(f":material/schedule: next check in {humanize_secs(nxt)} "
                               f"· every {humanize_secs(a.poll_interval_secs)}")

            stored = last_results.get(a.id)
            if stored is not None and stored.get("df") is not None:
                with row[4].popover("Result", icon=":material/table:"):
                    when = stored["when"]
                    when_txt = (when.strftime("%H:%M:%S") if hasattr(when, "strftime")
                                else str(when))
                    st.caption(f"{stored['rows']} row(s) · checked {when_txt} UTC"
                               + (" · triggered" if stored["triggered"] else ""))
                    st.dataframe(stored["df"], use_container_width=True,
                                 hide_index=True)
            else:
                row[4].caption("—")

        st.caption(f"Last loop: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC"
                   + ("" if running else " · monitoring paused"))

    _tick()
