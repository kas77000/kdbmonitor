# kdbmonitor/ui/admin.py
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from kdbmonitor.core.models import Connection
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.schema import introspect


def render(store, mgr: ConnectionManager) -> None:
    st.header("Admin — KDB Connections")

    with st.form("add_conn", clear_on_submit=True):
        name = st.text_input("Name (e.g. orders, kdp)")
        host = st.text_input("Host", value="localhost")
        port = st.number_input("Port", min_value=1, max_value=65535, value=5010, step=1)
        if st.form_submit_button("Add connection") and name:
            store.add_connection(Connection(id=None, name=name, host=host, port=int(port)))
            st.success(f"Added {name}")
            st.rerun()

    st.subheader("Registered servers")
    for c in store.list_connections():
        cols = st.columns([3, 3, 2, 2, 2])
        cols[0].write(f"**{c.name}**")
        cols[1].write(f"{c.host}:{c.port}")
        cols[2].write(f"{len(c.schema)} tables" if c.schema else "not introspected")
        if cols[3].button("Introspect", key=f"intro_{c.id}"):
            try:
                c.schema = introspect(mgr.get(c))
                c.last_introspected_at = datetime.now(timezone.utc).isoformat()
                store.update_connection(c)
                st.success(f"{c.name}: found {len(c.schema)} tables")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"Introspect failed: {exc}")
        if cols[4].button("Delete", key=f"del_{c.id}"):
            store.delete_connection(c.id)
            st.rerun()

    st.subheader("Email (SMTP) settings")
    st.caption("Used by alerts that select the email channel.")
    st.session_state.setdefault("smtp", {"host": "", "port": 25, "sender": ""})
    smtp = st.session_state["smtp"]
    smtp["host"] = st.text_input("SMTP host", value=smtp["host"])
    smtp["port"] = int(st.number_input("SMTP port", min_value=1, value=int(smtp["port"])))
    smtp["sender"] = st.text_input("From address", value=smtp["sender"])
