from kdbmonitor.core.dashboard_models import (
    ColumnSpec, Dashboard, Dataset, FileShape, Row, Widget,
)
from kdbmonitor.ui.dashboard_editor import _file_dataset_problems, validate


class _Store:
    """A store with no environments — a file dashboard must not care."""

    def list_environments(self):
        return {}


def _ds(**kw) -> Dataset:
    kw.setdefault("name", "orders")
    kw.setdefault("env", "")
    kw.setdefault("source", "file")
    return Dataset(**kw)


def _file_dash(datasets=None, rows=None) -> Dashboard:
    shape = FileShape(columns=[ColumnSpec(name="sym"), ColumnSpec(name="qty")])
    return Dashboard(
        id=1, name="Orders", source="file",
        datasets=datasets if datasets is not None else [_ds(shape=shape)],
        rows=rows if rows is not None else [
            Row(widgets=[Widget(type="table", dataset="orders")])])


# --- what makes a file dataset wrong -----------------------------------------

def test_a_file_dataset_with_no_shape_is_a_problem():
    assert any("shape" in p for p in _file_dataset_problems(_ds(shape=None)))


def test_a_shape_with_no_columns_is_a_problem():
    assert any("column" in p
               for p in _file_dataset_problems(_ds(shape=FileShape())))


def test_two_columns_with_one_name_is_a_problem():
    shape = FileShape(columns=[ColumnSpec(name="qty"), ColumnSpec(name="qty")])
    assert any("qty" in p for p in _file_dataset_problems(_ds(shape=shape)))


def test_a_column_with_no_name_is_a_problem():
    shape = FileShape(columns=[ColumnSpec(name="  ")])
    assert _file_dataset_problems(_ds(shape=shape))


def test_data_starting_on_the_header_line_is_a_problem():
    shape = FileShape(header_row=3, data_start=3,
                      columns=[ColumnSpec(name="qty")])
    assert any("header" in p for p in _file_dataset_problems(_ds(shape=shape)))


def test_data_starting_above_the_header_line_is_a_problem():
    shape = FileShape(header_row=3, data_start=1,
                      columns=[ColumnSpec(name="qty")])
    assert _file_dataset_problems(_ds(shape=shape))


def test_a_well_formed_file_dataset_has_no_problems():
    shape = FileShape(header_row=0, data_start=1,
                      columns=[ColumnSpec(name="sym"), ColumnSpec(name="qty")])
    assert _file_dataset_problems(_ds(shape=shape)) == []


# --- the KDB rules must not be applied to it ---------------------------------

def test_a_file_dataset_is_never_asked_for_an_environment():
    joined = " ".join(validate(_file_dash(), _Store()))
    assert "environment" not in joined


def test_a_file_dashboard_is_not_asked_about_periods():
    joined = " ".join(validate(_file_dash(), _Store())).lower()
    assert "period" not in joined and "real-time" not in joined


def test_a_well_formed_file_dashboard_validates_clean():
    assert validate(_file_dash(), _Store()) == []


# --- a dashboard and its datasets have to agree ------------------------------

def test_a_dataset_of_the_wrong_kind_for_its_dashboard_is_a_problem():
    """Switching a saved dashboard between sources leaves its old datasets
    behind. A dataset nobody is going to run is worth saying so about now,
    rather than showing an empty panel later."""
    dash = _file_dash(datasets=[_ds(source="kdb", env="prod")])
    assert any("uploaded file" in p for p in validate(dash, _Store()))


def test_a_file_dataset_on_a_kdb_dashboard_is_a_problem():
    shape = FileShape(columns=[ColumnSpec(name="sym")])
    dash = _file_dash(datasets=[_ds(source="file", shape=shape)])
    dash.source = "kdb"
    assert any("KDB" in p for p in validate(dash, _Store()))


# --- the shared checks still apply to both -----------------------------------

def test_a_file_dashboard_still_needs_a_name():
    dash = _file_dash()
    dash.name = ""
    assert any("no name" in p for p in validate(dash, _Store()))


def test_a_file_dashboard_still_needs_a_dataset():
    assert any("datasets" in p for p in validate(_file_dash(datasets=[]),
                                                 _Store()))


def test_a_file_dashboard_still_needs_a_widget():
    assert any("widgets" in p for p in validate(_file_dash(rows=[]), _Store()))


def test_duplicate_dataset_names_are_still_caught_on_a_file_dashboard():
    shape = FileShape(columns=[ColumnSpec(name="sym")])
    dash = _file_dash(datasets=[_ds(shape=shape), _ds(shape=shape)])
    assert any("Duplicate" in p for p in validate(dash, _Store()))


def test_a_transform_problem_is_still_caught_on_a_file_dataset():
    """Transforms apply to a file frame identically, so their checks must too."""
    from kdbmonitor.core.dashboard_models import Transform
    shape = FileShape(columns=[ColumnSpec(name="sym")])
    dash = _file_dash(datasets=[_ds(shape=shape, transforms=[
        Transform(kind="derive", params={"column": "", "kind": "arithmetic",
                                        "expr": ""})])])
    assert validate(dash, _Store())


def test_a_file_dataset_is_not_asked_about_q_it_no_longer_runs():
    """A dataset converted from a query keeps its raw_qsql and its extra
    connections, and the file editor shows neither. Complaining about them
    names a field its owner cannot reach, so the dashboard can never be saved."""
    shape = FileShape(columns=[ColumnSpec(name="sym")])
    stale = _ds(shape=shape, mode="raw", extra_connections=["GHOST"],
                raw_qsql="select from t where x={{conn:GHOST}}")
    joined = " ".join(validate(_file_dash(datasets=[stale]), _Store()))
    assert "GHOST" not in joined


def test_a_kdb_dataset_is_still_asked_about_its_connections():
    """The guard must not stop the check where it belongs."""
    kdb = Dataset(name="q", env="prod", source="kdb", mode="raw",
                  extra_connections=["GHOST"],
                  raw_qsql="select from t where date within "
                           "({{date_from}};{{date_to}})")
    dash = _file_dash(datasets=[kdb])
    dash.source = "kdb"
    assert any("GHOST" in p for p in validate(dash, _Store()))
