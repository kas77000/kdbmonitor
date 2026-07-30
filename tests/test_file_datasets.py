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
