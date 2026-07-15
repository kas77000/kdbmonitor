# app.py
import streamlit as st

from kdbmonitor.core.storage import Storage
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.ui import admin, builder, monitor

st.set_page_config(page_title="KdbMonitor", layout="wide")


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

st.sidebar.title("KdbMonitor")
page = st.sidebar.radio("View", ["Monitor", "Builder", "Admin"])

if page == "Admin":
    admin.render(store, mgr)
elif page == "Builder":
    builder.render(store)
else:
    monitor.render(store, mgr)
