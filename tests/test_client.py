# tests/test_client.py
import numpy as np
import pandas as pd
from kdbmonitor.core.client import (
    ConnectionManager, FakeClient, PyKxClient, Q_INT_NULLS, nulls_to_nan,
)
from kdbmonitor.core.models import Connection


class _QResult:
    """What pykx hands back: an object you call .pd() on."""
    def __init__(self, value):
        self._value = value

    def pd(self):
        return self._value


def pykx_client(responses: dict) -> PyKxClient:
    """A PyKxClient wired to a scripted connection.

    Built without ``__init__`` so nothing imports pykx or opens a socket. The
    point is to exercise ``query`` itself — the one path that scrubs kdb nulls,
    and therefore the one place a fake client cannot stand in for.
    """
    client = PyKxClient.__new__(PyKxClient)
    client._conn = lambda qsql: _QResult(responses[qsql])
    return client


def test_fake_client_returns_canned():
    df = pd.DataFrame({"sym": ["AAPL"]})
    client = FakeClient({"select from target": df})
    assert client.query("select from target").equals(df)


def test_connection_manager_caches_client(monkeypatch):
    created = []

    class DummyClient:
        def __init__(self, host, port):
            created.append((host, port))
        def query(self, q):
            return pd.DataFrame()

    mgr = ConnectionManager(client_factory=DummyClient)
    conn = Connection(id=1, name="orders", host="h", port=5010)
    c1 = mgr.get(conn)
    c2 = mgr.get(conn)
    assert c1 is c2
    assert created == [("h", 5010)]


# --- kdb+ integer nulls ------------------------------------------------------
#
# A left join that missed gives the row a null, and a kdb null int is the lowest
# number the width holds. Left alone it is data: a count of rejections summed to
# minus two billion, which is worse than an obviously empty cell because it can
# be plotted, formatted and believed.

def test_a_null_int_becomes_nan_not_a_number():
    df = pd.DataFrame({"nReject": np.array([1, Q_INT_NULLS["int32"], 2],
                                           dtype="int32")})
    out = nulls_to_nan(df)
    assert pd.isna(out["nReject"][1])
    assert out["nReject"].sum() == 3          # not -2,147,483,645


def test_every_integer_width_kdb_can_null():
    df = pd.DataFrame({w: pd.Series([sentinel], dtype=w)
                       for w, sentinel in Q_INT_NULLS.items()})
    out = nulls_to_nan(df)
    assert out.isna().all().all()


def test_honest_integers_are_left_exactly_as_they_came():
    df = pd.DataFrame({"size": np.array([100, -5, 0], dtype="int64"),
                       "sym": ["A", "B", "C"]})
    out = nulls_to_nan(df)
    assert out is df                          # not even copied
    assert str(out["size"].dtype) == "int64"


def test_only_the_column_holding_a_null_changes_type():
    df = pd.DataFrame({"size": np.array([1, 2], dtype="int64"),
                       "nReject": np.array([Q_INT_NULLS["int32"], 3],
                                           dtype="int32")})
    out = nulls_to_nan(df)
    assert str(out["size"].dtype) == "int64"
    assert str(out["nReject"].dtype) == "float64"


def test_a_negative_number_that_is_not_a_null_survives():
    """Only the exact sentinel is a null; -32,767 is a quantity."""
    df = pd.DataFrame({"pnl": np.array([-32767, -1], dtype="int16")})
    assert nulls_to_nan(df)["pnl"].tolist() == [-32767, -1]


def test_floats_and_symbols_are_not_searched_for_sentinels():
    df = pd.DataFrame({"px": [float(Q_INT_NULLS["int32"]), 1.5],
                       "sym": ["AAPL", "MSFT"]})
    out = nulls_to_nan(df)
    assert out["px"].tolist() == [float(Q_INT_NULLS["int32"]), 1.5]


def test_an_empty_frame_passes_straight_through():
    empty = pd.DataFrame(columns=["nReject"])
    assert nulls_to_nan(empty) is empty


# --- not every result is a table --------------------------------------------
#
# The scrub runs on everything the real client returns, and a q *vector* comes
# back as a 1-D Series with no columns to iterate. Asking one for .columns is
# what broke Introspect against a live server, while every test here drove a
# client that never reaches the scrub at all.

def test_a_q_vector_result_is_not_treated_as_a_table():
    out = nulls_to_nan(pd.Series(["target", "QATT"]))
    assert out.tolist() == ["target", "QATT"]


def test_a_vector_of_integers_still_loses_its_nulls():
    out = nulls_to_nan(pd.Series(np.array([1, Q_INT_NULLS["int64"]], dtype="int64")))
    assert out[0] == 1 and pd.isna(out[1])


def test_a_vector_holding_no_nulls_keeps_its_dtype():
    out = nulls_to_nan(pd.Series(np.array([1, 2], dtype="int64")))
    assert str(out.dtype) == "int64"


def test_a_scalar_result_passes_straight_through():
    """`count target` is a number, not a frame and not a vector."""
    assert nulls_to_nan(7) == 7


def test_the_real_client_returns_a_vector_query_unharmed():
    client = pykx_client({"tables[]": pd.Series(["target", "QATT"])})
    assert client.query("tables[]").tolist() == ["target", "QATT"]


def test_the_real_client_still_scrubs_a_table():
    client = pykx_client({"select from t": pd.DataFrame(
        {"nReject": np.array([1, Q_INT_NULLS["int32"]], dtype="int32")})})
    assert client.query("select from t")["nReject"].sum() == 1


def test_a_group_sum_over_nulls_counts_the_rows_it_has():
    """The short-sell report's failure, end to end: orders with no work orders
    join to a null, and the market's rejection total must be the two it knows
    about — not a number in the billions."""
    df = nulls_to_nan(pd.DataFrame({
        "market": ["Hong Kong", "Hong Kong", "Japan"],
        "nReject": np.array([2, Q_INT_NULLS["int32"], Q_INT_NULLS["int32"]],
                            dtype="int32")}))
    totals = df.groupby("market")["nReject"].sum()
    assert totals["Hong Kong"] == 2
    assert totals["Japan"] == 0
