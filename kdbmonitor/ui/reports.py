# kdbmonitor/ui/reports.py
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from kdbmonitor.core.reporting import (
    build_report_model, report_to_excel_bytes, report_filename, _result_df,
)
from kdbmonitor.ui.common import make_client_for

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def render(store, mgr) -> None:
    st.subheader(":material/summarize: Alert reports")
    st.caption("A shareable record of which alerts triggered on a given day — for "
               "handing to other teams. Result rows come from the stored daily "
               "snapshot (retention & row cap set in Admin); today, with no "
               "snapshot yet, they're re-fetched live.")

    c = st.columns([1.4, 1, 2], vertical_alignment="bottom")
    day = c[0].date_input("Day", value=datetime.now(timezone.utc).date(),
                          format="YYYY-MM-DD")
    fetch = c[1].toggle("Live fallback", value=True,
                        help="For today with no stored snapshot yet, re-run each "
                             "triggered alert to include its current rows.")
    generate = c[2].button("Generate report", type="primary",
                           icon=":material/play_arrow:", use_container_width=True)

    if not generate:
        return

    client_for = make_client_for(store, mgr) if fetch else None
    with st.spinner("Building report…"):
        model = build_report_model(store, day, client_for=client_for,
                                   now=datetime.now(timezone.utc))

    s = model["summary"]
    m = st.columns(3)
    m[0].metric("Alerts triggered", s["alerts"])
    m[1].metric("Total triggers", s["triggers"])
    m[2].metric("Day", model["day"])

    if not model["alerts"]:
        st.info(f"No alerts triggered on {model['day']}.", icon=":material/info:")
        return

    xlsx = report_to_excel_bytes(model)
    st.download_button("Download Excel workbook", data=xlsx,
                       file_name=report_filename(model["day"]), mime=_XLSX_MIME,
                       icon=":material/download:", type="primary")

    st.divider()
    for a in model["alerts"]:
        title = (f"**{a['name']}** · {a['triggers']} trigger(s) · "
                 f"last {a['last_ts'][11:19]} UTC")
        with st.expander(title):
            st.caption(f"Fires when {a['condition']} · servers: "
                       f"{', '.join(a['servers']) or '—'}")
            if a["result"] and "error" in a["result"]:
                st.error(a["result"]["error"], icon=":material/error:")
            else:
                st.dataframe(_result_df(a["result"]), use_container_width=True,
                             height=240)
