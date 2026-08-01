"""A time column on screen reads as a clock.

A q ``time``, ``minute``, ``second`` or ``timespan`` column arrives from pykx
as a timedelta, and a timedelta is a *length* of time as far as Streamlit is
concerned: it printed 14:30:00 as "15 hours". The format the dashboard author
set never got a look in either, because only numeric columns were translated.
Both are this module's business.
"""
import pandas as pd

from kdbmonitor.ui.tables import matching, moment_format, prepare


def _fmt_of(config: dict, header: str) -> str:
    return config[header]["type_config"]["format"]


def _type_of(config: dict, header: str) -> str:
    return config[header]["type_config"]["type"]


def _times(*values) -> pd.DataFrame:
    return pd.DataFrame({"bucket": pd.to_timedelta(list(values)),
                         "qty": [100] * len(values)})


# --- a duration is a clock reading -------------------------------------------

def test_a_time_column_prints_as_a_clock_even_with_no_format_set():
    """The reported bug: 14:30:00 shown as '15 hours'. There is no honest
    default to fall back to here, so this happens format or no format."""
    frame, config = prepare(["bucket", "qty"], ["", ""], _times("14:30:00"))
    assert _type_of(config, "bucket") == "datetime"
    assert _fmt_of(config, "bucket") == "HH:mm:ss"
    assert frame["bucket"].iloc[0].hour == 14
    assert frame["bucket"].iloc[0].minute == 30


def test_the_format_the_author_chose_is_the_one_that_prints():
    _, config = prepare(["bucket"], ["%H:%M"], _times("14:30:00"))
    assert _fmt_of(config, "bucket") == "HH:mm"


def test_the_clock_column_still_sorts_by_time():
    """It is converted, not stringified: '9:15' must not land after '10:00'."""
    frame, _ = prepare(["bucket"], [""], _times("10:00:00", "09:15:00"))
    assert frame["bucket"].sort_values().iloc[0].hour == 9


def test_a_duration_longer_than_a_day_is_not_squeezed_into_a_clock():
    """25 hours shown as 01:00:00 is not a formatting nicety, it is the wrong
    answer — so that column prints as text and says 25:00:00."""
    frame, config = prepare(["bucket"], [""], _times("25:00:00"))
    assert "bucket" not in config
    assert frame["bucket"].iloc[0] == "25:00:00"


def test_a_negative_duration_is_left_as_text_too():
    frame, config = prepare(["bucket"], [""], _times("-01:30:00"))
    assert "bucket" not in config
    assert frame["bucket"].iloc[0].startswith("-01:30")


def test_a_missing_time_stays_empty_rather_than_becoming_a_word():
    frame, _ = prepare(["bucket"], [""],
                       pd.DataFrame({"bucket": pd.to_timedelta(
                           ["14:30:00", None])}))
    assert pd.isna(frame["bucket"].iloc[1])


def test_the_dataset_s_own_frame_is_not_touched():
    """A widget must not be able to rewrite the frame another widget reads."""
    original = _times("14:30:00")
    prepare(["bucket"], [""], original)
    assert pd.api.types.is_timedelta64_dtype(original["bucket"])


# --- timestamps --------------------------------------------------------------

def test_a_timestamp_column_honours_its_format():
    frame = pd.DataFrame({"when": pd.to_datetime(["2026-07-30 09:15"])})
    _, config = prepare(["when"], ["%Y-%m-%d %H:%M"], frame)
    assert _fmt_of(config, "when") == "YYYY-MM-DD HH:mm"


def test_a_timestamp_shown_as_a_time_of_day_only_shows_the_time():
    frame = pd.DataFrame({"when": pd.to_datetime(["2026-07-30 09:15"])})
    _, config = prepare(["when"], ["%H:%M:%S"], frame)
    assert _fmt_of(config, "when") == "HH:mm:ss"


def test_a_timestamp_with_no_format_is_left_to_streamlit():
    frame = pd.DataFrame({"when": pd.to_datetime(["2026-07-30 09:15"])})
    _, config = prepare(["when"], [""], frame)
    assert config == {}


# --- translating the format spec ---------------------------------------------

def test_the_ordinary_patterns_translate():
    assert moment_format("%Y-%m-%d") == "YYYY-MM-DD"
    assert moment_format("%d/%m/%Y") == "DD/MM/YYYY"
    assert moment_format("%d %b %Y") == "DD MMM YYYY"
    assert moment_format("%H:%M:%S") == "HH:mm:ss"


def test_a_literal_letter_is_escaped_rather_than_read_as_a_token():
    """'h' is a twelve-hour clock to momentJS, so "9h30" would print the hour
    twice if the letter were left bare."""
    assert moment_format("%Hh%M") == "HH[h]mm"


def test_a_directive_with_no_counterpart_leaves_the_column_alone():
    assert moment_format("%V-%U") == ""
    frame = pd.DataFrame({"when": pd.to_datetime(["2026-07-30 09:15"])})
    assert prepare(["when"], ["%V"], frame)[1] == {}


def test_a_clock_column_with_an_untranslatable_format_still_reads_as_a_clock():
    """Better the default clock than Streamlit's '15 hours'."""
    _, config = prepare(["bucket"], ["%V"], _times("14:30:00"))
    assert _fmt_of(config, "bucket") == "HH:mm:ss"


# --- the numeric behaviour is unchanged --------------------------------------

def test_numbers_are_translated_as_they_always_were():
    frame = pd.DataFrame({"qty": [1234.5], "sym": ["AAPL"]})
    _, config = prepare(["qty", "sym"], [",.0f", ""], frame)
    assert _fmt_of(config, "qty") == "localized"
    assert "sym" not in config


def test_a_percentage_is_still_a_percentage():
    _, config = prepare(["share"], [".1%"], pd.DataFrame({"share": [0.42]}))
    assert _fmt_of(config, "share") == "percent"


def test_a_plain_decimal_is_still_a_plain_decimal():
    _, config = prepare(["px"], [".2f"], pd.DataFrame({"px": [1284.5]}))
    assert _fmt_of(config, "px") == "%.2f"


def test_a_column_that_is_not_shown_is_not_configured():
    _, config = prepare(["missing"], [",.0f"], pd.DataFrame({"qty": [1]}))
    assert config == {}


# --- searching reads what is on screen ---------------------------------------

def test_a_search_finds_the_time_the_way_the_clock_column_shows_it():
    frame, _ = prepare(["bucket", "qty"], ["", ""],
                       _times("14:30:00", "09:15:00"))
    assert len(matching(frame, "14:30")) == 1
