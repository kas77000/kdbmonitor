"""Is a scheduler running, should one be, and who owns the lock.

Two schedulers on one database means every recipient gets every report twice,
so the answer cannot be "probably not". It is a lock in the settings table,
claimed atomically and refreshed while the owner runs: the value leads with an
ISO-8601 UTC timestamp, which makes "stale" a string comparison and the claim a
single conditional statement that SQLite settles by itself.

Stopping is cooperative and never a kill. Windows has no SIGTERM to send
another process, and killing by a recorded pid risks killing whatever unrelated
process has since inherited it. Writing an intent the owner reads has neither
problem and needs no privileges.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

LOCK_KEY = "scheduler_lock"
DESIRED_KEY = "scheduler_desired"
STOP_REQUESTED_KEY = "scheduler_stop_requested_at"
SPAWN_ERROR_KEY = "scheduler_spawn_error"

# Beat often, expire slowly: three missed beats before another process may take
# over, which tolerates a long query or a paused VM without handing the lock to
# a second scheduler mid-report.
BEAT_SECS = 15
STALE_SECS = 60

# How long a cooperative stop is given before the process is called wedged.
STOP_GRACE_SECS = 10

_STAMP_LEN = 20          # "2026-08-13T07:00:00Z" — the substr() width below


def stamp(when: datetime) -> str:
    """The 20-character UTC text the lock is compared as."""
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unstamp(text: str) -> Optional[datetime]:
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _value(now: datetime, pid: Optional[int], host: Optional[str]) -> str:
    return (f"{stamp(now)}|{pid or os.getpid()}"
            f"|{host or socket.gethostname()}")


def claim(store, now: datetime, pid: Optional[int] = None,
          host: Optional[str] = None) -> bool:
    """Take the lock if it is free or stale. True when this process owns it.

    One statement, settled by SQLite: two processes asking at the same moment
    cannot both be told yes. A loser exits rather than running a second copy.
    """
    cutoff = stamp(now - timedelta(seconds=STALE_SECS))
    before = store.conn.total_changes
    store.conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value "
        f"WHERE substr(settings.value, 1, {_STAMP_LEN}) < ?",
        (LOCK_KEY, _value(now, pid, host), cutoff))
    store.conn.commit()
    return store.conn.total_changes > before


def heartbeat(store, now: datetime, pid: Optional[int] = None,
              host: Optional[str] = None) -> None:
    """Say the owner is still alive. Unconditional — the owner is not racing
    anybody for a lock it already holds."""
    store.set_setting(LOCK_KEY, _value(now, pid, host))


def release(store) -> None:
    """Drop the lock on a clean exit, so a restart is instant rather than
    waiting out the stale window."""
    store.set_setting(LOCK_KEY, "")


def holder(store) -> Optional[tuple[str, int, str]]:
    """``(beat_stamp, pid, host)`` of whoever holds the lock, or None."""
    raw = (store.get_setting(LOCK_KEY, "") or "").strip()
    if not raw:
        return None
    parts = raw.split("|")
    if len(parts) != 3:
        return None
    try:
        return parts[0], int(parts[1]), parts[2]
    except ValueError:
        return None


# --- what the operator wants -------------------------------------------------

def desired(store) -> str:
    """``"running"`` or ``"stopped"``. A database that has never been asked
    wants a scheduler: the ordinary case is that reports should go out."""
    value = (store.get_setting(DESIRED_KEY, "running") or "running").strip()
    return "stopped" if value == "stopped" else "running"


def request_stop(store, now: datetime) -> None:
    store.set_setting(DESIRED_KEY, "stopped")
    store.set_setting(STOP_REQUESTED_KEY, stamp(now))


def request_start(store) -> None:
    store.set_setting(DESIRED_KEY, "running")
    store.set_setting(STOP_REQUESTED_KEY, "")


@dataclass(frozen=True)
class SchedulerStatus:
    state: str                       # running|stopping|not_responding|stale|stopped
    pid: Optional[int] = None
    host: str = ""
    beat_age_secs: Optional[float] = None
    spawn_error: str = ""


def status(store, now: datetime) -> SchedulerStatus:
    """What the page and the sidebar badge report."""
    spawn_error = store.get_setting(SPAWN_ERROR_KEY, "") or ""
    held = holder(store)
    want = desired(store)

    beat = _unstamp(held[0]) if held else None
    age = (now - beat).total_seconds() if beat else None
    alive = age is not None and age < STALE_SECS

    if not alive:
        # Stale means "should be running and is not" — the one state the UI
        # reconciles by spawning.
        return SchedulerStatus(state="stopped" if want == "stopped" else "stale",
                               spawn_error=spawn_error)

    pid, host = held[1], held[2]
    if want == "running":
        return SchedulerStatus("running", pid, host, age, spawn_error)

    asked = _unstamp(store.get_setting(STOP_REQUESTED_KEY, "") or "")
    waited = (now - asked).total_seconds() if asked else 0.0
    state = "not_responding" if waited >= STOP_GRACE_SECS else "stopping"
    return SchedulerStatus(state, pid, host, age, spawn_error)


# --- starting one ------------------------------------------------------------

def spawn_detached(db_path: Optional[str] = None) -> int:
    """Start the scheduler as a process that outlives whoever started it.

    Detached, so closing the terminal that ran Streamlit does not take the
    scheduler with it. The database path is passed explicitly because the child
    must open the one the app is actually using, not the default relative path
    from whatever directory it inherits.
    """
    log_dir = os.environ.get("KDBMONITOR_LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log = open(os.path.join(log_dir, "scheduler.out"), "ab", buffering=0)

    env = dict(os.environ)
    env["KDBMONITOR_DB"] = db_path or env.get("KDBMONITOR_DB", "kdbmonitor.db")
    # Or the child would inherit the suppression and refuse to start itself.
    env.pop("KDBMONITOR_NO_AUTOSTART", None)

    kwargs: dict = {"stdout": log, "stderr": log, "close_fds": True, "env": env}
    if os.name == "nt":
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                   | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen([sys.executable, "-m", "kdbmonitor.scheduler"],
                            **kwargs)
    return proc.pid


def ensure_running(store, now: Optional[datetime] = None,
                   spawn: Optional[Callable[[], object]] = None,
                   db_path: Optional[str] = None) -> str:
    """Start a scheduler if one should be running and none is.

    Called once per Streamlit server process. The rule reads the desired state
    rather than only the lock, and that is what makes Stop actually stop: the
    naive version — spawn whenever nothing is running — would put the process
    back the moment after somebody stopped it.

    ``db_path`` is the database the *caller* has open. Passing it explicitly
    rather than letting the child re-derive one is what stops the scheduler
    quietly serving a different file from the app that started it.
    """
    now = now or datetime.now(timezone.utc)
    if os.environ.get("KDBMONITOR_NO_AUTOSTART"):
        return "autostart disabled"

    state = status(store, now).state
    if state != "stale":
        return state

    try:
        (spawn or (lambda: spawn_detached(db_path)))()
    except Exception as exc:            # noqa: BLE001 - reported, never raised
        # A scheduler that silently declines to start is the worst available
        # outcome: everything looks normal until a report does not arrive.
        store.set_setting(SPAWN_ERROR_KEY, str(exc))
        return "failed"

    store.set_setting(SPAWN_ERROR_KEY, "")
    return "starting"
