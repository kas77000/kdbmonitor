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


def _render_environments(store) -> None:
    """Show real-time/historical pairs, and flag any half-configured one.

    A dashboard can only offer 'historical' for an environment that actually has
    a historical server, so a missing side is worth surfacing here rather than as
    a query error later.
    """
    st.markdown("**Environments**")
    envs = store.list_environments()
    if not envs:
        st.caption("None yet — environments appear once you add connections.")
        return

    with st.container(border=True):
        st.caption("A real-time and a historical server sharing an environment "
                   "name form a pair. Dashboards pick the environment; the date "
                   "range decides which server is queried.")
        for env, pair in sorted(envs.items()):
            e = st.columns([2, 2.5, 2.5], vertical_alignment="center")
            e[0].markdown(f"**{env}**")
            for i, kind in enumerate(("realtime", "historical"), start=1):
                conn = pair[kind]
                label = "Real-time" if kind == "realtime" else "Historical"
                if conn is not None:
                    e[i].markdown(f":green-badge[{label}] `{conn.name}`")
                else:
                    e[i].markdown(f":orange-badge[{label} missing]")
            if pair["historical"] is None:
                st.caption(f":orange[Environment '{env}' has no historical server "
                           f"— dashboards on it cannot query date ranges.]")


def render(store, mgr: ConnectionManager) -> None:
    st.subheader(":material/settings: Admin")

    conns = store.list_connections()

    # ---- Demo mode -------------------------------------------------------- #
    with st.container(border=True):
        d = st.columns([5, 1.6], vertical_alignment="center")
        d[0].markdown("**Demo KDB** — try the app with an in-memory mock, no real "
                      "connection")
        d[0].caption("Adds `kdp_demo` (QATT), `orders_demo` (target, work_order, "
                     "target_state) and `orders_hdb_demo` (the same tables with a "
                     "date column) with live synthetic data.")
        existing = {c.name for c in conns}
        already = existing.issuperset({"kdp_demo", "orders_demo", "orders_hdb_demo"})
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
        f = st.columns([2, 2, 1, 1.6, 1.4, 1.2], vertical_alignment="bottom")
        name = f[0].text_input("Name", placeholder="e.g. order-rdb")
        host = f[1].text_input("Host", value="localhost")
        port = f[2].number_input("Port", 1, 65535, 5010)
        env = f[3].text_input("Environment", placeholder="e.g. orders",
                              help="Pair a real-time and a historical server by "
                                   "giving them the same environment name.")
        kind = f[4].selectbox("Kind", ["realtime", "historical"],
                              help="Historical servers carry a date column; "
                                   "dashboards inject the date range for you.")
        submitted = f[5].form_submit_button("Add", icon=":material/add:",
                                            use_container_width=True)
        if submitted and name:
            try:
                store.add_connection(Connection(id=None, name=name.strip(),
                                                host=host.strip(), port=int(port),
                                                kind=kind, env=env.strip()))
            except ValueError as exc:
                st.error(str(exc), icon=":material/error:")
            except Exception as exc:  # noqa: BLE001 — DB failure shouldn't crash the page
                st.error(f"Could not add connection: {exc}", icon=":material/error:")
            else:
                st.toast(f"Added '{name}'", icon=":material/check:")
                st.rerun()
        elif submitted and not name:
            st.error("Connection needs a name.", icon=":material/error:")

    # ---- Registered servers ---------------------------------------------- #
    st.markdown("**Registered servers**")
    if not conns:
        st.caption("None yet. Load the demo servers above or add one.")
    for c in conns:
        with st.container(border=True):
            row = st.columns([2, 2.4, 2, 1.2, 1], vertical_alignment="center")
            is_demo = c.host == "demo"
            badge = " :blue-badge[demo]" if is_demo else ""
            badge += (" :violet-badge[historical]" if c.kind == "historical"
                      else " :gray-badge[real-time]")
            row[0].markdown(f"**{c.name}**{badge}<br>:gray[env: {c.env or c.name}]",
                            unsafe_allow_html=True)
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

    _render_environments(store)

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

    # ---- Result snapshots (reports) -------------------------------------- #
    st.markdown("**Result snapshots (for reports)**")
    with st.container(border=True):
        st.caption("Triggered results are stored per alert per day so they appear in "
                   "reports. Older days are pruned; large results are capped.")
        r = st.columns([1.4, 1.4, 2], vertical_alignment="bottom")
        days = int(r[0].number_input("Keep days", 1, 3650,
                                     store.get_result_retention_days(),
                                     help="Snapshots older than this are deleted."))
        rows = int(r[1].number_input("Max rows per snapshot", 1, 1_000_000,
                                     store.get_result_max_rows(), step=100,
                                     help="Rows beyond this are dropped; the true "
                                          "count is still recorded and reports flag it."))
        if r[2].button("Save snapshot settings", icon=":material/save:"):
            store.set_result_retention_days(days)   # also prunes existing to the new window
            store.set_result_max_rows(rows)
            st.toast("Saved snapshot settings", icon=":material/check:")
