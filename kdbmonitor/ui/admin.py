# kdbmonitor/ui/admin.py
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from kdbmonitor.core.models import (
    CONNECTION_KINDS, KIND_LABELS, Connection,
)
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


NEW_ENV = "＋ New environment…"


def _add_connection(store, name: str, host: str, port, kind: str,
                    env: str) -> None:
    try:
        store.add_connection(Connection(id=None, name=name.strip(),
                                        host=host.strip(), port=int(port),
                                        kind=kind, env=env.strip()))
    except ValueError as exc:
        st.error(str(exc), icon=":material/error:")
    except Exception as exc:  # noqa: BLE001 — DB failure shouldn't crash the page
        st.error(f"Could not add connection: {exc}", icon=":material/error:")
    else:
        st.toast(f"Added '{name}' to {env}", icon=":material/check:")
        st.rerun()


def _env_options(store, kind: str) -> list[str]:
    """Environments a connection of ``kind`` could sensibly join.

    Market data stands alone — pairing it with a real-time/historical env would
    make 'which server does this dataset hit' ambiguous. Real-time and
    historical are offered the envs that do not already have that side filled.
    """
    envs = store.list_environments()
    if kind == "marketdata":
        return [e for e, pair in envs.items()
                if pair["realtime"] is None and pair["historical"] is None]
    return [e for e, pair in envs.items()
            if pair["marketdata"] is None and pair[kind] is None]


def _partner_hint(store, env: str, kind: str) -> str:
    """What this connection would be linked to by joining ``env``."""
    pair = store.list_environments().get(env)
    if not pair:
        return ""
    other = "historical" if kind == "realtime" else "realtime"
    partner = pair.get(other)
    if partner is not None:
        return (f"Will be linked to **{partner.name}** "
                f"({KIND_LABELS[other].lower()}) in environment `{env}`.")
    return f"Will be the first server in environment `{env}`."


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
        st.caption("A dashboard dataset targets an *environment*, not a server. "
                   "A real-time and a historical server in the same environment "
                   "are linked: the dashboard's period decides which one is "
                   "queried. Market-data environments hold reference data and "
                   "ignore the period.")
        for env, pair in sorted(envs.items()):
            market = pair["marketdata"]
            e = st.columns([2, 2.5, 2.5], vertical_alignment="center")
            e[0].markdown(f"**{env}**")

            if market is not None:
                e[1].markdown(f":violet-badge[Market data] `{market.name}`")
                e[2].markdown(":gray[period does not apply]")
                if pair["realtime"] or pair["historical"]:
                    st.caption(f":orange[Environment '{env}' mixes market data "
                               f"with real-time/historical servers. Datasets on "
                               f"it will always use the market-data server — "
                               f"give the others their own environment.]")
                continue

            for i, kind in enumerate(("realtime", "historical"), start=1):
                conn = pair[kind]
                label = KIND_LABELS[kind]
                if conn is not None:
                    e[i].markdown(f":green-badge[{label}] `{conn.name}`")
                else:
                    e[i].markdown(f":orange-badge[{label} missing]")

            if pair["realtime"] is not None and pair["historical"] is not None:
                st.caption(f":green[`{pair['realtime'].name}` ↔ "
                           f"`{pair['historical'].name}` are linked.]")
            elif pair["historical"] is None:
                st.caption(f":orange[Environment '{env}' has no historical server "
                           f"— dashboards on it cannot query date ranges.]")
            else:
                st.caption(f":orange[Environment '{env}' has no real-time server "
                           f"— dashboards on it cannot show today.]")


def _render_relink(store, conns) -> None:
    """Move an existing connection into another environment.

    Without this, linking two servers you already registered would mean deleting
    and re-adding one of them.
    """
    if not conns:
        return
    with st.expander("Link an existing connection to another environment"):
        by_name = {c.name: c for c in conns}
        r = st.columns([2.2, 1.6, 2.2, 1.2], vertical_alignment="bottom")
        chosen = r[0].selectbox("Connection", list(by_name), key="relink_conn")
        conn = by_name[chosen]

        kind = r[1].selectbox("Kind", list(CONNECTION_KINDS),
                              index=list(CONNECTION_KINDS).index(conn.kind),
                              format_func=lambda k: KIND_LABELS[k],
                              key="relink_kind")

        options = _env_options(store, kind) + [NEW_ENV]
        current = conn.env or conn.name
        if current not in options:
            options.insert(0, current)
        target = r[2].selectbox("Environment", options,
                                index=options.index(current), key="relink_env")
        if target == NEW_ENV:
            target = st.text_input("New environment name", key="relink_new").strip()

        if target and target != NEW_ENV:
            st.caption(_partner_hint(store, target, kind)
                       if kind != "marketdata"
                       else f"Market-data environment `{target}`.")

        if r[3].button("Link", icon=":material/link:", use_container_width=True,
                       disabled=not target or target == NEW_ENV):
            conn.kind = kind
            conn.env = target
            store.update_connection(conn)
            st.toast(f"{conn.name} → {target}", icon=":material/check:")
            st.rerun()


def render(store, mgr: ConnectionManager) -> None:
    st.subheader(":material/settings: Admin")

    conns = store.list_connections()

    # ---- Demo mode -------------------------------------------------------- #
    with st.container(border=True):
        d = st.columns([5, 1.6], vertical_alignment="center")
        d[0].markdown("**Demo KDB** — try the app with an in-memory mock, no real "
                      "connection")
        d[0].caption("Adds `orders_demo` + `orders_hdb_demo` (a linked "
                     "real-time/historical pair), `kdp_demo` (quotes) and "
                     "`refdata_demo` (market data: instruments).")
        existing = {c.name for c in conns}
        already = existing.issuperset({s.name for s in demo_connection_specs()})
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
    # Outside a form: the environment options depend on the kind you pick, and a
    # form would not rerun until submit.
    with st.container(border=True):
        f = st.columns([2, 2, 1, 1.5], vertical_alignment="bottom")
        name = f[0].text_input("Name", placeholder="e.g. order-rdb", key="ac_name")
        host = f[1].text_input("Host", value="localhost", key="ac_host")
        port = f[2].number_input("Port", 1, 65535, 5010, key="ac_port")
        kind = f[3].selectbox(
            "Kind", list(CONNECTION_KINDS), key="ac_kind",
            format_func=lambda k: KIND_LABELS[k],
            help="Real-time = today's data. Historical = the partitioned HDB "
                 "(same tables plus a date column). Market data = reference "
                 "data such as instruments, which no date range applies to.")

        g = st.columns([3, 3, 1.2], vertical_alignment="bottom")
        options = _env_options(store, kind) + [NEW_ENV]
        picked = g[0].selectbox(
            "Environment", options, key="ac_env",
            help="Real-time and historical servers in the SAME environment are "
                 "linked — a dashboard switches between them by period.")
        env = picked
        if picked == NEW_ENV:
            env = g[1].text_input("New environment name",
                                  placeholder="e.g. orders", key="ac_newenv").strip()
        elif kind != "marketdata":
            g[1].caption(_partner_hint(store, picked, kind))

        if g[2].button("Add", icon=":material/add:", use_container_width=True,
                       type="primary", key="ac_add"):
            if not name:
                st.error("Connection needs a name.", icon=":material/error:")
            elif not env:
                st.error("Connection needs an environment.", icon=":material/error:")
            else:
                _add_connection(store, name, host, port, kind, env)

    _render_relink(store, conns)

    # ---- Registered servers ---------------------------------------------- #
    st.markdown("**Registered servers**")
    if not conns:
        st.caption("None yet. Load the demo servers above or add one.")
    for c in conns:
        with st.container(border=True):
            row = st.columns([2, 2.4, 2, 1.2, 1], vertical_alignment="center")
            is_demo = c.host == "demo"
            badge = " :blue-badge[demo]" if is_demo else ""
            _colour = {"historical": "violet", "marketdata": "orange"}.get(
                c.kind, "gray")
            badge += f" :{_colour}-badge[{KIND_LABELS.get(c.kind, c.kind)}]"
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
