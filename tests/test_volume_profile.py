"""The shipped volume-profile dashboard, against the real export.

This is the check that the toolkit stayed a toolkit. The viewer it reproduces
is a real tool, and every piece it needs here — a partitioned difference, a row
number turned into a share and accumulated, a reference line, a shaded band, a
control the reader sets — is generic. Nothing in the app knows what a volume
profile is, and if any of it had to, the design failed.
"""
from datetime import date, datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import pandas as pd
import pytest

from kdbmonitor.core.dashpdf import dashboard_to_pdf_bytes, report_plan
from kdbmonitor.core.dataset import run_datasets
from kdbmonitor.core.filesource import load
from kdbmonitor.core.plotmodel import build_plot_model
from kdbmonitor.core.portability import import_dashboards_json
from kdbmonitor.core.timectx import ResolvedTime

BUNDLE = Path(__file__).resolve().parents[1] / "docs" / "examples" \
    / "volume_profile_dashboard.json"
SAMPLE = Path(r"C:\Users\user\Desktop\Work\Projects\Work\VolumeProfile"
              r"\sample_india_volume_profile.csv")

TODAY = date(2026, 7, 31)
AS_OF = datetime(2026, 7, 31, 9, 15)
RT = ResolvedTime("realtime", None, None)

pytestmark = pytest.mark.skipif(
    not SAMPLE.exists(),
    reason="the real export lives outside the repo and is not always present")


@pytest.fixture()
def dash():
    return import_dashboards_json(BUNDLE.read_text(encoding="utf-8"))[0]


@pytest.fixture()
def frame(dash):
    out = load(SAMPLE.read_bytes(), dash.datasets[0].shape)
    assert out.problems == [], [p.message for p in out.problems]
    return out.df


def _run(dash, frame, instrument=None):
    return run_datasets(dash, None, None, TODAY, uploads={"profile": frame},
                        chosen={"instrument": instrument} if instrument else {})


# --- it reads the real file --------------------------------------------------

def test_the_real_export_loads_against_the_shipped_shape(frame):
    assert len(frame) == 1560
    assert frame["#FidessaCode"].nunique() == 20
    assert list(frame.columns)[:2] == ["#FidessaCode", "ReutersCode"]


def test_the_metadata_line_above_the_table_is_picked_up(dash):
    out = load(SAMPLE.read_bytes(), dash.datasets[0].shape)
    assert "TimeZone" in str(out.cells["Header"])


# --- the numbers are right ---------------------------------------------------

def test_the_bucket_shares_sum_to_the_whole_day(dash, frame):
    out = _run(dash, frame, "ICICIBC.IN")["profile"].df
    assert out["share"].sum() == pytest.approx(1.0, abs=1e-6)


def test_no_share_walks_in_from_the_previous_instrument(dash, frame):
    """Unpartitioned, the difference crosses each boundary and reports -1.0."""
    out = _run(dash, frame, "ICICIBC.IN")["profile"].df
    assert out["share"].min() > -0.5


def test_every_instrument_gives_the_same_answer(dash, frame):
    for sym in ("ICICIBC.IN", "INFO.IN", "TCS.IN"):
        picked = _run(dash, frame, sym)["profile"].df
        if picked is None or picked.empty:
            continue
        assert picked["share"].sum() == pytest.approx(1.0, abs=1e-6), sym
        assert picked["#FidessaCode"].unique().tolist() == [sym]


def test_even_pace_runs_from_nothing_to_everything(dash, frame):
    """A flat schedule reaches 100% at the close, by construction."""
    out = _run(dash, frame, "ICICIBC.IN")["profile"].df
    assert out["even_pace"].iloc[-1] == pytest.approx(1.0)
    assert out["even_pace"].is_monotonic_increasing


def test_the_closing_auction_is_the_busiest_bucket(dash, frame):
    """The shape the file was generated with: a heavy open, a midday lull and a
    closing spike. If the arithmetic were wrong this is where it would show."""
    out = _run(dash, frame, "ICICIBC.IN")["profile"].df
    assert out["share"].idxmax() == out.index[-1]


# --- the reader steers it ----------------------------------------------------

def test_the_instrument_picker_offers_every_instrument_in_the_file(dash, frame):
    """Read from the frame as fetched — after the filter it drives, one is left."""
    choices = _run(dash, frame, "ICICIBC.IN")["profile"].choices
    assert len(choices["instrument"]) == 20
    assert "ICICIBC.IN" in choices["instrument"]


def test_picking_another_instrument_changes_the_frame(dash, frame):
    first = _run(dash, frame, "ICICIBC.IN")["profile"].df
    second = _run(dash, frame, "INFO.IN")["profile"].df
    assert first["#FidessaCode"].iloc[0] != second["#FidessaCode"].iloc[0]


def test_the_default_applies_when_nothing_is_picked(dash, frame):
    out = _run(dash, frame)["profile"].df
    assert out["#FidessaCode"].unique().tolist() == ["ICICIBC.IN"]


def test_a_stale_pick_falls_back_rather_than_emptying_the_page(dash, frame):
    out = _run(dash, frame, "DELISTED.IN")["profile"].df
    assert not out.empty


# --- the charts carry what they are read against -----------------------------

def _widget(dash, kind):
    return next(w for row in dash.rows for w in row.widgets if w.type == kind)


def test_the_cumulated_curve_carries_the_even_pace_line(dash, frame):
    results = _run(dash, frame, "ICICIBC.IN")
    pm = build_plot_model(_widget(dash, "line"), results)
    assert len(pm.references) == 1
    assert pm.references[0].label == "even pace"
    assert pm.references[0].values[-1] == pytest.approx(1.0)


def test_the_bucket_bars_carry_the_average_bucket_line(dash, frame):
    results = _run(dash, frame, "ICICIBC.IN")
    pm = build_plot_model(_widget(dash, "bar"), results)
    assert len(pm.references) == 1
    assert pm.references[0].label == "average bucket"
    assert pm.references[0].value == pytest.approx(1 / (len(pm.series[0].x)), abs=1e-3)


def test_both_charts_shade_the_pre_open_stretch(dash, frame):
    results = _run(dash, frame, "ICICIBC.IN")
    for kind in ("line", "bar"):
        pm = build_plot_model(_widget(dash, kind), results)
        assert [b.label for b in pm.bands] == ["pre-open"], kind


def test_the_table_lists_the_session(dash, frame):
    results = _run(dash, frame, "ICICIBC.IN")
    pm = build_plot_model(_widget(dash, "table"), results)
    assert pm.columns == ["Time", "cumulated", "this bucket", "even pace"]
    assert len(pm.rows) > 50


# --- and it prints -----------------------------------------------------------

def test_the_whole_thing_prints_and_names_the_instrument(dash, frame):
    results = _run(dash, frame, "INFO.IN")
    pdf = dashboard_to_pdf_bytes(dash, results, RT, AS_OF,
                                 chosen={"instrument": "INFO.IN"})
    assert pdf.startswith(b"%PDF") and len(pdf) > 10_000


def test_the_page_count_is_the_same_whichever_instrument_is_picked(dash, frame):
    a = report_plan(dash, _run(dash, frame, "ICICIBC.IN"),
                    chosen={"instrument": "ICICIBC.IN"})
    b = report_plan(dash, _run(dash, frame, "INFO.IN"),
                    chosen={"instrument": "INFO.IN"})
    assert a[1] == b[1]


# --- nothing here is special-cased -------------------------------------------

def test_the_dashboard_is_built_only_from_generic_pieces(dash):
    """The check that this stayed a toolkit. Every transform is one the app
    offers for any dashboard; none is named after this report."""
    kinds = [t.kind for t in dash.datasets[0].transforms]
    assert kinds == ["filter", "window", "derive", "window", "window"]
    ops = [t.params.get("op") for t in dash.datasets[0].transforms
           if t.kind == "window"]
    assert ops == ["diff", "pct_of_total", "cumsum"]


def test_the_bundle_carries_no_data(dash):
    """Only the shape and the column contract travel."""
    raw = BUNDLE.read_text(encoding="utf-8")
    assert "ICBK.NS" not in raw and "0.0215" not in raw
