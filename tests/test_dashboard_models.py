from kdbmonitor.core.dashboard_models import (
    Dashboard, Dataset, Row, Transform, Widget,
    dashboard_from_json, dashboard_to_json,
)
from kdbmonitor.core.models import Filter


def _sample() -> Dashboard:
    return Dashboard(
        id=7, name="Short sell", description="by market", refresh_secs=15,
        time_context={"mode": "historical",
                      "range": {"kind": "preset", "name": "last_30d"}},
        datasets=[Dataset(
            name="orders", env="orders", mode="guided", table="target",
            filters=[Filter(column="side", op="=", value="sellshort",
                            value_type="symbol")],
            transforms=[Transform(kind="groupby", params={
                "keys": ["market"],
                "aggs": [{"column": "id_target", "func": "nunique",
                          "as": "n_orders"}]})],
        )],
        rows=[Row(height_in=0.9, widgets=[
            Widget(type="kpi", dataset="orders", title="Orders",
                   spec={"column": "n_orders", "agg": "sum"}, width=1.0)])],
    )


def test_dashboard_survives_a_json_roundtrip():
    d = _sample()
    assert dashboard_from_json(dashboard_to_json(d)) == d


def test_nested_types_are_rebuilt_not_left_as_dicts():
    back = dashboard_from_json(dashboard_to_json(_sample()))
    assert isinstance(back.datasets[0], Dataset)
    assert isinstance(back.datasets[0].filters[0], Filter)
    assert isinstance(back.datasets[0].transforms[0], Transform)
    assert isinstance(back.rows[0], Row)
    assert isinstance(back.rows[0].widgets[0], Widget)


def test_defaults_are_filled_for_a_minimal_payload():
    d = dashboard_from_json('{"id": null, "name": "Empty"}')
    assert d.refresh_secs == 15
    assert d.time_context == {"mode": "realtime"}
    assert d.datasets == []
    assert d.rows == []


def test_dataset_defaults():
    ds = Dataset(name="d", env="orders")
    assert ds.time_mode == "inherit"
    assert ds.mode == "guided"
    assert ds.max_rows == 5000
