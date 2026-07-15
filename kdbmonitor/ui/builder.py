# kdbmonitor/ui/builder.py
from __future__ import annotations

import streamlit as st

from kdbmonitor.core.models import (
    Alert, Step, Filter, TriggerCondition, RearmPolicy, Channels,
)

_OPS = ["=", "<>", "<", "<=", ">", ">=", "in"]
_VALUE_TYPES = ["symbol", "number", "string"]
_COND_TYPES = ["no_rows", "has_rows", "row_count_gte", "any_row", "all_rows", "aggregate"]
_AGGS = ["max", "min", "avg", "sum"]


def _server_names(store) -> list[str]:
    return [c.name for c in store.list_connections()]


def _schema_for(store, server: str) -> dict[str, list[str]]:
    c = store.get_connection_by_name(server)
    return c.schema if c else {}


def _step_editor(store, idx: int, servers: list[str]) -> Step:
    st.markdown(f"**Step {idx + 1}**")
    server = st.selectbox("Server", servers, key=f"srv_{idx}")
    schema = _schema_for(store, server)
    tables = list(schema.keys()) or ["<introspect server first>"]
    mode = st.radio("Mode", ["form", "raw"], horizontal=True, key=f"mode_{idx}")

    filters: list[Filter] = []
    raw_qsql = None
    table = st.selectbox("Table", tables, key=f"tbl_{idx}")

    if mode == "form":
        n_filters = st.number_input("Number of filters", 0, 5, 0, key=f"nf_{idx}")
        cols = schema.get(table, [])
        for fi in range(int(n_filters)):
            c1, c2, c3, c4 = st.columns([3, 2, 3, 2])
            col = c1.selectbox("Column", cols or ["<col>"], key=f"fcol_{idx}_{fi}")
            op = c2.selectbox("Op", _OPS, key=f"fop_{idx}_{fi}")
            raw_val = c3.text_input("Value(s), comma-separated for 'in'", key=f"fval_{idx}_{fi}")
            vtype = c4.selectbox("Type", _VALUE_TYPES, key=f"ftype_{idx}_{fi}")
            value = [v.strip() for v in raw_val.split(",")] if op == "in" else raw_val
            if vtype == "number":
                value = [float(v) for v in value] if op == "in" else float(raw_val or 0)
            filters.append(Filter(column=col, op=op, value=value, value_type=vtype))
    else:
        raw_qsql = st.text_area(
            "Raw qSQL (use {{stepN.col}} to reference earlier steps)",
            key=f"raw_{idx}", height=80,
        )

    return Step(server=server, table=table, mode=mode, filters=filters,
                raw_qsql=raw_qsql, output_name=f"step{idx + 1}")


def _trigger_editor() -> TriggerCondition:
    st.markdown("**Trigger condition (on final step result)**")
    ctype = st.selectbox("Condition", _COND_TYPES, key="cond_type")
    column = op = value = agg = None
    n = None
    if ctype == "row_count_gte":
        n = int(st.number_input("N (rows >=)", 1, 100000, 1, key="cond_n"))
    if ctype in ("any_row", "all_rows", "aggregate"):
        column = st.text_input("Column", key="cond_col")
        if ctype == "aggregate":
            agg = st.selectbox("Aggregate", _AGGS, key="cond_agg")
        op = st.selectbox("Operator", ["=", "<>", "<", "<=", ">", ">="], key="cond_op")
        value = float(st.number_input("Value", value=0.0, key="cond_val"))
    return TriggerCondition(type=ctype, column=column, op=op, value=value, n=n, agg=agg)


def _channels_editor() -> Channels:
    st.markdown("**Notify via (per-alert choice)**")
    in_app = st.checkbox("In-app banner", value=True)
    sound = st.checkbox("Sound", value=True)
    email_raw = st.text_input("Email recipients (comma-separated)")
    hooks_raw = st.text_input("Teams/Slack webhook URLs (comma-separated)")
    email_to = [e.strip() for e in email_raw.split(",") if e.strip()]
    webhook_urls = [h.strip() for h in hooks_raw.split(",") if h.strip()]
    return Channels(in_app=in_app, sound=sound, email_to=email_to, webhook_urls=webhook_urls)


def _rearm_editor() -> RearmPolicy:
    mode = st.selectbox("Re-arm", ["transition", "cooldown", "every_tick"], key="rearm_mode")
    cooldown = 0
    if mode == "cooldown":
        cooldown = int(st.number_input("Cooldown (seconds)", 1, 86400, 900, key="rearm_cd"))
    return RearmPolicy(mode=mode, cooldown_secs=cooldown)


def render(store) -> None:
    st.header("Builder — Alerts")
    servers = _server_names(store)
    if not servers:
        st.info("Add a connection in Admin first.")
        return

    st.subheader("Existing alerts")
    for a in store.list_alerts():
        cols = st.columns([4, 2, 2, 2])
        cols[0].write(f"**{a.name}** — {len(a.steps)} step(s), trigger: {a.trigger.type}")
        new_enabled = cols[1].toggle("Enabled", value=a.enabled, key=f"en_{a.id}")
        if new_enabled != a.enabled:
            store.set_alert_enabled(a.id, new_enabled)
            st.rerun()
        if cols[2].button("Delete", key=f"delA_{a.id}"):
            store.delete_alert(a.id)
            st.rerun()

    st.divider()
    st.subheader("Create new alert")
    name = st.text_input("Alert name")
    n_steps = int(st.number_input("Number of steps", 1, 5, 1))
    steps = [_step_editor(store, i, servers) for i in range(n_steps)]
    trigger = _trigger_editor()
    channels = _channels_editor()
    rearm = _rearm_editor()
    interval = int(st.number_input("Poll interval (seconds)", 5, 3600, 30))

    if st.button("Save alert") and name:
        store.add_alert(Alert(id=None, name=name, enabled=True,
                              poll_interval_secs=interval, steps=steps,
                              trigger=trigger, channels=channels, rearm=rearm))
        st.success(f"Saved alert '{name}'")
        st.rerun()
