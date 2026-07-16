"""Export and import a portable bundle of connections and alerts.

The document is a small versioned envelope so imports can be validated:

    {
      "kind": "kdbmonitor-export",
      "version": 2,
      "exported_at": "2026-07-17T10:00:00+00:00" | null,
      "connections": [ <connection dict>, ... ],
      "alerts":      [ <alert dict>, ... ]
    }

IDs are dropped on export so importing always creates fresh rows. Alert names
travel in the bundle and are used to detect collisions on import; connections
are matched by name (the caller decides how to handle name clashes).

Older alert-only files (kind "kdbmonitor-alerts", no connections) still import.
"""
from __future__ import annotations

import json
from typing import Iterable, Optional

from kdbmonitor.core.models import (
    Alert, Connection, alert_to_dict, alert_from_dict,
    connection_to_dict, connection_from_dict,
)

EXPORT_KIND = "kdbmonitor-export"
LEGACY_KIND = "kdbmonitor-alerts"
EXPORT_VERSION = 2


def export_bundle_json(connections: Iterable[Connection], alerts: Iterable[Alert],
                       exported_at: Optional[str] = None) -> str:
    payload = {
        "kind": EXPORT_KIND,
        "version": EXPORT_VERSION,
        "exported_at": exported_at,
        "connections": [{**connection_to_dict(c), "id": None} for c in connections],
        "alerts": [{**alert_to_dict(a), "id": None} for a in alerts],
    }
    return json.dumps(payload, indent=2)


def import_bundle_json(raw: str) -> tuple[list[Connection], list[Alert]]:
    """Parse an export document into (connections, alerts), each with id=None.

    Raises ValueError with a human-readable message on any malformed input.
    """
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"Not valid JSON: {exc}")

    if not isinstance(payload, dict) or payload.get("kind") not in (EXPORT_KIND, LEGACY_KIND):
        raise ValueError("Not a KdbMonitor export file "
                         f"(expected kind '{EXPORT_KIND}').")

    raw_alerts = payload.get("alerts")
    if not isinstance(raw_alerts, list):
        raise ValueError("Export file has no 'alerts' list.")

    raw_conns = payload.get("connections", [])
    if not isinstance(raw_conns, list):
        raise ValueError("Export file 'connections' must be a list.")

    connections: list[Connection] = []
    for i, d in enumerate(raw_conns):
        try:
            conn = connection_from_dict(d)
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Connection #{i + 1} is malformed: {exc}")
        conn.id = None
        connections.append(conn)

    alerts: list[Alert] = []
    for i, d in enumerate(raw_alerts):
        try:
            alert = alert_from_dict(d)
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Alert #{i + 1} is malformed: {exc}")
        alert.id = None
        alerts.append(alert)

    return connections, alerts


def conflicting_alert_names(existing: Iterable[str], alerts: Iterable[Alert]) -> list[str]:
    """Names of incoming alerts that already exist (order-preserving, deduped)."""
    existing_set = set(existing)
    seen: set[str] = set()
    out: list[str] = []
    for a in alerts:
        if a.name in existing_set and a.name not in seen:
            out.append(a.name)
            seen.add(a.name)
    return out
