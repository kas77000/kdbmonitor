# app.py
import streamlit as st

from kdbmonitor.core.storage import Storage
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.ui import admin, builder, monitor

st.set_page_config(page_title="KdbMonitor", page_icon=":material/radar:", layout="wide")


@st.cache_resource
def get_store():
    store = Storage("kdbmonitor.db")
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

def monitor_page():
    monitor.render(store, mgr)


def builder_page():
    builder.render(store)


def admin_page():
    admin.render(store, mgr)


pages = [
    st.Page(monitor_page, title="Monitor", url_path="monitor",
            icon=":material/monitoring:", default=True),
    st.Page(builder_page, title="Builder", url_path="builder",
            icon=":material/build:"),
    st.Page(admin_page, title="Admin", url_path="admin",
            icon=":material/settings:"),
]
st.navigation(pages).run()
