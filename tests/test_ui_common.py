from datetime import datetime, timezone

from kdbmonitor.core.models import TriggerCondition, Step, Filter
from kdbmonitor.ui.common import (
    is_due, secs_until_due, humanize_secs, condition_summary, step_summary,
)

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


def test_step_summary():
    raw = Step(server="kdp", table="QATT", mode="raw", raw_qsql="select from QATT")
    assert step_summary(raw) == "kdp · raw qSQL"
    form = Step(server="orders", table="target", mode="form",
               filters=[Filter("sym", "in", ["AAPL"], "symbol")])
    assert step_summary(form) == "orders · target where sym in ['AAPL']"
    neg = Step(server="orders", table="target", mode="form",
               filters=[Filter("sym", "in", ["AAPL"], "symbol", negated=True)])
    assert step_summary(neg) == "orders · target where not sym in ['AAPL']"
