"""Copying a query that has been shown but cannot be edited.

The query the app built is the first thing anybody wants in their own q session
when it comes back with an error, and selecting it by hand takes the block's
line numbers with it. Streamlit has no clipboard, so the button is a component
that runs in the browser — which means the query has to survive being written
into a page as a string, and that is what most of this is about.
"""
import json

import pytest

from kdbmonitor.ui import qeditor


@pytest.fixture()
def sent(monkeypatch):
    """What reached ``components.html``, per call."""
    calls = []
    monkeypatch.setattr(qeditor.components, "html",
                        lambda body, **kw: calls.append((body, kw)))
    return calls


def _payload(body: str) -> str:
    """The query as the script will see it, read back out of the page."""
    line = next(ln for ln in body.splitlines() if ln.strip().startswith("var TEXT"))
    return json.loads(line.split("=", 1)[1].split(", LABEL")[0].strip())


# --- the query survives the trip into a page ---------------------------------

def test_the_query_reaches_the_button_unchanged(sent):
    qeditor.copy_button("select from trade where sym=`VOD.L")
    assert _payload(sent[0][0]) == "select from trade where sym=`VOD.L"


def test_a_query_with_quotes_and_backslashes_arrives_as_itself(sent):
    """The failure this guards: a literal that closes the script early leaves a
    broken page, and it is exactly the queries with strings in them — the ones
    most worth copying — that would do it."""
    q = 'select from t where s like "a\\*b", note="he said \\"no\\""'
    qeditor.copy_button(q)
    assert _payload(sent[0][0]) == q


def test_a_multi_line_query_keeps_its_lines(sent):
    q = "t: select from trade\nselect sum size by sym from t"
    qeditor.copy_button(q)
    assert _payload(sent[0][0]) == q
    assert "\n</script>" not in _payload(sent[0][0])


def test_a_closing_script_tag_in_a_query_cannot_end_the_script(sent):
    """The HTML parser ends a script at the first '</script' whether or not it
    is inside a string, so JSON alone is not enough — it leaves '/' as it is.
    A query carrying one would have closed the script and spilled its own tail
    into the page as markup."""
    q = "/ </script><script>alert(1)</script>"
    qeditor.copy_button(q)
    body = sent[0][0]
    assert "</script><script>" not in body
    assert body.count("</script>") == 1              # the one that is really it
    assert _payload(body) == q                       # and it still arrives whole


def test_the_label_prints_as_words_not_as_a_json_literal(sent):
    """It goes in twice and differently: quoted for the script, plain for the
    button's own face."""
    qeditor.copy_button("select from t", label="Copy the query")
    body = sent[0][0]
    assert ">Copy the query</button>" in body
    assert 'var TEXT = "select from t", LABEL = "Copy the query"' in body


def test_the_button_is_given_room_to_draw_in(sent):
    """A components iframe with no height is an invisible button."""
    qeditor.copy_button("select from t")
    assert sent[0][1]["height"] == qeditor.COPY_HEIGHT


# --- when the block offers one ------------------------------------------------

def test_a_block_asked_for_a_copy_button_renders_one(sent):
    qeditor.q_block("select from t", copy=True)
    assert len(sent) == 1


def test_a_block_not_asked_for_one_renders_none(sent):
    qeditor.q_block("select from t")
    assert sent == []


@pytest.mark.parametrize("text", ["", "   ", None])
def test_there_is_nothing_to_copy_from_an_empty_block(sent, text):
    """A dataset that never built a query shows '(no query)' — a button that
    would put nothing on the clipboard is worse than no button."""
    qeditor.q_block(text, empty="(no query)", copy=True)
    assert sent == []


# --- both ways of writing to the clipboard are there --------------------------

def test_the_old_way_is_kept_for_where_the_new_one_is_refused(sent):
    """navigator.clipboard needs a secure context, and these dashboards get
    served over plain http on desk networks."""
    body = qeditor.copy_button("select from t") or sent[0][0]
    assert "navigator.clipboard" in body and "execCommand('copy')" in body


def test_the_button_says_when_it_could_not_copy(sent):
    """Its whole result is invisible, so 'nothing appeared to happen' is the
    one outcome that has to be ruled out."""
    qeditor.copy_button("select from t")
    assert "Cannot copy here" in sent[0][0]


# --- where the Data section shows it ------------------------------------------

def _traced(qsql: str, error: str):
    from kdbmonitor.core.dataset import DatasetTrace

    return {"orders": DatasetTrace("orders", qsql, error)}


def test_a_dataset_that_failed_offers_its_query_to_be_copied(sent):
    """The reason for all of this: a query that came back with an error is one
    you want to run somewhere you can poke at it."""
    from kdbmonitor.ui.dashboard_editor import _render_dataset_results

    q = "select from trade where date within (2026.08.01;2026.08.03)"
    _render_dataset_results(_traced(q, "'trade"))
    assert len(sent) == 1
    assert _payload(sent[0][0]) == q


def test_a_dataset_that_never_built_a_query_offers_nothing(sent):
    from kdbmonitor.ui.dashboard_editor import _render_dataset_results

    _render_dataset_results(_traced("", "environment 'prod' has no realtime "
                                        "server — add one in Admin"))
    assert sent == []
