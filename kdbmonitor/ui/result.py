# kdbmonitor/ui/result.py
from __future__ import annotations

import re

import streamlit as st

from kdbmonitor.core.exporting import (
    column_as_text, df_to_excel_bytes, df_to_csv, df_to_tsv,
)

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_FMT_LABELS = {"lines": "One per line", "comma": "Comma-separated", "q": "q list literal"}


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "result"


def render(store) -> None:
    st.subheader(":material/table_view: Alert result")

    nav = st.session_state.get("_nav_pages", {})
    if nav.get("monitor") is not None:
        if st.button("Back to monitor", icon=":material/arrow_back:"):
            st.switch_page(nav["monitor"])

    aid = st.session_state.get("result_alert_id")
    last_results = st.session_state.get("last_results", {})
    stored = last_results.get(aid) if aid is not None else None
    if stored is None or stored.get("df") is None:
        st.info("No result to show yet. Open one from the Monitor — the **View** "
                "button appears on an alert once it has captured a triggered result.",
                icon=":material/monitoring:")
        return

    alert = store.get_alert(aid)
    name = alert.name if alert else f"alert {aid}"
    df = stored["df"]
    when = stored["when"]
    when_txt = (when.strftime("%Y-%m-%d %H:%M:%S") if hasattr(when, "strftime")
                else str(when))

    top = st.columns([5, 1], vertical_alignment="center")
    top[0].markdown(f"### {name}")
    if top[1].button("Clear", icon=":material/delete:", help="Forget this captured result"):
        last_results.pop(aid, None)
        st.rerun()
    st.caption(f"{len(df)} row(s) × {len(df.columns)} column(s) · captured "
               f"{when_txt} UTC · {stored.get('mode', 'latest')} retention")

    # Full table: st.dataframe gives search, column sort, CSV toolbar, and fullscreen
    st.dataframe(df, use_container_width=True, height=520)

    # Exports
    st.markdown("**Export**")
    ex = st.columns(2)
    ex[0].download_button("Export to Excel (.xlsx)", icon=":material/download:",
                          data=df_to_excel_bytes(df), file_name=f"{_slug(name)}.xlsx",
                          mime=_XLSX_MIME, use_container_width=True)
    ex[1].download_button("Export to CSV", icon=":material/download:",
                          data=df_to_csv(df), file_name=f"{_slug(name)}.csv",
                          mime="text/csv", use_container_width=True)

    with st.expander("Copy the whole table (TSV)", icon=":material/content_copy:"):
        st.caption("Use the copy button on the top-right of the box.")
        st.code(df_to_tsv(df), language=None)

    # Copy a single column, in a chosen format
    st.markdown("**Copy a column**")
    cc = st.columns([2, 2, 1], vertical_alignment="bottom")
    col = cc[0].selectbox("Column", list(df.columns), key="res_col")
    fmt = cc[1].selectbox("Format", list(_FMT_LABELS.keys()),
                          format_func=lambda f: _FMT_LABELS[f], key="res_fmt")
    distinct = cc[2].checkbox("Distinct", value=True, key="res_distinct")
    st.code(column_as_text(df[col].tolist(), fmt, distinct) or "(empty)", language=None)
