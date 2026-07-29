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
