"""The report archive: where a scheduled PDF is filed, beside being mailed.

Deliberately ignorant of jobs, stores and Streamlit. It is handed a folder, a
dashboard name, a moment and some bytes, and answers two questions: is this
folder usable, and where did the file go. That is what lets the Admin page
validate a path, the scheduler refuse a broken one, and the tests exercise all
of it against a temporary directory with no job in sight.

The layout is one folder per dashboard under a single root::

    <root>/equity_orders/equity_orders_20260820_070000.pdf

The dashboard folder is created on demand; **the root never is**. A missing
root is a typo in Admin, and silently building ``\\\\nas\\reprots\\`` hides the
mistake until somebody goes looking for six weeks of reports.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Optional

ARCHIVE_DIR_KEY = "report_archive_dir"

# Written and removed by check_root. Named so that one left behind by a killed
# process is recognisable rather than mysterious.
_PROBE = ".kdbmonitor-write-test"


def slug(name: str) -> str:
    """A dashboard's name as a filesystem-safe stem.

    Shared with :func:`kdbmonitor.core.dashpdf.pdf_filename` so the folder a
    report is filed in and the attachment it arrives as agree about what a
    dashboard is called.
    """
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_") or "dashboard"


def archive_filename(dashboard_name: str, when: datetime) -> str:
    """``equity_orders_20260820_070000.pdf``.

    Seconds are in the name on purpose: two runs cannot share one, so no run
    ever overwrites another and there is no overwrite policy to get wrong.

    The moment is written in UTC whatever zone it arrives in — the scheduler
    records fire times in UTC, and a name that drifted with the server's zone
    would sort against the run history wrongly.
    """
    stamp = when.astimezone(timezone.utc) if when.tzinfo else when
    return f"{slug(dashboard_name)}_{stamp:%Y%m%d_%H%M%S}.pdf"


def dashboard_dir(root: str, dashboard_name: str) -> str:
    """Where this dashboard's reports live. Says nothing about existence."""
    return os.path.join((root or "").strip(), slug(dashboard_name))


def check_root(path: Optional[str]) -> Optional[str]:
    """``None`` when the folder is usable, else why it is not.

    The reasons are sentences the Admin page prints as-is. The writability
    answer comes from actually writing — ``os.access`` reports on permission
    bits, which on Windows say almost nothing about whether a share will take a
    file, and this question is only worth asking if the answer is true.
    """
    root = (path or "").strip()
    if not root:
        return "no folder is set"
    if not os.path.exists(root):
        return "that folder does not exist"
    if not os.path.isdir(root):
        return "that path is a file, not a folder"

    probe = os.path.join(root, _PROBE)
    try:
        with open(probe, "wb"):
            pass
    except OSError as exc:
        reason = getattr(exc, "strerror", None) or exc
        return f"no permission to write there ({reason})"
    finally:
        # A probe outliving its check would be a file the admin has to wonder
        # about, so removal is not conditional on the write having worked.
        try:
            os.remove(probe)
        except OSError:
            pass
    return None


def write_report(root: str, dashboard_name: str, when: datetime,
                 pdf: bytes) -> str:
    """File ``pdf`` under ``root`` and return the full path written.

    Raises ``FileNotFoundError`` when the root is absent rather than creating
    it — ``makedirs`` would happily build the whole typo.
    """
    base = (root or "").strip()
    if not os.path.isdir(base):
        raise FileNotFoundError(f"the report archive folder {base!r} does not exist")

    folder = dashboard_dir(base, dashboard_name)
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, archive_filename(dashboard_name, when))
    with open(target, "wb") as fh:
        fh.write(pdf)
    return target
