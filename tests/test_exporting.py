import io

import pandas as pd
import pytest

from kdbmonitor.core.exporting import (
    column_as_text, df_to_excel_bytes, df_to_csv, df_to_tsv,
)


def test_column_as_text_formats():
    syms = ["AAPL", "MSFT", "AAPL"]
    assert column_as_text(syms, "lines") == "AAPL\nMSFT\nAAPL"
    assert column_as_text(syms, "comma") == "AAPL, MSFT, AAPL"
    assert column_as_text(syms, "q") == "`AAPL`MSFT`AAPL"


def test_column_as_text_distinct_preserves_order():
    syms = ["AAPL", "MSFT", "AAPL"]
    assert column_as_text(syms, "q", distinct=True) == "`AAPL`MSFT"
    assert column_as_text(syms, "lines", distinct=True) == "AAPL\nMSFT"


def test_column_as_text_numbers_and_empty():
    assert column_as_text([1, 2, 3], "q") == "1 2 3"
    assert column_as_text([5], "q") == "enlist 5"
    assert column_as_text([], "lines") == ""
    assert column_as_text([], "q") == ""


def test_column_as_text_bad_format():
    with pytest.raises(ValueError):
        column_as_text([1], "nope")


def test_df_to_csv_and_tsv():
    df = pd.DataFrame({"sym": ["AAPL"], "bid": [101.0]})
    assert df_to_csv(df).splitlines()[0] == "sym,bid"
    assert df_to_tsv(df).splitlines()[0] == "sym\tbid"


def test_df_to_excel_bytes_is_valid_xlsx():
    df = pd.DataFrame({"sym": ["AAPL", "MSFT"], "bid": [101.0, 99.5]})
    data = df_to_excel_bytes(df)
    assert isinstance(data, bytes) and data[:2] == b"PK"     # xlsx is a zip
    back = pd.read_excel(io.BytesIO(data))                    # round-trips
    assert list(back["sym"]) == ["AAPL", "MSFT"]
