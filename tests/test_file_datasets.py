from datetime import date

import pandas as pd

from kdbmonitor.core.dashboard_models import (
    ColumnSpec, Dashboard, Dataset, FileShape, Row, Transform, Widget,
)
from kdbmonitor.core.dataset import run_datasets, trace_datasets

TODAY = date(2026, 7, 30)


def _frame() -> pd.DataFrame:
    return pd.DataFrame({"sym": ["0005.HK", "7203.JP", "0005.HK"],
                         "qty": [10.0, 20.0, 30.0]})


def _shape() -> FileShape:
    return FileShape(columns=[ColumnSpec(name="sym"),
                              ColumnSpec(name="qty", type="number")])


def _dash(transforms=None, max_rows=5000) -> Dashboard:
    return Dashboard(id=1, name="Orders", source="file", datasets=[
        Dataset(name="orders", env="", source="file", shape=_shape(),
                file_label="your orders export",
                transforms=transforms or [], max_rows=max_rows)])


def test_an_uploaded_frame_becomes_the_dataset_result():
    results = run_datasets(_dash(), None, None, TODAY,
                           uploads={"orders": _frame()})
    out = results["orders"]
    assert out.error is None
    assert out.df["qty"].tolist() == [10.0, 20.0, 30.0]
    assert out.row_count == 3


def test_transforms_apply_to_a_file_frame_exactly_as_to_a_query():
    dash = _dash(transforms=[Transform(kind="groupby", params={
        "keys": ["sym"], "aggs": [{"column": "qty", "func": "sum",
                                   "as": "total"}]})])
    out = run_datasets(dash, None, None, TODAY, uploads={"orders": _frame()})
    frame = out["orders"].df.set_index("sym")
    assert frame.loc["0005.HK", "total"] == 40.0


def test_max_rows_caps_a_file_frame_and_says_it_did():
    out = run_datasets(_dash(max_rows=2), None, None, TODAY,
                       uploads={"orders": _frame()})["orders"]
    assert len(out.df) == 2 and out.row_count == 3 and out.truncated


def test_no_upload_yet_is_a_waiting_state_not_a_failure():
    out = run_datasets(_dash(), None, None, TODAY, uploads={})["orders"]
    assert out.df is None
    assert out.waiting is True
    assert "your orders export" in out.error


def test_no_uploads_argument_at_all_still_waits_rather_than_crashing():
    out = run_datasets(_dash(), None, None, TODAY)["orders"]
    assert out.waiting is True


def test_a_file_dataset_with_a_frame_is_never_marked_waiting():
    """The flag has to mean something, so it must not be on by default."""
    out = run_datasets(_dash(), None, None, TODAY,
                       uploads={"orders": _frame()})["orders"]
    assert out.waiting is False


def test_a_waiting_dataset_names_the_file_generically_when_unlabelled():
    dash = _dash()
    dash.datasets[0].file_label = ""
    out = run_datasets(dash, None, None, TODAY, uploads={})["orders"]
    assert out.waiting is True and out.error


def test_the_editor_can_step_through_a_file_dataset_transform_by_transform():
    dash = _dash(transforms=[Transform(kind="sort", params={
        "columns": ["qty"], "ascending": False})])
    trace = trace_datasets(dash, None, None, TODAY,
                           uploads={"orders": _frame()})["orders"]
    assert [s.kind for s in trace.steps] == ["query", "sort"]
    assert trace.df["qty"].tolist() == [30.0, 20.0, 10.0]


def test_a_broken_transform_on_a_file_frame_degrades_one_panel():
    dash = _dash(transforms=[Transform(kind="sort",
                                       params={"columns": ["nope"]})])
    out = run_datasets(dash, None, None, TODAY,
                       uploads={"orders": _frame()})["orders"]
    assert out.df is None and "nope" in out.error


def test_a_file_dataset_describes_what_it_reads_where_a_query_would_be():
    """The editor shows this where it shows a query, so it must say something."""
    out = run_datasets(_dash(), None, None, TODAY,
                       uploads={"orders": _frame()})["orders"]
    assert out.qsql and "file" in out.qsql.lower()


def test_a_file_dataset_with_no_shape_still_reports_rather_than_raising():
    dash = _dash()
    dash.datasets[0].shape = None
    out = run_datasets(dash, None, None, TODAY, uploads={})["orders"]
    assert out.waiting is True and out.qsql


def test_an_uploaded_frame_is_not_mutated_by_the_pipeline():
    """The frame is held in session state and reused across reruns; a transform
    writing through it would corrupt what the next rerun starts from."""
    frame = _frame()
    dash = _dash(transforms=[Transform(kind="derive", params={
        "column": "double", "kind": "arithmetic", "expr": "qty * 2"})])
    run_datasets(dash, None, None, TODAY, uploads={"orders": frame})
    assert list(frame.columns) == ["sym", "qty"]


def test_a_file_frame_is_fed_forward_so_a_later_dataset_can_reference_it():
    """outputs feeds {{name.column}} references; a file frame must join in."""
    dash = _dash()
    out = run_datasets(dash, None, None, TODAY, uploads={"orders": _frame()})
    assert out["orders"].df is not None


def test_a_kdb_dataset_is_never_waiting_whatever_the_server_said():
    """`waiting` decides whether the reader is shown an instruction or a fault,
    so it is settled by the dataset's source, not by the wording of an error.
    A q-side message that happened to begin "waiting for" would otherwise ask
    somebody to upload a file to a dashboard that has no upload box."""
    from kdbmonitor.core.dataset import run_dataset
    from kdbmonitor.core.timectx import ResolvedTime

    class _Store:
        def list_environments(self):
            return {}

    kdb = Dataset(name="q", env="nowhere", source="kdb")
    out = run_dataset(kdb, ResolvedTime("realtime", None, None), _Store(),
                      None, {})
    assert out.error and out.waiting is False


from datetime import datetime

from kdbmonitor.core.dashpdf import (
    LANDSCAPE, dashboard_to_pdf_bytes, page_count, report_plan,
)
from kdbmonitor.core.dataset import DatasetResult
from kdbmonitor.core.plotmodel import build_plot_model
from kdbmonitor.core.timectx import ResolvedTime

AS_OF = datetime(2026, 7, 30, 9, 15)
RT = ResolvedTime("realtime", None, None)


def _printable() -> Dashboard:
    dash = _dash()
    dash.rows = [Row(widgets=[Widget(type="table", dataset="orders",
                                     title="Working orders")]),
                 Row(widgets=[Widget(type="line", dataset="orders",
                                     title="Quantity",
                                     spec={"x": "sym", "y": "qty"})])]
    return dash


def test_a_file_dashboard_prints_a_pdf():
    dash = _printable()
    results = run_datasets(dash, None, None, TODAY,
                           uploads={"orders": _frame()})
    assert dashboard_to_pdf_bytes(dash, results, RT, AS_OF).startswith(b"%PDF")


def test_a_widget_cannot_tell_a_file_dataset_from_a_query():
    """The proof the pipeline is shared rather than parallel: the same frame
    reaches a widget identically whether it was queried or uploaded."""
    widget = Widget(type="table", dataset="orders", title="Orders")
    from_file = run_datasets(_dash(), None, None, TODAY,
                             uploads={"orders": _frame()})
    from_kdb = {"orders": DatasetResult("orders", _frame(), "select from o",
                                        None, row_count=3)}

    assert (build_plot_model(widget, from_file).rows
            == build_plot_model(widget, from_kdb).rows)


def test_a_dashboard_waiting_for_a_file_still_prints_rather_than_crashing():
    dash = _printable()
    results = run_datasets(dash, None, None, TODAY, uploads={})
    assert dashboard_to_pdf_bytes(dash, results, RT, AS_OF).startswith(b"%PDF")
    assert page_count(dash, results) >= 1


def test_a_file_dashboard_with_a_wide_table_still_turns_the_page():
    """Orientation is decided from the data, and uploaded data is data."""
    wide_cols = [ColumnSpec(name=n) for n in
                 ("sym", "side", "orderId", "qty", "filledQty", "avgPrice",
                  "limitPrice", "venue", "trader", "status", "startTime",
                  "endTime")]
    values = ["RELIANCE.IN", "BUY", "ORD-00012345", "125000", "118400",
              "1284.55", "1290.00", "NSE-MAIN", "jdoe", "PARTIAL",
              "09:15:03.221", "15:29:58.004"]
    wide = pd.DataFrame([dict(zip([c.name for c in wide_cols], values))
                         for _ in range(6)])
    dash = Dashboard(id=1, name="Wide", source="file", datasets=[
        Dataset(name="orders", env="", source="file",
                shape=FileShape(columns=wide_cols))],
        rows=[Row(height_in=3.0, widgets=[Widget(type="table",
                                                 dataset="orders")])])
    results = run_datasets(dash, None, None, TODAY, uploads={"orders": wide})
    sheet, _ = report_plan(dash, results)
    assert sheet is LANDSCAPE


def test_a_transform_pipeline_over_an_upload_reaches_the_printed_page():
    """End to end: uploaded frame, grouped, charted, printed."""
    dash = _dash(transforms=[Transform(kind="groupby", params={
        "keys": ["sym"], "aggs": [{"column": "qty", "func": "sum",
                                   "as": "total"}]})])
    dash.rows = [Row(widgets=[Widget(type="bar", dataset="orders",
                                     title="By symbol",
                                     spec={"x": "sym", "y": "total"})])]
    results = run_datasets(dash, None, None, TODAY,
                           uploads={"orders": _frame()})
    assert results["orders"].df["total"].sum() == 60.0
    assert dashboard_to_pdf_bytes(dash, results, RT, AS_OF).startswith(b"%PDF")


def test_a_file_dashboard_round_trips_through_an_export_bundle():
    """A bundle carries the shape and no data, and comes back runnable."""
    from kdbmonitor.core.portability import (
        export_dashboards_json, import_dashboards_json,
    )
    raw = export_dashboards_json([_printable()])
    assert "0005.HK" not in raw and "7203.JP" not in raw   # no data travelled

    back = import_dashboards_json(raw)[0]
    assert back.source == "file"
    assert [c.name for c in back.datasets[0].shape.columns] == ["sym", "qty"]

    results = run_datasets(back, None, None, TODAY,
                           uploads={"orders": _frame()})
    assert results["orders"].df["qty"].tolist() == [10.0, 20.0, 30.0]
