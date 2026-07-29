import pandas as pd
import pytest

from kdbmonitor.core.filesource import read_grid


def test_a_plain_csv_becomes_a_grid():
    assert read_grid(b"a,b\n1,2\n") == [["a", "b"], ["1", "2"]]


def test_short_rows_are_padded_so_the_grid_is_rectangular():
    """A cell is addressed by (row, col); a ragged grid makes that a lie."""
    assert read_grid(b"a,b,c\n1\n") == [["a", "b", "c"], ["1", "", ""]]


def test_quoted_fields_keep_their_commas():
    assert read_grid(b'a,b\n"125,000",x\n') == [["a", "b"], ["125,000", "x"]]


def test_a_byte_order_mark_is_not_part_of_the_first_header():
    """Excel writes one, and it made the first column unmatchable by name."""
    assert read_grid("﻿sym,qty\n0005.HK,10\n".encode("utf-8")) == [
        ["sym", "qty"], ["0005.HK", "10"]]


def test_an_empty_file_is_an_empty_grid():
    assert read_grid(b"") == []


def test_a_file_that_is_not_utf8_text_is_refused_by_name():
    with pytest.raises(ValueError, match="UTF-8"):
        read_grid(b"\xff\xfe\x00s\x00y")


from kdbmonitor.core.dashboard_models import ColumnSpec, FileShape
from kdbmonitor.core.filesource import Problem, header_columns, orient


def _shape(**kw) -> FileShape:
    kw.setdefault("columns", [ColumnSpec(name="sym"), ColumnSpec(name="qty")])
    return FileShape(**kw)


def test_row_headers_are_left_alone():
    grid = [["a", "b"], ["1", "2"]]
    assert orient(grid, "row") == grid


def test_column_headers_are_transposed_into_row_headers():
    """Headers running down the first column, one record per column."""
    assert orient([["sym", "0005.HK", "7203.JP"],
                   ["qty", "10", "20"]], "column") == [
        ["sym", "qty"], ["0005.HK", "10"], ["7203.JP", "20"]]


def test_transposing_an_empty_grid_is_not_an_error():
    assert orient([], "column") == []


def test_headers_are_read_from_the_declared_line():
    grid = [["report", "", ""], ["", "", ""], ["sym", "qty", "note"]]
    found, problems = header_columns(grid, _shape(header_row=2, data_start=3))
    assert problems == []
    assert found == [("sym", 0), ("qty", 1), ("note", 2)]


def test_headers_are_read_from_the_declared_column_onwards():
    grid = [["#", "sym", "qty"]]
    found, problems = header_columns(grid, _shape(first_col=1, data_start=1))
    assert problems == []
    assert found == [("sym", 1), ("qty", 2)]


def test_a_header_somewhere_else_is_refused_and_the_line_is_quoted():
    """No searching. The declared line is the contract."""
    grid = [["sym", "qty"], ["0005.HK", "10"]]
    _, problems = header_columns(grid, _shape(header_row=1, data_start=2))
    assert len(problems) == 1
    assert "line 2" in problems[0].message
    assert "0005.HK" in problems[0].message      # what was actually there


def test_a_header_line_past_the_end_of_the_file_is_refused_not_an_index_error():
    _, problems = header_columns([["sym", "qty"]], _shape(header_row=9,
                                                          data_start=10))
    assert len(problems) == 1
    assert "line 10" in problems[0].message


def test_a_trailing_comma_makes_a_blank_header_which_is_dropped():
    """Nothing can reference an unnamed column, so it is not offered."""
    found, problems = header_columns([["sym", "qty", ""]], _shape(data_start=1))
    assert problems == []
    assert found == [("sym", 0), ("qty", 1)]


def test_two_columns_with_the_same_name_are_refused():
    _, problems = header_columns([["qty", "qty"]], _shape(data_start=1))
    assert len(problems) == 1
    assert "qty" in problems[0].message


def test_a_header_line_with_no_names_at_all_is_refused():
    found, problems = header_columns([["", ""]], _shape(data_start=1))
    assert found == []
    assert len(problems) == 1
    assert "(empty)" in problems[0].message


def test_a_file_whose_headers_run_downwards_says_column_not_line():
    """With records running down the page, "line 14" would send the reader to
    entirely the wrong place."""
    _, problems = header_columns([["x"]], _shape(header_axis="column",
                                                 header_row=5, data_start=6))
    assert "column 6" in problems[0].message


def test_transposing_a_ragged_grid_loses_no_record():
    """zip stops at the shortest row, so a ragged grid would drop whole records
    off the end silently — the one failure this module exists to prevent."""
    assert orient([["sym", "a", "b"], ["qty", "1"]], "column") == [
        ["sym", "qty"], ["a", "1"], ["b", ""]]


from kdbmonitor.core.filesource import data_records


def test_data_starts_where_it_was_declared_to():
    grid = [["sym", "qty"], ["0005.HK", "10"], ["7203.JP", "20"]]
    records, skipped = data_records(grid, _shape(data_start=1),
                                    [("sym", 0), ("qty", 1)])
    assert skipped == 0
    assert records == [(2, ["0005.HK", "10"]), (3, ["7203.JP", "20"])]


def test_only_the_named_columns_are_taken():
    """A column dropped for having no header takes its data with it."""
    grid = [["sym", "", "qty"], ["0005.HK", "junk", "10"]]
    records, _ = data_records(grid, _shape(data_start=1),
                              [("sym", 0), ("qty", 2)])
    assert records == [(2, ["0005.HK", "10"])]


def test_a_wholly_blank_row_is_dropped_and_counted():
    """A trailing blank line is not a row of nulls."""
    grid = [["sym", "qty"], ["0005.HK", "10"], ["", ""], ["7203.JP", "20"]]
    records, skipped = data_records(grid, _shape(data_start=1),
                                    [("sym", 0), ("qty", 1)])
    assert skipped == 1
    assert [r[1] for r in records] == [["0005.HK", "10"], ["7203.JP", "20"]]


def test_the_line_number_survives_a_dropped_row():
    """It points into the file the reader has open, not into what we kept."""
    grid = [["sym", "qty"], ["", ""], ["7203.JP", "20"]]
    records, _ = data_records(grid, _shape(data_start=1),
                              [("sym", 0), ("qty", 1)])
    assert records == [(3, ["7203.JP", "20"])]


def test_whitespace_only_counts_as_blank():
    grid = [["sym", "qty"], ["  ", " "]]
    records, skipped = data_records(grid, _shape(data_start=1),
                                    [("sym", 0), ("qty", 1)])
    assert (records, skipped) == ([], 1)


def test_a_file_with_headers_and_no_data_yields_no_records():
    records, skipped = data_records([["sym", "qty"]], _shape(data_start=1),
                                    [("sym", 0), ("qty", 1)])
    assert (records, skipped) == ([], 0)


def test_a_short_row_reads_as_blanks_rather_than_raising():
    """read_grid pads, but this must not be the only thing standing between a
    ragged grid and an IndexError."""
    grid = [["sym", "qty"], ["0005.HK"]]
    records, _ = data_records(grid, _shape(data_start=1),
                              [("sym", 0), ("qty", 1)])
    assert records == [(2, ["0005.HK", ""])]


def test_a_blank_row_is_judged_only_on_the_columns_being_taken():
    """A row blank in the table but carrying a note in an ignored column is
    still a blank row: the note is not data this dashboard reads."""
    grid = [["sym", "qty", "note"], ["", "", "see appendix"]]
    records, skipped = data_records(grid, _shape(data_start=1),
                                    [("sym", 0), ("qty", 1)])
    assert (records, skipped) == ([], 1)


def test_a_negative_data_start_does_not_wrap_around_to_the_end_of_the_grid():
    """A negative offset is not out of range to list indexing — it reads
    backwards from the end, which would splice the header back in as a row of
    data under a nonsensical line number."""
    grid = [["sym", "qty"], ["0005.HK", "10"], ["7203.JP", "20"]]
    records, skipped = data_records(grid, _shape(data_start=-1),
                                    [("sym", 0), ("qty", 1)])
    assert skipped == 0
    assert records == [(1, ["sym", "qty"]), (2, ["0005.HK", "10"]),
                       (3, ["7203.JP", "20"])]


from kdbmonitor.core.filesource import is_blank, null_set, read_values


DEFAULTS = null_set(FileShape())


def test_the_default_markers_all_read_as_missing():
    for marker in ("", "NA", "N/A", "NaN", "NULL", "NONE", "-", "--", "#N/A"):
        assert is_blank(marker, DEFAULTS), marker


def test_markers_are_matched_whatever_their_case_or_padding():
    assert is_blank("  n/a  ", DEFAULTS)


def test_a_real_value_is_not_missing():
    assert not is_blank("0", DEFAULTS)
    assert not is_blank("0005.HK", DEFAULTS)


def test_a_marker_can_be_taken_off_the_list():
    """A 'side' column where '-' is a real category must keep it."""
    markers = null_set(FileShape(null_markers=["", "N/A"]))
    assert not is_blank("-", markers)


def test_integers_satisfy_a_number_column():
    values, failures = read_values(["10", "20"], "number", DEFAULTS)
    assert failures == []
    assert list(values) == [10.0, 20.0]


def test_a_thousands_separator_does_not_stop_a_number_being_read():
    values, failures = read_values(["125,000"], "number", DEFAULTS)
    assert failures == [] and list(values) == [125000.0]


def test_a_blank_never_fails_a_number_column():
    values, failures = read_values(["10", "N/A", ""], "number", DEFAULTS)
    assert failures == []
    assert values.isna().tolist() == [False, True, True]


def test_text_where_a_number_was_promised_fails_and_says_which_value():
    _, failures = read_values(["10", "hello"], "number", DEFAULTS)
    assert [f[0] for f in failures] == [1]          # index within the column
    assert failures[0][1] == "hello"


def test_a_fraction_is_refused_by_an_integer_column():
    """Narrowing loses information, and silently."""
    _, failures = read_values(["1.5"], "integer", DEFAULTS)
    assert len(failures) == 1


def test_a_whole_number_written_as_a_float_is_accepted_by_an_integer_column():
    values, failures = read_values(["10.0"], "integer", DEFAULTS)
    assert failures == [] and list(values) == [10]


def test_a_text_column_accepts_anything():
    values, failures = read_values(["10", "hello", "-"], "text", DEFAULTS)
    assert failures == []
    assert values.tolist()[:2] == ["10", "hello"]
    assert pd.isna(values.tolist()[2])              # "-" is still a null marker


def test_a_date_column_reads_dates_and_refuses_prose():
    values, failures = read_values(["2026-07-30"], "date", DEFAULTS)
    assert failures == [] and values.iloc[0] == pd.Timestamp("2026-07-30")
    _, bad = read_values(["not a date"], "date", DEFAULTS)
    assert len(bad) == 1


def test_a_boolean_column_reads_the_usual_spellings():
    values, failures = read_values(["true", "N", "1", "0"], "boolean", DEFAULTS)
    assert failures == []
    assert list(values) == [True, False, True, False]


def test_an_empty_column_reads_as_all_null_whatever_the_type():
    values, failures = read_values(["", ""], "number", DEFAULTS)
    assert failures == [] and values.isna().all()


def test_an_unknown_type_name_falls_back_to_text_rather_than_raising():
    """A hand-edited bundle can name a type this version does not have."""
    values, failures = read_values(["anything"], "sasquatch", DEFAULTS)
    assert failures == [] and values.tolist() == ["anything"]


def test_reading_no_cells_at_all_gives_an_empty_column():
    values, failures = read_values([], "number", DEFAULTS)
    assert failures == [] and len(values) == 0


def test_a_failure_index_points_at_the_cell_within_the_column():
    """The caller turns this into a file line number, so it must be the
    position in the column it was handed, not in the file."""
    _, failures = read_values(["1", "2", "bad", "4"], "number", DEFAULTS)
    assert failures == [(2, "bad")]
