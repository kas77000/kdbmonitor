from kdbmonitor.core.dashboard_models import Dashboard, Dataset, Row, Transform, Widget
from kdbmonitor.core.models import Connection
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui import dashboard_editor as ed
from kdbmonitor.ui import dashboards


# --- the view page ---------------------------------------------------------

def test_modules_import_without_a_running_streamlit_app():
    assert hasattr(dashboards, "render")
    assert hasattr(ed, "render")


def test_time_context_options_cover_realtime_and_the_presets():
    labels = list(dashboards.TIME_OPTIONS)
    assert labels[0] == "Real-time"
    assert "Last 30 days" in labels
    assert "Custom range…" in labels


def test_time_option_roundtrips_to_a_spec():
    spec = dashboards.spec_for_option("Last 30 days")
    assert spec == {"mode": "historical",
                    "range": {"kind": "preset", "name": "last_30d"}}
    assert dashboards.option_for_spec(spec) == "Last 30 days"


def test_realtime_spec_roundtrips():
    assert dashboards.spec_for_option("Real-time") == {"mode": "realtime"}
    assert dashboards.option_for_spec({"mode": "realtime"}) == "Real-time"


def test_absolute_spec_maps_to_the_custom_option():
    spec = {"mode": "historical",
            "range": {"kind": "absolute", "from": "2026-06-01", "to": "2026-06-30"}}
    assert dashboards.option_for_spec(spec) == "Custom range…"


def test_native_kinds_are_the_ones_plotly_does_not_draw():
    from kdbmonitor.core.render_plotly import CHART_KINDS
    assert dashboards.NATIVE_KINDS.isdisjoint(CHART_KINDS)
    assert dashboards.NATIVE_KINDS == {"kpi", "table", "text", "error"}


def test_every_widget_type_is_rendered_by_exactly_one_path():
    from kdbmonitor.core.render_plotly import CHART_KINDS
    covered = dashboards.NATIVE_KINDS | CHART_KINDS
    assert set(ed.WIDGET_TYPES) <= covered


def test_row_height_converts_inches_to_pixels():
    assert dashboards.row_height_px(2.0) == 192          # 2in at 96 dpi
    assert dashboards.row_height_px(0.9) == 86


# --- the editor ------------------------------------------------------------

def _store(tmp_path) -> Storage:
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="rdb", host="h", port=1,
                                kind="realtime", env="orders",
                                schema={"target": ["sym", "size"]}))
    return s


class _FakeConn:
    schema = {"target": ["sym", "size", "side"]}


def test_columns_for_a_guided_dataset_come_from_the_connection_schema():
    ds = Dataset(name="d", env="orders", table="target")
    assert ed.dataset_columns(ds, _FakeConn()) == ["sym", "size", "side"]


def test_columns_include_transform_outputs():
    ds = Dataset(name="d", env="orders", table="target", transforms=[
        Transform(kind="derive", params={"column": "market"}),
        Transform(kind="groupby", params={"keys": ["market"], "aggs": [
            {"column": "size", "func": "sum", "as": "order_qty"}]}),
    ])
    cols = ed.dataset_columns(ds, _FakeConn())
    assert "market" in cols and "order_qty" in cols


def test_groupby_output_replaces_the_upstream_columns():
    ds = Dataset(name="d", env="orders", table="target", transforms=[
        Transform(kind="groupby", params={"keys": ["sym"], "aggs": [
            {"column": "size", "func": "sum", "as": "order_qty"}]})])
    assert ed.dataset_columns(ds, _FakeConn()) == ["sym", "order_qty"]


def test_rename_is_reflected_in_the_column_list():
    ds = Dataset(name="d", env="orders", table="target", transforms=[
        Transform(kind="rename", params={"mapping": {"size": "order_qty"}})])
    assert ed.dataset_columns(ds, _FakeConn()) == ["sym", "order_qty", "side"]


def test_raw_datasets_have_no_predictable_columns():
    ds = Dataset(name="d", env="orders", mode="raw", raw_qsql="select from t")
    assert ed.dataset_columns(ds, _FakeConn()) == []


def test_unique_name_avoids_collisions():
    assert ed.unique_name("orders", ["orders", "orders_2"]) == "orders_3"
    assert ed.unique_name("fills", ["orders"]) == "fills"


# --- save-time validation --------------------------------------------------

def test_a_valid_dashboard_has_no_complaints(tmp_path):
    d = Dashboard(id=1, name="D",
                  datasets=[Dataset(name="orders", env="orders", table="target")],
                  rows=[Row(widgets=[Widget(type="kpi", dataset="orders",
                                            spec={"column": "size", "agg": "sum"})])])
    assert ed.validate(d, _store(tmp_path)) == []


def test_widget_pointing_at_a_missing_dataset_is_reported(tmp_path):
    d = Dashboard(id=1, name="D", rows=[
        Row(widgets=[Widget(type="kpi", dataset="ghost")])])
    assert any("ghost" in m for m in ed.validate(d, _store(tmp_path)))


def test_historical_raw_dataset_without_a_date_is_reported(tmp_path):
    d = Dashboard(id=1, name="D",
                  time_context={"mode": "historical",
                                "range": {"kind": "preset", "name": "last_30d"}},
                  datasets=[Dataset(name="o", env="orders", mode="raw",
                                    raw_qsql="select from target")])
    assert any("date" in m for m in ed.validate(d, _store(tmp_path)))


def test_historical_guided_dataset_needs_no_date_of_its_own(tmp_path):
    s = _store(tmp_path)
    s.add_connection(Connection(id=None, name="hdb", host="h", port=2,
                                kind="historical", env="orders"))
    d = Dashboard(id=1, name="D",
                  time_context={"mode": "historical",
                                "range": {"kind": "preset", "name": "last_30d"}},
                  datasets=[Dataset(name="o", env="orders", table="target")])
    # Concern-specific: an incomplete draft has other problems (no widgets yet).
    assert not [m for m in ed.validate(d, s) if "date" in m]


def test_environment_without_a_historical_side_is_reported(tmp_path):
    d = Dashboard(id=1, name="D",
                  time_context={"mode": "historical",
                                "range": {"kind": "preset", "name": "last_30d"}},
                  datasets=[Dataset(name="o", env="orders", table="target")])
    assert any("no historical server" in m for m in ed.validate(d, _store(tmp_path)))


def test_unknown_environment_is_reported(tmp_path):
    d = Dashboard(id=1, name="D",
                  datasets=[Dataset(name="o", env="nowhere", table="target")])
    assert any("nowhere" in m for m in ed.validate(d, _store(tmp_path)))


def test_duplicate_dataset_names_are_reported(tmp_path):
    d = Dashboard(id=1, name="D", datasets=[
        Dataset(name="o", env="orders", table="target"),
        Dataset(name="o", env="orders", table="target")])
    assert any("duplicate" in m.lower() for m in ed.validate(d, _store(tmp_path)))


def test_forward_dataset_reference_is_reported(tmp_path):
    d = Dashboard(id=1, name="D", datasets=[
        Dataset(name="second", env="orders", mode="raw",
                raw_qsql="select from t where id in {{first.id}}"),
        Dataset(name="first", env="orders", table="target")])
    assert any("'first'" in m for m in ed.validate(d, _store(tmp_path)))


def test_a_backward_dataset_reference_is_fine(tmp_path):
    d = Dashboard(id=1, name="D", datasets=[
        Dataset(name="first", env="orders", table="target"),
        Dataset(name="second", env="orders", mode="raw",
                raw_qsql="select from t where id in {{first.id}}")])
    assert not [m for m in ed.validate(d, _store(tmp_path)) if "references" in m]


def test_a_row_may_not_hold_more_than_four_widgets(tmp_path):
    d = Dashboard(id=1, name="D",
                  datasets=[Dataset(name="o", env="orders", table="target")],
                  rows=[Row(widgets=[Widget(type="kpi", dataset="o")] * 5)])
    assert any("at most 4 widgets" in m for m in ed.validate(d, _store(tmp_path)))


def test_a_guided_dataset_without_a_table_is_reported(tmp_path):
    d = Dashboard(id=1, name="D",
                  datasets=[Dataset(name="o", env="orders", table="")])
    assert any("no table" in m for m in ed.validate(d, _store(tmp_path)))


def test_widget_spec_forms_exist_for_every_renderable_type():
    from kdbmonitor.core.plotmodel import _RESOLVERS
    assert set(ed.WIDGET_TYPES) == set(_RESOLVERS) | {"text"}


def test_a_short_table_is_not_padded_with_blank_rows():
    # 3 rows need ~143px; a 1.9in slot is 182px, so let it fit its content
    assert dashboards.table_height(3, dashboards.row_height_px(1.9)) == "content"


def test_a_long_table_is_constrained_so_it_scrolls():
    assert dashboards.table_height(50, dashboards.row_height_px(1.9)) == 182


def test_an_empty_table_is_not_constrained():
    assert dashboards.table_height(0, 500) == "content"


def test_table_height_is_a_value_streamlit_accepts():
    """st.dataframe rejects None; only a positive int, "stretch" or "content"."""
    from streamlit.elements.lib.layout_utils import validate_height
    for n in (0, 3, 50):
        validate_height(dashboards.table_height(n, 182), allow_content=True)


# --- unfilled inputs --------------------------------------------------------

def _complete(tmp_path):
    """A dashboard with nothing missing, to mutate one field at a time."""
    return Dashboard(
        id=1, name="D",
        datasets=[Dataset(name="o", env="orders", table="target")],
        rows=[Row(widgets=[Widget(type="kpi", dataset="o", title="Total",
                                  spec={"column": "size", "agg": "sum"})])])


def test_the_complete_fixture_really_is_complete(tmp_path):
    assert ed.validate(_complete(tmp_path), _store(tmp_path)) == []


def test_a_nameless_dashboard_is_reported(tmp_path):
    d = _complete(tmp_path)
    d.name = "  "
    assert any("no name" in m for m in ed.validate(d, _store(tmp_path)))


def test_a_dashboard_with_no_datasets_is_reported(tmp_path):
    d = Dashboard(id=1, name="D")
    assert any("No datasets yet" in m for m in ed.validate(d, _store(tmp_path)))


def test_a_dashboard_with_no_widgets_is_reported(tmp_path):
    d = _complete(tmp_path)
    d.rows = []
    assert any("No widgets yet" in m for m in ed.validate(d, _store(tmp_path)))


def test_an_empty_row_is_reported(tmp_path):
    d = _complete(tmp_path)
    d.rows.append(Row(widgets=[]))
    assert any("is empty" in m for m in ed.validate(d, _store(tmp_path)))


def test_a_raw_dataset_with_no_query_is_reported(tmp_path):
    d = _complete(tmp_path)
    d.datasets[0].mode = "raw"
    d.datasets[0].raw_qsql = ""
    assert any("query is empty" in m for m in ed.validate(d, _store(tmp_path)))


def test_a_filter_with_no_value_is_reported(tmp_path):
    from kdbmonitor.core.models import Filter
    d = _complete(tmp_path)
    d.datasets[0].filters = [Filter(column="side", op="=", value="",
                                    value_type="symbol")]
    assert any("no value entered" in m for m in ed.validate(d, _store(tmp_path)))


def test_a_kpi_without_a_column_is_reported(tmp_path):
    d = _complete(tmp_path)
    d.rows[0].widgets[0].spec = {"agg": "sum"}
    assert any("'column'" in m for m in ed.validate(d, _store(tmp_path)))


def test_a_bar_without_axes_is_reported(tmp_path):
    d = _complete(tmp_path)
    d.rows[0].widgets[0] = Widget(type="bar", dataset="o", title="B", spec={})
    problems = ed.validate(d, _store(tmp_path))
    assert any("'x'" in m and "'y'" in m for m in problems)


def test_a_text_widget_with_no_markdown_is_reported(tmp_path):
    d = _complete(tmp_path)
    d.rows[0].widgets[0] = Widget(type="text", dataset="o", spec={"markdown": ""})
    assert any("'markdown'" in m for m in ed.validate(d, _store(tmp_path)))


def test_a_widget_bound_to_a_vanished_column_is_reported(tmp_path):
    d = _complete(tmp_path)
    d.rows[0].widgets[0].spec["column"] = "gone"
    assert any("not produced by dataset" in m
               for m in ed.validate(d, _store(tmp_path)))


def test_a_derive_transform_with_no_expression_is_reported(tmp_path):
    d = _complete(tmp_path)
    d.datasets[0].transforms = [Transform(kind="derive", params={
        "column": "notional", "kind": "arithmetic", "expr": ""})]
    assert any("no expression" in m for m in ed.validate(d, _store(tmp_path)))


def test_a_groupby_with_no_keys_is_reported(tmp_path):
    d = _complete(tmp_path)
    d.datasets[0].transforms = [Transform(kind="groupby",
                                          params={"keys": [], "aggs": []})]
    problems = ed.validate(d, _store(tmp_path))
    assert any("nothing to group by" in m for m in problems)
    assert any("no aggregations" in m for m in problems)


def test_a_sort_with_no_columns_is_reported(tmp_path):
    d = _complete(tmp_path)
    d.datasets[0].transforms = [Transform(kind="sort", params={"columns": []})]
    assert any("no sort columns" in m for m in ed.validate(d, _store(tmp_path)))


def test_problem_messages_say_where_the_problem_is(tmp_path):
    d = _complete(tmp_path)
    d.datasets[0].transforms = [Transform(kind="sort", params={"columns": []})]
    assert all(m.startswith(("Dataset", "Row", "The dashboard", "No ", "Duplicate", "A "))
               for m in ed.validate(d, _store(tmp_path)))
