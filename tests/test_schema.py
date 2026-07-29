# tests/test_schema.py
import pandas as pd
from kdbmonitor.core.schema import introspect
from kdbmonitor.core.dashboard_models import (
    ColumnSpec, Dashboard, Dataset, FileShape, NamedCell,
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
