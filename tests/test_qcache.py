"""The cache itself: what it hands back, and when it stops.

Everything else in this feature decides *what* to hold. This decides what
holding means — which is a question of two things only: the key, and the age.
"""
from datetime import datetime, timedelta, timezone

import pandas as pd

from kdbmonitor.core.qcache import QueryCache

T0 = datetime(2026, 8, 5, 9, 0, 0)


def _frame(n=1) -> pd.DataFrame:
    return pd.DataFrame({"sym": ["AAPL"] * n})


# --- holding and handing back ----------------------------------------------

def test_a_held_frame_comes_back_under_its_own_key():
    c = QueryCache()
    c.put(("h", 1, "select from t"), _frame(), T0)
    assert c.get(("h", 1, "select from t")).df.equals(_frame())


def test_a_key_nobody_held_is_a_miss():
    assert QueryCache().get(("h", 1, "select from t")) is None


def test_the_same_query_to_a_different_server_is_a_different_question():
    c = QueryCache()
    c.put(("rdb", 1, "select from t"), _frame(), T0)
    assert c.get(("hdb", 2, "select from t")) is None


def test_a_changed_query_is_a_different_question():
    """The self-invalidation the whole design rests on: edit the query, or
    change a parameter or period that goes into it, and nothing is held."""
    c = QueryCache()
    c.put(("h", 1, "select from t where sym=`AAPL"), _frame(), T0)
    assert c.get(("h", 1, "select from t where sym=`MSFT")) is None


def test_the_fetch_time_is_kept_with_the_frame():
    c = QueryCache()
    assert c.put(("h", 1, "q"), _frame(), T0).at == T0
    assert c.get(("h", 1, "q")).at == T0


# --- how long it stands ----------------------------------------------------

def test_with_no_ttl_a_held_frame_stands():
    """The dashboard's case: it is being looked at, and there is a control on
    the page for asking again."""
    c = QueryCache()
    c.put(("h", 1, "q"), _frame(), T0)
    assert c.get(("h", 1, "q"), now=T0 + timedelta(days=3)) is not None


def test_inside_its_ttl_a_held_frame_stands():
    c = QueryCache()
    c.put(("h", 1, "q"), _frame(), T0)
    assert c.get(("h", 1, "q"), now=T0 + timedelta(seconds=59), ttl=60) is not None


def test_past_its_ttl_it_is_gone():
    c = QueryCache()
    c.put(("h", 1, "q"), _frame(), T0)
    assert c.get(("h", 1, "q"), now=T0 + timedelta(seconds=60), ttl=60) is None


def test_a_ttl_needs_a_now_to_mean_anything():
    c = QueryCache()
    c.put(("h", 1, "q"), _frame(), T0)
    assert c.get(("h", 1, "q"), ttl=60) is not None


def test_stamps_that_cannot_be_compared_count_as_expired():
    """One side tz-aware and the other not: the age is unknowable, and going
    back to the server is the safe way to be wrong about a cache."""
    c = QueryCache()
    c.put(("h", 1, "q"), _frame(), T0)                     # naive
    aware = datetime(2026, 8, 5, 9, 0, 1, tzinfo=timezone.utc)
    assert c.get(("h", 1, "q"), now=aware, ttl=3600) is None


# --- letting go ------------------------------------------------------------

def test_one_key_can_be_dropped():
    c = QueryCache()
    c.put(("h", 1, "a"), _frame(), T0)
    c.put(("h", 1, "b"), _frame(), T0)
    c.drop(("h", 1, "a"))
    assert c.get(("h", 1, "a")) is None
    assert c.get(("h", 1, "b")) is not None


def test_clear_lets_go_of_everything():
    c = QueryCache()
    c.put(("h", 1, "a"), _frame(), T0)
    c.clear()
    assert len(c) == 0


def test_the_oldest_goes_first_once_it_is_full():
    """A query whose text varies makes a new key every time it changes, so
    without a bound a long session would hold every frame it ever fetched."""
    c = QueryCache(max_entries=3)
    for i in range(5):
        c.put(("h", 1, f"q{i}"), _frame(), T0 + timedelta(seconds=i))
    assert len(c) == 3
    assert c.get(("h", 1, "q0")) is None
    assert c.get(("h", 1, "q4")) is not None
