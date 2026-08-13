"""python -m kdbmonitor.scheduler

The process that sends the reports. Started by the app when one should be
running and none is, or by hand with run_scheduler.bat — neither path is
privileged, because the lock in the database is what decides who runs.
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler


def _configure_logging() -> str:
    """Console and a rotating file. The file is not optional: a detached
    process has no console to write to, and without it the answer to "why did
    nothing arrive" is unavailable."""
    log_dir = os.environ.get("KDBMONITOR_LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "scheduler.log")

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    rotating = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5,
                                   encoding="utf-8")
    rotating.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)

    root = logging.getLogger("kdbmonitor")
    root.setLevel(logging.INFO)
    root.handlers = [rotating, console]
    return path


def main() -> int:
    # Before anything imports pyplot. core.dashpdf imports it lazily, which is
    # what makes setting the backend here sufficient — and necessary, since a
    # detached process has no display to draw on.
    import matplotlib
    matplotlib.use("Agg")

    from kdbmonitor.core.client import ConnectionManager
    from kdbmonitor.core.storage import Storage
    from kdbmonitor.scheduler import loop

    path = _configure_logging()
    db = os.environ.get("KDBMONITOR_DB", "kdbmonitor.db")

    log = logging.getLogger("kdbmonitor.scheduler")
    log.info("starting: db=%s log=%s pid=%s", db, path, os.getpid())

    store = Storage(db)
    store.init_db()
    try:
        return loop.serve(store, ConnectionManager())
    except KeyboardInterrupt:
        # Ctrl-C is how somebody stops one they started by hand. serve()
        # releases the lock in its finally, so the next start is instant.
        log.info("interrupted")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
