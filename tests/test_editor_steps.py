"""Checking a dataset step by step, in the editor.

A dashboard query is built as a pipeline: a q query, then transforms applied in
order. Running only the end of it tells you *that* the numbers are wrong, never
*where*, so the editor runs each stage and shows the frame after each one.
"""
import pandas as pd
import pytest

from streamlit.testing.v1 import AppTest

from kdbmonitor.core.dashboard_models import Dashboard, Dataset, Transform
from kdbmonitor.core.models import Connection
from kdbmonitor.core.storage import Storage

RAW_Q = "select id_target, sym, size, executed from target where side=`sellshort"

ORDERS = pd.DataFrame([
    {"id_target": 1, "sym": "5.HK", "size": 100, "executed": 50},
    {"id_target": 2, "sym": "700.HK", "size": 200, "executed": 200},
    {"id_target": 3, "sym": "7203.JP", "size": 50, "executed": 0},
])


def _dashboard(transforms) -> Dashboard:
    return Dashboard(id=1, name="Short sell by market", datasets=[Dataset(
        name="by_market", env="orders", mode="raw", raw_qsql=RAW_Q,
        transforms=transforms)])


def _pipeline() -> list[Transform]:
    return [
        Transform(kind="derive", params={
            "column": "market", "kind": "suffix_map", "source": "sym",
            "mapping": {".HK": "Hong Kong", ".JP": "Japan"},
            "default": "Unknown"}),
        Transform(kind="groupby", params={"keys": ["market"], "aggs": [
            {"column": "id_target", "func": "nunique", "as": "n_orders"},
            {"column": "size", "func": "sum", "as": "order_qty"}]}),
        Transform(kind="sort", params={"columns": ["market"],
                                       "ascending": True}),
    ]


# The manager is faked in the script itself: the editor takes one as an
# argument, so a preview can be driven end to end without a KDB process.
_SCRIPT = '''
import pandas as pd
import streamlit as st
from kdbmonitor.core.client import ConnectionManager, FakeClient
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import dashboard_editor

st.segmented_control = lambda *a, **k: "Data"
client = FakeClient({{{responses}}})
store = Storage(r"{db}")
store.init_db()
st.session_state["dash_mode"] = "edit"
st.session_state["dash_edit_id"] = 1
dashboard_editor.render(store,
                        ConnectionManager(client_factory=lambda h, p: client))
'''

_RESPONSES = ('"' + RAW_Q.replace("\\", "\\\\") + '": pd.DataFrame('
              + repr(ORDERS.to_dict("records")) + ')')


@pytest.fixture()
def db(tmp_path):
    def build(transforms):
        path = str(tmp_path / "t.db")
        s = Storage(path)
        s.init_db()
        # Not the "demo" sentinel host: that is served by the built-in mock
        # database, which would answer instead of the fake client below.
        s.add_connection(Connection(id=None, name="rdb", host="oms-rdb", port=1,
                                    kind="realtime", env="orders", schema={}))
        s.add_dashboard(_dashboard(transforms))
        return path
    return build


def _preview(db_path: str) -> AppTest:
    at = AppTest.from_string(_SCRIPT.format(db=db_path, responses=_RESPONSES),
                             default_timeout=60).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    at.button(key="dash_preview_run").click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _text(at: AppTest) -> str:
    return "\n".join([el.value for el in at.markdown]
                     + [el.value for el in at.caption])


def test_every_stage_of_the_pipeline_is_shown(db):
    at = _preview(db(_pipeline()))
    body = _text(at)

    assert "Query result" in body
    assert "1. derive market from sym" in body
    assert "2. group by market" in body
    assert "3. sort by market ascending" in body
    # One frame per stage: the query plus each transform.
    assert len(at.dataframe) == 4


def test_each_stage_reports_what_it_did_to_the_data(db):
    at = _preview(db(_pipeline()))
    body = _text(at)

    assert "3 row(s)" in body            # the query returned three orders
    assert "+ market" in body            # the derive added a column
    assert "2 row(s)" in body            # the group-by collapsed them
    assert "-1 vs previous step" in body


def test_the_frames_shown_are_the_real_ones(db):
    at = _preview(db(_pipeline()))
    grouped = at.dataframe[2].value      # after the group-by

    assert list(grouped["market"]) == ["Hong Kong", "Japan"]
    assert list(grouped["n_orders"]) == [2, 1]


def test_a_broken_step_is_named_and_the_earlier_ones_still_show(db):
    """Group by a column nothing derived: the preview must point at that step,
    not just refuse the whole dataset."""
    at = _preview(db(_pipeline()[1:]))            # the derive is missing
    body = _text(at)

    assert "Query result" in body
    assert len(at.dataframe) == 1                 # only the query survived
    assert any("no column 'market'" in e.value for e in at.error)


def test_a_preview_teaches_the_editor_what_a_raw_query_returns(db):
    """A raw q's shape is unknowable until it runs — once it has, the column
    pickers can offer real columns instead of nothing."""
    at = _preview(db(_pipeline()))
    assert at.session_state["dash_learned_cols"]["by_market"] == [
        "id_target", "sym", "size", "executed"]


def test_the_pickers_fill_in_on_the_same_click(db):
    """'No options to select' on the group-by: one run must be enough to fix
    it, not a run and then some unrelated interaction."""
    at = _preview(db(_pipeline()))
    keys = next(el for el in at.multiselect if el.key == "ds0_t1_gk")

    assert set(keys.options) >= {"sym", "size", "id_target", "market"}
    assert keys.value == ["market"]


def test_a_group_by_offers_its_own_output_downstream(db):
    """Whatever the query returns, everything after a group-by is knowable."""
    at = _preview(db(_pipeline()))
    sort_by = next(el for el in at.multiselect if el.key == "ds0_t2_sc")

    assert set(sort_by.options) == {"market", "n_orders", "order_qty"}


def test_the_query_that_was_sent_is_shown(db):
    at = _preview(db(_pipeline()))
    assert any(RAW_Q in el.value for el in at.code)
