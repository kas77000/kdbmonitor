from datetime import date

import pytest

from kdbmonitor.core.timectx import (
    ResolvedTime, date_clause, has_date_constraint, q_date, resolve,
    substitute_dates,
)

TODAY = date(2026, 7, 26)          # mid-year, so no month/year edge cases


def test_realtime_spec_resolves_without_dates():
    rt = resolve({"mode": "realtime"}, TODAY)
    assert rt.mode == "realtime"
    assert rt.start is None and rt.end is None


def test_absolute_range():
    rt = resolve({"mode": "historical",
                  "range": {"kind": "absolute",
                            "from": "2026-06-01", "to": "2026-06-30"}}, TODAY)
    assert (rt.start, rt.end) == (date(2026, 6, 1), date(2026, 6, 30))


def test_relative_range_is_inclusive_of_today():
    rt = resolve({"mode": "historical",
                  "range": {"kind": "relative", "n": 30, "unit": "days"}}, TODAY)
    assert rt.end == TODAY
    assert rt.start == date(2026, 6, 27)          # 30 days inclusive


def test_relative_weeks():
    rt = resolve({"mode": "historical",
                  "range": {"kind": "relative", "n": 2, "unit": "weeks"}}, TODAY)
    assert (rt.start, rt.end) == (date(2026, 7, 13), TODAY)


@pytest.mark.parametrize("name,start,end", [
    ("today",      date(2026, 7, 26), date(2026, 7, 26)),
    ("yesterday",  date(2026, 7, 25), date(2026, 7, 25)),
    ("last_7d",    date(2026, 7, 20), date(2026, 7, 26)),
    ("last_30d",   date(2026, 6, 27), date(2026, 7, 26)),
    ("mtd",        date(2026, 7, 1),  date(2026, 7, 26)),
    ("last_month", date(2026, 6, 1),  date(2026, 6, 30)),
    ("ytd",        date(2026, 1, 1),  date(2026, 7, 26)),
])
def test_presets(name, start, end):
    rt = resolve({"mode": "historical",
                  "range": {"kind": "preset", "name": name}}, TODAY)
    assert (rt.start, rt.end) == (start, end)


def test_last_month_handles_january():
    rt = resolve({"mode": "historical",
                  "range": {"kind": "preset", "name": "last_month"}},
                 date(2026, 1, 15))
    assert (rt.start, rt.end) == (date(2025, 12, 1), date(2025, 12, 31))


def test_unknown_preset_is_an_error():
    with pytest.raises(ValueError, match="unknown preset"):
        resolve({"mode": "historical",
                 "range": {"kind": "preset", "name": "nope"}}, TODAY)


def test_inverted_range_is_an_error():
    with pytest.raises(ValueError, match="starts after"):
        resolve({"mode": "historical",
                 "range": {"kind": "absolute",
                           "from": "2026-06-30", "to": "2026-06-01"}}, TODAY)


def test_q_date_literal():
    assert q_date(date(2026, 6, 1)) == "2026.06.01"


def test_date_clause():
    rt = ResolvedTime("historical", date(2026, 6, 1), date(2026, 6, 30))
    assert date_clause(rt) == "date within (2026.06.01;2026.06.30)"


def test_date_clause_is_empty_for_realtime():
    assert date_clause(ResolvedTime("realtime", None, None)) == ""


def test_substitute_dates_fills_placeholders():
    rt = ResolvedTime("historical", date(2026, 6, 1), date(2026, 6, 3))
    q = "select from t where date within ({{date_from}};{{date_to}})"
    assert substitute_dates(q, rt) == \
        "select from t where date within (2026.06.01;2026.06.03)"


def test_substitute_dates_expands_a_date_list():
    rt = ResolvedTime("historical", date(2026, 6, 1), date(2026, 6, 3))
    assert substitute_dates("select from t where date in {{date_list}}", rt) == \
        "select from t where date in 2026.06.01 2026.06.02 2026.06.03"


def test_substitute_dates_leaves_realtime_queries_alone():
    rt = ResolvedTime("realtime", None, None)
    q = "select from t where side=`sellshort"
    assert substitute_dates(q, rt) == q


def test_has_date_constraint():
    assert has_date_constraint("select from t where date within (a;b)")
    assert has_date_constraint("select from t where date={{date_from}}")
    assert not has_date_constraint("select from t where side=`sellshort")
    assert not has_date_constraint("select from t where update_time>0")


def test_label_describes_the_range():
    assert ResolvedTime("realtime", None, None).label == "Real-time"
    assert ResolvedTime("historical", date(2026, 6, 1), date(2026, 6, 30)).label \
        == "Historical · 2026-06-01 → 2026-06-30"
