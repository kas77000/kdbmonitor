from datetime import datetime, timezone

from kdbmonitor.core.models import (
    TriggerCondition, Step, Filter, Alert, Channels, RearmPolicy,
)
from kdbmonitor.ui.common import (
    is_due, secs_until_due, humanize_secs, condition_summary, step_summary,
    should_capture_result, group_label, sort_group_names, group_alerts, pluralize,
)


def test_pluralize():
    assert pluralize(1, "step") == "1 step"
    assert pluralize(0, "step") == "0 steps"
    assert pluralize(3, "row") == "3 rows"


def _alert(name, group=""):
    return Alert(id=None, name=name, enabled=True, poll_interval_secs=30,
                 steps=[Step(server="kdp", table="QATT", mode="form")],
                 trigger=TriggerCondition(type="has_rows"),
                 channels=Channels(), rearm=RearmPolicy(), group=group)


def test_group_label():
    assert group_label(_alert("a", "Equities")) == "Equities"
    assert group_label(_alert("a", "  ")) == "Ungrouped"       # blank -> Ungrouped
    assert group_label(_alert("a", "")) == "Ungrouped"


def test_sort_group_names_ungrouped_last():
    assert sort_group_names(["Fx", "Ungrouped", "Equities"]) == \
        ["Equities", "Fx", "Ungrouped"]
    assert sort_group_names(["Ungrouped"]) == ["Ungrouped"]
    assert sort_group_names(["b", "a"]) == ["a", "b"]


def test_group_alerts_buckets_and_order():
    alerts = [_alert("a1", "Fx"), _alert("a2"), _alert("a3", "Equities"),
              _alert("a4", "Fx")]
    grouped = group_alerts(alerts)
    assert [g for g, _ in grouped] == ["Equities", "Fx", "Ungrouped"]
    assert [a.name for a in dict(grouped)["Fx"]] == ["a1", "a4"]   # input order kept

NOW = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)


def test_is_due():
    assert is_due(None, 30, NOW) is True                       # never run -> due
    t_20s_ago = datetime(2026, 7, 15, 9, 59, 40, tzinfo=timezone.utc).isoformat()
    assert is_due(t_20s_ago, 30, NOW) is False                 # 20s < 30s
    t_40s_ago = datetime(2026, 7, 15, 9, 59, 20, tzinfo=timezone.utc).isoformat()
    assert is_due(t_40s_ago, 30, NOW) is True                  # 40s >= 30s


def test_secs_until_due():
    assert secs_until_due(None, 30, NOW) == 0
    t_20s_ago = datetime(2026, 7, 15, 9, 59, 40, tzinfo=timezone.utc).isoformat()
    assert secs_until_due(t_20s_ago, 30, NOW) == 10
    t_40s_ago = datetime(2026, 7, 15, 9, 59, 20, tzinfo=timezone.utc).isoformat()
    assert secs_until_due(t_40s_ago, 30, NOW) == 0


def test_humanize_secs():
    assert humanize_secs(5) == "5s"
    assert humanize_secs(60) == "1m"
    assert humanize_secs(90) == "1m 30s"
    assert humanize_secs(300) == "5m"


def test_condition_summary():
    assert condition_summary(TriggerCondition(type="no_rows")) == \
        "the final query returns no rows"
    assert condition_summary(TriggerCondition(type="row_count_gte", n=3)) == \
        "the final query returns at least 3 rows"
    assert condition_summary(
        TriggerCondition(type="any_row", column="bid", op=">", value=100)
    ) == "at least one row has bid > 100"
    assert condition_summary(
        TriggerCondition(type="aggregate", agg="max", column="bid", op=">", value=100)
    ) == "max(bid) > 100"


def test_should_capture_result():
    # never capture on a non-triggered check (armed/error keep prior data)
    assert should_capture_result("latest", False, False) is False
    assert should_capture_result("snapshot", False, True) is False
    # latest: capture on every triggered check
    assert should_capture_result("latest", True, False) is True
    assert should_capture_result("latest", True, True) is True
    # snapshot: only on the rising edge (was not triggered before)
    assert should_capture_result("snapshot", True, False) is True
    assert should_capture_result("snapshot", True, True) is False


def test_step_summary():
    raw = Step(server="kdp", table="QATT", mode="raw", raw_qsql="select from QATT")
    assert step_summary(raw) == "kdp · raw qSQL"
    form = Step(server="orders", table="target", mode="form",
               filters=[Filter("sym", "in", ["AAPL"], "symbol")])
    assert step_summary(form) == "orders · target where sym in ['AAPL']"
    neg = Step(server="orders", table="target", mode="form",
               filters=[Filter("sym", "in", ["AAPL"], "symbol", negated=True)])
    assert step_summary(neg) == "orders · target where not sym in ['AAPL']"
