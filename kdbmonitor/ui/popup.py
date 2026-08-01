"""The modal that puts a fired alert's rows in front of the user.

A notification says an alert fired; it can't say what came back. This opens in
the app window with the result already in it, so the answer to "what fired?"
is on screen rather than two clicks away on the Result page.

Two rules keep it from becoming a nuisance:

* it opens **once per fired alert**. Closing it with the X or the Dismiss
  button is final, because the queue records what it has already put on
  screen — the monitoring loop reruns every few seconds and would otherwise
  reopen a modal the user just closed, which is unclosable in practice.
* it is rendered from ``app.py`` at the top level, not from inside the
  monitoring fragment. A fragment rerun redraws only its own elements; a
  dialog owned by the fragment would be torn down on the next tick, seconds
  after appearing.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import streamlit as st

_QUEUE = "popup_queue"       # everything that has fired and not been dismissed
_SHOWN = "popup_shown"       # keys already put on screen, so they stay dismissed
_MAX_PREVIEW_ROWS = 25


def _queue() -> list[dict]:
    return st.session_state.setdefault(_QUEUE, [])


def _shown() -> set:
    return st.session_state.setdefault(_SHOWN, set())


def queue(alert, message: str, when: datetime, row_count: Optional[int]) -> bool:
    """Record that ``alert`` fired and should pop up. True if this is new."""
    key = f"{alert.id}-{when.isoformat()}"
    if any(item["key"] == key for item in _queue()) or key in _shown():
        return False
    _queue().append({"key": key, "alert_id": alert.id, "name": alert.name,
                     "message": message, "when": when, "rows": row_count})
    return True


def pending() -> list[dict]:
    """Queued items that have not been shown yet."""
    shown = _shown()
    return [i for i in _queue() if i["key"] not in shown]


def dismiss_all() -> None:
    st.session_state[_QUEUE] = []


def _result_for(store, alert_id: int):
    """The captured rows for an alert: this session's first, then the DB snapshot."""
    stored = st.session_state.get("last_results", {}).get(alert_id)
    if stored is not None and stored.get("df") is not None:
        return stored["df"]
    from kdbmonitor.ui.result import snapshot_from_store
    snap = snapshot_from_store(store, alert_id)
    return None if snap is None else snap["df"]


def _body(store, items: list[dict]) -> None:
    for n, item in enumerate(items):
        if n:
            st.divider()
        when = item["when"]
        when_txt = when.strftime("%H:%M:%S") if hasattr(when, "strftime") else str(when)
        st.markdown(f"**{item['name']}**")
        rows = item["rows"]
        st.caption(f":material/schedule: {when_txt} UTC"
                   + (f" · {rows} row(s)" if rows is not None else ""))
        df = _result_for(store, item["alert_id"])
        if df is None or len(df) == 0:
            st.info("No rows were captured for this trigger.",
                    icon=":material/info:")
        else:
            st.dataframe(df.head(_MAX_PREVIEW_ROWS), use_container_width=True,
                         hide_index=True)
            if len(df) > _MAX_PREVIEW_ROWS:
                st.caption(f"Showing the first {_MAX_PREVIEW_ROWS} of {len(df)} rows.")
        if st.button("Open the full result", key=f"popup_open_{item['key']}",
                     icon=":material/open_in_full:", type="primary"):
            store.mark_triggers_seen(item["alert_id"])
            st.session_state["result_alert_id"] = item["alert_id"]
            st.session_state["_open_result"] = True
            dismiss_all()
            st.rerun()

    st.divider()
    if st.button("Dismiss", key="popup_dismiss", icon=":material/check:"):
        dismiss_all()
        st.rerun()


def render_pending(store) -> None:
    """Open the modal if something fired that hasn't been shown yet.

    Call from the app shell, outside the monitoring fragment. A Streamlit too
    old for ``st.dialog`` falls back to a toast — the Monitor's NEW badge and
    the View button still lead to the same rows.
    """
    items = pending()
    if not items:
        return
    for item in items:
        _shown().add(item["key"])

    if not hasattr(st, "dialog"):
        for item in items:
            st.toast(f"{item['name']} triggered", icon=":material/notifications_active:")
        return

    title = ("Alert triggered" if len(items) == 1
             else f"{len(items)} alerts triggered")

    @st.dialog(title, width="large")
    def _show() -> None:
        _body(store, items)

    _show()


def request_open() -> None:
    """Ask the app shell for a full rerun so a queued pop-up can be drawn.

    The monitoring loop is a fragment: on its own, a tick redraws the sidebar
    and nothing else, and the modal would wait for whatever made the user
    interact with the page next.
    """
    try:
        st.rerun(scope="app")
    except TypeError:                       # Streamlit without fragment scopes
        st.rerun()
