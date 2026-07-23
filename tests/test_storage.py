# tests/test_storage.py
from kdbmonitor.core.storage import Storage
from kdbmonitor.core.models import Connection


def test_connection_crud():
    store = Storage(":memory:")
    store.init_db()

    cid = store.add_connection(Connection(id=None, name="orders", host="h", port=5010))
    assert isinstance(cid, int)

    conns = store.list_connections()
    assert len(conns) == 1 and conns[0].name == "orders" and conns[0].id == cid

    got = store.get_connection(cid)
    got.schema = {"target": ["sym", "orderId"]}
    got.last_introspected_at = "2026-07-15T10:00:00"
    store.update_connection(got)
    assert store.get_connection(cid).schema == {"target": ["sym", "orderId"]}

    store.delete_connection(cid)
    assert store.list_connections() == []


def test_connection_name_unique():
    store = Storage(":memory:")
    store.init_db()
    store.add_connection(Connection(id=None, name="dup", host="h", port=1))
    import pytest
    with pytest.raises(Exception):
        store.add_connection(Connection(id=None, name="dup", host="h", port=2))


def _sample_alert():
    from kdbmonitor.core.models import (
        Alert, Step, Filter, TriggerCondition, RearmPolicy, Channels,
    )
    return Alert(
        id=None, name="a1", enabled=True, poll_interval_secs=30,
        steps=[Step(server="orders", table="target", mode="form",
                    filters=[Filter("sym", "in", ["AAPL"], "symbol")],
                    raw_qsql=None, output_name="step1")],
        trigger=TriggerCondition(type="has_rows"),
        channels=Channels(), rearm=RearmPolicy(),
    )


def test_alert_crud_and_toggle():
    store = Storage(":memory:")
    store.init_db()

    aid = store.add_alert(_sample_alert())
    got = store.get_alert(aid)
    assert got.name == "a1" and got.id == aid and got.enabled is True

    got.name = "a1-renamed"
    store.update_alert(got)
    assert store.get_alert(aid).name == "a1-renamed"

    store.set_alert_enabled(aid, False)
    assert store.get_alert(aid).enabled is False
    assert store.list_alerts()[0].enabled is False

    store.delete_alert(aid)
    assert store.list_alerts() == []


def _named_alert(name, group=""):
    a = _sample_alert()
    a.name = name
    a.group = group
    return a


def test_set_alert_group():
    store = Storage(":memory:")
    store.init_db()
    aid = store.add_alert(_named_alert("a1", group="Equities"))
    store.set_alert_group(aid, "Orders")
    assert store.get_alert(aid).group == "Orders"
    store.set_alert_group(aid, "")            # back to Ungrouped
    assert store.get_alert(aid).group == ""
    store.set_alert_group(aid, "  Fx  ")      # trimmed
    assert store.get_alert(aid).group == "Fx"
    store.set_alert_group(9999, "x")          # unknown id is a no-op, no crash


def test_rename_group():
    store = Storage(":memory:")
    store.init_db()
    store.add_alert(_named_alert("a1", group="Equities"))
    store.add_alert(_named_alert("a2", group="Equities"))
    store.add_alert(_named_alert("a3", group="Orders"))

    assert store.rename_group("Equities", "US Equities") == 2
    groups = {a.name: a.group for a in store.list_alerts()}
    assert groups == {"a1": "US Equities", "a2": "US Equities", "a3": "Orders"}

    # dissolve: blank target sends alerts to Ungrouped
    assert store.rename_group("Orders", "") == 1
    assert store.get_alert(store.list_alerts()[-1].id).group == ""

    # renaming onto an existing group merges
    assert store.rename_group("US Equities", "") == 2   # dissolve first
    assert store.rename_group("nope", "x") == 0          # no members -> 0
    assert store.rename_group("A", "A") == 0             # same name -> no-op


def test_rename_group_merges():
    store = Storage(":memory:")
    store.init_db()
    store.add_alert(_named_alert("a1", group="Fx"))
    store.add_alert(_named_alert("a2", group="Rates"))
    store.rename_group("Fx", "Rates")                    # merge Fx into Rates
    assert {a.group for a in store.list_alerts()} == {"Rates"}


def test_runs_record_and_latest():
    store = Storage(":memory:")
    store.init_db()
    aid = store.add_alert(_sample_alert())
    store.record_run(aid, ts="2026-07-15T10:00:00", status="armed", triggered=False,
                     notified=False, row_count=0, message="")
    store.record_run(aid, ts="2026-07-15T10:00:30", status="triggered", triggered=True,
                     notified=True, row_count=3, message="hit")
    latest = store.latest_run(aid)
    assert latest["status"] == "triggered" and latest["triggered"] == 1
    assert len(store.list_runs(aid)) == 2
    assert store.latest_run(9999) is None


def test_last_notified_at():
    store = Storage(":memory:")
    store.init_db()
    aid = store.add_alert(_sample_alert())
    assert store.last_notified_at(aid) is None
    store.record_run(aid, ts="2026-07-15T10:00:00", status="triggered", triggered=True,
                     notified=True, row_count=3, message="hit")
    store.record_run(aid, ts="2026-07-15T10:05:00", status="armed", triggered=False,
                     notified=False, row_count=0, message="")
    assert store.last_notified_at(aid) == "2026-07-15T10:00:00"


def test_settings_get_set():
    store = Storage(":memory:")
    store.init_db()
    assert store.get_setting("missing") is None
    assert store.get_setting("missing", "dflt") == "dflt"
    store.set_setting("smtp_host", "mail.example.com")
    assert store.get_setting("smtp_host") == "mail.example.com"
    store.set_setting("smtp_host", "mail2.example.com")
    assert store.get_setting("smtp_host") == "mail2.example.com"


def test_add_alert_duplicate_name_raises():
    import pytest
    store = Storage(":memory:")
    store.init_db()
    store.add_alert(_sample_alert())
    with pytest.raises(ValueError, match="already exists"):
        store.add_alert(_sample_alert())          # same name "a1"
    assert len(store.list_alerts()) == 1


def test_update_alert_keeping_same_name_ok():
    store = Storage(":memory:")
    store.init_db()
    aid = store.add_alert(_sample_alert())
    got = store.get_alert(aid)
    got.enabled = False                            # rename-less update must not trip the check
    store.update_alert(got)
    assert store.get_alert(aid).enabled is False


def test_update_alert_rename_onto_existing_raises():
    import pytest
    store = Storage(":memory:")
    store.init_db()
    a1 = _sample_alert()
    store.add_alert(a1)
    a2 = _sample_alert(); a2.name = "a2"
    aid2 = store.add_alert(a2)
    dup = store.get_alert(aid2); dup.name = "a1"    # collide with the first alert
    with pytest.raises(ValueError, match="already exists"):
        store.update_alert(dup)


def test_save_and_get_result_upserts_latest_per_day():
    import pandas as pd
    store = Storage(":memory:")
    store.init_db()
    aid = store.add_alert(_sample_alert())
    store.save_result(aid, "2026-07-20T10:00:00+00:00", pd.DataFrame([{"sym": "AAPL"}]))
    store.save_result(aid, "2026-07-20T11:30:00+00:00",
                      pd.DataFrame([{"sym": "AAPL"}, {"sym": "MSFT"}]))
    snap = store.get_result(aid, "2026-07-20")
    assert snap["row_count"] == 2                      # upsert kept the latest that day
    assert snap["ts"].startswith("2026-07-20T11:30")
    assert store.result_days(aid) == ["2026-07-20"]    # still one row for the day


def test_result_retention_prunes_days_beyond_20():
    import pandas as pd
    store = Storage(":memory:")
    store.init_db()
    aid = store.add_alert(_sample_alert())
    df = pd.DataFrame([{"sym": "AAPL"}])
    store.save_result(aid, "2026-06-01T10:00:00+00:00", df)
    store.save_result(aid, "2026-06-26T10:00:00+00:00", df)   # 25 days later -> prunes 06-01
    days = store.result_days(aid)
    assert "2026-06-01" not in days
    assert "2026-06-26" in days


def test_result_retention_setting_configurable_and_prunes_on_change():
    import pandas as pd
    store = Storage(":memory:")
    store.init_db()
    aid = store.add_alert(_sample_alert())
    df = pd.DataFrame([{"sym": "AAPL"}])
    assert store.get_result_retention_days() == 20        # default when unset
    for d in ("2026-07-01", "2026-07-10", "2026-07-15"):
        store.save_result(aid, f"{d}T10:00:00+00:00", df)
    store.set_result_retention_days(7)                    # anchor = latest day 07-15 -> keep >= 07-09
    days = store.result_days(aid)
    assert "2026-07-01" not in days and "2026-07-10" in days and "2026-07-15" in days


def test_huge_snapshot_is_capped_but_true_count_kept():
    import pandas as pd
    store = Storage(":memory:")
    store.init_db()
    aid = store.add_alert(_sample_alert())
    store.set_result_max_rows(10)
    big = pd.DataFrame({"sym": [f"S{i}" for i in range(5000)]})
    store.save_result(aid, "2026-07-20T10:00:00+00:00", big)
    snap = store.get_result(aid, "2026-07-20")
    assert snap["row_count"] == 5000                      # true size preserved
    assert snap["truncated"] is True
    assert len(snap["rows"]) == 10                        # only the cap was serialized


def test_daily_stats_counts_events_and_distinct_alerts():
    store = Storage(":memory:")
    store.init_db()
    a1 = store.add_alert(_named_alert("a1"))
    a2 = store.add_alert(_named_alert("a2"))
    # a1: two triggered (one notified) + one error; a2: one armed. All on 07-23.
    store.record_run(a1, "2026-07-23T10:00:00+00:00", "triggered", True, True, 3, "m")
    store.record_run(a1, "2026-07-23T10:01:00+00:00", "triggered", True, False, 4, "m")
    store.record_run(a1, "2026-07-23T10:02:00+00:00", "error", False, False, None, "m")
    store.record_run(a2, "2026-07-23T10:03:00+00:00", "armed", False, False, 0, "m")
    # a different day must not leak in
    store.record_run(a2, "2026-07-24T09:00:00+00:00", "triggered", True, True, 1, "m")

    d = store.daily_stats("2026-07-23")
    assert d["triggered_events"] == 2 and d["triggered_alerts"] == 1
    assert d["armed_events"] == 1 and d["armed_alerts"] == 1
    assert d["error_events"] == 1 and d["error_alerts"] == 1
    assert d["notifications"] == 1
    assert d["total_checks"] == 4
    # empty day -> all zeros, no crash
    assert store.daily_stats("2000-01-01")["triggered_events"] == 0


def test_daily_stats_history_newest_first():
    store = Storage(":memory:")
    store.init_db()
    aid = store.add_alert(_sample_alert())
    for day in ("2026-07-20", "2026-07-21", "2026-07-23"):
        store.record_run(aid, f"{day}T10:00:00+00:00", "armed", False, False, 0, "m")
    hist = store.daily_stats_history(days=2)
    assert [h["day"] for h in hist] == ["2026-07-23", "2026-07-21"]   # newest 2 only


def test_unseen_trigger_badge_lifecycle():
    store = Storage(":memory:")
    store.init_db()
    aid = store.add_alert(_sample_alert())
    assert store.has_unseen_trigger(aid) is False          # no trigger yet
    store.record_run(aid, "2026-07-23T10:00:00+00:00", "triggered", True, True, 1, "m")
    assert store.has_unseen_trigger(aid) is True           # fired, not yet viewed
    store.mark_triggers_seen(aid)                          # user pressed View
    assert store.has_unseen_trigger(aid) is False
    store.record_run(aid, "2026-07-23T10:05:00+00:00", "triggered", True, True, 2, "m")
    assert store.has_unseen_trigger(aid) is True           # a newer trigger re-flags it


def test_last_triggered_hash_returns_latest_triggered_run_hash():
    store = Storage(":memory:")
    store.init_db()
    aid = store.add_alert(_sample_alert())
    assert store.last_triggered_hash(aid) is None                 # none yet
    store.record_run(aid, "2026-07-21T10:00:00+00:00", "triggered", True, True, 1, "m", result_hash="A")
    store.record_run(aid, "2026-07-21T10:01:00+00:00", "armed", False, False, 0, "m", result_hash="Z")
    assert store.last_triggered_hash(aid) == "A"                  # ignores the later armed run
    store.record_run(aid, "2026-07-21T10:02:00+00:00", "triggered", True, True, 2, "m", result_hash="B")
    assert store.last_triggered_hash(aid) == "B"
