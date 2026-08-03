"""Writing and reading q in the app: a box that behaves, and coloured output.

Two halves, and they fail independently on purpose.

:func:`q_block` is the printed half — line numbers and colour, rendered from
:mod:`kdbmonitor.core.qhighlight`, entirely in Python. It cannot fail in a way
that costs anybody their query, and it is what every place that shows a query
now uses.

:func:`q_area` is the input half. It is a plain ``st.text_area`` — the real
widget, holding the real value — with a small script that finds it in the page
and adds the things a text box lacks: a line-number gutter, Tab for indent
rather than for leaving the field, and a monospace face. The value never
travels through that script. If it does not run at all — an old browser, a
locked-down frame, Streamlit moving its DOM around — the box is still a
working box, which is the whole reason the editor was not replaced by a
third-party component.
"""
from __future__ import annotations

import html
import json

import streamlit as st
import streamlit.components.v1 as components

from kdbmonitor.core.qhighlight import tokenize

# Colours from the app's own palette (.streamlit/config.toml), assigned by what
# each token *is* rather than by fashion: the app's placeholders take the
# primary blue because they are the one thing on screen that is not q, and
# names — tables, columns, whatever the query is actually about — stay body
# text so the highlighting sits behind the query instead of on top of it.
_COLOURS = {
    "comment": "#8b98a5",
    "string": "#3fb950",
    "symbol": "#5b9dff",
    "placeholder": "#3b82f6",
    "keyword": "#a371f7",
    "system": "#d29922",
    "number": "#d29922",
    "name": "#dfe7ef",
    "operator": "#8b98a5",
    "space": "",
}

_GUTTER = "#5a6672"
_RULE = "rgba(128, 128, 128, 0.25)"

_BLOCK_CSS = f"""
<style>
.kdbq {{
  font-family: var(--font-code, 'JetBrains Mono', ui-monospace, Consolas, monospace);
  font-size: 13px;
  line-height: 1.55;
  background: #0e141b;
  border: 1px solid {_RULE};
  border-radius: 6px;
  padding: 8px 0;
  overflow-x: auto;
  margin-bottom: 6px;
}}
.kdbq-line {{ display: flex; white-space: pre; }}
.kdbq-n {{
  flex: 0 0 auto;
  width: 3ch;
  margin-right: 12px;
  padding-left: 10px;
  text-align: right;
  color: {_GUTTER};
  user-select: none;
}}
.kdbq-src {{ flex: 1 1 auto; padding-right: 12px; }}
.kdbq-placeholder {{
  background: rgba(59, 130, 246, 0.14);
  border-radius: 3px;
}}
.kdbq-comment {{ font-style: italic; }}
.kdbq-keyword {{ font-weight: 500; }}
</style>
"""


def js_literal(value) -> str:
    r"""``value`` as a JavaScript literal safe to sit inside a ``<script>``.

    JSON is a subset of JavaScript, so ``json.dumps`` is nearly the whole
    answer — except that it leaves ``/`` alone, and the HTML parser ends a
    script at the first ``</script`` it sees whether or not that text is inside
    a string. A query holding one, in a comment or a string literal, would close
    the script early and spill its own tail into the page as markup.

    ``<\/`` is the same character to JavaScript and not a closing tag to the
    HTML parser, which is what makes the two readings agree.
    """
    return json.dumps(value).replace("</", "<\\/")


def to_html(text: str, *, empty: str = "(empty)") -> str:
    """``text`` as line-numbered, coloured q."""
    lines = tokenize(text or "")
    if not (text or "").strip():
        return (f'{_BLOCK_CSS}<div class="kdbq"><div class="kdbq-line">'
                f'<span class="kdbq-n">1</span>'
                f'<span class="kdbq-src" style="color:{_COLOURS["comment"]}">'
                f'{html.escape(empty)}</span></div></div>')

    rows = []
    for n, tokens in enumerate(lines, start=1):
        painted = []
        for token in tokens:
            escaped = html.escape(token.text)
            colour = _COLOURS.get(token.kind, "")
            if not colour:
                painted.append(escaped)
                continue
            painted.append(f'<span class="kdbq-{token.kind}" '
                           f'style="color:{colour}">{escaped}</span>')
        rows.append(f'<div class="kdbq-line"><span class="kdbq-n">{n}</span>'
                    f'<span class="kdbq-src">{"".join(painted)}</span></div>')
    return f'{_BLOCK_CSS}<div class="kdbq">{"".join(rows)}</div>'


# The copy button. A component of its own rather than an ``st.button``, because
# Streamlit has no clipboard and could not have one: writing to it needs the
# browser's own record of a user gesture, and a Streamlit button spends that
# gesture on a round trip to Python. By the time the script reruns, the click
# that would have been allowed to write is over.
#
# Two ways to write, in order. ``navigator.clipboard`` is the real API and needs
# a secure context (https, or localhost) — which the app usually is, and
# sometimes is not, since these dashboards get served over plain http on a desk
# network. ``execCommand('copy')`` is the old way: deprecated, no permissions,
# and works in every browser this is likely to meet. Neither is assumed; the
# button says which one happened, and says so plainly when neither did, because
# "nothing appeared to happen" is the one outcome worth ruling out for a control
# whose whole result is invisible.
_COPY_HTML = """
<style>
  html, body { margin: 0; padding: 0; background: transparent; }
  button {
    font-family: var(--font, ui-sans-serif, system-ui, sans-serif);
    font-size: 13px; line-height: 1;
    color: #dfe7ef; background: #0e141b;
    border: 1px solid rgba(128, 128, 128, 0.35); border-radius: 6px;
    padding: 6px 12px; cursor: pointer; width: 100%;
  }
  button:hover { border-color: rgba(128, 128, 128, 0.7); }
  button:disabled { cursor: default; color: #8b98a5; }
</style>
<button id="c">__LABEL_TEXT__</button>
<script>
(function(){
  var TEXT = __TEXT__, LABEL = __LABEL__;
  var btn = document.getElementById('c');

  function say(message){
    btn.textContent = message;
    btn.disabled = true;
    setTimeout(function(){ btn.textContent = LABEL; btn.disabled = false; }, 1600);
  }

  function legacy(){
    // Off-screen rather than hidden: a field that is display:none or
    // visibility:hidden cannot be selected, and an unselected field copies
    // nothing at all.
    try {
      var ta = document.createElement('textarea');
      ta.value = TEXT;
      ta.setAttribute('readonly', '');
      ta.style.cssText = 'position:absolute;left:-9999px;top:0;';
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand('copy');
      document.body.removeChild(ta);
      say(ok ? 'Copied' : 'Cannot copy here');
    } catch(e) { say('Cannot copy here'); }
  }

  btn.addEventListener('click', function(){
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(TEXT).then(
          function(){ say('Copied'); }, legacy);
        return;
      }
    } catch(e) {}
    legacy();
  });
})();
</script>
"""

COPY_HEIGHT = 38            # the button plus the room its border needs


def copy_button(text: str, *, label: str = "Copy the query") -> None:
    """A button that puts ``text`` on the reader's clipboard.

    For text that is shown but not editable — a query the app built, which is
    exactly the thing somebody wants in their own q session the moment it comes
    back with an error. Selecting it by hand is not the same: the block it is
    shown in carries line numbers, and dragging across them takes the numbers
    with the query.

    ``text`` travels as a JSON literal, so a query full of quotes, backslashes
    and newlines arrives as itself rather than as something that closes the
    script early. The label goes in twice and differently — as a JSON literal
    for the script, and HTML-escaped for the button's own face, where a JSON
    literal would print its quotes.
    """
    components.html(
        _COPY_HTML.replace("__TEXT__", js_literal(text or ""))
                  .replace("__LABEL_TEXT__", html.escape(label))
                  .replace("__LABEL__", js_literal(label)),
        height=COPY_HEIGHT)


def q_block(text: str, *, empty: str = "(empty)", copy: bool = False) -> None:
    """Show a query, coloured and numbered. Replaces st.code for q.

    ``copy`` adds a button that puts the query on the clipboard. Offered rather
    than always on, since it costs a row of height, and left off where the query
    is already sitting in an editable box beside it.
    """
    st.markdown(to_html(text, empty=empty), unsafe_allow_html=True)
    if copy and (text or "").strip():
        # Under the block, not over it: the query is what was asked for and the
        # button is what you can do about it, and a control above the thing it
        # acts on reads as a heading for it.
        _, right = st.columns([3, 1])
        with right:
            copy_button(text)


# --- the input --------------------------------------------------------------
#
# The script runs in a components iframe and reaches out to the page for the
# one text area it was given the key of — Streamlit stamps `st-key-<key>` onto
# the widget's container, which is what makes "the one" possible at all. Every
# step is wrapped so that a failure leaves an ordinary text area behind rather
# than a broken one.
#
# Tab and Enter are applied with execCommand('insertText') rather than by
# assigning to .value: React does not see an assignment, so the typed query
# would look right on screen and reach Python as whatever it was before.
# execCommand raises a real input event, and it leaves the browser's own undo
# stack intact, which assignment also destroys.
_EDITOR_JS = """
<script>
(function(){
  var KEY = __KEY__, INDENT = __INDENT__;
  var doc;
  try { doc = window.parent.document; } catch(e) { return; }

  function editor(){
    var root = doc.querySelector('.st-key-' + KEY);
    return root ? root.querySelector('textarea') : null;
  }

  function insert(ta, text){
    ta.focus();
    if(doc.execCommand){ doc.execCommand('insertText', false, text); return; }
    // Nothing else can tell React what happened, so rather than write a value
    // it will not read, do nothing and let Tab behave as Tab.
  }

  function leadingSpace(line){
    var m = /^[ \\t]*/.exec(line);
    return m ? m[0] : '';
  }

  function onKey(ev){
    var ta = ev.target;
    if(ev.key === 'Tab' && !ev.ctrlKey && !ev.altKey){
      // Shift+Tab keeps its meaning: there has to be a way out of the field
      // with the keyboard alone.
      if(ev.shiftKey){ return; }
      ev.preventDefault();
      insert(ta, INDENT);
      return;
    }
    if(ev.key === 'Enter' && !ev.shiftKey && !ev.ctrlKey){
      var upto = ta.value.slice(0, ta.selectionStart);
      var line = upto.slice(upto.lastIndexOf('\\n') + 1);
      var pad = leadingSpace(line);
      if(pad){ ev.preventDefault(); insert(ta, '\\n' + pad); }
    }
  }

  function paint(ta, gutter){
    var n = ta.value.split('\\n').length;
    var want = [];
    for(var i = 1; i <= n; i++){ want.push(i); }
    var text = want.join('\\n');
    if(gutter.textContent !== text){ gutter.textContent = text; }
    gutter.scrollTop = ta.scrollTop;
  }

  function attach(){
    var ta = editor();
    if(!ta){ return false; }
    var style = window.parent.getComputedStyle(ta);
    ta.style.fontFamily = "var(--font-code, 'JetBrains Mono', ui-monospace, Consolas, monospace)";
    ta.style.tabSize = String(INDENT.length);
    // A computed line-height of 'normal' is a number only the font knows, and
    // a gutter that guesses it drifts a pixel per line until the numbers name
    // the wrong lines. Pin both sides to the same value instead.
    var lineHeight = style.lineHeight;
    if(!lineHeight || lineHeight === 'normal'){
      lineHeight = '1.5';
      ta.style.lineHeight = lineHeight;
    }
    if(ta.dataset.kdbq === '1'){ paint(ta, ta._kdbqGutter); return true; }
    ta.dataset.kdbq = '1';

    var wrap = ta.parentElement;
    if(!wrap){ return true; }
    wrap.style.position = 'relative';

    var gutter = doc.createElement('div');
    // Mirrors the text area's own metrics rather than setting its own, so the
    // numbers line up with the lines whatever the theme's font size is.
    gutter.style.cssText = [
      'position:absolute', 'left:0', 'top:0', 'bottom:0', 'width:2.6em',
      'padding-top:' + style.paddingTop, 'padding-right:6px',
      'font-family:' + ta.style.fontFamily, 'font-size:' + style.fontSize,
      'line-height:' + lineHeight, 'text-align:right',
      'color:__GUTTER__', 'overflow:hidden', 'pointer-events:none',
      'user-select:none', 'white-space:pre', 'opacity:0.85'
    ].join(';');
    wrap.insertBefore(gutter, ta);
    ta._kdbqGutter = gutter;
    ta.style.paddingLeft = 'calc(2.6em + 10px)';

    ta.addEventListener('keydown', onKey);
    ta.addEventListener('input', function(){ paint(ta, gutter); });
    ta.addEventListener('scroll', function(){ gutter.scrollTop = ta.scrollTop; });
    paint(ta, gutter);
    return true;
  }

  try {
    if(!attach()){
      // Streamlit may not have drawn the widget yet on a first paint.
      var tries = 0;
      var timer = setInterval(function(){
        tries += 1;
        var done = false;
        try { done = attach(); } catch(e) { done = true; }
        if(done || tries > 20){ clearInterval(timer); }
      }, 100);
    }
  } catch(e){}
})();
</script>
"""


def _editor_script(key: str, indent: int) -> str:
    # Through js_literal like everything else that goes into a script, even
    # though a widget key is the app's own word and could not carry a closing
    # tag. Two ways of writing a literal into a page is one more than there
    # should be, and the safe one costs nothing.
    return (_EDITOR_JS
            .replace("__KEY__", js_literal(key))
            .replace("__INDENT__", js_literal(" " * indent))
            .replace("__GUTTER__", _GUTTER))


def q_area(label: str, *, key: str, value: str | None = None, height: int = 160,
           help: str | None = None, indent: int = 2,
           label_visibility: str = "visible") -> str:
    """A q text area with line numbers, Tab-to-indent and a monospace face.

    The returned value is the text area's, unchanged — the enhancements are
    presentation and key handling, and none of them stand between what is typed
    and what is saved.
    """
    kwargs = {"key": key, "height": height, "help": help,
              "label_visibility": label_visibility}
    if value is not None:
        kwargs["value"] = value
    text = st.text_area(label, **kwargs)
    # Zero height: the component is a script, not something to look at.
    components.html(_editor_script(key, indent), height=0)
    return text
