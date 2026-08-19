"""Build a throwaway database for the browser tests.

Demo servers, the shipped example dashboards, a spread of alerts across groups
and states, some run history so the Monitor has something to show, and two
report jobs. Everything the end-to-end run needs to press against.

    python tests/e2e/seed_demo.py /tmp/e2e.db

Never point it at a database you care about — it is written from empty.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

# Runnable from anywhere, including `python tests/e2e/seed_demo.py`, where
# sys.path[0] is this directory rather than the repo root.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from kdbmonitor.core.mock import demo_connection_specs  # noqa: E402
from kdbmonitor.core.models import (
    Alert, Step, Filter, TriggerCondition, RearmPolicy, Channels, Schedule, Window,
)
from kdbmonitor.core.portability import import_dashboards_json
from kdbmonitor.core.storage import Storage
from kdbmonitor.core.reportjob import ReportJob

DB = sys.argv[1]
s = Storage(DB); s.init_db()

for spec in demo_connection_specs():
    try: s.add_connection(spec)
    except Exception as e: print("conn:", e)

EX = ROOT / "docs" / "examples"
groups = {"demo_orders_dashboard.json": "Trading",
          "short_sell_dashboard.json": "Risk",
          "equity_active_orders_dashboard.json": "Trading",
          "volume_profile_dashboard.json": ""}
for f, grp in groups.items():
    p = EX / f
    if not p.exists(): continue
    for d in import_dashboards_json(p.read_text(encoding="utf-8")):
        d.group = grp
        try: s.add_dashboard(d)
        except Exception as e: print("dash:", f, e)

def alert(name, group, interval, enabled=True, sched=None):
    return Alert(
        id=None, name=name, group=group, enabled=enabled,
        poll_interval_secs=interval,
        steps=[Step(server="orders_demo", table="target", mode="form",
                    filters=[Filter(column="side", op="=", value="sellshort",
                                    value_type="symbol")], output_name="step1")],
        trigger=TriggerCondition(type="has_rows"),
        rearm=RearmPolicy(mode="on_change"),
        channels=Channels(),
        schedule=sched or Schedule(mode="always"),
    )

specs = [
    ("Short sell surge", "Risk", 30, True, None),
    ("Rejected work orders", "Risk", 60, True, None),
    ("Limit locked names", "Trading", 15, True, None),
    ("Overnight book drift", "Trading", 300, False, None),
    ("Open auction imbalance", "", 60, True,
     Schedule(mode="windows", tz="Europe/London",
              windows=[Window(start="07:45", end="08:15")], days=[0,1,2,3,4])),
    ("Stale market data", "Infra", 15, True, None),
]
ids = {}
for nm, g, iv, en, sc in specs:
    try: ids[nm] = s.add_alert(alert(nm, g, iv, en, sc))
    except Exception as e: print("alert:", nm, e)

now = datetime.now(timezone.utc)
plan = {"Short sell surge": ("triggered", 42), "Rejected work orders": ("armed", 0),
        "Limit locked names": ("triggered", 7), "Stale market data": ("error", 0),
        "Open auction imbalance": ("armed", 0)}
for nm, (status, rows) in plan.items():
    aid = ids.get(nm)
    if aid is None: continue
    for i in range(6):
        ts = (now - timedelta(minutes=5 * i)).isoformat()
        st_i = status if i == 0 else ("armed" if status != "error" else "error")
        s.record_run(aid, ts, st_i, st_i == "triggered",
                     st_i == "triggered",
                     rows if st_i == "triggered" else 0,
                     "connection refused: demo host unreachable" if st_i == "error" else "")
    if status == "triggered":
        df = pd.DataFrame({"sym": [f"000{n}.HK" for n in range(rows)],
                           "side": ["sellshort"] * rows,
                           "size": [1000 * (n + 1) for n in range(rows)],
                           "price": [10.5 + n * 0.25 for n in range(rows)]})
        s.save_result(aid, now.isoformat(), df)

try:
    dash = s.list_dashboards()[0]
    s.add_report_job(ReportJob(id=None, name="Morning risk pack", dashboard_id=dash.id,
                               recipients=["desk@example.com"], enabled=True))
    s.add_report_job(ReportJob(id=None, name="EOD orders summary", dashboard_id=dash.id,
                               recipients=["ops@example.com", "risk@example.com"],
                               enabled=False))
except Exception as e:
    print("job:", e)

s.save_component("transform", "market from suffix",
                 {"kind": "derive", "params": {"column": "market", "kind": "suffix_map",
                                               "source": "sym", "mapping": {".HK": "Hong Kong"},
                                               "default": "Other"}})
s.save_component("widget", "Orders by market",
                 {"type": "table", "dataset": "d", "title": "By market",
                  "spec": {"columns": ["market", "size"]}, "width": 2.0})

print("dashboards:", [d.name for d in s.list_dashboards()])
print("alerts:", [a.name for a in s.list_alerts()])
print("connections:", [c.name for c in s.list_connections()])
print("jobs:", [j.name for j in s.list_report_jobs()])
