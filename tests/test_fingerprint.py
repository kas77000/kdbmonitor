import pandas as pd
from kdbmonitor.core.fingerprint import result_fingerprint


def test_fingerprint_is_row_order_insensitive():
    a = pd.DataFrame({"sym": ["AAPL", "MSFT"], "qty": [5, 3]})
    b = pd.DataFrame({"sym": ["MSFT", "AAPL"], "qty": [3, 5]})   # same rows, shuffled
    assert result_fingerprint(a) == result_fingerprint(b)


def test_fingerprint_detects_content_change():
    a = pd.DataFrame({"sym": ["AAPL", "MSFT"], "qty": [5, 3]})
    c = pd.DataFrame({"sym": ["AAPL", "MSFT"], "qty": [5, 4]})   # one qty changed
    assert result_fingerprint(a) != result_fingerprint(c)


def test_fingerprint_none_and_empty():
    assert result_fingerprint(None) == ""
    assert result_fingerprint(pd.DataFrame()) == "empty"
