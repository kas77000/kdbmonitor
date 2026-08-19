# kdbmonitor/ui/reports.py
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from kdbmonitor.core.reporting import (
    build_report_model, report_to_excel_bytes, report_filename, _result_df,
)
from kdbmonitor.ui import layout as L
from kdbmonitor.ui.common import make_client_for

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def render(store, mgr) -> None:
    # Generate is the page's one commitment, so it sits where every page-level
    # action now sits: the top right of the header. It used to float mid-page
    # with 400px of empty column beside it, level with the day picker but
    # aligned to nothing.
    act = L.page_header(
        "Alert reports", icon=":material/summarize:", actions=1,
        subtitle="A shareable record of which alerts triggered on a given day. Rows "
                 "come from the stored daily snapshot; today's are re-fetched live.")
    generate = act[0].button("Generate", type="primary",
                             icon=":material/play_arrow:", use_container_width=True)

    # Two controls of their own width, left-aligned, the rest of the row left
    # empty — rather than a three-column split that stretched a toggle across a
    # third of the page and put its label on a different baseline to the date.
    c = L.toolbar(L.FIELD, L.FIELD_NARROW)
    day = c[0].date_input("Day", value=datetime.now(timezone.utc).date(),
                          format="YYYY-MM-DD")
    fetch = L.switch(c[1], "Live fallback", True,
                     help="For today with no stored snapshot yet, re-run each "
                          "triggered alert to include its current rows.")

    if not generate:
        return

    client_for = make_client_for(store, mgr) if fetch else None
    with st.spinner("Building report…"):
        model = build_report_model(store, day, client_for=client_for,
                                   now=datetime.now(timezone.utc))

    s = model["summary"]
    L.section("Summary")
    L.kpi_row([("Alerts triggered", s["alerts"], None, None),
               ("Total triggers", s["triggers"], None, None),
               ("Day", model["day"], None, None)])

    if not model["alerts"]:
        st.info(f"No alerts triggered on {model['day']}.", icon=":material/info:")
        return

    xlsx = report_to_excel_bytes(model)
    dl = L.section(f"Triggered alerts ({len(model['alerts'])})", actions=1,
                   description="One panel per alert, with the rows it captured.")
    dl[0].download_button("Excel", data=xlsx,
                          file_name=report_filename(model["day"]), mime=_XLSX_MIME,
                          icon=":material/download:")

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
