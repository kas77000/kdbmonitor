from datetime import datetime

from kdbmonitor.core.exporting import export_filename

AS_OF = datetime(2026, 7, 26, 9, 15)


def test_includes_dashboard_dataset_and_timestamp():
    name = export_filename("Short sell", "by_market", {}, AS_OF, "csv")
    assert name == "short_sell_by_market_2026-07-26_0915.csv"


def test_slugged_like_pdf_filename():
    name = export_filename("P&L / risk (EOD)", "By Market!", {}, AS_OF, "csv")
    assert name == "p_l_risk_eod_by_market_2026-07-26_0915.csv"


def test_two_selections_differ():
    a = export_filename("Short sell", "by_market", {"market": "Hong Kong"}, AS_OF, "csv")
    b = export_filename("Short sell", "by_market", {"market": "Japan"}, AS_OF, "csv")
    assert a != b
    assert "hong_kong" in a
    assert "japan" in b


def test_empty_chosen_has_no_stray_separators():
    name = export_filename("Short sell", "by_market", {}, AS_OF, "csv")
    assert "__" not in name
    assert name == "short_sell_by_market_2026-07-26_0915.csv"


def test_blank_chosen_values_are_skipped_not_left_as_gaps():
    name = export_filename("Short sell", "by_market",
                           {"market": "", "side": None}, AS_OF, "csv")
    assert "__" not in name
    assert name == "short_sell_by_market_2026-07-26_0915.csv"


def test_long_value_is_capped():
    huge = "x" * 500
    name = export_filename("Short sell", "by_market", {"market": huge}, AS_OF, "csv")
    stem = name[:-len(".csv")]
    assert len(stem) < 100
    assert "x" * 25 not in stem


def test_path_separator_and_quote_cannot_escape_the_name():
    tricky = "../../etc/passwd\" OR \"1\"=\"1"
    name = export_filename("Short sell", "by_market", {"market": tricky}, AS_OF, "csv")
    for bad in ("/", "\\", '"', ".."):
        assert bad not in name


def test_suffix_is_honoured():
    csv_name = export_filename("Short sell", "by_market", {}, AS_OF, "csv")
    xlsx_name = export_filename("Short sell", "by_market", {}, AS_OF, "xlsx")
    assert csv_name.endswith(".csv")
    assert xlsx_name.endswith(".xlsx")
    assert csv_name[:-4] == xlsx_name[:-5]


def test_selection_order_does_not_change_the_name():
    a = export_filename("Short sell", "by_market",
                        {"market": "Hong Kong", "side": "buy"}, AS_OF, "csv")
    b = export_filename("Short sell", "by_market",
                        {"side": "buy", "market": "Hong Kong"}, AS_OF, "csv")
    assert a == b
