"""Reading q well enough to colour it.

Nothing off the shelf highlights q. The editors that come closest are told it
is SQL, which is worse than plain text for somebody learning the language: a
``/`` comment goes uncoloured, ``select 10 % 2`` and ``sum / til 10`` are read
as the same kind of thing, a backtick symbol looks like a stray character, and
half of SQL's own vocabulary lights up on words q has never heard of.

So the tokenizer is here, in one pure function, and the UI is only responsible
for painting what it says. Two rules carry most of the weight and neither is
guessable from a SQL grammar:

* ``/`` is a comment only at the start of a line or after whitespace. Anywhere
  else it is the *over* adverb or division — ``sum/`` and ``x%y`` are code, and
  colouring the rest of the line green there would be actively misleading.
* a line that is nothing but ``/`` opens a comment block, closed by a line that
  is nothing but ``\\``.

``{{step1.sym}}`` and ``{{param:venue}}`` are not q at all — they are this
app's own placeholders, filled in before the query is sent — so they get a
token of their own rather than being read as a lambda.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Token kinds the renderer knows how to paint.
KINDS = ("comment", "string", "symbol", "placeholder", "keyword", "system",
         "number", "name", "operator", "space")


@dataclass(frozen=True)
class Token:
    kind: str
    text: str


# q's reserved words: the qSQL clauses first, then the built-in verbs. A word
# not in here is a name — a table, a column, something the user defined — and
# is left in the body colour, which is what makes the keywords worth colouring.
KEYWORDS: frozenset[str] = frozenset("""
select exec update delete insert upsert from where by fby
abs acos aj aj0 ajf all and any asc asin asof atan attr avg avgs
bin binr ceiling cols cor cos count cov cross csv cut
deltas desc dev differ distinct div do dsave
each ej ema enlist eval except exit exp
fills first fkeys flip floor
get getenv group gtime
hclose hcount hdel hopen hsym
iasc idesc if ij ijf in inter inv
key keys last like lj ljf load log lower lsq ltime ltrim
mavg max maxs mcount md5 mdev med meta min mins mmax mmin mmu mod msum
neg next not null
or over parse peach pj prd prds prev prior
rand rank ratios raze read0 read1 reciprocal reval reverse rload rotate rsave rtrim
save scan scov sdev set setenv show signum sin sqrt ss ssr string sublist sum sums sv svar system
tables tan til trim type
uj ujf ungroup union upper
value var view views vs
wavg where while within wj wj1 wsum
xasc xbar xcol xcols xdesc xexp xgroup xkey xlog xprev xrank
""".split())

# Namespaces q reserves for itself. `.z.D` is the date the same way `til` is a
# verb — a name the language already owns, not one this query invented.
SYSTEM_NAMESPACES = (".z.", ".Q.", ".q.", ".j.", ".h.", ".m.", ".u.", ".s.")

_PLACEHOLDER_OPEN = "{{"
_PLACEHOLDER_CLOSE = "}}"

# Ordered: the null/infinity literals and the dated forms have to be tried
# before the plain number they start with, or 0N reads as 0 followed by a name
# and 2026.07.30 as three separate numbers.
_NUMBER = re.compile(r"""
    0[NnWw][ghijefpmdznuvt]?
  | 0[xX][0-9a-fA-F]+
  | \d{4}\.\d{2}\.\d{2}(?:[DT]\d{2}(?::\d{2}(?::\d{2}(?:\.\d+)?)?)?)?
  | \d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?
  | \d+\.\d*(?:[eE][+-]?\d+)?
  | \.\d+(?:[eE][+-]?\d+)?
  | \d+(?:[eE][+-]?\d+)?
""", re.VERBOSE)

# A type suffix belongs to the number only when nothing follows it that would
# make it a name: 1b and 2i are typed literals, 1binary is not.
_TYPE_SUFFIX = "bhijefpmdznuvtg"

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_DOTTED = re.compile(r"\.[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*")
_SPACE = re.compile(r"[ \t]+")


def _scan_string(line: str, i: int) -> int:
    """Index just past the string literal starting at ``i``.

    An unterminated string runs to the end of the line rather than swallowing
    the rest of the query: half-typed code is the normal state of a text box,
    and the colouring should degrade one line at a time.
    """
    j = i + 1
    while j < len(line):
        if line[j] == "\\" and j + 1 < len(line):
            j += 2
            continue
        if line[j] == '"':
            return j + 1
        j += 1
    return len(line)


def _scan_symbol(line: str, i: int) -> int:
    """Index just past the backtick symbol at ``i``.

    A bare backtick is the null symbol, and `:localhost:5000 is a handle, so
    colons count as part of the name here.
    """
    j = i + 1
    while j < len(line) and (line[j].isalnum() or line[j] in "._:"):
        j += 1
    return j


def _scan_number(line: str, i: int) -> int:
    m = _NUMBER.match(line, i)
    if not m:
        return i + 1
    end = m.end()
    if (end < len(line) and line[end] in _TYPE_SUFFIX
            and not (end + 1 < len(line)
                     and (line[end + 1].isalnum() or line[end + 1] == "_"))):
        end += 1
    return end


def _scan_line(line: str) -> list[Token]:
    out: list[Token] = []
    i, n = 0, len(line)
    while i < n:
        char = line[i]

        space = _SPACE.match(line, i)
        if space:
            out.append(Token("space", space.group()))
            i = space.end()
            continue

        if line.startswith(_PLACEHOLDER_OPEN, i):
            close = line.find(_PLACEHOLDER_CLOSE, i)
            end = n if close == -1 else close + len(_PLACEHOLDER_CLOSE)
            out.append(Token("placeholder", line[i:end]))
            i = end
            continue

        # Only at the start of a line or after whitespace. Elsewhere this is
        # over/division, and the code after it is code.
        if char == "/" and (i == 0 or line[i - 1] in " \t"):
            out.append(Token("comment", line[i:]))
            return out

        if char == '"':
            end = _scan_string(line, i)
            out.append(Token("string", line[i:end]))
            i = end
            continue

        if char == "`":
            end = _scan_symbol(line, i)
            out.append(Token("symbol", line[i:end]))
            i = end
            continue

        if char.isdigit() or (char == "." and i + 1 < n and line[i + 1].isdigit()):
            end = _scan_number(line, i)
            out.append(Token("number", line[i:end]))
            i = end
            continue

        dotted = _DOTTED.match(line, i)
        if dotted:
            word = dotted.group()
            kind = ("system" if word.startswith(SYSTEM_NAMESPACES) else "name")
            out.append(Token(kind, word))
            i = dotted.end()
            continue

        word = _NAME.match(line, i)
        if word:
            text = word.group()
            out.append(Token("keyword" if text in KEYWORDS else "name", text))
            i = word.end()
            continue

        out.append(Token("operator", char))
        i += 1
    return out


def tokenize(text: str) -> list[list[Token]]:
    """Every line of ``text`` as its tokens, in order.

    The tokens of a line always rejoin to exactly that line — nothing is
    dropped or invented — so a renderer can paint them without ever changing
    what the user typed.
    """
    lines: list[list[Token]] = []
    in_block = False
    for line in (text or "").split("\n"):
        if in_block:
            lines.append([Token("comment", line)])
            if line.strip() == "\\":
                in_block = False
            continue
        if line.strip() == "/":
            in_block = True
            lines.append([Token("comment", line)])
            continue
        lines.append(_scan_line(line))
    return lines
