"""Referencing the whole of an earlier dataset, not just one column of it.

``{{name.column}}`` was only ever able to ask "is it one of these" — it becomes
a q list for a where clause. ``{{table:name}}`` carries the rows themselves, so
a query can *join* against an earlier result: an uploaded file of order ids
matched row for row against the OMS, keeping what the file said beside each one.

Most of what follows is about the literal. A table written into a query has to
parse as q on arrival, and the ways that fails are quiet ones — a column name
that is not a q name, a null printed as "nan", a one-row table that is not a
table because parentheses do not make a list in q.
"""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from kdbmonitor.core.chain import substitute_refs
from kdbmonitor.core.dashboard_models import Dashboard, Dataset
from kdbmonitor.core.dataset import build_qsql
from kdbmonitor.core.qfmt import q_cell, q_column_type, q_table
from kdbmonitor.core.timectx import ResolvedTime

RT = ResolvedTime("realtime", None, None)


def _frame(**columns) -> pd.DataFrame:
    return pd.DataFrame(columns)


# --- what the columns are, in q's terms ---------------------------------------

@pytest.mark.parametrize("values, expected", [
    ([1, 2], "number"),
    ([1.5, 2.5], "number"),
    ([True, False], "boolean"),
    (["VOD.L", "HSBA.L"], "symbol"),
    (pd.to_datetime(["2026-08-03", "2026-08-04"]), "date"),
    (pd.to_datetime(["2026-08-03 09:15", "2026-08-04 00:00"]), "timestamp"),
    (pd.to_timedelta(["09:15:03", "10:00:00"]), "time"),
])
def test_a_column_is_read_from_its_dtype(values, expected):
    assert q_column_type(pd.Series(values)) == expected


def test_a_datetime_column_at_midnight_throughout_is_a_column_of_dates():
    """Which it is can only be told from the values: pandas holds both as
    datetime64, and q's date and timestamp are different types to join on."""
    midnight = pd.to_datetime(["2026-08-03", "2026-08-04"])
    assert q_column_type(pd.Series(midnight)) == "date"


# --- one value at a time ------------------------------------------------------

@pytest.mark.parametrize("value, kind, expected", [
    (7, "number", "7"),
    (True, "boolean", "1b"),
    (False, "boolean", "0b"),
    (date(2026, 8, 3), "date", "2026.08.03"),
    (pd.Timestamp("2026-08-03 09:15:03.221"), "timestamp",
     "2026.08.03D09:15:03.221000000"),
    (pd.Timedelta("09:15:03.221"), "time", "09:15:03.221"),
    ("VOD.L", "symbol", '`$"VOD.L"'),
])
def test_a_value_is_written_as_its_type(value, kind, expected):
    assert q_cell(value, kind) == expected


@pytest.mark.parametrize("kind, expected", [
    ("number", "0n"), ("boolean", "0b"), ("date", "0Nd"),
    ("timestamp", "0Np"), ("time", "0Nt"), ("symbol", "`"),
])
def test_a_gap_is_written_as_a_null_of_that_type(kind, expected):
    """'nan' is not a q word, and a frame that dropped its nulls on the way in
    would join against the wrong rows rather than against none."""
    assert q_cell(np.nan, kind) == expected
    assert q_cell(None, kind) == expected


def test_an_infinity_has_a_q_spelling_and_it_is_not_the_word_inf():
    assert q_cell(float("inf"), "number") == "0w"
    assert q_cell(float("-inf"), "number") == "-0w"


def test_a_clock_value_is_written_to_the_millisecond():
    """q's `time` is a count of milliseconds — 09:15:03.221000 is not a time
    literal to it."""
    assert q_cell(pd.Timedelta("09:15:03.221999"), "time") == "09:15:03.221"


def test_a_clock_value_is_truncated_and_never_rounded_up():
    """Rounding a value a hair under the minute gives 09:15:60.000, which is
    not a time at all."""
    assert q_cell(pd.Timedelta("09:15:59.9999"), "time") == "09:15:59.999"


def test_a_negative_duration_keeps_its_sign_and_its_size():
    assert q_cell(pd.Timedelta("-00:00:01.5"), "time") == "-00:00:01.500"


def test_a_value_that_is_not_a_single_thing_does_not_raise():
    """pd.isna answers elementwise for a list, and a truth test on the answer
    raises. A reference that blew up on the shape of a value it was only going
    to print would be a poor trade."""
    assert q_cell([1, 2], "symbol") == '`$"[1, 2]"'


# --- the table -----------------------------------------------------------------

def test_a_frame_becomes_a_flip_of_named_columns():
    q = q_table(_frame(sym=["VOD.L", "HSBA.L"], qty=[10, 20]))
    assert q == ('flip (`$("sym";"qty"))!((`$"VOD.L";`$"HSBA.L");(10;20))')


def test_a_column_name_that_is_not_a_q_name_still_works():
    """`order qty is two symbols and a syntax error; `$"order qty" is one
    symbol whatever is in it. Renames and file headers produce these."""
    assert q_table(_frame(**{"order qty": [1]})) == \
        'flip (`$enlist "order qty")!enlist enlist 1'


def test_quotes_and_backslashes_in_the_data_are_escaped():
    q = q_table(_frame(note=['he said "no"', "a\\b"]))
    assert '`$"he said \\"no\\""' in q and '`$"a\\\\b"' in q


def test_one_column_is_enlisted_so_its_key_is_a_list():
    """(x) is just x in q — parentheses do not make a list — so without enlist
    the table is keyed by an atom, and that is not a table."""
    assert q_table(_frame(sym=["A", "B"])).startswith('flip (`$enlist "sym")!')


def test_one_row_is_enlisted_so_its_columns_are_lists():
    q = q_table(_frame(sym=["A"], qty=[1]))
    assert q == 'flip (`$("sym";"qty"))!(enlist `$"A";enlist 1)'


def test_an_empty_frame_keeps_its_columns_and_their_types():
    """An empty result is still a shape to join against — losing the types
    would make the join fail on something other than having no rows."""
    frame = pd.DataFrame({"sym": pd.Series(dtype=object),
                          "qty": pd.Series(dtype=float)})
    assert q_table(frame) == 'flip (`$("sym";"qty"))!(`$();0#0n)'


def test_a_frame_with_no_columns_says_so_rather_than_writing_nonsense():
    with pytest.raises(ValueError, match="no columns"):
        q_table(pd.DataFrame())


# --- put into a query ----------------------------------------------------------

def _outputs():
    return {"orders": _frame(id=[1, 2], sym=["VOD.L", "HSBA.L"])}


def test_a_table_reference_becomes_the_table():
    out = substitute_refs("select from {{table:orders}}", _outputs())
    assert out.startswith("select from (flip ")


def test_it_is_parenthesised_so_it_can_stand_where_a_table_name_stands():
    """'select from flip ...' does not parse; 'select from (flip ...)' does."""
    out = substitute_refs("select from {{table:orders}}", _outputs())
    assert out.endswith(")") and out.count("(flip ") == 1


def test_both_kinds_of_reference_can_appear_in_one_query():
    joined, _, filtered = substitute_refs(
        "{{table:orders}} lj select from t where sym in {{orders.sym}}",
        _outputs()).partition(" lj ")
    assert joined.startswith("(flip ")               # the whole table
    assert filtered.endswith("sym in `VOD.L`HSBA.L")  # the column form


def test_a_table_reference_to_something_undefined_is_an_error():
    with pytest.raises(KeyError, match="nope"):
        substitute_refs("select from {{table:nope}}", _outputs())


def test_a_date_placeholder_is_not_mistaken_for_a_dataset():
    """Why the form is prefixed rather than a bare {{name}}: nothing could tell
    that apart from {{date_from}}, or from a typo."""
    q = "select from t where date within ({{date_from}};{{date_to}})"
    assert substitute_refs(q, _outputs()) == q


def test_a_dataset_query_resolves_its_table_reference(tmp_path):
    ds = Dataset(name="joined", env="prod", mode="raw",
                 raw_qsql="select from ({{table:orders}}) where id=1")
    out = build_qsql(ds, RT, _outputs())
    assert "{{table:orders}}" not in out and "flip (`$(" in out


def test_the_rows_themselves_travel_which_is_the_whole_point():
    """A column reference could only ever have asked 'is it one of these'."""
    out = substitute_refs("{{table:orders}}", _outputs())
    assert '`$"VOD.L"' in out and "(1;2)" in out


def test_an_uploaded_file_can_be_joined_into_a_query(tmp_path):
    """The workflow the form exists for: a file of ids nobody can put in a
    where clause, because what is wanted is the column beside each one."""
    from kdbmonitor.core.client import ConnectionManager, FakeClient
    from kdbmonitor.core.dashboard_models import ColumnSpec, FileShape
    from kdbmonitor.core.models import Connection
    from kdbmonitor.core.storage import Storage
    from kdbmonitor.core.dataset import run_datasets

    store = Storage(str(tmp_path / "t.db"))
    store.init_db()
    store.add_connection(Connection(id=None, name="rdb", host="h", port=1,
                                    kind="realtime", env="prod"))
    sent = {}
    mgr = ConnectionManager(
        client_factory=lambda host, port: FakeClient(sent))

    dash = Dashboard(id=1, name="D", datasets=[
        Dataset(name="mine", env="", source="file",
                shape=FileShape(columns=[ColumnSpec(name="id")])),
        Dataset(name="joined", env="prod", mode="raw",
                raw_qsql="select from ({{table:mine}}) lj `id xkey orders")])
    uploads = {"mine": _frame(id=[1, 2], note=["chase", "ok"])}

    out = run_datasets(dash, store, mgr, date(2026, 8, 3), uploads=uploads)
    q = out["joined"].qsql
    assert "{{table:mine}}" not in q
    assert '`$"chase"' in q                 # the note travelled, not just the id


# --- what the editor offers and checks ------------------------------------------

def _draft(*names) -> Dashboard:
    return Dashboard(id=1, name="D", datasets=[
        Dataset(name=n, env="prod", mode="raw", raw_qsql="select from t")
        for n in names])


def test_only_the_datasets_above_it_can_be_joined_against():
    from kdbmonitor.ui.dashboard_editor import _table_tokens

    draft = _draft("a", "b", "c")
    assert _table_tokens(draft, 2) == ["{{table:a}}", "{{table:b}}"]


def test_a_dataset_nobody_has_run_can_still_be_joined_against():
    """Unlike a column reference, this form needs no columns — it takes the
    whole result whatever shape it turns out to be."""
    from kdbmonitor.ui.dashboard_editor import _table_tokens

    assert _table_tokens(_draft("raw_thing", "second"), 1) == \
        ["{{table:raw_thing}}"]


def test_joining_against_a_dataset_declared_below_is_refused():
    from kdbmonitor.ui.dashboard_editor import validate

    class _Store:
        def list_environments(self):
            return {}

    draft = _draft("first", "second")
    draft.datasets[0].raw_qsql = "select from ({{table:second}})"
    assert any("joins against 'second'" in p for p in validate(draft, _Store()))
