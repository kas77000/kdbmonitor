# app.py
import os

import streamlit as st

from kdbmonitor.core.storage import Storage
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.ui import admin, builder, monitor, result, reports, engine

st.set_page_config(page_title="KdbMonitor", page_icon=":material/radar:", layout="wide")

DB_PATH = os.environ.get("KDBMONITOR_DB", "kdbmonitor.db")


@st.cache_resource
def get_store():
    store = Storage(DB_PATH)
    store.init_db()
    return store


@st.cache_resource
def get_manager():
    return ConnectionManager()


store = get_store()
mgr = get_manager()

with st.sidebar:
    st.title(":material/radar: KdbMonitor")
    st.caption("KDB query-chain alerting")
    n_alerts = len(store.list_alerts())
    n_conns = len(store.list_connections())
    st.markdown(f":gray[{n_alerts} alert(s) · {n_conns} server(s)]")

    # The monitoring loop runs here in the shell (not on the Monitor page) so
    # checks keep firing on every tab and after a restart. run_every is fixed at
    # definition time; toggling monitoring triggers a full rerun that redefines
    # this fragment with the right cadence.
    _mon_on = engine.monitoring_on(store)
    _tick = engine.tick_secs(store)
    st.markdown(":green-badge[:material/sensors: Monitoring on]" if _mon_on
                else ":gray-badge[:material/sensors_off: Monitoring paused]")

    @st.fragment(run_every=_tick if _mon_on else None)
    def _background_monitor():
        engine.run_tick(store, mgr)

    _background_monitor()

def monitor_page():
    monitor.render(store, mgr)


def builder_page():
    builder.render(store, mgr)


def admin_page():
    admin.render(store, mgr)


def result_page():
    result.render(store)


def reports_page():
    reports.render(store, mgr)


monitor_pg = st.Page(monitor_page, title="Monitor", url_path="monitor",
                     icon=":material/monitoring:", default=True)
builder_pg = st.Page(builder_page, title="Builder", url_path="builder",
                     icon=":material/build:")
reports_pg = st.Page(reports_page, title="Reports", url_path="reports",
                     icon=":material/summarize:")
admin_pg = st.Page(admin_page, title="Admin", url_path="admin",
                   icon=":material/settings:")
result_pg = st.Page(result_page, title="Result", url_path="result",
                    icon=":material/table_view:")

# expose pages so other views can navigate programmatically (Monitor -> Result)
st.session_state["_nav_pages"] = {"monitor": monitor_pg, "result": result_pg}

st.navigation([monitor_pg, builder_pg, reports_pg, admin_pg, result_pg]).run()
