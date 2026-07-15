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

pages = [
    st.Page(lambda: monitor.render(store, mgr), title="Monitor",
            icon=":material/monitoring:", default=True),
    st.Page(lambda: builder.render(store), title="Builder",
            icon=":material/build:"),
    st.Page(lambda: admin.render(store, mgr), title="Admin",
            icon=":material/settings:"),
]
st.navigation(pages).run()
