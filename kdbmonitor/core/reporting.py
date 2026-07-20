"""Alert reporting: gather a day's triggered alerts and render an Excel workbook.

Streamlit-free so it can be unit-tested and reused by cron/CLI later. The report
answers, for another team: which alerts fired on a day, how often, and what the
result rows were. Rows come from the persisted daily snapshot (kept for the last
20 days); for *today* with no snapshot yet, the chain is re-run live instead.
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime, timezone
from typing import Callable, Optional

import pandas as pd

from kdbmonitor.core.chain import run_chain
from kdbmonitor.core.summaries import condition_summary, step_summary


def _day_bounds(day: date) -> tuple[str, str]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    end = datetime.fromordinal(day.toordinal() + 1)
    end = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
    return start.isoformat(), end.isoformat()


def _hhmmss(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).strftime("%H:%M:%S")
    except ValueError:
        return ts


def _resolve_result(store, alert, alert_id: int, day: date, today: date,
                    client_for: Optional[Callable[[str], object]],
                    max_rows: int) -> Optional[dict]:
    """Prefer the persisted snapshot for ``day``; for *today* with no snapshot yet,
    re-run the chain live. Historical days never re-fetch (orders may have moved on).

    Both paths cap the rows carried into the workbook at ``max_rows`` while
    ``row_count`` keeps the true size, so huge results stay bounded.
    """
    snap = store.get_result(alert_id, day.isoformat())
    if snap is not None:
        df = pd.DataFrame(snap["rows"], columns=snap["columns"]).head(max_rows)
        return {"df": df, "row_count": snap["row_count"], "source": "snapshot",
                "captured_ts": snap["ts"]}
    if alert is not None and client_for is not None and day == today:
        try:
            full = run_chain(alert, client_for)
            return {"df": full.head(max_rows).reset_index(drop=True),
                    "row_count": len(full), "source": "live"}
        except Exception as exc:  # noqa: BLE001 - report the failure, don't abort
            return {"error": str(exc)}
    return None


def _is_truncated(result: Optional[dict]) -> bool:
    return bool(result and "df" in result and result["row_count"] > len(result["df"]))


def build_report_model(store, day: date,
                       client_for: Optional[Callable[[str], object]] = None,
                       now: Optional[datetime] = None,
                       max_rows: Optional[int] = None) -> dict:
    """Structured, render-agnostic report of the alerts triggered on ``day``.

    Result rows come from the stored daily snapshot; if the day is today and no
    snapshot exists yet, ``client_for`` (when given) is used to re-run the chain
    live, recording any error instead of aborting. Rows are capped at ``max_rows``
    (default: the store's configured cap) to keep the workbook bounded.
    """
    if max_rows is None:
        max_rows = store.get_result_max_rows()
    since, upper = _day_bounds(day)
    runs = [r for r in store.list_runs_since(since, triggered_only=True) if r["ts"] < upper]
    today = (now or datetime.now(timezone.utc)).date()

    by_alert: dict[int, list[dict]] = {}
    for r in runs:
        by_alert.setdefault(r["alert_id"], []).append(r)

    alerts = []
    for alert_id, ars in sorted(by_alert.items(), key=lambda kv: -len(kv[1])):
        alert = store.get_alert(alert_id)
        name = alert.name if alert else f"alert {alert_id} (deleted)"
        condition = condition_summary(alert.trigger) if alert else "—"
        steps = [step_summary(s) for s in alert.steps] if alert else []
        servers = sorted({s.server for s in alert.steps}) if alert else []

        result = _resolve_result(store, alert, alert_id, day, today, client_for, max_rows)

        alerts.append({
            "id": alert_id, "name": name, "condition": condition,
            "steps": steps, "servers": servers,
            "triggers": len(ars),
            "first_ts": ars[0]["ts"], "last_ts": ars[-1]["ts"],
            "runs": ars, "result": result,
        })

    return {
        "day": day.isoformat(),
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "summary": {"alerts": len(alerts), "triggers": len(runs)},
        "alerts": alerts,
    }


# --- Excel rendering ---------------------------------------------------------

_BAD_SHEET = re.compile(r"[\[\]:*?/\\]")


def _sheet_name(base: str, used: set[str]) -> str:
    name = _BAD_SHEET.sub("_", base).strip() or "alert"
    name = name[:31]
    i = 1
    while name.lower() in used:                      # Excel names are case-insensitive
        suffix = f"~{i}"
        name = name[:31 - len(suffix)] + suffix
        i += 1
    used.add(name.lower())
    return name


def _result_df(result: Optional[dict]) -> pd.DataFrame:
    if result is None:
        return pd.DataFrame({"(no live connection — result not fetched)": []})
    if "error" in result:
        return pd.DataFrame({"error": [result["error"]]})
    df = result["df"]
    return df if len(df.columns) else pd.DataFrame({"(query returned no columns)": []})


def _result_cell(result: Optional[dict]):
    if result is None:
        return "—"
    if "error" in result:
        return "error"
    return result["row_count"]


def _source_cell(result: Optional[dict]) -> str:
    if result is None:
        return "—"
    if "error" in result:
        return "error"
    return result.get("source", "—")


def report_to_excel_bytes(model: dict) -> bytes:
    """Render a report model to an .xlsx workbook: Summary + Triggers + per-alert."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        # 1) Summary — one row per triggered alert.
        if model["alerts"]:
            summary = pd.DataFrame([{
                "Alert": a["name"],
                "Triggers": a["triggers"],
                "First fired (UTC)": _hhmmss(a["first_ts"]),
                "Last fired (UTC)": _hhmmss(a["last_ts"]),
                "Result rows": _result_cell(a["result"]),
                "Truncated": (f"yes — showing {len(a['result']['df'])}"
                              if _is_truncated(a["result"]) else ""),
                "Result source": _source_cell(a["result"]),
                "Fires when": a["condition"],
                "Servers": ", ".join(a["servers"]),
            } for a in model["alerts"]])
        else:
            summary = pd.DataFrame({"Note": [f"No alerts triggered on {model['day']}."]})
        summary.to_excel(writer, index=False, sheet_name="Summary")

        # A little header above the summary table via a separate info block.
        info = pd.DataFrame({
            "Report": [f"KdbMonitor alert report — {model['day']}"],
            "Generated (UTC)": [_hhmmss(model["generated_at"])],
            "Alerts triggered": [model["summary"]["alerts"]],
            "Total triggers": [model["summary"]["triggers"]],
        })
        info.to_excel(writer, index=False, sheet_name="About")

        # 2) Triggers — flat audit list of every trigger today.
        trig_rows = [
            {"Alert": a["name"], "Time (UTC)": _hhmmss(r["ts"]),
             "Rows": r["row_count"], "Detail": r["message"]}
            for a in model["alerts"] for r in a["runs"]
        ]
        trig_df = (pd.DataFrame(trig_rows) if trig_rows
                   else pd.DataFrame({"Note": ["No triggers."]}))
        trig_df.to_excel(writer, index=False, sheet_name="Triggers")

        # 3) One sheet per alert with its captured result rows.
        used = {"summary", "about", "triggers"}
        for a in model["alerts"]:
            sheet = _sheet_name(a["name"], used)
            result, startrow = a["result"], 0
            if _is_truncated(result):
                note = f"Showing first {len(result['df'])} of {result['row_count']} rows (capped)"
                pd.DataFrame({note: []}).to_excel(writer, index=False, sheet_name=sheet)
                startrow = 2
            _result_df(result).to_excel(writer, index=False, sheet_name=sheet, startrow=startrow)

    return buf.getvalue()


def report_filename(day_iso: str) -> str:
    return f"alert_report_{day_iso}.xlsx"
