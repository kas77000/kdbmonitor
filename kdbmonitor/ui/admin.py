# kdbmonitor/ui/admin.py
from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st

from kdbmonitor.core.models import (
    KIND_LABELS, Connection,
)
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.dataset import standalone_side
from kdbmonitor.core.schema import introspect
from kdbmonitor.core.mock import demo_connection_specs
from kdbmonitor.core.notifiers import parse_address_list, send_report_email
from kdbmonitor.core.portability import export_connections_json, import_bundle_json
from kdbmonitor.core import reportfile
from kdbmonitor.core.reportrun import ADMIN_EMAIL_KEY
from kdbmonitor.ui import layout as L


def admin_problems(text: str) -> list[str]:
    """Problems with the admin-notification list; empty when it is usable.

    Pure, so the rule can be tested without building a page. An empty list is
    fine here — the warning about having no admin while report jobs exist
    belongs beside those jobs, not on a field nobody has reached yet.
    """
    return [f"{address!r} is not an email address."
            for address in parse_address_list(text) if "@" not in address]


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
    L.section("Environments")
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
            e = L.grid(L.FIELD, L.FIELD, L.FIELD, align="center")
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
    L.section(f"Edit `{c.name}`", level=5)

    a = L.grid(L.FIELD, L.FIELD, L.FIELD_NARROW, L.FIELD_NARROW + 2)
    name = a[0].text_input("Name", value=c.name, key=f"{key}_name")
    host = a[1].text_input("Host", value=c.host, key=f"{key}_host")
    port = int(a[2].number_input("Port", 1, 65535, c.port, key=f"{key}_port"))

    kinds = list(REGISTERABLE_KINDS)
    if c.kind not in kinds:            # a legacy market-data server keeps its kind
        kinds.append(c.kind)
    kind = a[3].selectbox("Kind", kinds, index=kinds.index(c.kind),
                          format_func=lambda k: KIND_LABELS[k], key=f"{key}_kind")

    options = _env_options(store, kind)
    current = c.env or c.name
    if current not in options:            # its own env is always a valid choice
        options.insert(0, current)
    options.append(NEW_ENV)
    # The second row of the same form, on the same grid, so its fields sit
    # directly under the first row's rather than 12, 32 and 54px off them.
    b = L.grid(L.FIELD, L.FIELD, L.FIELD_NARROW * 2)
    picked = b[0].selectbox("Environment", options, index=options.index(current),
                            key=f"{key}_env",
                            help="Put a real-time and a historical server in the "
                                 "same environment to link them.")
    env = picked
    if picked == NEW_ENV:
        env = b[1].text_input("New environment name", key=f"{key}_newenv").strip()
    elif kind != "marketdata" and picked != current:
        b[1].caption(_partner_hint(store, picked, kind))

    other = "historical" if kind == "realtime" else "real-time"
    standalone = L.check(
        b[2], "No counterpart", value=c.standalone, key=f"{key}_solo",
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

    if L.grid(L.ACTION_SPAN)[0].button(
            "Save changes", icon=":material/save:", type="primary",
            key=f"{key}_save", 
            disabled=not name.strip() or not env):
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
    # A page-level action, in the page-level action zone — the same menu, with
    # the same two halves, as the Builder's and the gallery's.
    with st.popover("Share", icon=":material/import_export:",
                    use_container_width=True):
        exp, imp = L.grid(L.FIELD_WIDE, L.FIELD_WIDE, align="top", pad=False)
        with exp:
            L.section("Export", level=5)
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
            L.section("Import", level=5)
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
    conns = store.list_connections()

    head = L.page_header(
        "Admin", icon=":material/settings:", actions=1,
        subtitle="KDB connections, the environments that pair them, and the "
                 "mail settings alerts and scheduled reports send through.")
    with head[0]:
        _render_import_export(store, conns)

    # ---- Demo mode -------------------------------------------------------- #
    # The only block on the page that carried no section label at all.
    L.section("Demo KDB",
              description="Try the app against an in-memory mock, with no real "
                          "connection.")
    with L.card():
        d = L.grid(L.GRID - L.ACTION_SPAN, L.ACTION_SPAN, align="center",
                   pad=False)
        d[0].caption("Adds `orders_demo` + `orders_hdb_demo` (a linked "
                     "real-time/historical pair), `kdp_demo` (quotes) and "
                     "`refdata_demo` (market data: instruments).")
        existing = {c.name for c in conns}
        already = existing.issuperset({s.name for s in demo_connection_specs()})
        if d[1].button("Load demo", icon=":material/science:",
                       
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
    L.section("Add a KDB connection")
    # Outside a form: the environment options depend on the kind you pick, and a
    # form would not rerun until submit.
    #
    # The two rows are measured in the same units from the same origin, so their
    # fields line up. They used to be [2, 2, 1.1, 1.6] and then
    # [2.6, 2.6, 1.8, 1.2] — a form that looked like a grid and was not, with
    # its columns 12, 32 and 54px out of true down the row.
    with L.card():
        f = L.grid(L.FIELD, L.FIELD, L.FIELD_NARROW, L.FIELD_NARROW + 2)
        name = f[0].text_input("Name", placeholder="e.g. order-rdb", key="ac_name")
        host = f[1].text_input("Host", value="localhost", key="ac_host")
        port = f[2].number_input("Port", 1, 65535, 5010, key="ac_port")
        kind = f[3].selectbox(
            "Kind", list(REGISTERABLE_KINDS), key="ac_kind",
            format_func=lambda k: KIND_LABELS[k],
            help="Real-time = today's data. Historical = the partitioned HDB "
                 "(the same tables plus a date column).")

        g = L.grid(L.FIELD, L.FIELD, L.FIELD_NARROW, L.FIELD_NARROW + 2)
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
        standalone = L.check(
            g[2], "No counterpart", key="ac_solo",
            help=f"Tick when this environment will never have a {other} server — "
                 f"a date-partitioned feed with nothing live behind it, say. It "
                 f"stops being reported as half-configured, and dashboards on it "
                 f"are held to the one period it can answer.")

        if g[3].button("Add connection", icon=":material/add:", type="primary",
                       key="ac_add"):
            if not name:
                st.error("Connection needs a name.", icon=":material/error:")
            elif not env:
                st.error("Connection needs an environment.", icon=":material/error:")
            else:
                _add_connection(store, name, host, port, kind, env, standalone)

    # ---- Registered servers ---------------------------------------------- #
    L.section(f"Registered servers ({len(conns)})")
    if not conns:
        st.caption("None yet. Load the demo servers above or add one.")
    for c in conns:
        # The same row shape as the alert list and the dashboard gallery: what
        # it is, some facts about it, the one action, and a menu for the rest.
        with L.list_row(spans=L.ROW_SPANS) as row:
            is_demo = c.host == "demo"
            _colour = {"historical": "violet", "marketdata": "orange"}.get(
                c.kind, "green")
            row[0].badge(KIND_LABELS.get(c.kind, c.kind), color=_colour)
            row[1].markdown(f"**{c.name}**{' :blue-badge[demo]' if is_demo else ''}"
                            f"<br>:gray[env: {c.env or c.name}]",
                            unsafe_allow_html=True)
            if c.schema:
                row[2].markdown(f"`{c.host}:{c.port}`  \n"
                                f":green-badge[:material/table: {len(c.schema)} tables] "
                                f":gray[· {_fmt_ts(c.last_introspected_at)}]")
            else:
                row[2].markdown(f"`{c.host}:{c.port}`  \n"
                                ":orange-badge[not introspected]")
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
            with row[4]:
                with L.overflow(help=f"More actions for '{c.name}'"):
                    _render_edit(store, c)
                    st.divider()
                    st.warning(f"Delete '{c.name}'?")
                    if st.button("Confirm", key=f"del_{c.id}", type="primary",
                                 icon=":material/delete:"):
                        store.delete_connection(c.id)
                        st.rerun()

    _render_environments(store)

    # ---- SMTP ------------------------------------------------------------- #
    # Full width, like everything above it. This page used to alternate between
    # a full-width block and a narrow one section by section — measured right
    # edges of 1207px and 1596px stacked straight on top of each other.
    L.section("Email (SMTP)",
              description="Used by alerts that select the email channel, and by "
                          "scheduled reports.")
    with L.card():
        # Keyed, because this page carries two boxes labelled "Port" — a KDB
        # server's and this one — and a label is not enough to tell them apart.
        s = L.grid(L.FIELD, L.FIELD_NARROW, L.FIELD)
        host = s[0].text_input("SMTP host", key="smtp_host",
                               value=store.get_setting("smtp_host", ""))
        port = int(s[1].number_input("Port", 1, 65535,
                                     int(store.get_setting("smtp_port", "25")),
                                     key="smtp_port"))
        sender = s[2].text_input("From address", key="smtp_sender",
                                 value=store.get_setting("smtp_sender", ""))

        # Verifying a relay by waiting for tomorrow's 07:00 is not a reasonable
        # way to find out that the port is wrong. The address goes *above* the
        # button that sends to it — it used to be rendered underneath, so the
        # input for an action came after the action.
        test_to = L.field_row(1)[0].text_input(
            "Send a test email to", key="smtp_test_to",
            placeholder="you@corp.local")

        # Two of the longest labels in the app, so they get a field's width
        # rather than an action's — at 1024px an action slot is 115px and
        # 'Save SMTP settings' came out on three lines.
        b = L.grid(L.FIELD, L.FIELD)
        if b[0].button("Save SMTP settings", icon=":material/save:",
                       type="primary"):
            store.set_setting("smtp_host", host)
            store.set_setting("smtp_port", str(port))
            store.set_setting("smtp_sender", sender)
            st.toast("Saved SMTP settings", icon=":material/check:")

        if b[1].button("Send test email", icon=":material/outgoing_mail:",
                       
                       disabled=not (host and test_to)):
            try:
                send_report_email(host, port, sender,
                                  parse_address_list(test_to),
                                  "KdbMonitor test email",
                                  "If you are reading this, SMTP is configured.")
                st.success(f"Sent to {test_to}.")
            except Exception as exc:      # noqa: BLE001 - the message is the point
                st.error(f"Could not send: {exc}")

    # ---- Admin notifications --------------------------------------------- #
    L.section("Admin notifications",
              description="Where a failed scheduled report is reported. The "
                          "people expecting the report are never told it failed "
                          "— they get the report or they get nothing.")
    with L.card():
        admins_text = L.field_row(1, span=L.FIELD_WIDE)[0].text_input(
            "Admin email address(es)", key="scheduler_admin_emails",
            value=store.get_setting(ADMIN_EMAIL_KEY, "") or "",
            placeholder="kdb-support@corp.local; alice@corp.local",
            help="Comma or semicolon separated.")

        parsed = parse_address_list(admins_text)
        problems = admin_problems(admins_text)
        for problem in problems:
            st.warning(problem)
        if parsed and not problems:
            # Printed back as it will actually be used, so a stray separator is
            # visible before it costs a notice rather than after.
            st.caption(f"→ {len(parsed)} address(es): {', '.join(parsed)}")

        if L.grid(L.ACTION_SPAN)[0].button(
                "Save settings", icon=":material/save:", type="primary",
                key="admin_notify_commit", 
                disabled=bool(problems)):
            store.set_setting(ADMIN_EMAIL_KEY, admins_text)
            st.toast("Saved admin notifications", icon=":material/check:")

        if not parsed and store.list_report_jobs():
            st.warning("There are scheduled reports but no admin address — a "
                       "failed run will be silent by mail.",
                       icon=":material/warning:")

    # ---- Report archive --------------------------------------------------- #
    L.section("Report archive",
              description="Where a scheduled report leaves a copy of its PDF, "
                          "beside mailing it. A folder per dashboard is made "
                          "underneath. Empty means no job can archive.")
    with L.card():
        root = L.field_row(1, span=L.FIELD_WIDE)[0].text_input(
            "Folder for scheduled report PDFs", key="report_archive_dir",
            value=store.get_setting(reportfile.ARCHIVE_DIR_KEY, "") or "",
            placeholder=r"\\nas\reports\kdbmonitor",
            help="Must already exist. The per-dashboard folders under it are "
                 "created on the first run.")

        # Checked as it is typed rather than on Save. check_root writes and
        # removes a probe file, so this is the same question the 07:00 run
        # asks — which is the point: a share is verified here, not by waiting
        # for tomorrow morning and reading a failure notice.
        problem = reportfile.check_root(root) if root.strip() else None
        if problem:
            st.warning(problem, icon=":material/warning:")
        elif root.strip():
            example = os.path.join(root.strip(), "equity_orders",
                                   "equity_orders_20260820_070000.pdf")
            st.caption(f"→ writable · a report is filed as {example}")

        if L.grid(L.ACTION_SPAN)[0].button(
                "Save settings", icon=":material/save:", type="primary",
                key="archive_commit", disabled=bool(problem)):
            store.set_setting(reportfile.ARCHIVE_DIR_KEY, root.strip())
            st.toast("Saved report archive", icon=":material/check:")

        # The same shape as the admin-address warning above: the hole is named
        # on the page that can close it, rather than found in a failed run.
        wanting = [j.name for j in store.list_report_jobs() if j.archive]
        if wanting and not root.strip():
            st.warning(
                f"{len(wanting)} scheduled report(s) ask to be archived but no "
                f"folder is set, so those runs will fail: {', '.join(wanting)}.",
                icon=":material/warning:")

    # ---- Result snapshots (reports) -------------------------------------- #
    L.section("Result snapshots",
              description="Triggered results are stored per alert per day so "
                          "they appear in reports. Older days are pruned; large "
                          "results are capped.")
    with L.card():
        r = L.grid(L.FIELD_NARROW, L.FIELD)
        days = int(r[0].number_input("Keep days", 1, 3650,
                                     store.get_result_retention_days(),
                                     help="Snapshots older than this are deleted."))
        rows = int(r[1].number_input("Max rows per snapshot", 1, 1_000_000,
                                     store.get_result_max_rows(), step=100,
                                     help="Rows beyond this are dropped; the true "
                                          "count is still recorded and reports flag it."))
        if L.grid(L.ACTION_SPAN)[0].button(
                "Save settings", icon=":material/save:", type="primary",
                key="snapshot_commit"):
            store.set_result_retention_days(days)   # also prunes existing to the new window
            store.set_result_max_rows(rows)
            st.toast("Saved snapshot settings", icon=":material/check:")
