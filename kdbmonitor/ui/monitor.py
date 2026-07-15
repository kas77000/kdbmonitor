# kdbmonitor/ui/monitor.py
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.evaluate import evaluate_alert
from kdbmonitor.core.notifiers import InAppSink, dispatch, send_email, post_webhook


def _client_for(store, mgr: ConnectionManager):
    def resolve(server_name: str):
        conn = store.get_connection_by_name(server_name)
        if conn is None:
            raise RuntimeError(f"unknown server '{server_name}'")
        return mgr.get(conn)
    return resolve


def render(store, mgr: ConnectionManager) -> None:
    st.header("Monitor — Live")
    refresh = int(st.number_input("Refresh every (seconds)", 5, 600, 30))
    running = st.toggle("Actively monitoring", value=False)
    sink: InAppSink = st.session_state.setdefault("in_app_sink", InAppSink())
    resolve = _client_for(store, mgr)

    @st.fragment(run_every=refresh if running else None)
    def _tick() -> None:
        now = datetime.now(timezone.utc)
        smtp_host = store.get_setting("smtp_host", "")
        rows = []
        new_sound = False
        for a in store.list_alerts():
            if not a.enabled:
                rows.append({"alert": a.name, "status": "disabled", "rows": None, "when": ""})
                continue
            prev = store.latest_run(a.id)
            last_notified = store.last_notified_at(a.id)
            res = evaluate_alert(a, resolve, prev_run=prev, now=now,
                                 last_notified_ts=last_notified)
            store.record_run(a.id, ts=now.isoformat(), status=res.status,
                             triggered=res.triggered, notified=res.notify,
                             row_count=res.row_count, message=res.message)
            if res.notify:
                email_fn = None
                if smtp_host:
                    smtp_port = int(store.get_setting("smtp_port", "25"))
                    smtp_sender = store.get_setting("smtp_sender", "")
                    email_fn = lambda to, msg: send_email(
                        smtp_host, smtp_port, smtp_sender, to,
                        subject="KdbMonitor alert", body=msg)
                dispatch(a.channels, res.message, in_app_sink=sink,
                         email_fn=email_fn, webhook_fn=post_webhook)
                if a.channels.in_app and a.channels.sound:
                    new_sound = True
            rows.append({"alert": a.name, "status": res.status,
                         "rows": res.row_count, "when": now.strftime("%H:%M:%S")})

        if sink.messages:
            for m in sink.messages[-10:]:
                st.error(f"🔔 {m}")
        if new_sound:
            st.markdown(
                "<audio autoplay><source src='https://actions.google.com/sounds/v1/alarms/beep_short.ogg'></audio>",
                unsafe_allow_html=True,
            )
        st.dataframe(rows, use_container_width=True)
        st.caption(f"Last check: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")

    _tick()
