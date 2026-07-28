"""Saving a transform or a widget once and reusing it in any dashboard.

The library holds copies, never links. Adding a saved component to a dashboard
must give that dashboard something it owns outright — editable, and with no way
for the edit to reach back into the library — and saving is how an improved copy
goes back, under its own name or over the one it came from.
"""
import pytest

from streamlit.testing.v1 import AppTest

from kdbmonitor.core.dashboard_models import (
    Dashboard, Dataset, Row, Transform, Widget,
)
from kdbmonitor.core.models import Connection
from kdbmonitor.core.storage import Storage

SCHEMA = {"target": ["sym", "size", "side", "price"]}

MARKET_STEP = {"kind": "derive",
               "params": {"column": "market", "kind": "suffix_map",
                          "source": "sym",
                          "mapping": {".HK": "Hong Kong", ".JP": "Japan"},
                          "default": "Unknown"}}

MARKET_TABLE = {"type": "table", "dataset": "somewhere_else", "title": "By market",
                "spec": {"columns": ["market", "size"],
                         "labels": {"market": "Market"},
                         "formats": {"size": ",.0f"}},
                "width": 2.0}


def _dashboard() -> Dashboard:
    return Dashboard(
        id=1, name="Reuse",
        datasets=[Dataset(name="d", env="orders", table="target",
                          transforms=[Transform(kind="sort",
                                                params={"columns": ["sym"],
                                                        "ascending": True})])],
        rows=[Row(widgets=[Widget(type="kpi", dataset="d", title="Orders",
                                  spec={"column": "size", "agg": "sum"})],
                  height_in=2.0)])


@pytest.fixture()
def db(tmp_path) -> str:
    path = str(tmp_path / "lib.db")
    s = Storage(path)
    s.init_db()
    s.add_connection(Connection(id=None, name="rdb", host="demo", port=1,
                                kind="realtime", env="orders", schema=SCHEMA))
    s.add_dashboard(_dashboard())
    return path


_SCRIPT = '''
import streamlit as st
from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import dashboard_editor

st.segmented_control = lambda *a, **k: "{section}"
store = Storage(r"{db}")
store.init_db()
st.session_state["dash_mode"] = "edit"
st.session_state["dash_edit_id"] = 1
dashboard_editor.render(store, ConnectionManager())
'''


def _open(db_path: str, section: str = "Data") -> AppTest:
    at = AppTest.from_string(_SCRIPT.format(db=db_path, section=section),
                             default_timeout=60).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _click(at: AppTest, key: str) -> AppTest:
    at.button(key=key).click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _pick(at: AppTest, key: str, value: str) -> AppTest:
    at.selectbox(key=key).set_value(value).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _draft(at: AppTest) -> Dashboard:
    return at.session_state["dash_draft"]


def _keys(at: AppTest) -> set[str]:
    return {el.key for el in at.button if el.key}


# --- what the library keeps ------------------------------------------------

def test_saving_under_a_name_already_used_replaces_it(db):
    s = Storage(db)
    s.save_component("transform", "market", MARKET_STEP)
    s.save_component("transform", "market", {"kind": "sort", "params": {}})

    saved = s.list_components("transform")
    assert len(saved) == 1                       # replaced, not duplicated
    assert saved[0].payload["kind"] == "sort"


def test_a_transform_and_a_widget_can_share_a_name(db):
    """They are picked from separate lists, so the names need not compete."""
    s = Storage(db)
    s.save_component("transform", "market", MARKET_STEP)
    s.save_component("widget", "market", MARKET_TABLE)

    assert [c.name for c in s.list_components("transform")] == ["market"]
    assert [c.name for c in s.list_components("widget")] == ["market"]


def test_a_component_survives_the_round_trip(db):
    s = Storage(db)
    s.save_component("transform", "market", MARKET_STEP)
    assert s.get_component_by_name("transform", "market").payload == MARKET_STEP


def test_renaming_and_deleting(db):
    s = Storage(db)
    cid = s.save_component("widget", "By market", MARKET_TABLE)

    s.rename_component(cid, "Market table")
    assert [c.name for c in s.list_components()] == ["Market table"]

    s.delete_component(cid)
    assert s.list_components() == []


# --- saving from the editor ------------------------------------------------

def test_a_transform_can_be_saved_over_one_in_the_library(db):
    """The flow the library exists for: load something, improve it in the
    dashboard, and put it back under the name it came from."""
    Storage(db).save_component("transform", "market", MARKET_STEP)

    at = _pick(_open(db), "ds0_t0_lib_name", "market")
    _click(at, "ds0_t0_lib_save")

    saved = Storage(db).list_components("transform")
    assert len(saved) == 1
    assert saved[0].payload["kind"] == "sort"        # the dataset's own step


def test_a_widget_can_be_saved_over_one_in_the_library(db):
    Storage(db).save_component("widget", "By market", MARKET_TABLE)

    at = _pick(_open(db, "Layout"), "r0w0_lib_name", "By market")
    _click(at, "r0w0_lib_save")

    saved = Storage(db).get_component_by_name("widget", "By market")
    assert saved.payload["type"] == "kpi"            # the row's own widget
    assert saved.payload["title"] == "Orders"


def test_saving_needs_a_name(db):
    """Nothing chosen, nothing typed — there is nothing to save it as."""
    Storage(db).save_component("transform", "market", MARKET_STEP)
    at = _open(db)
    assert at.button(key="ds0_t0_lib_save").disabled


# --- loading into a dashboard ----------------------------------------------

def test_nothing_offers_a_library_that_is_empty(db):
    at = _open(db)
    assert "ds0_tlib_add" not in _keys(at)


def test_a_saved_transform_is_added_to_the_dataset(db):
    Storage(db).save_component("transform", "market", MARKET_STEP)

    at = _pick(_open(db), "ds0_tlib_pick", "market")
    _click(at, "ds0_tlib_add")

    added = _draft(at).datasets[0].transforms[-1]
    assert added.kind == "derive"
    assert added.params["column"] == "market"
    assert added.params["source"] == "sym"


def test_what_is_added_is_a_copy_the_dashboard_owns(db):
    """Editing the dashboard's copy must not rewrite the library, or a one-off
    tweak would change every dashboard that used it."""
    Storage(db).save_component("transform", "market", MARKET_STEP)
    at = _click(_pick(_open(db), "ds0_tlib_pick", "market"), "ds0_tlib_add")

    _draft(at).datasets[0].transforms[-1].params["column"] = "venue"

    stored = Storage(db).get_component_by_name("transform", "market")
    assert stored.payload["params"]["column"] == "market"


def test_a_saved_widget_is_added_to_the_row(db):
    Storage(db).save_component("widget", "By market", MARKET_TABLE)

    at = _pick(_open(db, "Layout"), "r0_wlib_pick", "By market")
    _click(at, "r0_wlib_add")

    added = _draft(at).rows[0].widgets[-1]
    assert added.type == "table"
    assert added.spec["columns"] == ["market", "size"]
    assert added.spec["labels"] == {"market": "Market"}


def test_a_saved_widget_is_bound_to_a_dataset_this_dashboard_has(db):
    """It was saved against a dataset that is not here — point it at one that
    is, rather than at a name that means nothing."""
    Storage(db).save_component("widget", "By market", MARKET_TABLE)

    at = _pick(_open(db, "Layout"), "r0_wlib_pick", "By market")
    _click(at, "r0_wlib_add")

    assert _draft(at).rows[0].widgets[-1].dataset == "d"


def test_a_full_row_cannot_take_another_widget(db):
    dash = _dashboard()
    dash.rows[0].widgets = [Widget(type="kpi", dataset="d", title=str(i),
                                   spec={"column": "size", "agg": "sum"})
                            for i in range(4)]
    s = Storage(db)
    s.update_dashboard(dash)
    s.save_component("widget", "By market", MARKET_TABLE)

    at = _open(db, "Layout")
    assert at.button(key="r0_add").disabled


# --- the library section ---------------------------------------------------

def test_the_library_section_lists_what_is_saved(db):
    s = Storage(db)
    s.save_component("transform", "market", MARKET_STEP)
    s.save_component("widget", "By market", MARKET_TABLE)

    at = _open(db, "Library")
    shown = " ".join(el.value for el in at.markdown)
    assert "market" in shown and "By market" in shown


def test_a_component_can_be_deleted_from_the_library(db):
    cid = Storage(db).save_component("transform", "market", MARKET_STEP)
    _click(_open(db, "Library"), f"lib{cid}_delb")
    assert Storage(db).list_components() == []


def test_a_component_cannot_be_renamed_onto_another(db):
    s = Storage(db)
    s.save_component("transform", "market", MARKET_STEP)
    cid = s.save_component("transform", "venue", MARKET_STEP)

    at = _open(db, "Library")
    at.text_input(key=f"lib{cid}_rn").set_value("market").run()
    assert at.button(key=f"lib{cid}_rnb").disabled
