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
