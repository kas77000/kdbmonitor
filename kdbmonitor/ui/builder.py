# kdbmonitor/ui/builder.py
from __future__ import annotations

from datetime import datetime

import streamlit as st

from kdbmonitor.core.models import (
    Alert, Step, Filter, TriggerCondition, RearmPolicy, Channels,
)
from kdbmonitor.core.chain import build_step_qsql, preview_chain
from kdbmonitor.core.conditions import evaluate as eval_condition
from kdbmonitor.core.portability import (
    export_bundle_json, import_bundle_json, conflicting_alert_names,
)
from kdbmonitor.ui.common import (
    STATUS_META, INTERVAL_PRESETS, condition_summary, humanize_secs, make_client_for,
)

_OPS = ["=", "<>", "<", "<=", ">", ">=", "in", "like"]
_CMP_OPS = ["=", "<>", "<", "<=", ">", ">="]
_VALUE_TYPES = ["symbol", "number", "string"]
_COND_LABELS = {
    "no_rows": "No rows returned",
    "has_rows": "Has at least one row",
    "row_count_gte": "Row count is at least N",
    "any_row": "At least one row matches",
    "all_rows": "Every row matches",
    "aggregate": "Aggregate matches",
}
_AGGS = ["max", "min", "avg", "sum"]


def _safe_float(s, default: float = 0.0) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def _clear_builder() -> None:
    for k in list(st.session_state.keys()):
        if k.startswith("b_"):
            del st.session_state[k]


def _safe_select(container, label, options, key, **kw):
    """selectbox that won't crash when a persisted value leaves the option set."""
    if key in st.session_state and st.session_state[key] not in options:
        del st.session_state[key]
    return container.selectbox(label, options, key=key, **kw)


def _servers(store) -> list[str]:
    return [c.name for c in store.list_connections()]


def _schema_for(store, server: str) -> dict[str, list[str]]:
    c = store.get_connection_by_name(server)
    return c.schema if c else {}


# --------------------------------------------------------------------------- #
# builder state init / edit-load
# --------------------------------------------------------------------------- #
def _ensure_init(store, servers: list[str]) -> None:
    if st.session_state.get("b_initialized"):
        return
    st.session_state.update({
        "b_initialized": True, "b_edit_id": None, "b_edit_enabled": True,
        "b_name": "", "b_interval": 30, "b_nsteps": 1, "b_nf_0": 0,
        "b_srv_0": servers[0], "b_mode_0": "Guided", "b_raw_0": "",
        "b_ctype": "has_rows", "b_ccol": "", "b_cop": ">", "b_cvtype": "number",
        "b_cval": "", "b_cagg": "max", "b_cn": 1,
        "b_inapp": True, "b_sound": True, "b_email": "", "b_hooks": "",
        "b_rmode": "transition", "b_rcd": 900,
    })


def _load_edit(alert: Alert) -> None:
    _clear_builder()
    s = {
        "b_initialized": True, "b_edit_id": alert.id, "b_edit_enabled": alert.enabled,
        "b_name": alert.name, "b_interval": alert.poll_interval_secs,
        "b_nsteps": len(alert.steps),
        "b_ctype": alert.trigger.type, "b_ccol": alert.trigger.column or "",
        "b_cop": alert.trigger.op or ">", "b_cvtype": alert.trigger.value_type,
        "b_cval": "" if alert.trigger.value is None else str(alert.trigger.value),
        "b_cagg": alert.trigger.agg or "max", "b_cn": alert.trigger.n or 1,
        "b_inapp": alert.channels.in_app, "b_sound": alert.channels.sound,
        "b_email": ", ".join(alert.channels.email_to),
        "b_hooks": ", ".join(alert.channels.webhook_urls),
        "b_rmode": alert.rearm.mode, "b_rcd": alert.rearm.cooldown_secs or 900,
    }
    for i, step in enumerate(alert.steps):
        s[f"b_srv_{i}"] = step.server
        s[f"b_tbl_{i}"] = step.table
        s[f"b_mode_{i}"] = "Raw" if step.mode == "raw" else "Guided"
        s[f"b_raw_{i}"] = step.raw_qsql or ""
        s[f"b_nf_{i}"] = len(step.filters)
        for j, f in enumerate(step.filters):
            s[f"b_fnot_{i}_{j}"] = f.negated
            s[f"b_fcol_{i}_{j}"] = f.column
            s[f"b_fop_{i}_{j}"] = f.op
            s[f"b_fval_{i}_{j}"] = (", ".join(map(str, f.value))
                                    if f.op == "in" else str(f.value))
            s[f"b_ftype_{i}_{j}"] = f.value_type
    st.session_state.update(s)


# --------------------------------------------------------------------------- #
# step / trigger / channel widgets  (return live model objects)
# --------------------------------------------------------------------------- #
def _step_block(store, i: int, servers: list[str]) -> Step:
    with st.container(border=True):
        head = st.columns([6, 1], vertical_alignment="center")
        head[0].markdown(f"**Step {i + 1}**")
        if st.session_state["b_nsteps"] > 1:
            if head[1].button("Remove", key=f"b_rmstep_{i}", icon=":material/close:"):
                _remove_step(i)
                st.rerun()

        top = st.columns([2, 3, 2], vertical_alignment="bottom")
        server = _safe_select(top[0], "Server", servers, key=f"b_srv_{i}")
        schema = _schema_for(store, server)
        tables = list(schema.keys())
        mode = top[2].segmented_control("Mode", ["Guided", "Raw"], key=f"b_mode_{i}")
        mode = mode or "Guided"

        filters: list[Filter] = []
        raw_qsql = None
        if not tables:
            st.warning(f"'{server}' has no introspected tables. Introspect it in Admin, "
                       f"or use Raw mode.", icon=":material/info:")

        table = _safe_select(top[1], "Table", tables or ["(none)"], key=f"b_tbl_{i}")

        if mode == "Guided":
            cols = schema.get(table, [])
            nf = int(st.session_state.get(f"b_nf_{i}", 0))
            for j in range(nf):
                fc = st.columns([0.8, 3, 2, 3, 2, 1], vertical_alignment="bottom")
                negated = fc[0].checkbox("Not", key=f"b_fnot_{i}_{j}",
                                         help="Prefix this condition with q 'not'.")
                col = _safe_select(fc[1], "Column", cols or ["<col>"], key=f"b_fcol_{i}_{j}")
                op = fc[2].selectbox("Op", _OPS, key=f"b_fop_{i}_{j}")
                raw_val = fc[3].text_input(
                    "Value(s)", key=f"b_fval_{i}_{j}",
                    help="Comma-separated for 'in'. Use q like patterns such as "
                         "A* or *USD* for 'like'.")
                if op == "like":
                    vtype = "string"
                    fc[4].text_input("Type", value="string", disabled=True,
                                     key=f"b_ftype_display_{i}_{j}",
                                     help="The right-hand side of q 'like' is a string pattern.")
                else:
                    vtype = fc[4].selectbox("Type", _VALUE_TYPES, key=f"b_ftype_{i}_{j}")
                if fc[5].button("", key=f"b_rmf_{i}_{j}", icon=":material/delete:",
                                help="Remove filter"):
                    _remove_filter(i, j)
                    st.rerun()
                value = ([v.strip() for v in raw_val.split(",")] if op == "in" else raw_val)
                if vtype == "number":
                    value = ([_safe_float(v) for v in value] if op == "in"
                             else _safe_float(raw_val))
                filters.append(Filter(column=col, op=op, value=value,
                                      value_type=vtype, negated=negated))
            if st.button("Add filter", key=f"b_addf_{i}", icon=":material/add:"):
                st.session_state[f"b_nf_{i}"] = nf + 1
                st.rerun()
        else:
            _raw_ref_helper(i)
            raw_qsql = st.text_area(
                "Raw qSQL", key=f"b_raw_{i}", height=90,
                help="Use {{stepN.col}} to inject distinct values from an earlier step.",
            )

        step = Step(server=server, table=table if tables else "", mode=
                    "raw" if mode == "Raw" else "form", filters=filters,
                    raw_qsql=raw_qsql, output_name=f"step{i + 1}")
        st.caption("Query preview")
        st.code(build_step_qsql(step) or "(empty)", language="sql")
        return step


def _raw_ref_helper(i: int) -> None:
    if i == 0:
        return
    refs = [f"{{{{step{k + 1}.col}}}}" for k in range(i)]
    rc = st.columns([4, 1], vertical_alignment="bottom")
    token = rc[0].selectbox("Insert reference", refs, key=f"b_refsel_{i}")
    if rc[1].button("Insert", key=f"b_refins_{i}", icon=":material/add:"):
        st.session_state[f"b_raw_{i}"] = (st.session_state.get(f"b_raw_{i}", "") + " " + token).strip()
        st.rerun()


def _trigger_block() -> TriggerCondition:
    with st.container(border=True):
        st.markdown("**Trigger** — fire the alert when the final result matches")
        ctype = st.selectbox("Condition", list(_COND_LABELS.keys()),
                             format_func=lambda k: _COND_LABELS[k], key="b_ctype")
        column = op = value = agg = None
        n = None
        vtype = "number"
        if ctype == "row_count_gte":
            n = int(st.number_input("N (minimum rows)", 1, 1_000_000, key="b_cn"))
        elif ctype in ("any_row", "all_rows", "aggregate"):
            wc = st.columns([3, 2, 3] if ctype != "aggregate" else [2, 2, 2, 3],
                            vertical_alignment="bottom")
            if ctype == "aggregate":
                agg = wc[0].selectbox("Aggregate", _AGGS, key="b_cagg")
                column = wc[1].text_input("Column", key="b_ccol")
                op = wc[2].selectbox("Op", _CMP_OPS, key="b_cop")
                value = _safe_float(wc[3].text_input("Value", key="b_cval"))
                vtype = "number"
            else:
                column = wc[0].text_input("Column", key="b_ccol")
                op = wc[1].selectbox("Op", _CMP_OPS, key="b_cop")
                value = wc[2].text_input("Value", key="b_cval")
                vtype = st.selectbox("Value type", _VALUE_TYPES, key="b_cvtype")
                if vtype == "number":
                    value = _safe_float(value)
        trig = TriggerCondition(type=ctype, column=column, op=op, value=value,
                                n=n, agg=agg, value_type=vtype)
        st.markdown(f":blue-badge[:material/bolt: Triggers when {condition_summary(trig)}]")
        return trig


def _notify_block() -> tuple[Channels, RearmPolicy]:
    with st.container(border=True):
        st.markdown("**Notify** — chosen per alert")
        cc = st.columns(2)
        in_app = cc[0].checkbox("In-app banner", key="b_inapp")
        sound = cc[1].checkbox("Sound", key="b_sound")
        email_raw = st.text_input("Email recipients", key="b_email",
                                  help="Comma-separated. Needs SMTP set in Admin.")
        hooks_raw = st.text_input("Teams / Slack webhook URLs", key="b_hooks",
                                  help="Comma-separated incoming webhook URLs.")
        rc = st.columns([2, 2], vertical_alignment="bottom")
        mode = rc[0].selectbox("Re-arm", ["transition", "cooldown", "every_tick"],
                               key="b_rmode",
                               help="transition: notify once per rising edge. "
                                    "cooldown: at most every N seconds. "
                                    "every_tick: every check while triggered.")
        cooldown = 0
        if mode == "cooldown":
            cooldown = int(rc[1].number_input("Cooldown (seconds)", 1, 86400, key="b_rcd"))
        channels = Channels(
            in_app=in_app, sound=sound,
            email_to=[e.strip() for e in email_raw.split(",") if e.strip()],
            webhook_urls=[h.strip() for h in hooks_raw.split(",") if h.strip()],
        )
        return channels, RearmPolicy(mode=mode, cooldown_secs=cooldown)


# --------------------------------------------------------------------------- #
# structural mutations
# --------------------------------------------------------------------------- #
def _remove_step(i: int) -> None:
    n = st.session_state["b_nsteps"]
    # shift keys of steps after i down by one
    for k in range(i, n - 1):
        _copy_step_state(k + 1, k)
    _delete_step_state(n - 1)
    st.session_state["b_nsteps"] = n - 1


def _remove_filter(i: int, j: int) -> None:
    nf = int(st.session_state.get(f"b_nf_{i}", 0))
    for k in range(j, nf - 1):
        for p in ("fnot", "fcol", "fop", "fval", "ftype"):
            src, dst = f"b_{p}_{i}_{k + 1}", f"b_{p}_{i}_{k}"
            if src in st.session_state:
                st.session_state[dst] = st.session_state[src]
    for p in ("fnot", "fcol", "fop", "fval", "ftype"):
        st.session_state.pop(f"b_{p}_{i}_{nf - 1}", None)
    st.session_state[f"b_nf_{i}"] = max(0, nf - 1)


def _copy_step_state(src: int, dst: int) -> None:
    for p in ("srv", "tbl", "mode", "raw", "nf"):
        if f"b_{p}_{src}" in st.session_state:
            st.session_state[f"b_{p}_{dst}"] = st.session_state[f"b_{p}_{src}"]
    nf = int(st.session_state.get(f"b_nf_{src}", 0))
    for j in range(nf):
        for p in ("fnot", "fcol", "fop", "fval", "ftype"):
            if f"b_{p}_{src}_{j}" in st.session_state:
                st.session_state[f"b_{p}_{dst}_{j}"] = st.session_state[f"b_{p}_{src}_{j}"]
    # drop any surplus filter keys the previous occupant of `dst` left behind
    j = nf
    while any(f"b_{p}_{dst}_{j}" in st.session_state for p in ("fnot", "fcol", "fop", "fval", "ftype")):
        for p in ("fnot", "fcol", "fop", "fval", "ftype"):
            st.session_state.pop(f"b_{p}_{dst}_{j}", None)
        j += 1


def _delete_step_state(i: int) -> None:
    for k in list(st.session_state.keys()):
        if k.startswith(f"b_srv_{i}") or k.startswith(f"b_tbl_{i}") \
           or k.startswith(f"b_mode_{i}") or k.startswith(f"b_raw_{i}") \
           or k.startswith(f"b_nf_{i}") or k.startswith(f"b_f") and f"_{i}_" in k:
            st.session_state.pop(k, None)


# --------------------------------------------------------------------------- #
# existing-alert management
# --------------------------------------------------------------------------- #
def _manage_alerts(store) -> None:
    alerts = store.list_alerts()
    with st.expander(f"Your alerts ({len(alerts)})", expanded=bool(alerts),
                     icon=":material/list:"):
        if not alerts:
            st.caption("No alerts yet. Build one below.")
        for a in alerts:
            with st.container(border=True):
                c = st.columns([1.3, 4, 1.1, 1, 1.2], vertical_alignment="center")
                lbl, color, icon = (STATUS_META["armed"] if a.enabled
                                    else STATUS_META["disabled"])
                c[0].badge("Enabled" if a.enabled else "Disabled",
                           icon=icon, color=color)
                c[1].markdown(
                    f"**{a.name}**  \n:gray[{len(a.steps)} step(s) · every "
                    f"{humanize_secs(a.poll_interval_secs)} · triggers when "
                    f"{condition_summary(a.trigger)}]")
                new_en = c[2].toggle("On", value=a.enabled, key=f"mg_en_{a.id}")
                if new_en != a.enabled:
                    store.set_alert_enabled(a.id, new_en)
                    st.rerun()
                if c[3].button("Edit", key=f"mg_edit_{a.id}", icon=":material/edit:"):
                    _load_edit(a)
                    st.rerun()
                with c[4].popover("Delete", icon=":material/delete:"):
                    st.warning(f"Delete '{a.name}'?")
                    if st.button("Confirm delete", key=f"mg_del_{a.id}",
                                 type="primary"):
                        store.delete_alert(a.id)
                        st.rerun()


def _import_export(store) -> None:
    alerts = store.list_alerts()
    conns = store.list_connections()
    with st.expander("Import / export (alerts & connections)",
                     icon=":material/import_export:"):
        exp, imp = st.columns(2)
        with exp:
            st.markdown("**Export**")
            if not alerts:
                st.caption("No alerts to export yet.")
            else:
                names = [a.name for a in alerts]
                chosen = st.multiselect("Alerts to export", names, default=names)
                selected = [a for a in alerts if a.name in chosen]
                st.caption(f"Includes all {len(conns)} connection(s) and "
                           f"{len(selected)} alert(s).")
                st.download_button(
                    "Download JSON", icon=":material/download:",
                    data=export_bundle_json(conns, selected),
                    file_name="kdbmonitor-export.json", mime="application/json",
                    disabled=not selected)
        with imp:
            st.markdown("**Import**")
            nonce = st.session_state.get("io_import_nonce", 0)
            up = st.file_uploader("Upload an export file", type=["json"],
                                  key=f"io_import_file_{nonce}")
            if up is not None:
                try:
                    inc_conns, inc_alerts = import_bundle_json(
                        up.getvalue().decode("utf-8"))
                except ValueError as exc:
                    st.error(str(exc), icon=":material/error:")
                    return
                conflicts = conflicting_alert_names({a.name for a in alerts}, inc_alerts)
                if conflicts:
                    st.error(
                        "Import aborted — you already have alert(s) named: "
                        + ", ".join(conflicts)
                        + ". Rename or delete them first.",
                        icon=":material/error:")
                    return
                existing_conn_names = {c.name for c in conns}
                new_conns = [c for c in inc_conns if c.name not in existing_conn_names]
                skipped = len(inc_conns) - len(new_conns)
                preview = ", ".join(a.name for a in inc_alerts[:8])
                if len(inc_alerts) > 8:
                    preview += " …"
                st.caption(f"{len(inc_alerts)} alert(s): {preview}")
                note = f"{len(new_conns)} new connection(s)"
                if skipped:
                    note += f" · {skipped} already exist (keeping yours)"
                if new_conns:
                    note += " · run Introspect in Admin to load their schema"
                st.caption(note)
                if st.button(f"Import {len(inc_alerts)} alert(s)", type="primary",
                             icon=":material/upload:", key="io_import_btn"):
                    for c in new_conns:
                        store.add_connection(c)
                    for a in inc_alerts:
                        store.add_alert(a)
                    st.session_state["io_import_nonce"] = nonce + 1
                    st.toast(f"Imported {len(inc_alerts)} alert(s), "
                             f"{len(new_conns)} connection(s)", icon=":material/check:")
                    st.rerun()


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def _run_preview(store, mgr, steps: list[Step], trigger: TriggerCondition) -> None:
    alert = Alert(id=None, name="(preview)", enabled=True, poll_interval_secs=30,
                  steps=steps, trigger=trigger, channels=Channels(), rearm=RearmPolicy())
    results = preview_chain(alert, make_client_for(store, mgr))
    step_states = [{"index": r.index, "server": r.server, "qsql": r.qsql,
                    "error": r.error, "df": r.df,
                    "rows": None if r.df is None else len(r.df)}
                   for r in results]

    final = results[-1] if results else None
    triggered = None
    final_rows = None
    if final is not None and final.error is None:
        final_rows = len(final.df)
        try:
            triggered = bool(eval_condition(trigger, final.df))
        except Exception as exc:  # noqa: BLE001 - surface condition errors inline
            step_states.append({"index": len(results), "server": "", "qsql": "",
                                "error": f"condition error: {exc}", "df": None, "rows": None})

    st.session_state["b_preview"] = {
        "steps": step_states, "triggered": triggered,
        "summary": condition_summary(trigger), "final_rows": final_rows,
        "when": datetime.now().strftime("%H:%M:%S"),
    }


def _render_preview() -> None:
    p = st.session_state["b_preview"]
    v = st.columns([5, 1], vertical_alignment="center")
    v[0].caption(f":material/schedule: snapshot at {p['when']}")
    if v[1].button("Clear", key="b_preview_clear"):
        del st.session_state["b_preview"]
        st.rerun()

    if p["triggered"] is True:
        st.error(f"Would TRIGGER now — {p['summary']} · {p['final_rows']} row(s)",
                 icon=":material/notifications_active:")
    elif p["triggered"] is False:
        st.success(f"Would not trigger — condition not met ({p['summary']})",
                   icon=":material/check_circle:")

    for s in p["steps"]:
        title = f"Step {s['index'] + 1} · {s['server']}" if s["server"] else "Condition"
        if s["rows"] is not None:
            title += f" · {s['rows']} row(s)"
        with st.expander(title, expanded=s["error"] is not None):
            if s["qsql"]:
                st.code(s["qsql"], language="sql")
            if s["error"]:
                st.error(s["error"], icon=":material/error:")
            elif s["df"] is not None:
                st.dataframe(s["df"], use_container_width=True, hide_index=True)


def _preview_panel(store, mgr, steps: list[Step], trigger: TriggerCondition) -> None:
    with st.container(border=True):
        head = st.columns([5, 1.4], vertical_alignment="center")
        head[0].markdown("**Check result** — run these queries now against the live "
                         "data (nothing is saved, no notification sent)")
        if head[1].button("Run now", icon=":material/play_arrow:", key="b_preview_run"):
            _run_preview(store, mgr, steps, trigger)
        if "b_preview" in st.session_state:
            _render_preview()


def render(store, mgr) -> None:
    st.subheader(":material/build: Alert builder")
    _manage_alerts(store)
    _import_export(store)

    servers = _servers(store)
    if not servers:
        st.info("Add a KDB connection in Admin first to build new alerts.",
                icon=":material/database:")
        return
    _ensure_init(store, servers)

    editing = st.session_state.get("b_edit_id") is not None
    head = st.columns([5, 1.4], vertical_alignment="center")
    head[0].markdown(f"### {'Edit alert' if editing else 'New alert'}")
    if editing and head[1].button("New alert", icon=":material/add:"):
        _clear_builder()
        st.rerun()

    top = st.columns([3, 2], vertical_alignment="bottom")
    name = top[0].text_input("Alert name", key="b_name", placeholder="e.g. AAPL bid breakout")
    interval = int(top[1].number_input(
        "Check interval (seconds)", 5, 3600, key="b_interval",
        help="How often this alert runs. Presets: "
             + ", ".join(f"{k}={v}s" for k, v in INTERVAL_PRESETS.items())))
    st.caption(f":material/schedule: Runs every {humanize_secs(interval)} while monitoring is on.")

    nsteps = int(st.session_state["b_nsteps"])
    steps = [_step_block(store, i, servers) for i in range(nsteps)]
    sc = st.columns([1, 5])
    if nsteps < 8 and sc[0].button("Add step", icon=":material/add:"):
        st.session_state[f"b_nf_{nsteps}"] = 0
        st.session_state["b_nsteps"] = nsteps + 1
        st.rerun()

    trigger = _trigger_block()
    channels, rearm = _notify_block()
    _preview_panel(store, mgr, steps, trigger)

    # Save
    errors = []
    if not name.strip():
        errors.append("Alert needs a name.")
    for idx, s in enumerate(steps):
        if s.mode == "raw" and not (s.raw_qsql or "").strip():
            errors.append(f"Step {idx + 1}: raw qSQL is empty.")
        if s.mode == "form" and not s.table:
            errors.append(f"Step {idx + 1}: no table selected (introspect the server).")

    save = st.button("Save alert" if not editing else "Update alert",
                     type="primary", icon=":material/save:")
    if save:
        if errors:
            for e in errors:
                st.error(e, icon=":material/error:")
        else:
            alert = Alert(id=st.session_state.get("b_edit_id"), name=name.strip(),
                          enabled=st.session_state.get("b_edit_enabled", True),
                          poll_interval_secs=interval, steps=steps, trigger=trigger,
                          channels=channels, rearm=rearm)
            if editing:
                store.update_alert(alert)
                st.toast(f"Updated '{name}'", icon=":material/check:")
            else:
                store.add_alert(alert)
                st.toast(f"Saved '{name}'", icon=":material/check:")
            _clear_builder()
            st.rerun()
