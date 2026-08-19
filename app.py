# app.py
import os
from datetime import datetime, timezone

import streamlit as st

from kdbmonitor.core.storage import Storage
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.scheduler import supervisor as scheduler_supervisor
from kdbmonitor.ui import (
    admin, builder, dashboards, monitor, popup, result, reports, schedules,
    engine, layout,
)

st.set_page_config(page_title="KdbMonitor", page_icon=":material/radar:", layout="wide")

# The type scale, the grid's spacing rhythm and the sidebar's ordering, applied
# once for the whole app. It has to come before anything is drawn, and before
# the pages run, so no page can render at Streamlit's default heading sizes for
# a frame. See ui/layout.py for why the scale cannot live in config.toml.
layout.inject_css()
layout.brand()

DB_PATH = os.environ.get("KDBMONITOR_DB", "kdbmonitor.db")


@st.cache_resource
def get_store():
    store = Storage(DB_PATH)
    store.init_db()
    return store


@st.cache_resource
def get_manager():
    return ConnectionManager()


@st.cache_resource
def start_scheduler():
    """Start the report scheduler if one should be running and none is.

    In a cache_resource so this happens once per Streamlit server process —
    which is exactly "when the UI launches", not on every rerun and not once
    per browser session. The spawned process is detached and outlives this
    one; the lock in the database is what stops a second copy running, so
    reaching here from several sessions is safe.
    """
    return scheduler_supervisor.ensure_running(get_store(), db_path=DB_PATH)


store = get_store()
mgr = get_manager()
start_scheduler()

with st.sidebar:
    # Identity is above the navigation now (layout.brand), so what is left down
    # here is one thing: whether the two background processes are alive. It is
    # grouped into a single panel under one label rather than left as four
    # paragraphs of loose markdown separated by Streamlit's default gap.
    _mon_on = engine.monitoring_on(store)
    _tick = engine.tick_secs(store)
    _sched = scheduler_supervisor.status(store, datetime.now(timezone.utc))

    st.markdown("##### Status")
    with st.container(border=True):
        st.markdown(":green-badge[:material/sensors: Monitoring on]" if _mon_on
                    else ":gray-badge[:material/sensors_off: Monitoring paused]")
        # The scheduler runs in a process of its own, so nothing else on screen
        # would say whether it is there. Nobody visits the Schedules page on a
        # normal morning; this badge is where its absence gets noticed.
        st.markdown(schedules.BADGE.get(_sched.state, schedules.BADGE["stopped"]))
        st.markdown(
            f'<div class="kdb-measure">{len(store.list_alerts())} alerts · '
            f'{len(store.list_connections())} servers</div>',
            unsafe_allow_html=True)

    # The monitoring loop runs here in the shell (not on the Monitor page) so
    # checks keep firing on every tab and after a restart. run_every is fixed at
    # definition time; toggling monitoring triggers a full rerun that redefines
    # this fragment with the right cadence.
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


def dashboards_page():
    dashboards.render(store, mgr)


def schedules_page():
    schedules.render(store, mgr)


# No url_path: st.navigation serves the default page at "/" and never registers
# its url_path, so declaring one promised a /monitor URL that answered
# "Page not found". The sidebar link points at "/" either way.
monitor_pg = st.Page(monitor_page, title="Monitor",
                     icon=":material/monitoring:", default=True)
builder_pg = st.Page(builder_page, title="Builder", url_path="builder",
                     icon=":material/build:")
reports_pg = st.Page(reports_page, title="Reports", url_path="reports",
                     icon=":material/summarize:")
dashboards_pg = st.Page(dashboards_page, title="Dashboards", url_path="dashboards",
                        icon=":material/dashboard:")
schedules_pg = st.Page(schedules_page, title="Schedules", url_path="schedules",
                       icon=":material/schedule_send:")
admin_pg = st.Page(admin_page, title="Admin", url_path="admin",
                   icon=":material/settings:")
result_pg = st.Page(result_page, title="Result", url_path="result",
                    icon=":material/table_view:")

# expose pages so other views can navigate programmatically (Monitor -> Result)
st.session_state["_nav_pages"] = {"monitor": monitor_pg, "result": result_pg}

# Grouped by the object each page is about, because that is the only ordering a
# user can predict: everything to do with an alert, then everything to do with a
# dashboard, then the servers both of them query. A flat list of seven put
# Dashboards between Builder and Reports — two alert pages with a dashboard page
# wedged between them — and gave Result, which is a detail view of a single
# alert, the same standing as the Monitor. Result stays registered so
# `st.switch_page` can reach it and is hidden from the list in ui/layout.py.
nav = st.navigation({
    "Alerts": [monitor_pg, builder_pg, reports_pg, result_pg],
    "Dashboards": [dashboards_pg, schedules_pg],
    "Setup": [admin_pg],
})

# "Open the full result" works from wherever it was clicked — the pop-up can be
# on any page, so the jump can't be the Monitor's business alone.
if st.session_state.pop("_open_result", False):
    st.switch_page(result_pg)

# The alert pop-up is drawn here rather than inside the monitoring fragment: a
# fragment rerun redraws only its own elements, and a dialog it owned would be
# torn down on the next tick, seconds after opening.
popup.render_pending(store)

nav.run()
