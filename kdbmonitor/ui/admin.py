# kdbmonitor/ui/admin.py
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from kdbmonitor.core.models import Connection
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.schema import introspect
from kdbmonitor.core.mock import demo_connection_specs


def _fmt_ts(ts: str | None) -> str:
    if not ts:
        return "never"
    try:
        return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ts


def render(store, mgr: ConnectionManager) -> None:
    st.subheader(":material/settings: Admin")

    conns = store.list_connections()

    # ---- Demo mode -------------------------------------------------------- #
    with st.container(border=True):
        d = st.columns([5, 1.6], vertical_alignment="center")
        d[0].markdown("**Demo KDB** — try the app with an in-memory mock, no real "
                      "connection")
        d[0].caption("Adds `kdp_demo` (QATT) and `orders_demo` (target, work_order, "
                     "target_state) with live synthetic data.")
        existing = {c.name for c in conns}
        already = existing.issuperset({"kdp_demo", "orders_demo"})
        if d[1].button("Load demo servers", icon=":material/science:",
                       disabled=already, type="primary" if not conns else "secondary"):
            added = 0
            for spec in demo_connection_specs():
                if spec.name not in existing:
                    store.add_connection(spec)
                    added += 1
            st.toast(f"Loaded {added} demo server(s)", icon=":material/check:")
            st.rerun()
        if already:
            d[1].caption("Loaded ✓")

    # ---- Add connection --------------------------------------------------- #
    st.markdown("**Add a KDB connection**")
    with st.form("add_conn", clear_on_submit=True, border=True):
        f = st.columns([2, 2, 1, 1.2], vertical_alignment="bottom")
        name = f[0].text_input("Name", placeholder="e.g. kdp, orders")
        host = f[1].text_input("Host", value="localhost")
        port = f[2].number_input("Port", 1, 65535, 5010)
        submitted = f[3].form_submit_button("Add", icon=":material/add:",
                                            use_container_width=True)
        if submitted and name:
            store.add_connection(Connection(id=None, name=name, host=host, port=int(port)))
            st.toast(f"Added '{name}'", icon=":material/check:")
            st.rerun()

    # ---- Registered servers ---------------------------------------------- #
    st.markdown("**Registered servers**")
    if not conns:
        st.caption("None yet. Load the demo servers above or add one.")
    for c in conns:
        with st.container(border=True):
            row = st.columns([2, 2.4, 2, 1.2, 1], vertical_alignment="center")
            is_demo = c.host == "demo"
            row[0].markdown(f"**{c.name}**"
                            + (" :blue-badge[demo]" if is_demo else ""))
            row[1].markdown(f"`{c.host}:{c.port}`")
            if c.schema:
                row[2].markdown(f":green-badge[:material/table: {len(c.schema)} tables] "
                                f":gray[· {_fmt_ts(c.last_introspected_at)}]")
            else:
                row[2].markdown(":orange-badge[not introspected]")
            if row[3].button("Introspect", key=f"intro_{c.id}",
                             icon=":material/sync:"):
                try:
                    c.schema = introspect(mgr.get(c))
                    c.last_introspected_at = datetime.now(timezone.utc).isoformat()
                    store.update_connection(c)
                    st.toast(f"{c.name}: {len(c.schema)} tables", icon=":material/check:")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Introspect failed: {exc}", icon=":material/error:")
            with row[4].popover("", icon=":material/delete:"):
                st.warning(f"Delete '{c.name}'?")
                if st.button("Confirm", key=f"del_{c.id}", type="primary"):
                    store.delete_connection(c.id)
                    st.rerun()

    # ---- SMTP ------------------------------------------------------------- #
    st.markdown("**Email (SMTP)**")
    with st.container(border=True):
        st.caption("Used by alerts that select the email channel.")
        s = st.columns([2, 1, 2], vertical_alignment="bottom")
        host = s[0].text_input("SMTP host", value=store.get_setting("smtp_host", ""))
        port = int(s[1].number_input("Port", 1, 65535,
                                     int(store.get_setting("smtp_port", "25"))))
        sender = s[2].text_input("From address", value=store.get_setting("smtp_sender", ""))
        if st.button("Save SMTP settings", icon=":material/save:"):
            store.set_setting("smtp_host", host)
            store.set_setting("smtp_port", str(port))
            store.set_setting("smtp_sender", sender)
            st.toast("Saved SMTP settings", icon=":material/check:")
