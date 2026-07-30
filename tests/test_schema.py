# tests/test_schema.py
import pandas as pd
from kdbmonitor.core.schema import introspect
from kdbmonitor.core.dashboard_models import (
    ColumnSpec, Dashboard, Dataset, FileShape, NamedCell, Parameter,
    dashboard_from_dict, dashboard_to_dict,
)

from tests.test_client import pykx_client


class ScriptedClient:
    def __init__(self, mapping):
        self.mapping = mapping
    def query(self, q):
        return self.mapping[q]


def test_introspect_builds_table_column_map():
    client = ScriptedClient({
        "tables[]": pd.DataFrame({"t": ["target", "QATT"]}),
        "cols `target": pd.DataFrame({"c": ["sym", "orderId"]}),
        "cols `QATT": pd.DataFrame({"c": ["sym", "bid", "ask"]}),
    })
    schema = introspect(client)
    assert schema == {"target": ["sym", "orderId"], "QATT": ["sym", "bid", "ask"]}


class SeriesClient:
    """Mimics pykx: q vectors come back as 1-D pandas Series, not DataFrames."""
    def __init__(self, mapping):
        self.mapping = mapping
    def query(self, q):
        return pd.Series(self.mapping[q])


def test_introspect_handles_series_results():
    client = SeriesClient({
        "tables[]": ["target", "QATT"],
        "cols `target": ["sym", "orderId"],
        "cols `QATT": ["sym", "bid", "ask"],
    })
    schema = introspect(client)
    assert schema == {"target": ["sym", "orderId"], "QATT": ["sym", "bid", "ask"]}


def test_introspect_runs_through_the_client_a_real_server_uses():
    """The client above skips everything PyKxClient.query does on the way back,
    including the kdb-null scrub — which iterated columns a q vector has none
    of, so Introspect failed on a live server while this file stayed green."""
    client = pykx_client({
        "tables[]": pd.Series(["target", "QATT"]),
        "cols `target": pd.Series(["sym", "orderId"]),
        "cols `QATT": pd.Series(["sym", "bid", "ask"]),
    })
    schema = introspect(client)
    assert schema == {"target": ["sym", "orderId"], "QATT": ["sym", "bid", "ask"]}


def test_a_file_dataset_survives_a_round_trip():
    shape = FileShape(
        header_axis="column", header_row=2, first_col=1, data_start=3,
        null_markers=["", "n/a"],
        columns=[ColumnSpec(name="qty", type="number", allow_null=False)],
        cells=[NamedCell(name="Report date", row=0, col=1, type="date")])
    d = Dashboard(id=1, name="Orders", source="file", datasets=[
        Dataset(name="orders", env="", source="file", shape=shape,
                file_label="your orders export")])

    back = dashboard_from_dict(dashboard_to_dict(d))

    assert back.source == "file"
    ds = back.datasets[0]
    assert ds.source == "file" and ds.file_label == "your orders export"
    assert ds.shape.header_axis == "column"
    assert (ds.shape.header_row, ds.shape.first_col, ds.shape.data_start) == (2, 1, 3)
    assert ds.shape.null_markers == ["", "n/a"]
    assert ds.shape.columns[0] == ColumnSpec(name="qty", type="number",
                                             allow_null=False)
    assert ds.shape.cells[0] == NamedCell(name="Report date", row=0, col=1,
                                          type="date")


def test_a_dashboard_saved_before_file_sources_reads_back_as_kdb():
    back = dashboard_from_dict({"name": "Old", "rows": [],
                                "datasets": [{"name": "d", "env": "prod"}]})
    assert back.source == "kdb"
    assert back.datasets[0].source == "kdb"
    assert back.datasets[0].shape is None


def test_a_shape_left_off_a_file_dataset_reads_back_as_none():
    back = dashboard_from_dict({"name": "F", "source": "file", "rows": [],
                                "datasets": [{"name": "d", "source": "file"}]})
    assert back.datasets[0].shape is None
    assert back.datasets[0].file_label == ""


def test_a_hand_edited_bundle_cannot_break_reading_a_shape():
    """An import file can be edited by hand; a bad number in one costs that
    field its value, not the whole import its error message."""
    back = dashboard_from_dict({"name": "X", "rows": [], "datasets": [
        {"name": "d", "source": "file",
         "shape": {"header_row": "two", "first_col": None, "data_start": 1.9}}]})
    shape = back.datasets[0].shape
    assert (shape.header_row, shape.first_col, shape.data_start) == (0, 0, 1)


def test_a_shape_whose_columns_are_not_a_list_reads_as_no_columns():
    back = dashboard_from_dict({"name": "X", "rows": [], "datasets": [
        {"name": "d", "source": "file",
         "shape": {"columns": "oops", "cells": 7}}]})
    assert back.datasets[0].shape.columns == []
    assert back.datasets[0].shape.cells == []


def test_null_markers_given_as_a_string_do_not_become_single_characters():
    back = dashboard_from_dict({"name": "X", "rows": [], "datasets": [
        {"name": "d", "source": "file", "shape": {"null_markers": "N/A"}}]})
    assert back.datasets[0].shape.null_markers != ["N", "/", "A"]


def test_a_stored_shape_does_not_alias_the_dict_it_was_read_from():
    """Reading must not hand back a list the caller still holds: editing the
    dashboard would then write into the bundle it was imported from."""
    raw = {"name": "X", "rows": [], "datasets": [
        {"name": "d", "source": "file", "shape": {"null_markers": ["", "X"]}}]}
    shape = dashboard_from_dict(raw).datasets[0].shape

    shape.null_markers.append("MUTATED")

    assert raw["datasets"][0]["shape"]["null_markers"] == ["", "X"]


def test_parameters_survive_a_round_trip():
    d = Dashboard(id=1, name="VP", parameters=[
        Parameter(name="instrument", label="Instrument", kind="column",
                  dataset="profile", column="sym", default="A"),
        Parameter(name="mode", kind="choice", choices=["local", "source"],
                  default="local")])
    back = dashboard_from_dict(dashboard_to_dict(d))
    assert [p.name for p in back.parameters] == ["instrument", "mode"]
    assert back.parameters[0].label == "Instrument"
    assert back.parameters[0].dataset == "profile"
    assert back.parameters[0].column == "sym"
    assert back.parameters[1].choices == ["local", "source"]
    assert back.parameters[1].default == "local"


def test_a_dashboard_saved_before_parameters_reads_back_with_none():
    assert dashboard_from_dict({"name": "Old", "rows": []}).parameters == []


def test_a_parameter_whose_choices_are_not_a_list_reads_back_empty():
    """A bundle can be hand-edited; reading it must not raise."""
    back = dashboard_from_dict({"name": "X", "rows": [], "parameters": [
        {"name": "p", "choices": "oops"}]})
    assert back.parameters[0].choices == []


def test_parameters_that_are_not_a_list_read_back_as_none():
    assert dashboard_from_dict({"name": "X", "rows": [],
                                "parameters": "oops"}).parameters == []


def test_a_parameter_entry_that_is_not_a_dict_is_skipped():
    back = dashboard_from_dict({"name": "X", "rows": [],
                                "parameters": [{"name": "a"}, "junk", None]})
    assert [p.name for p in back.parameters] == ["a"]


def test_a_non_string_default_reads_back_as_text():
    """Substitution is textual, so the stored default is text too."""
    back = dashboard_from_dict({"name": "X", "rows": [],
                                "parameters": [{"name": "n", "default": 10}]})
    assert back.parameters[0].default == "10"


def test_choices_are_read_back_as_text_whatever_they_were_stored_as():
    back = dashboard_from_dict({"name": "X", "rows": [], "parameters": [
        {"name": "n", "choices": [1, 2.5, "three"]}]})
    assert back.parameters[0].choices == ["1", "2.5", "three"]


def test_a_parameter_with_no_label_falls_back_to_its_name_at_use_time():
    """The model stores what was given; the control decides what to show."""
    p = Parameter(name="instrument")
    assert p.label == ""


def test_two_dashboards_do_not_share_one_parameter_list():
    a, b = Dashboard(id=1, name="A"), Dashboard(id=2, name="B")
    a.parameters.append(Parameter(name="p"))
    assert b.parameters == []


def test_a_parameter_list_read_back_is_not_the_dict_it_came_from():
    raw = {"name": "X", "rows": [], "parameters": [
        {"name": "p", "choices": ["a"]}]}
    back = dashboard_from_dict(raw)
    back.parameters[0].choices.append("MUTATED")
    assert raw["parameters"][0]["choices"] == ["a"]
