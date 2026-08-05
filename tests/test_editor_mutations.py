"""What the dashboard editor must never do to a draft it is only showing.

Every control writes its value straight back into the draft on each rerun. That
makes the editor simple, but it means a picker offering the wrong options does
not merely look wrong — it *rewrites the dashboard*. A raw-q dataset has no
schema to offer, so before this was fixed, opening such a dashboard silently
emptied its group-by keys, its sort columns and its table's column list, and
deleting one transform appeared to gut another.

These run the editor for real with AppTest: the defect lives in the round trip
through Streamlit's widget state, not in any single function.
"""
import copy

import pytest

from streamlit.testing.v1 import AppTest

from kdbmonitor.core.dashboard_models import (
    Dashboard, Dataset, Row, Transform, Widget, dashboard_to_dict,
)
from kdbmonitor.core.models import Connection, Filter
from kdbmonitor.core.storage import Storage

SCHEMA = {"target": ["sym", "size", "side", "price"]}

RAW_Q = "select id_target, sym, size, executed from target where side=`sellshort"


def _market_transforms() -> list[Transform]:
    """The shape of the shipped 'Short sell by market' pipeline."""
    return [
        Transform(kind="derive", params={
            "column": "market", "kind": "suffix_map", "source": "sym",
            "mapping": {".HK": "Hong Kong", ".JP": "Japan"},
            "default": "Unknown"}),
        Transform(kind="groupby", params={"keys": ["market"], "aggs": [
            {"column": "id_target", "func": "nunique", "as": "n_orders"},
            {"column": "size", "func": "sum", "as": "order_qty"},
            {"column": "executed", "func": "sum", "as": "executed_qty"}]}),
        Transform(kind="derive", params={
            "column": "completion_pct", "kind": "arithmetic",
            "expr": "100 * executed_qty / order_qty"}),
        Transform(kind="sort", params={"columns": ["market"], "ascending": True}),
    ]


def _raw_dashboard() -> Dashboard:
    """A raw-q dataset: no schema, so nothing can be offered from the table."""
    return Dashboard(
        id=1, name="Short sell by market",
        datasets=[Dataset(name="by_market", env="orders", mode="raw",
                          raw_qsql=RAW_Q, transforms=_market_transforms())],
        rows=[Row(widgets=[
            Widget(type="kpi", dataset="by_market", title="Orders",
                   spec={"column": "n_orders", "agg": "sum", "fmt": ",.0f"}),
            Widget(type="table", dataset="by_market", title="By market",
                   spec={"columns": ["market", "n_orders", "order_qty"],
                         "labels": {"market": "Market"},
                         "formats": {"order_qty": ",.0f"},
                         "group_by": "market",
                         "highlight": [{"column": "n_orders", "op": ">",
                                        "value": 0, "color": "critical"}]})],
                  height_in=2.0)])


def _guided_dashboard() -> Dashboard:
    """A guided dataset whose filters address columns the transforms consume."""
    return Dashboard(
        id=1, name="Guided",
        datasets=[Dataset(
            name="d", env="orders", table="target",
            filters=[Filter(column="side", op="=", value="sellshort",
                            value_type="symbol"),
                     Filter(column="sym", op="like", value="*.HK",
                            value_type="string"),
                     Filter(column="size", op=">", value=100,
                            value_type="number")],
            transforms=[
                Transform(kind="groupby", params={"keys": ["sym"], "aggs": [
                    {"column": "size", "func": "sum", "as": "qty"},
                    {"column": "price", "func": "mean", "as": "avg_px"},
                    {"column": "size", "func": "count", "as": "n"}]}),
                Transform(kind="sort", params={"columns": ["qty"],
                                               "ascending": False}),
                Transform(kind="limit", params={"n": 7}),
            ])],
        rows=[Row(widgets=[Widget(type="kpi", dataset="d", title="One",
                                  spec={"column": "qty", "agg": "sum",
                                        "fmt": ",.0f"}),
                           Widget(type="kpi", dataset="d", title="Two",
                                  spec={"column": "avg_px", "agg": "mean",
                                        "fmt": ",.2f"})],
                  height_in=1.0),
              Row(widgets=[Widget(type="table", dataset="d", title="Three",
                                  spec={"columns": ["sym", "qty"]})],
                  height_in=2.0)])


# AppTest cannot round-trip a segmented_control across a second run in
# Streamlit 1.45, and these tests all need a click plus a rerun. Pin the
# section instead — everything under test is inside the section body.
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


def _store(tmp_path, dashboard: Dashboard, name: str = "t.db") -> str:
    path = str(tmp_path / name)
    s = Storage(path)
    s.init_db()
    s.add_connection(Connection(id=None, name="rdb", host="demo", port=1,
                                kind="realtime", env="orders", schema=SCHEMA))
    s.add_dashboard(dashboard)
    return path


@pytest.fixture()
def raw_db(tmp_path):
    return _store(tmp_path, _raw_dashboard())


@pytest.fixture()
def guided_db(tmp_path):
    return _store(tmp_path, _guided_dashboard(), "g.db")


def _open(db_path: str, section: str = "Data") -> AppTest:
    at = AppTest.from_string(_SCRIPT.format(db=db_path, section=section),
                             default_timeout=60).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _click(at: AppTest, key: str) -> AppTest:
    at.button(key=key).click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _set_position(at: AppTest, key: str, position: int) -> AppTest:
    """Type a destination into one of the 'move to' boxes."""
    at.number_input(key=key).set_value(position).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _draft(at: AppTest) -> Dashboard:
    return at.session_state["dash_draft"]


def _transforms(at: AppTest) -> list[Transform]:
    return _draft(at).datasets[0].transforms


# --- showing a dashboard must not change it --------------------------------

def _assert_nothing_lost(before, after, where: str = "dashboard") -> None:
    """Every setting that was stored is still there, unchanged.

    Deliberately one-directional: the forms legitimately fill in empty defaults
    (a KPI with no threshold gains ``thresholds: []``). What they must never do
    is drop or overwrite a value the user configured.
    """
    if isinstance(before, dict):
        assert isinstance(after, dict), where
        for key, value in before.items():
            assert key in after, f"{where}.{key} was dropped"
            _assert_nothing_lost(value, after[key], f"{where}.{key}")
    elif isinstance(before, list):
        assert isinstance(after, list) and len(after) == len(before), \
            f"{where} changed length: {before} -> {after}"
        for i, value in enumerate(before):
            _assert_nothing_lost(value, after[i], f"{where}[{i}]")
    else:
        assert after == before, f"{where}: {before!r} was rewritten to {after!r}"


@pytest.mark.parametrize("section", ["Data", "Layout"])
def test_opening_a_raw_dashboard_changes_nothing(raw_db, section):
    """The bug in the flesh: no click at all, and the group-by lost its key."""
    at = _open(raw_db, section)
    _assert_nothing_lost(dashboard_to_dict(_raw_dashboard()),
                         dashboard_to_dict(_draft(at)))


@pytest.mark.parametrize("section", ["Data", "Layout"])
def test_opening_a_guided_dashboard_changes_nothing(guided_db, section):
    """Filters address the table, so a group-by downstream must not rewrite
    them to whichever column it happens to leave behind."""
    at = _open(guided_db, section)
    _assert_nothing_lost(dashboard_to_dict(_guided_dashboard()),
                         dashboard_to_dict(_draft(at)))


def test_a_suffix_map_gets_the_length_its_own_suffixes_already_have(raw_db):
    """Making the length explicit must not silently re-point an existing map:
    '.HK' and '.JP' are 3 characters, so 3 is what it has always matched."""
    at = _open(raw_db)
    assert _transforms(at)[0].params["length"] == 3


def test_a_group_by_can_offer_the_column_an_earlier_transform_derives(raw_db):
    """'No options to select' on a raw dataset: the derive above it names
    'market', so the group-by has always had one thing it could offer."""
    keys = at_multiselect(_open(raw_db), "ds0_t1_gk")
    assert "market" in keys.options
    assert keys.value == ["market"]


def at_multiselect(at: AppTest, key: str):
    return next(el for el in at.multiselect if el.key == key)


def test_a_raw_dashboard_has_nothing_to_fix_when_opened(raw_db):
    """It was reported as 'N problems to fix' purely because showing it broke
    it — a dashboard that was valid on disk must stay valid on screen."""
    from kdbmonitor.ui.dashboard_editor import validate
    at = _open(raw_db)
    problems = validate(_draft(at), Storage(raw_db))
    assert problems == []


# --- deleting removes exactly the element you asked for --------------------

@pytest.mark.parametrize("index, remaining", [
    (0, ["groupby", "derive", "sort"]),
    (1, ["derive", "derive", "sort"]),
    (2, ["derive", "groupby", "sort"]),
    (3, ["derive", "groupby", "derive"]),
])
def test_removing_a_transform_removes_that_one(raw_db, index, remaining):
    kept = [t for i, t in enumerate(_market_transforms()) if i != index]
    at = _click(_open(raw_db), f"ds0_tx_{index}")

    assert [t.kind for t in _transforms(at)] == remaining
    _assert_nothing_lost([t.params for t in kept],
                         [t.params for t in _transforms(at)], "transforms")


def test_removing_a_filter_removes_that_one(guided_db):
    at = _click(_open(guided_db), "ds0_fx_0")
    filters = _draft(at).datasets[0].filters
    assert [(f.column, f.op, f.value) for f in filters] == [
        ("sym", "like", "*.HK"), ("size", ">", 100)]


def test_removing_an_aggregation_removes_that_one(guided_db):
    at = _click(_open(guided_db), "ds0_t0_gx_1")
    aggs = _transforms(at)[0].params["aggs"]
    assert [a["as"] for a in aggs] == ["qty", "n"]
    assert [a["func"] for a in aggs] == ["sum", "count"]


def test_removing_a_widget_removes_that_one(guided_db):
    at = _click(_open(guided_db, "Layout"), "r0w0_del")
    widgets = _draft(at).rows[0].widgets
    assert [w.title for w in widgets] == ["Two"]
    assert widgets[0].spec["column"] == "avg_px"


def test_removing_a_row_removes_that_one(guided_db):
    at = _click(_open(guided_db, "Layout"), "r0_x")
    rows = _draft(at).rows
    assert len(rows) == 1
    assert [w.title for w in rows[0].widgets] == ["Three"]
    assert rows[0].height_in == 2.0


def test_removing_a_dataset_removes_that_one(tmp_path):
    dash = _raw_dashboard()
    dash.datasets.append(Dataset(name="second", env="orders", mode="raw",
                                 raw_qsql="select from workorder"))
    at = _click(_open(_store(tmp_path, dash, "two.db")), "ds0_del")
    datasets = _draft(at).datasets
    assert [d.name for d in datasets] == ["second"]
    assert datasets[0].raw_qsql == "select from workorder"


# --- reordering carries each element's settings with it --------------------

def test_moving_a_transform_down_carries_its_settings(raw_db):
    at = _click(_open(raw_db), "ds0_td_0")
    assert [t.kind for t in _transforms(at)] == ["groupby", "derive", "derive",
                                                 "sort"]
    assert _transforms(at)[1].params["column"] == "market"
    assert _transforms(at)[0].params["keys"] == ["market"]


def test_moving_a_transform_up_carries_its_settings(raw_db):
    at = _click(_open(raw_db), "ds0_tu_3")
    assert [t.kind for t in _transforms(at)] == ["derive", "groupby", "sort",
                                                 "derive"]
    assert _transforms(at)[2].params["columns"] == ["market"]


def test_moving_a_row_up_carries_its_widgets(guided_db):
    at = _click(_open(guided_db, "Layout"), "r1_u")
    rows = _draft(at).rows
    assert [[w.title for w in r.widgets] for r in rows] == [["Three"],
                                                            ["One", "Two"]]
    assert rows[0].height_in == 2.0


def test_moving_a_row_down_carries_its_widgets(guided_db):
    at = _click(_open(guided_db, "Layout"), "r0_d")
    rows = _draft(at).rows
    assert [[w.title for w in r.widgets] for r in rows] == [["Three"],
                                                            ["One", "Two"]]
    assert rows[1].height_in == 1.0


def test_jumping_a_row_carries_its_widgets(guided_db):
    at = _set_position(_open(guided_db, "Layout"), "r1_pos", 1)
    rows = _draft(at).rows
    assert [[w.title for w in r.widgets] for r in rows] == [["Three"],
                                                            ["One", "Two"]]
    assert rows[0].height_in == 2.0


def test_moving_a_widget_left_carries_its_spec(guided_db):
    at = _click(_open(guided_db, "Layout"), "r0w1_l")
    widgets = _draft(at).rows[0].widgets
    assert [w.title for w in widgets] == ["Two", "One"]
    assert widgets[0].spec["column"] == "avg_px"
    assert widgets[1].spec["column"] == "qty"


# --- a deletion must not disturb its neighbours ----------------------------

def test_deleting_a_widget_leaves_the_other_rows_alone(guided_db):
    """Forgetting the shifted controls has to stop at the row it happened in:
    clear too much and the rows below are re-read from a draft they never
    wrote to; clear too little and they inherit each other's values."""
    at = _click(_open(guided_db, "Layout"), "r0w0_del")
    rows = _draft(at).rows

    assert [w.title for w in rows[0].widgets] == ["Two"]
    assert rows[1].height_in == 2.0
    assert [w.title for w in rows[1].widgets] == ["Three"]
    assert rows[1].widgets[0].spec["columns"] == ["sym", "qty"]


def test_deleting_a_transform_leaves_the_other_datasets_alone(tmp_path):
    """Each dataset card has its own key space; only the one that renumbered
    may be forgotten."""
    dash = _raw_dashboard()
    dash.datasets.append(Dataset(
        name="second", env="orders", mode="raw", raw_qsql="select from workorder",
        transforms=[
            Transform(kind="limit", params={"n": 5}),
            Transform(kind="sort", params={"columns": ["sym"],
                                           "ascending": False})]))
    at = _click(_open(_store(tmp_path, dash, "two.db")), "ds1_tx_0")

    first, second = _draft(at).datasets
    assert [t.kind for t in second.transforms] == ["sort"]
    assert second.transforms[0].params["columns"] == ["sym"]
    assert second.transforms[0].params["ascending"] is False
    _assert_nothing_lost([t.params for t in _market_transforms()],
                         [t.params for t in first.transforms], "ds0.transforms")

def test_deleting_the_first_of_three_identical_kinds(tmp_path):
    dash = _raw_dashboard()
    dash.datasets[0].transforms = [
        Transform(kind="derive", params={"column": c, "kind": "arithmetic",
                                         "expr": f"size * {i}"})
        for i, c in enumerate(("a", "b", "c"), start=1)]
    at = _click(_open(_store(tmp_path, dash, "same.db")), "ds0_tx_0")
    assert [(t.params["column"], t.params["expr"]) for t in _transforms(at)] == [
        ("b", "size * 2"), ("c", "size * 3")]


# --- a table's headers and formats belong to their columns -----------------

TABLE_SPEC = {"columns": ["sym", "size", "price", "state"],
              "labels": {"sym": "Symbol", "size": "Qty", "price": "Px",
                         "state": "State"},
              "formats": {"size": ",.0f", "price": ",.2f",
                          "state": "%H:%M:%S"}}


@pytest.fixture()
def table_db(tmp_path):
    dash = Dashboard(
        id=1, name="Table",
        datasets=[Dataset(name="d", env="orders", table="target")],
        rows=[Row(widgets=[Widget(type="table", dataset="d", title="Rows",
                                  spec=dict(TABLE_SPEC))], height_in=2.0)])
    return _store(tmp_path, dash, "tbl.db")


def _spec(at: AppTest) -> dict:
    return _draft(at).rows[0].widgets[0].spec


def _drop_column(at: AppTest, keep: list[str]) -> AppTest:
    picker = next(el for el in at.multiselect if el.key == "r0w0_spec_cols")
    picker.set_value(keep).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def test_each_header_box_belongs_to_its_own_column(table_db):
    """Keyed by row number, the boxes shifted when a column went and a header
    could land on the column below it."""
    at = _open(table_db, "Layout")
    boxes = {el.key: el.value for el in at.text_input if "_lbl" in (el.key or "")}
    assert boxes == {"r0w0_spec_col_sym_lbl": "Symbol",
                     "r0w0_spec_col_size_lbl": "Qty",
                     "r0w0_spec_col_price_lbl": "Px",
                     "r0w0_spec_col_state_lbl": "State"}


def test_removing_a_column_leaves_every_other_header_where_it_was(table_db):
    at = _drop_column(_open(table_db, "Layout"), ["size", "price", "state"])

    assert _spec(at)["columns"] == ["size", "price", "state"]
    boxes = {el.key: el.value for el in at.text_input if "_lbl" in (el.key or "")}
    assert boxes == {"r0w0_spec_col_size_lbl": "Qty",
                     "r0w0_spec_col_price_lbl": "Px",
                     "r0w0_spec_col_state_lbl": "State"}


def test_removing_a_column_leaves_every_other_format_where_it_was(table_db):
    at = _drop_column(_open(table_db, "Layout"), ["size", "price", "state"])
    assert _spec(at)["formats"] == {"size": ",.0f", "price": ",.2f",
                                    "state": "%H:%M:%S"}


def test_a_removed_column_keeps_its_header_for_when_it_comes_back(table_db):
    """Deselecting a column to try another must not throw away the header you
    typed for it — putting it back would mean typing it again."""
    at = _drop_column(_open(table_db, "Layout"), ["size", "price", "state"])
    assert _spec(at)["labels"]["sym"] == "Symbol"


def test_a_column_can_be_given_a_width_of_its_own(table_db):
    at = _open(table_db, "Layout")
    picker = next(el for el in at.selectbox
                  if el.key == "r0w0_spec_col_price_w")
    assert picker.value == "auto"                  # automatic until it is set
    picker.set_value("small").run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert _spec(at)["widths"] == {"price": "small"}


def test_a_column_set_back_to_automatic_stores_nothing(tmp_path):
    dash = Dashboard(
        id=1, name="Table",
        datasets=[Dataset(name="d", env="orders", table="target")],
        rows=[Row(widgets=[Widget(type="table", dataset="d", title="Rows",
                                  spec={**TABLE_SPEC,
                                        "widths": {"price": "large"}})],
                  height_in=2.0)])
    at = _open(_store(tmp_path, dash, "w.db"), "Layout")
    assert at.selectbox(key="r0w0_spec_col_price_w").value == "large"
    at.selectbox(key="r0w0_spec_col_price_w").set_value("auto").run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert _spec(at)["widths"] == {}


def test_a_width_belongs_to_its_column_and_not_to_its_position(table_db):
    """The same rule the headers and formats live by: drop a column and the
    ones below keep their own settings instead of inheriting the gap."""
    at = _open(table_db, "Layout")
    at.selectbox(key="r0w0_spec_col_price_w").set_value("small").run()
    at = _drop_column(at, ["size", "price", "state"])
    assert _spec(at)["widths"] == {"price": "small"}


# --- moving a dataset to another environment --------------------------------
#
# The card asks its server what tables exist. That server has to be the one the
# *environment picker on this run* names — not the one it named last run. Every
# picker below reads it, and a Streamlit picker does not merely show a list: it
# writes one of its options straight back into the draft.

REF_SCHEMA = {"instrument": ["sym", "sector", "currency"]}


@pytest.fixture()
def two_env_db(tmp_path):
    """Two environments whose servers hold entirely different tables."""
    path = str(tmp_path / "envs.db")
    s = Storage(path)
    s.init_db()
    s.add_connection(Connection(id=None, name="rdb", host="demo", port=1,
                                kind="realtime", env="orders", schema=SCHEMA))
    s.add_connection(Connection(id=None, name="ref", host="demo", port=2,
                                kind="marketdata", env="refdata",
                                schema=REF_SCHEMA))
    s.add_dashboard(Dashboard(
        id=1, name="Envs",
        datasets=[Dataset(name="d", env="refdata", table="instrument")],
        rows=[Row(widgets=[Widget(type="table", dataset="d", title="Rows",
                                  spec={})], height_in=2.0)]))
    return path


def _env_moved(db_path) -> AppTest:
    at = _open(db_path, "Data")
    assert at.selectbox(key="ds0_t").options == ["instrument"]   # before
    at.selectbox(key="ds0_e").set_value("orders").run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def test_changing_the_environment_offers_that_environment_s_tables(two_env_db):
    """It offered the old one's until something else caused a second rerun."""
    assert _env_moved(two_env_db).selectbox(key="ds0_t").options == sorted(SCHEMA)


def test_a_moved_dataset_does_not_keep_pointing_at_the_old_server_s_table(two_env_db):
    """The picker writes its value back, so a stale list is not a display fault
    — it re-points the dataset at a table its new server has never heard of."""
    ds = _draft(_env_moved(two_env_db)).datasets[0]
    assert ds.env == "orders" and ds.table in SCHEMA


def test_the_filters_offer_the_new_table_s_columns_too(two_env_db):
    """Same stale server, one row further down the card."""
    at = _env_moved(two_env_db)
    at.button(key="ds0_addf").click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert at.selectbox(key="ds0_fc_0").options == SCHEMA["target"]


# --- the grouping a table starts out under ----------------------------------

def test_a_table_is_flat_until_somebody_picks_a_column_to_gather_it_by(table_db):
    at = _open(table_db, "Layout")
    assert at.selectbox(key="r0w0_spec_grp").value == "(none)"
    assert not _spec(at).get("group_by")


def test_picking_one_writes_it_into_the_table(table_db):
    at = _open(table_db, "Layout")
    at.selectbox(key="r0w0_spec_grp").set_value("price").run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert _spec(at)["group_by"] == "price"


def test_setting_it_back_to_none_stores_no_grouping_rather_than_the_word(tmp_path):
    dash = Dashboard(
        id=1, name="Table",
        datasets=[Dataset(name="d", env="orders", table="target")],
        rows=[Row(widgets=[Widget(type="table", dataset="d", title="Rows",
                                  spec={**TABLE_SPEC, "group_by": "state"})],
                  height_in=2.0)])
    at = _open(_store(tmp_path, dash, "grp.db"), "Layout")
    assert at.selectbox(key="r0w0_spec_grp").value == "state"
    at.selectbox(key="r0w0_spec_grp").set_value("(none)").run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert _spec(at)["group_by"] == ""


def test_a_moved_column_takes_its_width_with_it(table_db):
    at = _open(table_db, "Layout")
    at.selectbox(key="r0w0_spec_col_state_w").set_value("small").run()
    at = _set_position(at, "r0w0_spec_col_state_pos", 1)
    assert _spec(at)["columns"] == ["state", "sym", "size", "price"]
    assert _spec(at)["widths"] == {"state": "small"}


def test_swapping_a_column_gives_the_new_one_a_blank_header(table_db):
    at = _drop_column(_open(table_db, "Layout"),
                      ["size", "price", "state", "when"])
    boxes = {el.key: el.value for el in at.text_input if "_lbl" in (el.key or "")}
    assert boxes["r0w0_spec_col_when_lbl"] == ""       # not 'Symbol'
    assert boxes["r0w0_spec_col_size_lbl"] == "Qty"


# --- a table's columns can be reordered where they stand -------------------

def test_a_column_can_be_moved_later(table_db):
    """Reordering used to mean deselecting every column and picking them all
    again in the order you wanted."""
    at = _click(_open(table_db, "Layout"), "r0w0_spec_col_sym_down")
    assert _spec(at)["columns"] == ["size", "sym", "price", "state"]


def test_a_column_can_be_moved_earlier(table_db):
    at = _click(_open(table_db, "Layout"), "r0w0_spec_col_state_up")
    assert _spec(at)["columns"] == ["sym", "size", "state", "price"]


def test_a_column_crosses_the_whole_list_in_one_move(table_db):
    """Why the box exists beside the arrows: reaching the front of a long table
    was one click and one rerun per column passed."""
    at = _set_position(_open(table_db, "Layout"), "r0w0_spec_col_state_pos", 1)
    assert _spec(at)["columns"] == ["state", "sym", "size", "price"]


def test_the_multiselect_agrees_with_the_new_order(table_db):
    """The picker keeps the selection in its own widget state; if it is not
    told, the next rerun hands back the old order and undoes the move."""
    at = _click(_open(table_db, "Layout"), "r0w0_spec_col_sym_down")
    picker = next(el for el in at.multiselect if el.key == "r0w0_spec_cols")
    assert picker.value == ["size", "sym", "price", "state"]


def test_a_moved_column_takes_its_header_and_format_with_it(table_db):
    at = _click(_open(table_db, "Layout"), "r0w0_spec_col_sym_down")
    assert _spec(at)["labels"] == TABLE_SPEC["labels"]
    assert _spec(at)["formats"] == TABLE_SPEC["formats"]


def test_the_ends_cannot_be_moved_off_the_list(table_db):
    at = _open(table_db, "Layout")
    assert at.button(key="r0w0_spec_col_sym_up").disabled
    assert at.button(key="r0w0_spec_col_state_down").disabled
    box = at.number_input(key="r0w0_spec_col_sym_pos")
    assert (box.min, box.max) == (1, 4)


def test_moving_writes_down_an_all_columns_table(tmp_path):
    """'Empty = all' has no order to move within, so the first move records the
    columns as they stand and moves within that."""
    dash = Dashboard(
        id=1, name="All",
        datasets=[Dataset(name="d", env="orders", table="target")],
        rows=[Row(widgets=[Widget(type="table", dataset="d", title="Rows",
                                  spec={"columns": []})], height_in=2.0)])
    at = _click(_open(_store(tmp_path, dash, "all.db"), "Layout"),
                "r0w0_spec_col_sym_down")
    assert _spec(at)["columns"] == ["size", "sym", "side", "price"]


# --- a 'move to' box asks for a destination, and keeps asking ---------------
#
# It must never show the position it sits at. A box that does belongs to the
# slot rather than to what is in it: press its stepper twice and the second
# press moves whatever has since landed there, silently undoing the first — the
# whole layout back where it started, which reads as the control not working.

def _four_rows(tmp_path) -> AppTest:
    dash = _guided_dashboard()
    dash.rows = [Row(widgets=[Widget(type="kpi", dataset="d", title=t)],
                     height_in=1.0) for t in ("A", "B", "C", "D")]
    return _open(_store(tmp_path, dash, "four.db"), "Layout")


def _row_titles(at: AppTest) -> list[str]:
    return [r.widgets[0].title for r in _draft(at).rows]


def test_a_row_move_to_box_starts_and_stays_blank(tmp_path):
    """The reported bug: the box showed the row's own position, so stepping it
    once moved the row and sprang straight back to the number it started on —
    which reads as the control having done nothing."""
    at = _four_rows(tmp_path)
    assert at.number_input(key="r3_pos").value is None
    at = _set_position(at, "r3_pos", 3)
    assert _row_titles(at) == ["A", "B", "D", "C"]
    assert at.number_input(key="r3_pos").value is None


def test_the_box_shows_where_the_row_is_now_without_holding_it(tmp_path):
    """Blank, but not uninformative: the current position is the placeholder,
    which is a prompt rather than a value the steppers can act on."""
    at = _four_rows(tmp_path)
    assert at.number_input(key="r3_pos").placeholder == "4"


def test_every_row_move_says_what_it_moved(tmp_path):
    """A control that belongs to a slot acts on whatever is in that slot, so a
    second use is a second, different move — not the first one failing. Rows
    are indistinguishable from the outside, so each move announces itself."""
    at = _four_rows(tmp_path)
    at = _set_position(at, "r3_pos", 3)
    assert at.toast[0].value == "Row 4 moved to position 3"
    at = _click(at, "r2_u")
    assert at.toast[0].value == "Row 3 moved to position 2"
    assert _row_titles(at) == ["A", "D", "B", "C"]


def test_a_column_move_to_box_starts_and_stays_blank(table_db):
    at = _open(table_db, "Layout")
    assert at.number_input(key="r0w0_spec_col_state_pos").value is None
    at = _set_position(at, "r0w0_spec_col_state_pos", 1)
    assert at.number_input(key="r0w0_spec_col_state_pos").value is None
    boxes = [el.value for el in at.number_input
             if "_col_" in (el.key or "") and (el.key or "").endswith("_pos")]
    assert boxes == [None, None, None, None]


def test_stepping_one_column_box_twice_does_not_undo_itself(table_db):
    at = _open(table_db, "Layout")
    at = _set_position(_set_position(at, "r0w0_spec_col_state_pos", 1),
                       "r0w0_spec_col_state_pos", 1)
    assert _spec(at)["columns"] == ["state", "sym", "size", "price"]


# --- a dataset built on another one -----------------------------------------

def test_adding_a_derived_dataset_starts_it_on_the_one_above(raw_db):
    at = _click(_open(raw_db), "dash_ds_addderived")
    added = _draft(at).datasets[1]
    assert (added.source, added.base) == ("derived", "by_market")
    # Named after what it reads rather than 'dataset_2', which would read as a
    # second dataset of its own.
    assert added.name == "by_market_grouped"


def test_renaming_a_dataset_carries_everything_derived_from_it(raw_db):
    at = _click(_open(raw_db), "dash_ds_addderived")
    at.text_input(key="ds0_n").set_value("orders").run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert _draft(at).datasets[1].base == "orders"


def test_a_derived_dataset_can_only_read_the_ones_above_it(raw_db):
    at = _click(_open(raw_db), "dash_ds_addderived")
    picker = next(el for el in at.selectbox if el.key == "ds1_b")
    assert picker.options == ["by_market"]


# --- copy and paste: a copy lands where you put it -------------------------

def _keys(at: AppTest) -> list[str]:
    return [b.key for b in at.button]


def test_copying_a_widget_does_not_place_it_anywhere_yet(guided_db):
    """The copy goes to the clipboard. Deciding where it lands is the point of
    the feature — inserting it beside the original decided for you."""
    at = _click(_open(guided_db, "Layout"), "r0w0_dup")
    rows = _draft(at).rows
    assert [[w.title for w in r.widgets] for r in rows] == [["One", "Two"],
                                                            ["Three"]]
    assert at.session_state["dash_clip_widget"]["label"] == "One"


def test_a_copied_widget_pastes_onto_another_row(guided_db):
    at = _click(_click(_open(guided_db, "Layout"), "r0w0_dup"), "r1_wpaste")
    widgets = _draft(at).rows[1].widgets
    assert [w.title for w in widgets] == ["Three", "One"]
    assert widgets[1].spec["column"] == "qty"


def test_a_pasted_widget_shares_nothing_with_the_one_it_came_from(guided_db):
    at = _click(_click(_open(guided_db, "Layout"), "r0w0_dup"), "r1_wpaste")
    original = _draft(at).rows[0].widgets[0]
    pasted = _draft(at).rows[1].widgets[1]
    assert pasted.spec == original.spec and pasted.spec is not original.spec


def test_a_copied_widget_stays_on_the_clipboard_for_a_second_paste(guided_db):
    at = _click(_open(guided_db, "Layout"), "r0w0_dup")
    at = _click(_click(at, "r1_wpaste"), "r1_wpaste")
    assert [w.title for w in _draft(at).rows[1].widgets] == ["Three", "One",
                                                             "One"]


def test_there_is_nothing_to_paste_until_something_is_copied(guided_db):
    assert "r1_wpaste" not in _keys(_open(guided_db, "Layout"))


def test_a_copied_row_pastes_at_the_gap_you_choose(guided_db):
    """'Below the one you copied' was the only place a row could land."""
    at = _click(_click(_open(guided_db, "Layout"), "r1_c"), "r0_rpaste")
    rows = _draft(at).rows
    assert [[w.title for w in r.widgets] for r in rows] == [
        ["Three"], ["One", "Two"], ["Three"]]
    assert rows[0].height_in == 2.0


def test_a_copied_row_can_also_land_at_the_bottom(guided_db):
    at = _click(_click(_open(guided_db, "Layout"), "r0_c"), "rend_rpaste")
    rows = _draft(at).rows
    assert [[w.title for w in r.widgets] for r in rows] == [
        ["One", "Two"], ["Three"], ["One", "Two"]]


def test_a_pasted_row_shares_no_widget_with_the_row_it_came_from(guided_db):
    at = _click(_click(_open(guided_db, "Layout"), "r0_c"), "rend_rpaste")
    original, pasted = _draft(at).rows[0], _draft(at).rows[2]
    assert pasted.widgets[0].spec == original.widgets[0].spec
    assert pasted.widgets[0].spec is not original.widgets[0].spec


def test_the_clipboard_can_be_put_down(guided_db):
    at = _click(_click(_open(guided_db, "Layout"), "r0_c"), "dash_clip_clear")
    assert "dash_clip_row" not in at.session_state
    assert "r0_rpaste" not in _keys(at)


def test_a_row_can_cross_the_whole_layout_in_one_move(tmp_path):
    dash = _guided_dashboard()
    dash.rows.append(Row(widgets=[Widget(type="kpi", dataset="d", title="Four")],
                         height_in=1.0))
    at = _set_position(_open(_store(tmp_path, dash, "four.db"), "Layout"),
                       "r2_pos", 1)
    assert [[w.title for w in r.widgets] for r in _draft(at).rows] == [
        ["Four"], ["One", "Two"], ["Three"]]


# --- a picker with nothing in it can say why --------------------------------

def test_a_dataset_nobody_has_run_offers_a_way_to_find_its_columns(tmp_path):
    """A raw query's shape is only knowable by running it, and the Layout
    section is where not knowing it costs something."""
    dash = Dashboard(
        id=1, name="Unknown",
        datasets=[Dataset(name="d", env="orders", mode="raw",
                          raw_qsql="select from target")],
        rows=[Row(widgets=[Widget(type="table", dataset="d", title="Rows")])])
    at = _open(_store(tmp_path, dash, "unknown.db"), "Layout")
    assert "dash_learn_cols" in _keys(at)


def test_a_dataset_whose_columns_are_known_says_nothing(table_db):
    assert "dash_learn_cols" not in _keys(_open(table_db, "Layout"))


def _unknown_columns_dashboard() -> Dashboard:
    return Dashboard(
        id=1, name="Unknown",
        datasets=[Dataset(name="d", env="orders", mode="raw",
                          raw_qsql="select from target")],
        rows=[Row(widgets=[Widget(type="table", dataset="d", title="Rows")])])


def test_looking_for_columns_teaches_the_pickers_what_the_query_returns(tmp_path):
    at = _click(_open(_store(tmp_path, _unknown_columns_dashboard(), "learn.db"),
                      "Layout"), "dash_learn_cols")
    assert at.session_state["dash_learned_cols"]["d"]
    # Answered, so the notice that offered it has nothing left to say.
    assert "dash_learn_cols" not in _keys(at)


def test_looking_for_columns_against_a_dead_server_says_why(tmp_path):
    """A run that cannot reach anything must not leave the button looking like
    it did nothing — the reason is kept and shown under it."""
    path = str(tmp_path / "dead.db")
    s = Storage(path)
    s.init_db()
    s.add_connection(Connection(id=None, name="rdb", host="127.0.0.1", port=1,
                                kind="realtime", env="orders"))
    s.add_dashboard(_unknown_columns_dashboard())
    at = _click(_open(path, "Layout"), "dash_learn_cols")
    assert at.session_state["dash_learn_failed"]["d"]


def test_deleting_a_transform_leaves_the_widgets_alone(raw_db):
    before = copy.deepcopy(dashboard_to_dict(_raw_dashboard())["rows"])
    at = _click(_open(raw_db), "ds0_tx_3")
    _assert_nothing_lost(before, dashboard_to_dict(_draft(at))["rows"], "rows")
