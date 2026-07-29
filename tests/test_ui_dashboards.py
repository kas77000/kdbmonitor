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


def test_a_raw_dataset_uses_the_columns_a_run_actually_returned():
    """The only way to know a raw q's shape is to run it, so the editor offers
    what the last preview came back with."""
    ds = Dataset(name="d", env="orders", mode="raw", raw_qsql="select from t",
                 transforms=[Transform(kind="groupby", params={
                     "keys": ["market"], "aggs": [
                         {"column": "size", "func": "sum", "as": "order_qty"}]})])
    assert ed.dataset_columns(ds, _FakeConn(), ["sym", "size", "market"]) == \
        ["market", "order_qty"]


def test_filters_see_the_table_not_the_transformed_shape():
    """A filter becomes the query's where clause: it runs against the table
    before any transform, so a group-by must not narrow what it can name."""
    ds = Dataset(name="d", env="orders", table="target", transforms=[
        Transform(kind="groupby", params={"keys": ["sym"], "aggs": [
            {"column": "size", "func": "sum", "as": "order_qty"}]})])
    assert ed.table_columns(ds, _FakeConn()) == ["sym", "size", "side"]
    assert ed.table_columns(Dataset(name="d", env="orders", mode="raw"),
                            _FakeConn()) == []


def test_a_raw_dataset_still_knows_what_its_transforms_produce():
    """A group-by replaces the frame with its keys and aggregates whatever it
    was given, so those columns are known even when the query's are not."""
    ds = Dataset(name="d", env="orders", mode="raw", raw_qsql="select from t",
                 transforms=[
                     Transform(kind="derive", params={"column": "market"}),
                     Transform(kind="groupby", params={"keys": ["market"], "aggs": [
                         {"column": "size", "func": "sum", "as": "order_qty"}]}),
                     Transform(kind="derive", params={"column": "pct"})])
    assert ed.dataset_columns(ds, None) == ["market", "order_qty", "pct"]


def test_a_suffix_length_is_inferred_from_the_map_it_is_missing_from():
    assert ed.suffix_length({".HK": "Hong Kong", ".JP": "Japan"}) == 3
    assert ed.suffix_length({"HK": "Hong Kong", "JP": "Japan"}) == 2
    assert ed.suffix_length({".HK": "Hong Kong", ".HKG": "Hong Kong"}) == 0
    assert ed.suffix_length({}) == 0


def test_a_picker_always_offers_what_is_already_configured():
    """Otherwise a selectbox falls back to its first option and a multiselect
    drops the value — rewriting the dashboard just for being opened."""
    assert ed.with_stored(["sym", "size"], "market") == ["sym", "size", "market"]
    assert ed.with_stored(["sym"], ["market", "sym"]) == ["sym", "market"]
    assert ed.with_stored([], ["market"]) == ["market"]
    assert ed.with_stored(["sym"], "") == ["sym"]
    assert ed.with_stored(["sym"], None) == ["sym"]


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
    # 3 rows and a header come to 143px; a 1.9in slot is 182px, so it fits and
    # gets the 143 rather than being stretched to fill the slot.
    assert dashboards.table_height(3, dashboards.row_height_px(1.9)) == 143


def test_a_long_table_is_constrained_so_it_scrolls():
    assert dashboards.table_height(50, dashboards.row_height_px(1.9)) == 182


def test_an_empty_table_keeps_room_for_its_empty_state():
    assert dashboards.table_height(0, 500) == dashboards.table_height(1, 500)


def test_table_height_is_a_value_streamlit_accepts():
    """st.dataframe takes a positive int on every version; "content" and
    "stretch" need 1.46, which is above the floor in requirements.txt."""
    for n in (0, 3, 50):
        height = dashboards.table_height(n, 182)
        assert isinstance(height, int) and height > 0


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
    assert any("has no column set" in m for m in ed.validate(d, _store(tmp_path)))


def test_a_bar_without_axes_is_reported(tmp_path):
    d = _complete(tmp_path)
    d.rows[0].widgets[0] = Widget(type="bar", dataset="o", title="B", spec={})
    problems = ed.validate(d, _store(tmp_path))
    assert any("X axis" in m and "Y axis" in m for m in problems)


def test_a_text_widget_with_no_markdown_is_reported(tmp_path):
    d = _complete(tmp_path)
    d.rows[0].widgets[0] = Widget(type="text", dataset="o", spec={"markdown": ""})
    assert any("has no text set" in m for m in ed.validate(d, _store(tmp_path)))


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


# --- number format picker ---------------------------------------------------

def test_every_catalogued_format_is_valid():
    for label, spec in ed.NUMBER_FORMATS.items():
        assert ed.is_valid_format(spec), f"{label} -> {spec!r}"


def test_the_catalogue_labels_are_their_own_samples():
    """The label a user picks must be what they actually get."""
    for label, spec in ed.NUMBER_FORMATS.items():
        if label in ("No formatting",) or "(" in label:
            continue
        assert ed.format_sample(spec) == label


def test_format_label_roundtrips():
    assert ed.format_label_for(",.0f") == "1,235"
    assert ed.format_label_for("") == "No formatting"


def test_an_unknown_spec_is_offered_as_custom():
    assert ed.format_label_for(",.4f") == ed.CUSTOM_FORMAT


def test_a_typo_in_a_custom_format_is_reported_not_raised():
    assert ed.format_sample("qq") == "invalid format"
    assert not ed.is_valid_format("qq")


# --- dates and timestamps ---------------------------------------------------

def test_every_date_format_is_valid_and_shows_its_own_sample():
    for label, spec in ed.DATE_FORMATS.items():
        assert ed.is_valid_format(spec), f"{label} -> {spec!r}"
        assert ed.format_sample(spec) == label
        assert ed.format_label_for(spec) == label


def test_a_date_spec_is_told_apart_from_a_percentage():
    """'.1%' ends in a bare percent sign; '%d' is a strftime directive. Reading
    them the same way would sample a date format against 1234.567."""
    assert ed.is_date_format("%Y-%m-%d")
    assert ed.is_date_format("%H:%M")
    assert not ed.is_date_format(".1%")
    assert not ed.is_date_format(",.0f")
    assert not ed.is_date_format("")


def test_a_date_spec_is_sampled_against_a_date():
    assert ed.format_sample("%Y-%m-%d") == "2026-07-27"
    assert ed.format_sample(",.0f") == "1,235"


def test_an_unknown_date_directive_is_refused():
    """datetime hands an unknown directive straight back instead of raising, so
    a typo would otherwise look like a working format."""
    assert not ed.is_valid_format("%Q")
    assert ed.format_sample("%Q") == "invalid format"


def test_numbers_and_dates_share_one_catalogue():
    assert ed.VALUE_FORMATS[",.0f" and "1,235"] == ",.0f"
    assert ed.VALUE_FORMATS["09:30:15"] == "%H:%M:%S"
    assert ed.VALUE_FORMATS[ed.NO_FORMAT] == ""
    assert set(ed.NUMBER_FORMATS) | set(ed.DATE_FORMATS) < set(ed.VALUE_FORMATS)


def test_a_column_key_survives_whatever_kdb_calls_a_column():
    """Widget keys are per column name, so the name has to be key-safe."""
    assert ed._slug("stateStart") == "stateStart"
    assert ed._slug("last price") != ed._slug("last.price")   # no collisions
    assert " " not in ed._slug("last price")


def test_a_widget_with_an_unusable_format_is_reported(tmp_path):
    d = _complete(tmp_path)
    d.rows[0].widgets[0].spec["fmt"] = "not-a-format"
    assert any("format" in m.lower() for m in ed.validate(d, _store(tmp_path)))


# --- export / import --------------------------------------------------------

def test_export_filename_is_slugged():
    assert dashboards.dashboard_filename(Dashboard(id=1, name="Short sell")) == \
        "short_sell.json"
    assert dashboards.dashboard_filename(Dashboard(id=1, name="P&L / risk")) == \
        "p_l_risk.json"


def test_export_filename_survives_a_nameless_dashboard():
    assert dashboards.dashboard_filename(Dashboard(id=1, name="  ")) == \
        "dashboard.json"


def test_a_fresh_name_is_left_alone():
    assert dashboards.unique_dashboard_name("Fills", {"Orders"}) == "Fills"


def test_an_existing_name_is_suffixed_not_overwritten():
    assert dashboards.unique_dashboard_name("Orders", {"Orders"}) == \
        "Orders (imported)"


def test_repeated_imports_keep_counting():
    taken = {"Orders", "Orders (imported)"}
    assert dashboards.unique_dashboard_name("Orders", taken) == "Orders (imported 2)"
    taken.add("Orders (imported 2)")
    assert dashboards.unique_dashboard_name("Orders", taken) == "Orders (imported 3)"


def test_a_single_dashboard_export_roundtrips(tmp_path):
    from kdbmonitor.core.portability import (export_dashboards_json,
                                             import_dashboards_json)
    d = _complete(tmp_path)
    back = import_dashboards_json(export_dashboards_json([d]))
    assert len(back) == 1
    assert back[0].name == d.name
    assert back[0].id is None
    assert back[0].rows[0].widgets[0].spec == d.rows[0].widgets[0].spec


class _Upload:
    """Stand-in for Streamlit's UploadedFile — it keeps the same file_id across
    reruns, which is what made a single upload import repeatedly."""
    def __init__(self, file_id, name="d.json"):
        self.file_id = file_id
        self.name = name


def test_an_upload_is_pending_only_until_it_is_processed():
    up = _Upload("abc")
    processed = set()
    assert dashboards.pending_uploads([up], processed) == [up]
    processed.add("abc")
    assert dashboards.pending_uploads([up], processed) == []


def test_the_same_upload_across_reruns_imports_once():
    """Regression: st.file_uploader returns the same file every rerun, so
    importing whatever it holds duplicated the dashboard without bound."""
    up = _Upload("abc")
    processed, imports = set(), 0
    for _ in range(20):                     # 20 reruns, as a refresh would cause
        for u in dashboards.pending_uploads([up], processed):
            processed.add(u.file_id)
            imports += 1
    assert imports == 1


def test_distinct_files_each_import():
    a, b, c = _Upload("a"), _Upload("b"), _Upload("c")
    processed = set()
    assert dashboards.pending_uploads([a, b], processed) == [a, b]

    processed.update({"a", "b"})
    assert dashboards.pending_uploads([a, b, c], processed) == [c]


def test_no_uploads_is_not_an_error():
    assert dashboards.pending_uploads(None, set()) == []
    assert dashboards.pending_uploads([], {"a"}) == []


# --- market-data environments ----------------------------------------------

def _md_store(tmp_path) -> Storage:
    s = Storage(str(tmp_path / "md.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="refdata", host="h", port=9,
                                kind="marketdata", env="marketdata",
                                schema={"instrument": ["sym", "sector"]}))
    return s


def _md_dashboard() -> Dashboard:
    return Dashboard(
        id=1, name="Ref",
        time_context={"mode": "historical",
                      "range": {"kind": "preset", "name": "last_30d"}},
        datasets=[Dataset(name="ref", env="marketdata", table="instrument")],
        rows=[Row(widgets=[Widget(type="kpi", dataset="ref",
                                  spec={"column": "sym", "agg": "nunique"})])])


def test_a_marketdata_env_is_valid_on_a_historical_dashboard(tmp_path):
    """The period does not apply to reference data, so demanding a historical
    server for it would be wrong."""
    assert ed.validate(_md_dashboard(), _md_store(tmp_path)) == []


def test_a_realtime_only_env_on_a_historical_dashboard_is_still_reported(tmp_path):
    s = Storage(str(tmp_path / "rt.db"))
    s.init_db()
    s.add_connection(Connection(id=None, name="rdb", host="h", port=1,
                                kind="realtime", env="orders",
                                schema={"target": ["sym"]}))
    d = _md_dashboard()
    d.datasets[0].env = "orders"
    d.datasets[0].table = "target"
    d.rows[0].widgets[0].spec = {"column": "sym", "agg": "nunique"}
    assert any("no historical server" in m for m in ed.validate(d, s))


def test_column_pickers_work_for_a_marketdata_dataset(tmp_path):
    s = _md_store(tmp_path)
    ds = Dataset(name="ref", env="marketdata", table="instrument")
    assert ed.dataset_columns(ds, ed._connection_for(s, ds)) == ["sym", "sector"]
