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
    refresh = st.number_input("Refresh every (seconds)", 5, 600, 30)
    running = st.toggle("Actively monitoring", value=False)
    sink: InAppSink = st.session_state.setdefault("in_app_sink", InAppSink())

    now = datetime.now(timezone.utc)
    resolve = _client_for(store, mgr)
    rows = []
    for a in store.list_alerts():
        if not a.enabled:
            rows.append({"alert": a.name, "status": "disabled", "rows": None, "when": ""})
            continue
        prev = store.latest_run(a.id)
        res = evaluate_alert(a, resolve, prev_run=prev, now=now)
        store.record_run(a.id, ts=now.isoformat(), status=res.status,
                         triggered=res.triggered, notified=res.notify,
                         row_count=res.row_count, message=res.message)
        if res.notify:
            smtp = st.session_state.get("smtp", {})
            email_fn = None
            if smtp.get("host"):
                email_fn = lambda to, msg: send_email(
                    smtp["host"], int(smtp["port"]), smtp["sender"], to,
                    subject="KdbMonitor alert", body=msg)
            dispatch(a.channels, res.message, in_app_sink=sink,
                     email_fn=email_fn, webhook_fn=post_webhook)
        rows.append({"alert": a.name, "status": res.status,
                     "rows": res.row_count, "when": now.strftime("%H:%M:%S")})

    if sink.messages:
        for m in sink.messages[-10:]:
            st.error(f"🔔 {m}")
        st.markdown(
            "<audio autoplay><source src='https://actions.google.com/sounds/v1/alarms/beep_short.ogg'></audio>",
            unsafe_allow_html=True,
        )

    st.dataframe(rows, use_container_width=True)
    st.caption(f"Last check: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")

    if running:
        # Streamlit auto-refresh: rerun after `refresh` seconds while the page is open.
        st.markdown(
            f"<meta http-equiv='refresh' content='{int(refresh)}'>",
            unsafe_allow_html=True,
        )
