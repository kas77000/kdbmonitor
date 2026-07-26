"""Render the editor for real with Streamlit's AppTest.

Unit tests over the pure helpers cannot catch duplicate widget keys or bad
argument values — those only surface when Streamlit actually builds the page.
This exercises every widget type and several rows, which is what shook out the
`r2w0_x` collision between a widget's remove button and its X-axis picker.
"""
import pytest

from streamlit.testing.v1 import AppTest

from kdbmonitor.core.dashboard_models import Dashboard, Dataset, Row, Transform, Widget
from kdbmonitor.core.models import Connection, Filter
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui.dashboard_editor import WIDGET_TYPES

SCHEMA = {"target": ["sym", "size", "side", "algo", "price", "hour"]}


def _dashboard_with_every_widget_type() -> Dashboard:
    spec_for = {
        "kpi": {"column": "size", "agg": "sum", "fmt": ",.0f"},
        "table": {"columns": ["sym", "size"]},
        "text": {"markdown": "{{d.sum.size}} shares"},
        "bar": {"x": "algo", "y": "size", "orientation": "h"},
        "line": {"x": "hour", "y": "size"},
        "scatter": {"x": "price", "y": "size"},
        "hist": {"x": "size", "bins": 20},
        "box": {"x": "algo", "y": "size"},
        "heatmap": {"rows": "algo", "cols": "hour", "value": "size", "agg": "sum"},
        "pie": {"by": "algo", "value": "size", "donut": True},
    }
    # Several rows, several widgets each — the layout that triggered the clash.
    rows, batch = [], []
    for t in WIDGET_TYPES:
        batch.append(Widget(type=t, dataset="d", title=t.title(), spec=spec_for[t]))
        if len(batch) == 2:
            rows.append(Row(widgets=batch, height_in=2.0))
            batch = []
    if batch:
        rows.append(Row(widgets=batch, height_in=2.0))

    return Dashboard(
        id=1, name="Every widget", description="render check",
        datasets=[Dataset(
            name="d", env="orders", table="target",
            filters=[Filter(column="side", op="=", value="sellshort",
                            value_type="symbol")],
            transforms=[
                Transform(kind="derive", params={"column": "notional",
                                                 "kind": "arithmetic",
                                                 "expr": "size * price"}),
                Transform(kind="sort", params={"columns": ["size"],
                                               "ascending": False}),
            ])],
        rows=rows)


def _script(db_path: str) -> str:
    return f'''
import streamlit as st
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import dashboard_editor

store = Storage(r"{db_path}")
store.init_db()
st.session_state["dash_mode"] = "edit"
st.session_state["dash_edit_id"] = 1
st.session_state["dash_edit_section"] = st.session_state.get("_section", "Layout")
dashboard_editor.render(store, ConnectionManager())
'''


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "t.db")
    s = Storage(path)
    s.init_db()
    s.add_connection(Connection(id=None, name="rdb", host="demo", port=1,
                                kind="realtime", env="orders", schema=SCHEMA))
    s.add_connection(Connection(id=None, name="hdb", host="demo", port=2,
                                kind="historical", env="orders", schema=SCHEMA))
    s.add_dashboard(_dashboard_with_every_widget_type())
    return path


def test_the_layout_editor_renders_every_widget_type_without_duplicate_keys(db):
    at = AppTest.from_string(_script(db), default_timeout=60).run()
    assert not at.exception, [str(e.value) for e in at.exception]


def test_the_data_editor_renders(db):
    script = _script(db).replace('st.session_state.get("_section", "Layout")',
                                 '"Data"')
    at = AppTest.from_string(script, default_timeout=60).run()
    assert not at.exception, [str(e.value) for e in at.exception]


def test_every_streamlit_key_on_the_layout_page_is_unique(db):
    """A duplicate key raises, but assert on the keys directly too so a failure
    names the offender instead of just 'StreamlitDuplicateElementKey'."""
    at = AppTest.from_string(_script(db), default_timeout=60).run()
    keys = []
    for group in (at.selectbox, at.text_input, at.number_input, at.checkbox,
                  at.multiselect, at.text_area, at.button, at.radio):
        keys += [el.key for el in group if el.key]
    duplicates = {k for k in keys if keys.count(k) > 1}
    assert not duplicates, f"duplicate keys: {sorted(duplicates)}"
