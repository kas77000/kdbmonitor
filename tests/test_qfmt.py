# tests/test_qfmt.py
from kdbmonitor.core.qfmt import format_q_value, format_q_list


def test_format_scalar():
    assert format_q_value("AAPL", "symbol") == "`AAPL"
    assert format_q_value(100, "number") == "100"
    assert format_q_value(100.5, "number") == "100.5"
    assert format_q_value('he"llo', "string") == '"he\\"llo"'


def test_format_list_symbol():
    assert format_q_list(["AAPL", "MSFT"], "symbol") == "`AAPL`MSFT"
    assert format_q_list(["AAPL"], "symbol") == "enlist `AAPL"


def test_format_list_number():
    assert format_q_list([1, 2, 3], "number") == "1 2 3"
    assert format_q_list([5], "number") == "enlist 5"


def test_format_list_string():
    assert format_q_list(["a", "b"], "string") == '("a";"b")'
    assert format_q_list(["a"], "string") == 'enlist "a"'


def test_format_list_empty_is_valid_q():
    # Empty upstream results must still produce parseable `x in ...` clauses.
    assert format_q_list([], "symbol") == "`$()"
    assert format_q_list([], "number") == "0#0"
    assert format_q_list([], "string") == "()"
