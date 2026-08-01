"""Colouring q, and the two rules that make it q rather than SQL.

Nothing off the shelf highlights q, and the editors that come closest are told
it is SQL — which gets `/` and backticks wrong in ways that would teach a
reader the wrong language. These tests are that claim, held to.
"""
import pytest

from kdbmonitor.core.qhighlight import Token, tokenize
from kdbmonitor.ui.qeditor import to_html


def kinds(text: str, want: str) -> list[str]:
    """The text of every token of kind ``want``, across all lines."""
    return [t.text for line in tokenize(text) for t in line if t.kind == want]


def one_line(text: str) -> list[Token]:
    lines = tokenize(text)
    assert len(lines) == 1
    return lines[0]


# --- nothing is lost ---------------------------------------------------------

@pytest.mark.parametrize("source", [
    "",
    "select from t",
    "/ a comment",
    'select from t where sym in `AAPL`MSFT, px>100.5, note like "*fill*"',
    "{[x] x*2}",
    "a:1;b:2;\n\nc:a+b\n",
    "/\nblock\n\\\nselect from t",
    "\t indented \t",
    "select from t where date=.z.D-1, {{step1.sym}}",
    "unterminated \"string",
    "`",
])
def test_the_tokens_rejoin_to_exactly_what_was_typed(source):
    """A renderer paints what this returns, so anything dropped here is
    silently dropped from the user's query on screen."""
    assert "\n".join("".join(t.text for t in line)
                     for line in tokenize(source)) == source


def test_every_token_carries_a_known_kind():
    from kdbmonitor.core.qhighlight import KINDS

    source = 'select from t where sym=`AAPL, px>1.5 / note\n{{p}} .z.D "s"'
    assert all(t.kind in KINDS for line in tokenize(source) for t in line)


# --- comments: the rule SQL gets wrong ---------------------------------------

def test_a_slash_starting_a_line_is_a_comment():
    assert kinds("/ active orders only", "comment") == ["/ active orders only"]


def test_a_slash_after_a_space_is_a_comment():
    assert kinds("select from t  / today's", "comment") == ["/ today's"]


def test_a_slash_against_a_token_is_over_not_a_comment():
    """`sum/` is the over adverb and `x%y`'s cousin — colouring the rest of the
    line as a comment there would hide real code."""
    assert kinds("total:sum/ til 10", "comment") == []
    assert kinds("r:count/[x]", "comment") == []


def test_division_is_not_a_comment():
    assert kinds("select px%qty from t", "comment") == []


def test_a_lone_slash_line_opens_a_comment_block():
    source = "/\nnone of this runs\nselect from t\n\\\nselect from live"
    commented = kinds(source, "comment")
    assert "none of this runs" in commented
    assert "select from t" in commented          # inside the block, not code
    assert kinds(source, "keyword") == ["select", "from"]   # only the last line


def test_a_block_that_is_never_closed_stays_a_comment():
    source = "/\nstill commented\nand this too"
    assert len(kinds(source, "comment")) == 3


# --- the rest of q -----------------------------------------------------------

def test_a_backtick_symbol_is_its_own_thing():
    assert kinds("select from t where sym in `AAPL`MSFT", "symbol") == [
        "`AAPL", "`MSFT"]


def test_a_bare_backtick_is_the_null_symbol():
    assert kinds("x:`", "symbol") == ["`"]


def test_a_handle_keeps_its_colons():
    assert kinds("h:hopen `:localhost:5000", "symbol") == ["`:localhost:5000"]


def test_a_string_is_a_string_and_a_backtick_inside_it_is_not_a_symbol():
    assert kinds('note like "a `b c"', "string") == ['"a `b c"']
    assert kinds('note like "a `b c"', "symbol") == []


def test_an_escaped_quote_does_not_end_the_string():
    assert kinds(r'x:"say \"hi\" now"', "string") == [r'"say \"hi\" now"']


def test_an_unterminated_string_stops_at_the_end_of_its_line():
    source = 'x:"open\nselect from t'
    assert kinds(source, "string") == ['"open']
    assert kinds(source, "keyword") == ["select", "from"]   # next line is code


def test_the_qsql_clauses_are_keywords():
    got = kinds("select sum qty by sym from t where date=.z.D", "keyword")
    assert got == ["select", "sum", "by", "from", "where"]


def test_a_table_or_column_name_is_not_a_keyword():
    assert kinds("select orderId from target", "name") == ["orderId", "target"]


def test_a_reserved_namespace_is_marked_as_the_language_s_own():
    assert kinds("select from t where date=.z.D", "system") == [".z.D"]
    assert kinds("x:.Q.dd[`a;`b]", "system") == [".Q.dd"]


def test_a_users_own_namespace_is_just_a_name():
    assert kinds("x:.mydesk.helper[1]", "name") == ["x", ".mydesk.helper"]
    assert kinds("x:.mydesk.helper[1]", "system") == []


@pytest.mark.parametrize("literal", [
    "100", "1.5", ".5", "1e6", "0x1f", "1b", "0N", "0Wj",
    "2026.07.30", "2026.07.30D09:30:00.000", "09:30", "09:30:15.250",
])
def test_a_literal_is_read_whole(literal):
    assert kinds(f"x:{literal}", "number") == [literal]


def test_a_type_suffix_is_not_stolen_from_a_name():
    """1b is a boolean; `1 binary` is a number and then a name."""
    assert kinds("x:1 binary", "number") == ["1"]
    assert kinds("x:1 binary", "name") == ["x", "binary"]
    assert kinds("x:1b", "number") == ["1b"]


def test_a_date_is_one_number_not_three():
    assert kinds("select from t where date=2026.07.30", "number") == ["2026.07.30"]


# --- the app's own placeholders ---------------------------------------------

def test_a_reference_is_neither_q_nor_a_lambda():
    assert kinds("select from t where sym in {{step1.sym}}", "placeholder") == [
        "{{step1.sym}}"]


def test_a_parameter_and_a_connection_reference_too():
    source = "select from t where venue={{param:venue}}; h:hopen {{conn:PROD}}"
    assert kinds(source, "placeholder") == ["{{param:venue}}", "{{conn:PROD}}"]


def test_a_real_lambda_is_not_a_placeholder():
    assert kinds("f:{[x] x*2}", "placeholder") == []
    assert "{" in [t.text for t in one_line("f:{[x] x*2}") if t.kind == "operator"]


def test_an_unterminated_placeholder_does_not_eat_the_next_line():
    """Half-typed is the normal state of a text box; the colouring degrades one
    line at a time rather than turning the rest of the query into one token."""
    source = "select from t where sym in {{step1.sym\nselect from u"
    assert kinds(source, "placeholder") == ["{{step1.sym"]
    assert kinds(source, "keyword") == ["select", "from", "where", "in",
                                        "select", "from"]


# --- what the page is handed -------------------------------------------------

def test_the_html_numbers_every_line():
    html = to_html("a:1\nb:2\nc:3")
    assert '<span class="kdbq-n">1</span>' in html
    assert '<span class="kdbq-n">3</span>' in html
    assert '<span class="kdbq-n">4</span>' not in html


def test_the_html_escapes_what_it_prints():
    """A query is user input on its way into a page that allows HTML."""
    html = to_html('select from t where note like "<script>alert(1)</script>"')
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_an_ampersand_in_a_query_survives_as_itself():
    assert "&amp;" in to_html("x:a&b")


def test_an_empty_query_says_so_rather_than_printing_nothing():
    assert "(empty)" in to_html("")
    assert "(no query)" in to_html("   ", empty="(no query)")


def test_the_builder_s_raw_mode_draws_the_editor_and_the_colours():
    """The box is still an ordinary text area holding the real value — the
    line numbers and Tab are added to it, not put in front of it."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_string('''
import streamlit as st
from kdbmonitor.core.storage import Storage
from kdbmonitor.core.mock import demo_connection_specs
from kdbmonitor.ui import builder

store = Storage(":memory:")
store.init_db()
for spec in demo_connection_specs():
    store.add_connection(spec)
st.session_state.update({
    "b_nsteps": 1, "b_nf_0": 0, "b_mode_0": "Raw",
    "b_raw_0": "select from target where date=.z.D / today",
})
step = builder._step_block(store, 0, [c.name for c in store.list_connections()])
st.session_state["_mode"] = step.mode
st.session_state["_q"] = step.raw_qsql
''', default_timeout=60).run()

    assert not at.exception, [str(e.value) for e in at.exception]
    assert at.session_state["_mode"] == "raw"
    # Straight from the text area, unchanged by anything on the way.
    assert at.session_state["_q"] == "select from target where date=.z.D / today"
    assert any(el.key == "b_raw_0" for el in at.text_area)
    printed = " ".join(str(m.value) for m in at.markdown)
    assert "kdbq-line" in printed and "/ today" in printed


def test_the_colours_are_distinct_per_kind():
    from kdbmonitor.ui.qeditor import _COLOURS

    painted = [c for k, c in _COLOURS.items() if c and k != "operator"]
    # A comment must not be the colour of a name, and so on: the point of the
    # exercise is that the kinds are told apart at a glance.
    assert len(set(painted)) >= len(painted) - 1
    assert _COLOURS["comment"] != _COLOURS["name"]
    assert _COLOURS["keyword"] != _COLOURS["name"]
    assert _COLOURS["placeholder"] != _COLOURS["name"]
