"""A guided filter may take its value from an earlier dataset.

Raw q could always do this — `sym in {{orders.sym}}` — but guided mode built
its where clause first and substituted after, so the placeholder was formatted
as though it were data: one backtick per character, and nothing left for the
substitution to find. It produced no error, just a query that matched nothing.
"""
import pandas as pd

from kdbmonitor.core.chain import filter_clause, substitute_refs
from kdbmonitor.core.dashboard_models import Dataset
from kdbmonitor.core.dataset import build_qsql
from kdbmonitor.core.models import Filter
from kdbmonitor.core.qfmt import format_q_list, format_q_value, is_placeholder
from kdbmonitor.core.timectx import ResolvedTime

RT = ResolvedTime("realtime", None, None)
OUTPUTS = {"yesterday": pd.DataFrame({"sym": ["RELIANCE.IB", "INFY.IB"]}),
           "sizes": pd.DataFrame({"qty": [100, 200]})}


def test_a_placeholder_is_recognised_whatever_it_names():
    for token in ("{{yesterday.sym}}", "{{param:instrument}}", "{{date_from}}",
                  "  {{a.b}}  "):
        assert is_placeholder(token), token


def test_ordinary_text_is_not_a_placeholder():
    for value in ("RELIANCE.IB", "{{unclosed", "a {{b.c}} d", 10, None):
        assert not is_placeholder(value)


def test_a_symbol_filter_no_longer_shreds_a_reference():
    """It used to become `{`{`y`e`s`t`e`r`d`a`y..."""
    assert format_q_value("{{yesterday.sym}}", "symbol") == "{{yesterday.sym}}"


def test_a_reference_standing_for_a_whole_list_is_not_enlisted():
    """Whatever fills it in is already a list."""
    assert format_q_list(["{{yesterday.sym}}"], "symbol") == "{{yesterday.sym}}"


def test_a_guided_in_filter_reads_as_q_after_substitution():
    clause = filter_clause(Filter(column="sym", op="in",
                                  value=["{{yesterday.sym}}"],
                                  value_type="symbol"))
    assert substitute_refs(clause, OUTPUTS) == "sym in `RELIANCE.IB`INFY.IB"


def test_a_guided_equals_filter_reads_as_q_after_substitution():
    clause = filter_clause(Filter(column="qty", op=">=", value="{{sizes.qty}}",
                                  value_type="number"))
    assert substitute_refs(clause, OUTPUTS) == "qty>=100 200"


def test_a_whole_guided_dataset_builds_the_same_query_raw_mode_would():
    guided = Dataset(name="live", env="oms", mode="guided", table="target",
                     filters=[Filter(column="sym", op="in",
                                     value=["{{yesterday.sym}}"],
                                     value_type="symbol")])
    raw = Dataset(name="live", env="oms", mode="raw",
                  raw_qsql="select from target where sym in {{yesterday.sym}}")
    assert build_qsql(guided, RT, OUTPUTS) == build_qsql(raw, RT, OUTPUTS)


def test_a_placeholder_beside_a_real_value_still_works():
    guided = Dataset(name="live", env="oms", mode="guided", table="target",
                     filters=[Filter(column="sym", op="in",
                                     value=["{{yesterday.sym}}"],
                                     value_type="symbol"),
                              Filter(column="side", op="=", value="BUY",
                                     value_type="symbol")])
    assert build_qsql(guided, RT, OUTPUTS) == (
        "select from target where sym in `RELIANCE.IB`INFY.IB, side=`BUY")


def test_real_values_are_still_formatted_as_they_always_were():
    assert format_q_value("RELIANCE.IB", "symbol") == "`RELIANCE.IB"
    assert format_q_list(["A", "B"], "symbol") == "`A`B"
    assert format_q_list(["A"], "symbol") == "enlist `A"


def test_a_list_of_several_placeholders_is_not_special_cased():
    """Only one standing for the whole list makes sense; several are values."""
    out = format_q_list(["{{a.b}}", "{{c.d}}"], "symbol")
    assert out.startswith("`")
