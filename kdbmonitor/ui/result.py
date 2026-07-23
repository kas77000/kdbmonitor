# kdbmonitor/ui/result.py
from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from kdbmonitor.core.exporting import (
    column_as_text, df_to_excel_bytes, df_to_csv, df_to_tsv,
)

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_FMT_LABELS = {"lines": "One per line", "comma": "Comma-separated", "q": "q list literal"}


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "result"


def _snapshot_from_store(store, aid: int):
    """Rebuild a result from the newest persisted daily snapshot, or None.

    The in-session result is cleared on restart; the daily snapshot in the DB
    is not, so this keeps View working (and lets it clear the NEW flag) later.
    """
    for day in store.result_days(aid):
        snap = store.get_result(aid, day)
        if snap is not None:
            df = pd.DataFrame(snap["rows"], columns=snap["columns"])
            return {"df": df, "when": snap["ts"], "mode": "snapshot",
                    "truncated": snap["truncated"], "row_count": snap["row_count"]}
    return None


def render(store) -> None:
    st.subheader(":material/table_view: Alert result")

    nav = st.session_state.get("_nav_pages", {})
    if nav.get("monitor") is not None:
        st.page_link(nav["monitor"], label="Back to monitor", icon=":material/arrow_back:")

    aid = st.session_state.get("result_alert_id")
    last_results = st.session_state.get("last_results", {})
    stored = last_results.get(aid) if aid is not None else None
    if (stored is None or stored.get("df") is None) and aid is not None:
        stored = _snapshot_from_store(store, aid)   # fall back to the DB snapshot
    if stored is None or stored.get("df") is None:
        st.info("No result yet. Open one from the Monitor with the View button that "
                "appears once an alert captures a triggered result.",
                icon=":material/monitoring:")
        return

    alert = store.get_alert(aid)
    name = alert.name if alert else f"alert {aid}"
    df = stored["df"]
    when = stored["when"]
    when_txt = (when.strftime("%H:%M:%S") if hasattr(when, "strftime") else str(when))

    st.markdown(f"**{name}**")
    cap = (f"`{len(df)}` rows · `{len(df.columns)}` cols · captured {when_txt} UTC "
           f"· {stored.get('mode', 'latest')} retention")
    if stored.get("truncated"):
        cap += f" · showing {len(df)} of {stored.get('row_count', len(df))} (capped)"
    st.caption(cap)

    st.dataframe(df, use_container_width=True, height=480)

    with st.container(horizontal=True):
        st.download_button("Excel", data=df_to_excel_bytes(df),
                           file_name=f"{_slug(name)}.xlsx", mime=_XLSX_MIME,
                           icon=":material/download:")
        st.download_button("CSV", data=df_to_csv(df), file_name=f"{_slug(name)}.csv",
                           mime="text/csv", icon=":material/download:")
        with st.popover("Copy", icon=":material/content_copy:"):
            by_col, by_table = st.tabs(["Column", "Whole table"])
            with by_col:
                c = st.columns([2, 2], vertical_alignment="bottom")
                col = c[0].selectbox("Column", list(df.columns), key="res_col")
                fmt = c[1].selectbox("Format", list(_FMT_LABELS),
                                     format_func=lambda f: _FMT_LABELS[f], key="res_fmt")
                distinct = st.checkbox("Distinct only", value=True, key="res_distinct")
                st.code(column_as_text(df[col].tolist(), fmt, distinct) or "(empty)",
                        language=None)
            with by_table:
                st.code(df_to_tsv(df), language=None)
        if st.button("Clear", icon=":material/delete:", help="Forget this result"):
            last_results.pop(aid, None)
            st.rerun()
