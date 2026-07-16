"""Export and import alerts as a portable JSON document.

The document is a small envelope so imports can be validated and versioned:

    {
      "kind": "kdbmonitor-alerts",
      "version": 1,
      "exported_at": "2026-07-17T10:00:00+00:00" | null,
      "alerts": [ <alert dict>, ... ]
    }

Only alerts travel, not connections. Each alert's `server` references a
connection by name; importing into an environment without that connection is
allowed (the alert simply won't resolve until the connection exists).
"""
from __future__ import annotations

import json
from typing import Optional

from kdbmonitor.core.models import Alert, alert_to_dict, alert_from_dict

EXPORT_KIND = "kdbmonitor-alerts"
EXPORT_VERSION = 1


def export_alerts_json(alerts: list[Alert], exported_at: Optional[str] = None) -> str:
    """Serialize alerts to the export envelope. IDs are dropped so importing
    always creates fresh alerts rather than colliding with existing ones."""
    payload = {
        "kind": EXPORT_KIND,
        "version": EXPORT_VERSION,
        "exported_at": exported_at,
        "alerts": [{**alert_to_dict(a), "id": None} for a in alerts],
    }
    return json.dumps(payload, indent=2)


def import_alerts_json(raw: str) -> list[Alert]:
    """Parse an export document into Alerts (with id=None, ready to add).

    Raises ValueError with a human-readable message on any malformed input.
    """
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"Not valid JSON: {exc}")

    if not isinstance(payload, dict) or payload.get("kind") != EXPORT_KIND:
        raise ValueError("Not a KdbMonitor alerts export file "
                         f"(missing kind='{EXPORT_KIND}').")

    raw_alerts = payload.get("alerts")
    if not isinstance(raw_alerts, list):
        raise ValueError("Export file has no 'alerts' list.")

    alerts: list[Alert] = []
    for i, d in enumerate(raw_alerts):
        try:
            alert = alert_from_dict(d)
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Alert #{i + 1} is malformed: {exc}")
        alert.id = None
        alerts.append(alert)
    return alerts
