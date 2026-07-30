from datetime import datetime

import pandas as pd
import pytest

from kdbmonitor.core.dashboard_models import Transform
from kdbmonitor.core.summaries import transform_summary
from kdbmonitor.core.transform import apply_transforms
from kdbmonitor.core.zones import convert, day_offset, to_iana


# --- naming a zone -----------------------------------------------------------

def test_a_windows_display_name_resolves():
    """Exports name zones the way Windows does, not the way tzdata does."""
    assert to_iana("India Standard Time") == "Asia/Kolkata"
    assert to_iana("GMT Standard Time") == "Europe/London"
    assert to_iana("Tokyo Standard Time") == "Asia/Tokyo"


def test_an_iana_id_passes_through():
    assert to_iana("Asia/Kolkata") == "Asia/Kolkata"
    assert to_iana("Europe/Paris") == "Europe/Paris"


def test_a_bare_abbreviation_resolves():
    assert to_iana("IST") == "Asia/Kolkata"
    assert to_iana("JST") == "Asia/Tokyo"
    assert to_iana("UTC") == "UTC"


def test_a_literal_offset_resolves():
    for written in ("UTC+05:30", "+05:30", "UTC+0530"):
        assert to_iana(written) is not None


def test_matching_ignores_case_and_padding():
    assert to_iana("  india standard time  ") == "Asia/Kolkata"


def test_an_unknown_zone_says_what_it_was_given():
    with pytest.raises(ValueError, match="Sasquatch"):
        to_iana("Sasquatch Standard Time")


def test_an_empty_zone_is_refused_rather_than_guessed():
    with pytest.raises(ValueError):
        to_iana("")


# --- converting --------------------------------------------------------------

def _stamps(*texts) -> pd.Series:
    return pd.Series([pd.Timestamp(t) for t in texts])


def test_a_bucket_converts_between_zones():
    """09:15 in Mumbai is 05:45 in Paris."""
    out = convert(_stamps("2026-07-30 09:15:00"), "Asia/Kolkata",
                  "Europe/Paris")
    assert out.iloc[0].hour == 5 and out.iloc[0].minute == 45


def test_daylight_saving_is_computed_at_the_timestamp_not_assumed():
    """Paris is UTC+2 in July and UTC+1 in January; the same Mumbai time
    therefore lands an hour apart."""
    summer = convert(_stamps("2026-07-30 09:15:00"), "Asia/Kolkata",
                     "Europe/Paris").iloc[0]
    winter = convert(_stamps("2026-01-30 09:15:00"), "Asia/Kolkata",
                     "Europe/Paris").iloc[0]
    assert (summer.hour, summer.minute) == (5, 45)
    assert (winter.hour, winter.minute) == (4, 45)


def test_converting_to_the_same_zone_changes_nothing():
    out = convert(_stamps("2026-07-30 09:15:00"), "Asia/Kolkata",
                  "Asia/Kolkata")
    assert out.iloc[0].hour == 9


def test_a_windows_name_may_be_used_either_side():
    out = convert(_stamps("2026-07-30 09:15:00"), "India Standard Time",
                  "Romance Standard Time")
    assert out.iloc[0].hour == 5


def test_an_empty_series_converts_to_an_empty_series():
    assert len(convert(pd.Series([], dtype="datetime64[ns]"),
                       "UTC", "Asia/Tokyo")) == 0


def test_a_null_timestamp_stays_null():
    out = convert(pd.Series([pd.NaT, pd.Timestamp("2026-07-30 09:15")]),
                  "Asia/Kolkata", "Europe/Paris")
    assert pd.isna(out.iloc[0]) and not pd.isna(out.iloc[1])


def test_the_result_carries_no_zone_so_it_prints_as_a_local_time():
    """A tz-aware column prints its offset in every cell of the table."""
    out = convert(_stamps("2026-07-30 09:15:00"), "Asia/Kolkata",
                  "Europe/Paris")
    assert out.dt.tz is None


# --- crossing a day ----------------------------------------------------------

def test_a_bucket_landing_on_the_previous_day_is_marked():
    """09:15 in Mumbai is 23:45 the day before in New York."""
    before = _stamps("2026-07-30 09:15:00")
    after = convert(before, "Asia/Kolkata", "America/New_York")
    assert day_offset(before, after).iloc[0] == -1


def test_a_bucket_landing_on_the_next_day_is_marked():
    before = _stamps("2026-07-30 23:30:00")
    after = convert(before, "America/New_York", "Asia/Tokyo")
    assert day_offset(before, after).iloc[0] == 1


def test_a_bucket_staying_on_the_day_is_marked_zero():
    before = _stamps("2026-07-30 09:15:00")
    after = convert(before, "Asia/Kolkata", "Europe/Paris")
    assert day_offset(before, after).iloc[0] == 0


# --- the timezone transform ---------------------------------------------------

def _session(*texts) -> pd.DataFrame:
    return pd.DataFrame({"Time": [pd.Timestamp(t) for t in texts]})


def test_a_fixed_zone_converts_the_whole_column():
    df = _session("2026-07-30 09:15:00", "2026-07-30 10:00:00")
    out = apply_transforms(df, [Transform(kind="timezone", params={
        "column": "Time", "from_zone": "Asia/Kolkata", "to": "Europe/Paris",
        "as": "LocalTime"})])
    assert list(out["LocalTime"].dt.hour) == [5, 6]


def test_a_per_row_zone_column_converts_each_row():
    df = _session("2026-07-30 09:15:00")
    df["TimeZone"] = ["Asia/Kolkata"]
    out = apply_transforms(df, [Transform(kind="timezone", params={
        "column": "Time", "from_column": "TimeZone", "to": "Europe/Paris",
        "as": "LocalTime"})])
    assert out["LocalTime"].iloc[0].hour == 5


def test_mixed_zones_in_one_frame_convert_independently():
    df = _session("2026-07-30 09:15:00", "2026-07-30 09:15:00")
    df["TimeZone"] = ["Asia/Kolkata", "America/New_York"]
    out = apply_transforms(df, [Transform(kind="timezone", params={
        "column": "Time", "from_column": "TimeZone", "to": "UTC",
        "as": "LocalTime"})])
    assert out["LocalTime"].iloc[0].hour == 3   # 09:15 IST -> 03:45 UTC
    assert out["LocalTime"].iloc[0].minute == 45
    assert out["LocalTime"].iloc[1].hour == 13  # 09:15 EDT -> 13:15 UTC


def test_to_local_resolves_the_machines_own_zone():
    df = _session("2026-07-30 09:15:00")
    out = apply_transforms(df, [Transform(kind="timezone", params={
        "column": "Time", "from_zone": "UTC", "to": "local",
        "as": "LocalTime"})])
    assert out["LocalTime"].iloc[0] is not pd.NaT


def test_a_day_offset_column_may_be_requested():
    df = _session("2026-07-30 09:15:00")
    out = apply_transforms(df, [Transform(kind="timezone", params={
        "column": "Time", "from_zone": "Asia/Kolkata",
        "to": "America/New_York", "as": "LocalTime",
        "day_offset_as": "DayShift"})])
    assert out["DayShift"].iloc[0] == -1


def test_an_unknown_zone_names_itself_and_the_row_count():
    df = _session("2026-07-30 09:15:00", "2026-07-30 10:00:00",
                  "2026-07-30 11:00:00")
    df["TimeZone"] = ["Asia/Kolkata", "Sasquatch Time", "Sasquatch Time"]
    with pytest.raises(ValueError, match="Sasquatch"):
        apply_transforms(df, [Transform(kind="timezone", params={
            "column": "Time", "from_column": "TimeZone", "to": "UTC",
            "as": "LocalTime"})])
    with pytest.raises(ValueError, match="2 row"):
        apply_transforms(df, [Transform(kind="timezone", params={
            "column": "Time", "from_column": "TimeZone", "to": "UTC",
            "as": "LocalTime"})])


def test_a_missing_column_is_refused():
    df = _session("2026-07-30 09:15:00")
    with pytest.raises(ValueError, match="Time"):
        apply_transforms(df, [Transform(kind="timezone", params={
            "column": "Missing", "from_zone": "UTC", "to": "UTC",
            "as": "LocalTime"})])
    with pytest.raises(ValueError, match="TimeZone"):
        apply_transforms(df, [Transform(kind="timezone", params={
            "column": "Time", "from_column": "TimeZone", "to": "UTC",
            "as": "LocalTime"})])


def test_neither_or_both_zone_sources_is_refused():
    df = _session("2026-07-30 09:15:00")
    with pytest.raises(ValueError):
        apply_transforms(df, [Transform(kind="timezone", params={
            "column": "Time", "to": "UTC", "as": "LocalTime"})])
    df["TimeZone"] = ["Asia/Kolkata"]
    with pytest.raises(ValueError):
        apply_transforms(df, [Transform(kind="timezone", params={
            "column": "Time", "from_column": "TimeZone",
            "from_zone": "UTC", "to": "UTC", "as": "LocalTime"})])


def test_row_order_is_preserved_across_zone_groups():
    """A session resorted around midnight is wrong: the second row's zone
    group must not leak into the first row's slot."""
    df = _session("2026-07-30 09:15:00", "2026-07-30 08:00:00",
                  "2026-07-30 09:30:00")
    df["TimeZone"] = ["Asia/Kolkata", "America/New_York", "Asia/Kolkata"]
    out = apply_transforms(df, [Transform(kind="timezone", params={
        "column": "Time", "from_column": "TimeZone", "to": "UTC",
        "as": "LocalTime"})])
    assert list(out["Time"]) == list(df["Time"])
    assert out["LocalTime"].iloc[0].minute == 45  # 09:15 IST row stays first
    assert out["LocalTime"].iloc[1].hour == 12    # 08:00 EDT row stays second


def test_an_empty_frame_converts_to_an_empty_frame():
    df = _session()
    df["TimeZone"] = pd.Series([], dtype=object)
    out = apply_transforms(df, [Transform(kind="timezone", params={
        "column": "Time", "from_column": "TimeZone", "to": "UTC",
        "as": "LocalTime"})])
    assert len(out) == 0
    assert "LocalTime" in out.columns


def test_the_transform_has_a_plain_english_summary():
    t = Transform(kind="timezone", params={
        "column": "Time", "from_zone": "Asia/Kolkata", "to": "Europe/Paris",
        "as": "LocalTime"})
    summary = transform_summary(t)
    assert "Time" in summary and "LocalTime" in summary
