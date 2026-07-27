"""Opening and closing the printed-page preview on an open dashboard.

The preview used to be a one-way door: the button that opened it only ever set
the flag, so once you had looked at the pages the only way back to the dashboard
was to leave and return.
"""
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from kdbmonitor.core.dashboard_models import Dashboard, Dataset, Row, Widget
from kdbmonitor.core.mock import demo_connection_specs
from kdbmonitor.core.storage import Storage


def _dashboard() -> Dashboard:
    return Dashboard(
        id=None, name="Demo orders", refresh_secs=0,
        datasets=[Dataset(name="orders", env="orders", table="target")],
        rows=[Row(height_in=0.9, widgets=[
            Widget(type="kpi", dataset="orders", title="Orders",
                   spec={"column": "qty", "agg": "sum", "fmt": ",.0f"})])])


# st.dataframe accepts a string height from 1.49; the open-dashboard page uses
# one, so on an older Streamlit this page cannot render at all.
_STREAMLIT = tuple(int(p) for p in st.__version__.split(".")[:2])
pytestmark = pytest.mark.skipif(_STREAMLIT < (1, 49),
                                reason="needs Streamlit >= 1.49 to render the page")

# AppTest cannot round-trip a pills widget across a rerun, and the tab strip is
# not what is under test.
SCRIPT = '''
import streamlit as st
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import dashboards

st.pills = lambda label, options, **k: k.get("default")
store = Storage(r"{db}")
store.init_db()
st.query_params["dash"] = "1"
dashboards.render(store, ConnectionManager())
'''


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "d.db")
    s = Storage(path)
    s.init_db()
    for spec in demo_connection_specs():
        s.add_connection(spec)
    s.add_dashboard(_dashboard())
    return path


def _open(db_path: str) -> AppTest:
    at = AppTest.from_string(SCRIPT.format(db=db_path), default_timeout=90).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _labels(at: AppTest) -> list[str]:
    return [(el.label or "").strip() for el in at.button]


def _click(at: AppTest, label: str) -> AppTest:
    next(el for el in at.button if (el.label or "").strip() == label).click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def test_the_preview_opens_and_the_button_offers_the_way_back(db):
    at = _open(db)
    assert "Preview page" in _labels(at) or "Preview pages" in _labels(at)

    at = _click(at, next(x for x in _labels(at) if x.startswith("Preview")))
    assert at.session_state["pdfpreview_on_1"] is True
    assert "Hide preview" in _labels(at)


def test_close_puts_the_dashboard_back(db):
    at = _click(_open(db), "Preview page")
    at = _click(at, "Close")

    assert at.session_state["pdfpreview_on_1"] is False
    # AppTest keeps what was drawn before an st.rerun(), so look at the next
    # clean pass — that is what the browser is left showing.
    at.run()
    assert not [m for m in at.markdown if "Printed page" in (m.value or "")]
    assert "Close" not in _labels(at)
    assert "Preview page" in _labels(at)


def test_hide_preview_closes_it_too(db):
    at = _click(_open(db), "Preview page")
    at = _click(at, "Hide preview")
    at.run()

    assert at.session_state["pdfpreview_on_1"] is False
    assert "Hide preview" not in _labels(at)
