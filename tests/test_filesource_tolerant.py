"""Phase E: a file no longer has to be a plain comma-separated UTF-8 export.

Delimiter and encoding tolerance sit entirely behind ``read_grid`` (see
``filesource``'s module docstring); decimal commas and Excel date serials sit
in the ``_to_*`` readers. Nothing above either boundary changed, which is why
these tests exercise ``read_grid``, ``read_values`` and ``load`` directly
rather than anything about headers or column contracts.
"""
import pandas as pd
import pytest

from kdbmonitor.core.dashboard_models import ColumnSpec, FileShape
from kdbmonitor.core.filesource import load, null_set, read_grid, read_values

DEFAULTS = {"", "na", "n/a", "nan", "null", "none", "-", "--", "#n/a"}


def _shape(**kw) -> FileShape:
    kw.setdefault("columns", [ColumnSpec(name="sym"),
                              ColumnSpec(name="qty", type="number")])
    return FileShape(**kw)


# --- delimiter sniffing ------------------------------------------------

def test_a_semicolon_file_loads_and_its_columns_are_right():
    out = load(b"sym;qty\n0005.HK;10\n", _shape())
    assert out.problems == []
    assert list(out.df.columns) == ["sym", "qty"]
    assert out.df["qty"].tolist() == [10.0]


def test_a_tab_file_loads():
    out = load(b"sym\tqty\n0005.HK\t10\n", _shape())
    assert out.problems == []
    assert out.df["sym"].tolist() == ["0005.HK"]


def test_a_pipe_file_loads():
    out = load(b"sym|qty\n0005.HK|10\n", _shape())
    assert out.problems == []
    assert out.df["qty"].tolist() == [10.0]


def test_a_comma_file_still_loads_nothing_regressed():
    out = load(b"sym,qty\n0005.HK,10\n7203.JP,20\n", _shape())
    assert out.problems == []
    assert out.df["qty"].tolist() == [10.0, 20.0]


def test_a_quoted_comma_does_not_fool_the_sniffer_into_picking_comma():
    """A naive sniffer counting raw ',' characters would see one comma on
    every line and call that consistent. Parsing for real shows the comma is
    trapped inside quotes and never actually splits a line, while the
    semicolon does — every line, into the same two fields."""
    data = (b'name;note\n'
            b'"Doe, John";"first note"\n'
            b'"Smith, Ann";"second note"\n')
    grid = read_grid(data)
    assert grid == [["name", "note"], ["Doe, John", "first note"],
                    ["Smith, Ann", "second note"]]


def test_an_explicit_delimiter_is_used_even_on_a_file_that_looks_comma_separated():
    """"auto" would sniff comma here — a semicolon file with commas inside
    quoted fields still parses as one column per sniffing rules elsewhere,
    but that is not the point: an explicit delimiter is never second-guessed,
    even against a file that would otherwise sniff differently."""
    grid = read_grid(b"a,b;c\n1,2;3\n", delimiter=";")
    assert grid == [["a,b", "c"], ["1,2", "3"]]


def test_an_explicit_wrong_delimiter_produces_a_refusal_not_a_guess():
    out = load(b"sym,qty\n0005.HK,10\n", _shape(delimiter=";"))
    assert out.df is None
    assert any("sym" in p.message or "qty" in p.message for p in out.problems)


def test_a_single_column_file_still_loads():
    shape = _shape(columns=[ColumnSpec(name="sym")])
    out = load(b"sym\n0005.HK\n7203.JP\n", shape)
    assert out.problems == []
    assert out.df["sym"].tolist() == ["0005.HK", "7203.JP"]


def test_an_empty_file_still_refuses_as_before():
    out = load(b"", _shape())
    assert out.df is None and out.problems


# --- encoding ------------------------------------------------------------

def test_a_cp1252_file_loads_and_notes_the_encoding_was_not_utf8():
    # 0x93/0x94 are cp1252's curly quotes -- invalid as a UTF-8 leading byte,
    # so this can only be read by falling through to cp1252.
    data = b"sym;note\n0005.HK;\x93quoted\x94 test\n"
    out = load(data, _shape(columns=[ColumnSpec(name="sym"),
                                     ColumnSpec(name="note")]))
    assert out.problems == []
    assert out.df["sym"].tolist() == ["0005.HK"]
    assert any("UTF-8" in n for n in out.notes)


def test_a_utf8_file_produces_no_encoding_note():
    out = load(b"sym,qty\n0005.HK,10\n", _shape())
    assert out.problems == []
    assert not any("UTF-8" in n for n in out.notes)


# --- decimal commas -------------------------------------------------------

def test_a_decimal_comma_reads_as_a_number_in_a_semicolon_file():
    values, failures = read_values(["0,0215"], "number", DEFAULTS, ";")
    assert failures == []
    assert values.iloc[0] == pytest.approx(0.0215)


def test_a_thousands_separator_still_reads_in_a_comma_file():
    values, failures = read_values(["125,000"], "number", DEFAULTS, ",")
    assert failures == [] and values.iloc[0] == 125000.0


def test_a_decimal_comma_in_a_comma_file_is_refused_not_guessed():
    """"0,0215" groups as 1-then-4 digits, which is not how a thousands
    separator is ever written — and in a comma file a bare comma cannot mean
    anything else, since the delimiter already owns the job a decimal comma
    would otherwise do. Refusing it is the honest answer; silently reading it
    as 0.0215 would be guessing a European export snuck into a comma file,
    and silently reading it as 215 (stripping the comma the way a real
    thousands separator would) would be no better a guess."""
    values, failures = read_values(["0,0215"], "number", DEFAULTS, ",")
    assert [f[0] for f in failures] == [0]
    assert values.isna().all()


def test_the_same_value_is_read_differently_by_delimiter():
    """The point of the collision: identical text, opposite readings,
    entirely decided by which character the file already uses to end a
    field."""
    comma_values, comma_failures = read_values(["0,0215"], "number", DEFAULTS, ",")
    semi_values, semi_failures = read_values(["0,0215"], "number", DEFAULTS, ";")
    assert comma_failures and not semi_failures
    assert semi_values.iloc[0] == pytest.approx(0.0215)


# --- Excel date serials ----------------------------------------------------

def test_an_excel_time_fraction_reads_as_a_time_of_day():
    values, failures = read_values(["0.385416"], "date", DEFAULTS)
    assert failures == []
    assert values.iloc[0].round("min").strftime("%H:%M") == "09:15"


def test_an_excel_day_serial_reads_as_a_date_in_2023():
    values, failures = read_values(["45000"], "date", DEFAULTS)
    assert failures == []
    assert values.iloc[0].year == 2023


def test_a_normal_iso_date_still_reads():
    values, failures = read_values(["2026-07-30"], "date", DEFAULTS)
    assert failures == []
    assert values.iloc[0] == pd.Timestamp("2026-07-30")


def test_text_in_a_date_column_is_still_refused():
    _, failures = read_values(["not a date"], "date", DEFAULTS)
    assert len(failures) == 1


# --- FileShape.delimiter round-trip ----------------------------------------

from kdbmonitor.core.dashboard_models import dashboard_from_dict, dashboard_to_dict
from kdbmonitor.core.dashboard_models import Dashboard, Dataset


def _one_dataset_dashboard(shape: FileShape) -> Dashboard:
    return Dashboard(id=None, name="d",
                     datasets=[Dataset(name="f", env="", source="file",
                                       shape=shape)])


def test_delimiter_round_trips_through_the_dashboard_dict():
    shape = _shape(delimiter=";")
    d = _one_dataset_dashboard(shape)
    back = dashboard_from_dict(dashboard_to_dict(d))
    assert back.datasets[0].shape.delimiter == ";"


def test_a_shape_stored_before_this_field_reads_back_as_auto():
    d = _one_dataset_dashboard(_shape())
    raw = dashboard_to_dict(d)
    del raw["datasets"][0]["shape"]["delimiter"]
    back = dashboard_from_dict(raw)
    assert back.datasets[0].shape.delimiter == "auto"


# --- a file that was never text ---------------------------------------------

def test_a_binary_file_is_refused_as_not_text():
    """latin-1 defines all 256 byte values and so cannot fail. Without a guard
    a PNG decodes into mojibake, gets as far as looking for a header row, and
    is refused for having the wrong columns — sending somebody hunting for a
    column problem in a file that was never a spreadsheet."""
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
    out = load(png, FileShape(columns=[ColumnSpec(name="sym")]))
    assert out.df is None
    assert "not" in out.problems[0].message and "text" in out.problems[0].message


def test_an_accented_text_file_is_not_mistaken_for_binary():
    data = "sym;société\n0005.HK;10\n".encode("cp1252")
    out = load(data, FileShape(columns=[ColumnSpec(name="sym")]))
    assert out.df is not None


def test_an_ordinary_csv_is_not_mistaken_for_binary():
    out = load(b"sym,qty\n0005.HK,10\n",
               FileShape(columns=[ColumnSpec(name="sym")]))
    assert out.df is not None


# --- an Excel serial has to be plausible ------------------------------------

def test_a_small_number_in_a_date_column_is_refused_not_read_as_1900():
    """A 5 in a date column is a quantity in the wrong column far more often
    than it is the fourth of January 1900, and a wrong date that looks like a
    date is the failure that never gets caught."""
    values, failures = read_values(["5"], "date", null_set(FileShape()))
    assert len(failures) == 1
    assert values.isna().all()


def test_a_real_excel_serial_still_reads():
    values, failures = read_values(["45000"], "date", null_set(FileShape()))
    assert failures == []
    assert values.iloc[0].year == 2023


def test_a_time_of_day_fraction_still_reads():
    """09:15 is 9.25 hours, which is 0.3854166... of a day."""
    values, failures = read_values(["0.38541666666667"], "date",
                                   null_set(FileShape()))
    assert failures == []
    assert (values.iloc[0].hour, values.iloc[0].minute) == (9, 15)


def test_an_ordinary_date_is_unaffected_by_the_guard():
    values, failures = read_values(["2026-07-30"], "date", null_set(FileShape()))
    assert failures == [] and values.iloc[0].year == 2026
