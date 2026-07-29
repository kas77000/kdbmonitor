# kdbmonitor/ui/admin.py
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from kdbmonitor.core.models import (
    KIND_LABELS, Connection,
)
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.dataset import standalone_side
from kdbmonitor.core.schema import introspect
from kdbmonitor.core.mock import demo_connection_specs
from kdbmonitor.core.portability import export_connections_json, import_bundle_json
from kdbmonitor.ui.common import form_area


def _fmt_ts(ts: str | None) -> str:
    if not ts:
        return "never"
    try:
        return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ts


NEW_ENV = "＋ New environment…"

# Kinds a user can register from the Admin page. Market data is intentionally
# not offered — a KDB connection is either today's data (real-time) or the
# partitioned HDB (historical). Legacy or imported market-data servers still
# work and stay editable; there is just no way to create a new one here.
REGISTERABLE_KINDS = ("realtime", "historical")



def _add_connection(store, name: str, host: str, port, kind: str,
                    env: str, standalone: bool = False) -> None:
    try:
        store.add_connection(Connection(id=None, name=name.strip(),
                                        host=host.strip(), port=int(port),
                                        kind=kind, env=env.strip(),
                                        standalone=standalone))
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

    An environment whose server is marked as having no counterpart is not
    offered either: it was declared one-sided, and joining it would contradict
    that. Untick the box on that server first, which is the same thing said the
    other way round.
    """
    envs = store.list_environments()
    if kind == "marketdata":
        return [e for e, pair in envs.items()
                if pair["realtime"] is None and pair["historical"] is None]
    return [e for e, pair in envs.items()
            if pair["marketdata"] is None and pair[kind] is None
            and standalone_side(pair) is None]


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

            # A side that is missing on purpose is not missing. Say what the
            # environment does rather than nagging for a server nobody will add.
            solo = standalone_side(pair)
            for i, kind in enumerate(("realtime", "historical"), start=1):
                conn = pair[kind]
                label = KIND_LABELS[kind]
                if conn is not None:
                    e[i].markdown(f":green-badge[{label}] `{conn.name}`")
                elif solo:
                    e[i].markdown(f":gray[no {label.lower()} — by design]")
                else:
                    e[i].markdown(f":orange-badge[{label} missing]")

            if pair["realtime"] is not None and pair["historical"] is not None:
                st.caption(f":green[`{pair['realtime'].name}` ↔ "
                           f"`{pair['historical'].name}` are linked.]")
            elif solo:
                answers = ("date ranges" if solo == "historical" else "today")
                st.caption(f":gray[Environment '{env}' is "
                           f"{KIND_LABELS[solo].lower()} only, by design — it "
                           f"answers {answers}, and datasets on it need a period "
                           f"it can serve.]")
            elif pair["historical"] is None:
                st.caption(f":orange[Environment '{env}' has no historical server "
                           f"— dashboards on it cannot query date ranges. Tick "
                           f"*No counterpart* on `{pair['realtime'].name}` if "
                           f"there will never be one.]")
            else:
                st.caption(f":orange[Environment '{env}' has no real-time server "
                           f"— dashboards on it cannot show today. Tick *No "
                           f"counterpart* on `{pair['historical'].name}` if there "
                           f"will never be one.]")


def _render_edit(store, c: Connection) -> None:
    """Edit a registered connection in place — name, address, kind, environment.

    Relinking lives here too: changing the environment is how you pair a server
    with its real-time or historical counterpart after the fact, without having
    to delete and re-add it.
    """
    key = f"ed{c.id}"
    st.markdown(f"**Edit `{c.name}`**")

    a = st.columns([2, 2, 1], vertical_alignment="bottom")
    name = a[0].text_input("Name", value=c.name, key=f"{key}_name")
    host = a[1].text_input("Host", value=c.host, key=f"{key}_host")
    port = int(a[2].number_input("Port", 1, 65535, c.port, key=f"{key}_port"))

    kinds = list(REGISTERABLE_KINDS)
    if c.kind not in kinds:            # a legacy market-data server keeps its kind
        kinds.append(c.kind)
    kind = st.selectbox("Kind", kinds, index=kinds.index(c.kind),
                        format_func=lambda k: KIND_LABELS[k], key=f"{key}_kind")

    options = _env_options(store, kind)
    current = c.env or c.name
    if current not in options:            # its own env is always a valid choice
        options.insert(0, current)
    options.append(NEW_ENV)
    picked = st.selectbox("Environment", options, index=options.index(current),
                          key=f"{key}_env",
                          help="Put a real-time and a historical server in the "
                               "same environment to link them.")
    env = picked
    if picked == NEW_ENV:
        env = st.text_input("New environment name", key=f"{key}_newenv").strip()
    elif kind != "marketdata" and picked != current:
        st.caption(_partner_hint(store, picked, kind))

    other = "historical" if kind == "realtime" else "real-time"
    standalone = st.checkbox(
        "No counterpart", value=c.standalone, key=f"{key}_solo",
        disabled=kind == "marketdata",
        help=f"Tick when this environment will never have a {other} server. "
             f"Untick it to go looking for one: the environment becomes "
             f"available again when adding or moving a {other} server."
             if kind != "marketdata" else
             "Market data already stands alone — it has no counterpart to miss.")

    moved = (host.strip(), port) != (c.host, c.port)
    if moved and c.schema:
        st.caption(":orange[Changing the address clears the cached schema — "
                   "run Introspect afterwards.]")

    if st.button("Save changes", icon=":material/save:", type="primary",
                 key=f"{key}_save", disabled=not name.strip() or not env):
        c.name, c.host, c.port, c.kind, c.env = (
            name.strip(), host.strip(), port, kind, env)
        # A server that just gained a counterpart is not standalone whatever the
        # box said: the environment it joined has both sides now.
        c.standalone = standalone and kind != "marketdata"
        if moved:
            # The schema came from the old server; keeping it would let the
            # builder offer tables that may not exist on the new one.
            c.schema, c.last_introspected_at = {}, None
        try:
            store.update_connection(c)
        except Exception as exc:  # noqa: BLE001 — e.g. the name is already taken
            st.error(f"Could not save: {exc}", icon=":material/error:")
        else:
            st.toast(f"Saved '{c.name}'", icon=":material/check:")
            st.rerun()


def _render_import_export(store, conns: list[Connection]) -> None:
    """Move the registered servers between machines.

    Connections only. Alerts travel from the Alert builder, which bundles the
    connections they need with them — an alert without its server imports to
    nothing, whereas a server stands on its own.
    """
    with st.expander("Import / export connections",
                     icon=":material/import_export:"):
        exp, imp = st.columns(2)
        with exp:
            st.markdown("**Export**")
            st.caption("The registered servers — host, port, kind and "
                       "environment. Cached schema is left behind: it is "
                       "re-fetched with Introspect on the other machine.")
            st.download_button(
                "Export connections", icon=":material/database:",
                data=export_connections_json(conns),
                file_name="kdbmonitor-connections.json", mime="application/json",
                disabled=not conns, key="admin_io_export",
                help=f"{len(conns)} KDB connection(s), no alerts.")

        with imp:
            st.markdown("**Import**")
            # The uploader is keyed by a nonce so a completed import clears the
            # file, instead of offering to import the same one again.
            nonce = st.session_state.get("admin_io_nonce", 0)
            up = st.file_uploader("Upload an export file", type=["json"],
                                  key=f"admin_io_file_{nonce}")
            if up is None:
                return
            try:
                inc_conns, inc_alerts = import_bundle_json(
                    up.getvalue().decode("utf-8"))
            except ValueError as exc:
                st.error(str(exc), icon=":material/error:")
                return

            existing = {c.name for c in conns}
            new_conns = [c for c in inc_conns if c.name not in existing]
            skipped = len(inc_conns) - len(new_conns)
            note = f"{len(new_conns)} new connection(s)"
            if skipped:
                note += f" · {skipped} already exist (keeping yours)"
            st.caption(note)
            # Any file this app writes imports here; a full bundle just leaves
            # its alerts alone, and saying so beats silently dropping them.
            if inc_alerts:
                st.caption(f":gray[Also holds {len(inc_alerts)} alert(s) — "
                           f"import those from the Alert builder.]")
            if st.button(f"Import {len(new_conns)} connection(s)",
                         type="primary", icon=":material/upload:",
                         disabled=not new_conns, key="admin_io_import"):
                for c in new_conns:
                    store.add_connection(c)
                st.session_state["admin_io_nonce"] = nonce + 1
                st.toast(f"Imported {len(new_conns)} connection(s) — run "
                         f"Introspect to load their schema",
                         icon=":material/check:")
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
    with form_area().container(border=True):
        f = st.columns([2, 2, 1.1, 1.6], vertical_alignment="bottom")
        name = f[0].text_input("Name", placeholder="e.g. order-rdb", key="ac_name")
        host = f[1].text_input("Host", value="localhost", key="ac_host")
        port = f[2].number_input("Port", 1, 65535, 5010, key="ac_port")
        kind = f[3].selectbox(
            "Kind", list(REGISTERABLE_KINDS), key="ac_kind",
            format_func=lambda k: KIND_LABELS[k],
            help="Real-time = today's data. Historical = the partitioned HDB "
                 "(the same tables plus a date column).")

        g = st.columns([2.6, 2.6, 1.8, 1.2], vertical_alignment="bottom")
        options = _env_options(store, kind) + [NEW_ENV]
        picked = g[0].selectbox(
            "Environment", options, key="ac_env",
            help="Real-time and historical servers in the SAME environment are "
                 "linked — a dashboard switches between them by period.")
        env = picked
        if picked == NEW_ENV:
            env = g[1].text_input("New environment name",
                                  placeholder="e.g. orders", key="ac_newenv").strip()
        else:
            g[1].caption(_partner_hint(store, picked, kind))

        other = "historical" if kind == "realtime" else "real-time"
        standalone = g[2].checkbox(
            "No counterpart", key="ac_solo",
            help=f"Tick when this environment will never have a {other} server — "
                 f"a date-partitioned feed with nothing live behind it, say. It "
                 f"stops being reported as half-configured, and dashboards on it "
                 f"are held to the one period it can answer.")

        if g[3].button("Add", icon=":material/add:", type="primary", key="ac_add"):
            if not name:
                st.error("Connection needs a name.", icon=":material/error:")
            elif not env:
                st.error("Connection needs an environment.", icon=":material/error:")
            else:
                _add_connection(store, name, host, port, kind, env, standalone)

    # ---- Registered servers ---------------------------------------------- #
    st.markdown("**Registered servers**")
    if not conns:
        st.caption("None yet. Load the demo servers above or add one.")
    for c in conns:
        with st.container(border=True):
            row = st.columns([2, 1.9, 2, 1.2, 1, 0.9], vertical_alignment="center")
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
            with row[4].popover("Edit", icon=":material/edit:",
                                use_container_width=True):
                _render_edit(store, c)
            with row[5].popover("", icon=":material/delete:"):
                st.warning(f"Delete '{c.name}'?")
                if st.button("Confirm", key=f"del_{c.id}", type="primary"):
                    store.delete_connection(c.id)
                    st.rerun()

    _render_environments(store)

    # ---- Import / export -------------------------------------------------- #
    _render_import_export(store, conns)

    # ---- SMTP ------------------------------------------------------------- #
    st.markdown("**Email (SMTP)**")
    with form_area().container(border=True):
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
    with form_area().container(border=True):
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
