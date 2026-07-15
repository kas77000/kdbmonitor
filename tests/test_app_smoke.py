"""AppTest smoke tests: actually execute the Streamlit script for each page.

These run the app logic in-process (unlike an HTTP boot check, which only
confirms the server starts). They exercise every page against the demo mock,
so no real KDB is needed.
"""
import os

from streamlit.testing.v1 import AppTest

from kdbmonitor.core.storage import Storage
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.mock import demo_connection_specs
from kdbmonitor.core.models import (
    Alert, Step, Filter, TriggerCondition, RearmPolicy, Channels,
)

APP = os.path.join(os.path.dirname(__file__), "..", "app.py")


def test_app_file_runs_without_exception():
    # Executes app.py incl. st.navigation() — regression guard for the
    # lambda url_path collision that broke startup.
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception


def _demo_store():
    store = Storage(":memory:")
    store.init_db()
    for spec in demo_connection_specs():
        store.add_connection(spec)
    return store


def _admin_script():
    from kdbmonitor.ui import admin
    from kdbmonitor.core.storage import Storage as _S
    from kdbmonitor.core.client import ConnectionManager as _CM
    from kdbmonitor.core.mock import demo_connection_specs as _specs
    store = _S(":memory:")
    store.init_db()
    for spec in _specs():
        store.add_connection(spec)
    admin.render(store, _CM())


def _builder_script():
    from kdbmonitor.ui import builder
    from kdbmonitor.core.storage import Storage as _S
    from kdbmonitor.core.mock import demo_connection_specs as _specs
    store = _S(":memory:")
    store.init_db()
    for spec in _specs():
        store.add_connection(spec)
    builder.render(store)


def _monitor_script():
    from kdbmonitor.ui import monitor
    from kdbmonitor.core.storage import Storage as _S
    from kdbmonitor.core.client import ConnectionManager as _CM
    from kdbmonitor.core.mock import demo_connection_specs as _specs
    from kdbmonitor.core.models import (
        Alert as _A, Step as _St, Filter as _F, TriggerCondition as _T,
        RearmPolicy as _R, Channels as _C,
    )
    store = _S(":memory:")
    store.init_db()
    for spec in _specs():
        store.add_connection(spec)
    store.add_alert(_A(
        id=None, name="demo bid", enabled=True, poll_interval_secs=30,
        steps=[_St(server="kdp_demo", table="QATT", mode="form",
                   filters=[_F("sym", "in", ["AAPL"], "symbol")], output_name="step1")],
        trigger=_T(type="has_rows"), channels=_C(), rearm=_R(),
    ))
    monitor.render(store, _CM())


def test_admin_page_renders_with_demo():
    at = AppTest.from_function(_admin_script, default_timeout=30).run()
    assert not at.exception


def test_builder_page_renders_with_demo():
    at = AppTest.from_function(_builder_script, default_timeout=30).run()
    assert not at.exception


def test_monitor_page_renders_and_evaluates_demo_alert():
    at = AppTest.from_function(_monitor_script, default_timeout=30).run()
    assert not at.exception
