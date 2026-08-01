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


def q_block(text: str, *, empty: str = "(empty)") -> None:
    """Show a query, coloured and numbered. Replaces st.code for q."""
    st.markdown(to_html(text, empty=empty), unsafe_allow_html=True)


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
    return (_EDITOR_JS
            .replace("__KEY__", json.dumps(key))
            .replace("__INDENT__", json.dumps(" " * indent))
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
