"""A form whose values reach the query.

A parameter used to stop at the transforms, which meant "show me one symbol"
pulled the whole table out of kdb and threw most of it away in pandas. Written
into the query it is one predicate on the server — but a value on its way into
q has to be *typed* on the way (a symbol is not a char vector) and it has to be
safe (a symbol with a semicolon in it is two statements), and both of those are
what these tests are about.

Nothing here knows what a symbol is for. The mechanism is the same whether the
form asks for an instrument, a venue, a trader id or a threshold.
"""
from datetime import date

import pytest

from kdbmonitor.core.dashboard_models import Dashboard, Dataset, Parameter
from kdbmonitor.core.dataset import build_qsql
from kdbmonitor.core.models import Filter
from kdbmonitor.core.parameters import (
    as_q, q_values, query_params, unresolved_params,
)
from kdbmonitor.core.timectx import ResolvedTime

REALTIME = ResolvedTime("realtime", None, None)


def _p(name="sym", **kw) -> Parameter:
    # 'text' by default: a box somebody types into. A 'choice' with no choices
    # deliberately falls back to its default however it is filled in, which is
    # right for a dropdown and wrong for everything here.
    kw.setdefault("kind", "text")
    return Parameter(name=name, **kw)


def _raw(q: str, **kw) -> Dataset:
    return Dataset(name="d", env="orders", table="", mode="raw", raw_qsql=q, **kw)


def _q(ds: Dataset, params: dict) -> str:
    return build_qsql(ds, REALTIME, outputs={}, params=params)


# --- typed on the way in -----------------------------------------------------

@pytest.mark.parametrize("q_type,value,written", [
    ("symbol", "AAPL", "`AAPL"),
    ("string", "partial fill", '"partial fill"'),
    ("number", "100.5", "100.5"),
    ("date", "2026-07-30", "2026.07.30"),
    ("boolean", "true", "1b"),
    ("boolean", "false", "0b"),
    ("expression", ".z.D-1", ".z.D-1"),
])
def test_a_value_is_written_as_the_q_it_was_declared_to_be(q_type, value, written):
    assert as_q(_p(q_type=q_type), value) == written


def test_the_same_text_is_a_symbol_or_a_string_depending_only_on_the_declaration():
    """Which is the whole reason a q type is stored beside the control's kind:
    one text box feeds `sym in (…)` or `note like (…)`."""
    assert as_q(_p(q_type="symbol"), "AAPL") == "`AAPL"
    assert as_q(_p(q_type="string"), "AAPL") == '"AAPL"'


def test_a_string_has_its_quotes_escaped():
    assert as_q(_p(q_type="string"), 'say "hi"') == '"say \\"hi\\""'


def test_a_date_control_hands_over_a_date_object_and_it_still_works():
    assert as_q(_p(kind="date"), date(2026, 7, 30)) == "2026.07.30"


def test_a_kind_writes_itself_when_the_author_said_nothing():
    assert as_q(_p(kind="date"), "2026-07-30") == "2026.07.30"
    assert as_q(_p(kind="number"), "5") == "5"
    assert as_q(_p(kind="toggle"), "yes") == "1b"
    assert as_q(_p(kind="text"), "AAPL") == "`AAPL"


# --- into the query ----------------------------------------------------------

def test_a_raw_query_takes_the_value():
    ds = _raw("select from QATT where sym={{param:sym}}")
    assert _q(ds, {"sym": "`AAPL"}) == "select from QATT where sym=`AAPL"


def test_a_parameter_can_appear_as_often_as_it_likes():
    ds = _raw("select from t where a={{param:s}}, b={{param:s}}")
    assert _q(ds, {"s": "`X"}) == "select from t where a=`X, b=`X"


def test_several_parameters_in_one_query():
    ds = _raw("select from t where sym={{param:sym}}, date={{param:d}}, "
              "qty>{{param:n}}")
    got = _q(ds, {"sym": "`AAPL", "d": "2026.07.30", "n": "100"})
    assert got == ("select from t where sym=`AAPL, date=2026.07.30, qty>100")


def test_a_guided_filter_takes_one_too():
    """qfmt passes a placeholder through rather than quoting it, so a guided
    filter and a raw query arrive at the same substitution by the same route."""
    ds = Dataset(name="d", env="orders", table="target", mode="guided",
                 filters=[Filter(column="sym", op="=", value="{{param:sym}}",
                                 value_type="symbol")])
    assert _q(ds, {"sym": "`AAPL"}) == "select from target where sym=`AAPL"


def test_an_unfilled_parameter_is_left_alone_so_the_query_names_it():
    """Better a query that fails naming the token than one silently missing a
    predicate — that one returns the whole table and looks like it worked."""
    ds = _raw("select from t where sym={{param:sym}}")
    assert "{{param:sym}}" in _q(ds, {})


def test_a_query_with_no_parameters_is_untouched():
    ds = _raw("select from t where date=.z.D")
    assert _q(ds, {"sym": "`AAPL"}) == "select from t where date=.z.D"


def test_a_parameter_reference_does_not_disturb_the_other_kinds():
    ds = _raw("select from t where date within ({{date_from}};{{date_to}}), "
              "sym={{param:sym}}")
    out = build_qsql(ds, ResolvedTime("historical", date(2026, 7, 30),
                                      date(2026, 7, 31)),
                     outputs={}, params={"sym": "`AAPL"})
    assert "2026.07.30" in out and "2026.07.31" in out and "`AAPL" in out


# --- which parameters cost a round trip --------------------------------------

def test_a_parameter_in_a_query_is_known_to_be_one():
    board = Dashboard(id=1, name="d",
                      datasets=[_raw("select from t where sym={{param:sym}}")])
    assert query_params(board) == {"sym"}


def test_a_parameter_in_a_guided_filter_counts_too():
    ds = Dataset(name="d", env="e", table="t", mode="guided",
                 filters=[Filter(column="sym", op="in", value="{{param:sym}}",
                                 value_type="symbol")])
    assert query_params(Dashboard(id=1, name="d", datasets=[ds])) == {"sym"}


def test_a_parameter_only_a_transform_reads_is_not_a_query_parameter():
    from kdbmonitor.core.dashboard_models import Transform

    ds = _raw("select from t")
    ds.transforms = [Transform(kind="filter", params={
        "column": "sym", "op": "=", "value": "{{param:sym}}"})]
    board = Dashboard(id=1, name="d", datasets=[ds])
    assert query_params(board) == set()
    assert unresolved_params(board) == {"sym"}      # still has to be declared


def test_a_typo_in_a_query_is_caught_by_the_same_check_as_everywhere_else():
    """It used to slip through and arrive as a parse error from kdb naming a
    token it had never heard of."""
    board = Dashboard(id=1, name="d",
                      datasets=[_raw("select from t where sym={{param:smy}}")])
    assert "smy" in unresolved_params(board)


# --- resolving a whole form --------------------------------------------------

def test_q_values_formats_every_declared_parameter():
    params = [_p("sym", default="AAPL"),
              _p("d", kind="date", default="2026-07-30"),
              _p("n", kind="number", default="100")]
    got = q_values(params, chosen={}, frames={})
    assert got == {"sym": "`AAPL", "d": "2026.07.30", "n": "100"}


def test_what_the_reader_chose_wins_over_the_default():
    got = q_values([_p("sym", default="AAPL")], chosen={"sym": "MSFT"}, frames={})
    assert got == {"sym": "`MSFT"}


def test_a_dropdown_offering_nothing_still_falls_back_to_its_default():
    """A 'choice' with no choices has nothing to honour a pick with — the same
    rule the transforms have always followed, now reaching the query too."""
    p = Parameter(name="venue", kind="choice", default="LSE")
    assert q_values([p], chosen={"venue": "XETR"}, frames={}) == {"venue": "`LSE"}


def test_a_dropdown_honours_a_pick_that_is_on_offer():
    p = Parameter(name="venue", kind="choice", choices=["LSE", "XETR"],
                  default="LSE")
    assert q_values([p], chosen={"venue": "XETR"}, frames={}) == {"venue": "`XETR"}


def test_a_blank_with_no_default_is_left_out_rather_than_becoming_an_empty_symbol():
    """`` is a valid q symbol, so substituting one would quietly ask for rows
    whose sym is null instead of admitting nobody filled the box in."""
    assert q_values([_p("sym", default="")], chosen={}, frames={}) == {}


def test_a_value_that_cannot_be_formatted_is_left_out_too():
    got = q_values([_p("d", kind="date", default="not a date")],
                   chosen={}, frames={})
    assert got == {}
