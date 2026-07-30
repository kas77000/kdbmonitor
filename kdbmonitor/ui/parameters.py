"""The controls a dashboard's parameters are set with.

Nothing here decides anything: which values are on offer and which one applies
are both settled in ``core.parameters``. This renders the controls, remembers
what this reader picked, and drops the derived frames when it changes.

The picks live in session state rather than on the dashboard because they belong
to whoever is looking: two people reading the same report are entitled to
different instruments, and neither should be writing their choice into a
document the other will open.
"""
from __future__ import annotations

import streamlit as st

from kdbmonitor.core.dashboard_models import Dashboard, Parameter


def value_key(dashboard_id, name: str) -> str:
    return f"dash_param_{dashboard_id}_{name}"


def chosen_values(dashboard: Dashboard) -> dict[str, str]:
    """What this reader has picked so far.

    A name that is missing is simply absent: ``core.parameters.resolve_values``
    falls back to the default, and knows what to do when a stored pick is no
    longer on offer.
    """
    out: dict[str, str] = {}
    for p in dashboard.parameters:
        held = st.session_state.get(value_key(dashboard.id, p.name))
        if held is not None:
            out[p.name] = str(held)
    return out


def forget(dashboard: Dashboard) -> None:
    """Drop this dashboard's picks — used when its parameters are redefined."""
    for p in dashboard.parameters:
        st.session_state.pop(value_key(dashboard.id, p.name), None)


def rename(dashboard: Dashboard, was: str, now: str) -> None:
    """Follow a parameter's pick across a rename in the editor.

    A pick is filed under the parameter's name, and the name is editable in
    the same card — renaming it without moving the pick would lose whatever a
    reader had already chosen, the moment the author renamed the control out
    from under them. Same idea as ``fileshape.rename_sample`` following a
    dataset's held sample across its own rename.
    """
    if not was or was == now or not now:
        return
    held_key = value_key(dashboard.id, was)
    if held_key in st.session_state:
        st.session_state[value_key(dashboard.id, now)] = \
            st.session_state.pop(held_key)


def render(dashboard: Dashboard, choices: dict, on_change) -> None:
    """One row of controls above the dashboard.

    ``choices`` comes from the last run's results, so a picker offers what the
    data actually held rather than what it held when the dashboard was saved.
    ``on_change`` drops the *derived* frames; the fetched ones stand, because a
    parameter never reaches a query.
    """
    if not dashboard.parameters:
        return
    with st.container(border=True):
        cols = st.columns(min(len(dashboard.parameters), 4))
        for i, p in enumerate(dashboard.parameters):
            _control(cols[i % len(cols)], dashboard, p,
                     choices.get(p.name) or [], on_change)


def _control(container, dashboard: Dashboard, p: Parameter,
             options: list, on_change) -> None:
    key = value_key(dashboard.id, p.name)
    label = p.label or p.name
    if p.kind in ("choice", "column"):
        offered = [str(o) for o in (options or p.choices)]
        if not offered:
            # A column picker before its dataset has run, or after it failed.
            # Saying so beats an empty dropdown that looks broken.
            container.selectbox(label, [p.default or "—"], disabled=True,
                                help="Waiting for the data this reads its "
                                     "values from.")
            return
        if p.default and p.default not in offered:
            offered = offered + [p.default]
        current = st.session_state.get(key, p.default)
        if current not in offered:
            current = offered[0]
        st.session_state.setdefault(key, current)
        container.selectbox(label, offered, key=key, on_change=on_change)
    elif p.kind == "toggle":
        st.session_state.setdefault(key, str(p.default).strip().lower()
                                    in ("true", "1", "yes"))
        container.checkbox(label, key=key, on_change=on_change)
    elif p.kind == "number":
        try:
            start = float(p.default)
        except (TypeError, ValueError):
            start = 0.0
        st.session_state.setdefault(key, start)
        container.number_input(label, key=key, on_change=on_change)
    else:
        st.session_state.setdefault(key, p.default)
        container.text_input(label, key=key, on_change=on_change)
