"""Turning a page of the printed preview must not rebuild the report.

Every button on the dashboard reruns the whole script, and the live fragment used
to re-query KDB on each of those reruns. That restamped the frames, and because
the rendered pages are cached against that stamp, going to page 2 threw away page
1 and drew both again — a fresh render, and a fresh round of queries, for a page
turn. The frames now change on the refresh interval or when Refresh is pressed,
and nothing else.
"""
from datetime import datetime, timedelta

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from kdbmonitor.core.dashboard_models import Dashboard, Dataset, Row, Widget
from kdbmonitor.core.mock import demo_connection_specs
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui.dashboards import is_due


# --- when frames come due (pure) -------------------------------------------

NOW = datetime(2026, 7, 27, 9, 15, 0)


def test_frames_younger_than_the_interval_are_not_due():
    assert not is_due(NOW - timedelta(seconds=3), 15, NOW)


def test_frames_older_than_the_interval_are_due():
    assert is_due(NOW - timedelta(seconds=20), 15, NOW)


def test_the_timer_does_not_miss_its_own_beat():
    """run_every fires *on* the interval; demanding the full span to the
    microsecond would defer every other tick to the one after."""
    assert is_due(NOW - timedelta(seconds=14.9), 15, NOW)


def test_refresh_off_never_comes_due():
    assert not is_due(NOW - timedelta(hours=4), 0, NOW)


# --- the preview on a running app ------------------------------------------

_STREAMLIT = tuple(int(p) for p in st.__version__.split(".")[:2])
pytestmark = pytest.mark.skipif(_STREAMLIT < (1, 49),
                                reason="needs Streamlit >= 1.49 to render the page")

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


def _dashboard(refresh_secs: int) -> Dashboard:
    """Two tall charts — more than one A4 page, so the preview can be paged."""
    return Dashboard(
        id=None, name="Demo orders", refresh_secs=refresh_secs,
        datasets=[Dataset(name="orders", env="orders", table="target")],
        rows=[Row(height_in=6.0, widgets=[
            Widget(type="bar", dataset="orders", title=f"Qty {i}",
                   spec={"x": "sym", "y": "qty"})]) for i in range(2)])


@pytest.fixture()
def db(tmp_path):
    def _make(refresh_secs: int = 0) -> str:
        path = str(tmp_path / f"d{refresh_secs}.db")
        s = Storage(path)
        s.init_db()
        for spec in demo_connection_specs():
            s.add_connection(spec)
        s.add_dashboard(_dashboard(refresh_secs))
        return path
    return _make


def _click(at: AppTest, label: str) -> AppTest:
    """Click the most recent button with this label.

    AppTest keeps the elements from every pass of a run, so after a rerun the
    same control appears more than once; the last one is the live copy.
    """
    [el for el in at.button if (el.label or "").strip() == label][-1].click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _click_key(at: AppTest, key: str) -> AppTest:
    [el for el in at.button if el.key == key][-1].click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _open_preview(db_path: str) -> AppTest:
    at = AppTest.from_string(SCRIPT.format(db=db_path), default_timeout=120).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return _click(at, "Preview pages")


def _frames(at: AppTest) -> dict:
    return at.session_state["dash_frames_1"]


def test_paging_the_preview_keeps_the_same_frames(db):
    at = _open_preview(db())
    before = _frames(at)

    at = _click(at, "Next")
    assert _frames(at) is before, "a page turn re-queried the datasets"
    assert at.session_state["pv_page_1"] == 2


def test_a_page_already_drawn_is_not_drawn_again(db):
    at = _open_preview(db())
    page_one = at.session_state["pdfpages_1"]["pages"][1]

    at = _click(at, "Next")
    at = _click(at, "Previous")

    cache = at.session_state["pdfpages_1"]["pages"]
    assert cache[1] is page_one, "page 1 was rendered again on the way back"
    assert set(cache) == {1, 2}


def test_an_interval_that_has_not_elapsed_leaves_the_frames_alone(db):
    """Refresh 15s, and a page turn a moment later is not the interval."""
    at = _open_preview(db(15))
    before = _frames(at)
    at = _click(at, "Next")
    assert _frames(at) is before


def test_refresh_takes_the_frames_again(db):
    at = _open_preview(db())
    before = _frames(at)

    at = _click_key(at, "pv_refresh_1")
    assert _frames(at) is not before
    assert _frames(at)["as_of"] >= before["as_of"]
    # Pages drawn from frames that are gone cannot describe the ones that
    # replaced them, so they are drawn again against the new stamp.
    assert at.session_state["pdfpages_1"]["as_of"] == _frames(at)["as_of"]


def test_the_view_offers_a_refresh_of_its_own(db):
    """The preview lives at the foot of a long page; the live view needs the
    same control without scrolling to it."""
    at = AppTest.from_string(SCRIPT.format(db=db()), default_timeout=120).run()
    before = _frames(at)

    at = _click_key(at, "rf_now_1")
    assert _frames(at) is not before
