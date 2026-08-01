"""The controls a dashboard's parameters are set with.

Nothing here decides anything: which values are on offer, which one applies and
whether it is allowed are all settled in ``core.parameters`` and
``core.paramrules``. This renders the controls, remembers what this reader
picked, and says whether the dashboard may run.

The picks live in session state rather than on the dashboard because they belong
to whoever is looking: two people reading the same report are entitled to
different instruments, and neither should be writing their choice into a
document the other will open.

Two shapes, chosen by where the parameters actually reach:

* **none of them reach a query** — the controls are live, exactly as before.
  Changing one re-shapes frames already in hand, so there is nothing to wait
  for and no reason to make somebody press a button.
* **one or more reach a query** — the controls become a form with **Apply** and
  **Reset**. A query per keystroke would be a query per keystroke, and the form
  is also where a value gets checked against the author's rules *before*
  anything is sent: an empty dashboard teaches the reader nothing, while "that
  date is a Sunday" tells them exactly what to do next.
"""
from __future__ import annotations

from datetime import date

import streamlit as st

from kdbmonitor.core import paramrules
from kdbmonitor.core.dashboard_models import Dashboard, Parameter
from kdbmonitor.core.parameters import query_params


def value_key(dashboard_id, name: str, nonce: int = 0) -> str:
    """The widget's key. The nonce is how Reset works — see :func:`_reset`."""
    suffix = f"_{nonce}" if nonce else ""
    return f"dash_param_{dashboard_id}_{name}{suffix}"


def applied_key(dashboard_id) -> str:
    return f"dash_applied_{dashboard_id}"


def nonce_key(dashboard_id) -> str:
    return f"dash_pnonce_{dashboard_id}"


def problems_key(dashboard_id) -> str:
    return f"dash_pproblems_{dashboard_id}"


def _nonce(dashboard_id) -> int:
    return int(st.session_state.get(nonce_key(dashboard_id), 0))


def defaults(dashboard: Dashboard) -> dict[str, str]:
    return {p.name: p.default for p in dashboard.parameters}


def chosen_values(dashboard: Dashboard) -> dict[str, str]:
    """What this reader has settled on.

    Where a form is in play that is the *applied* set, not what the controls
    currently show: a half-typed symbol must not reach a query, and an Apply
    that failed its rules must leave the dashboard on the values it was already
    running.

    A name that is missing is simply absent: ``core.parameters.resolve_values``
    falls back to the default, and knows what to do when a stored pick is no
    longer on offer.
    """
    if query_params(dashboard):
        applied = st.session_state.get(applied_key(dashboard.id))
        return dict(applied if applied is not None else defaults(dashboard))
    out: dict[str, str] = {}
    nonce = _nonce(dashboard.id)
    for p in dashboard.parameters:
        held = st.session_state.get(value_key(dashboard.id, p.name, nonce))
        if held is not None:
            out[p.name] = _as_text(held)
    return out


def _as_text(value) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def forget(dashboard: Dashboard) -> None:
    """Drop this dashboard's picks — used when its parameters are redefined."""
    nonce = _nonce(dashboard.id)
    for p in dashboard.parameters:
        for n in range(nonce + 1):
            st.session_state.pop(value_key(dashboard.id, p.name, n), None)
    st.session_state.pop(applied_key(dashboard.id), None)
    st.session_state.pop(problems_key(dashboard.id), None)


def forget_one(dashboard: Dashboard, name: str) -> None:
    """Drop one parameter's pick — used when it is deleted in the editor."""
    for n in range(_nonce(dashboard.id) + 1):
        st.session_state.pop(value_key(dashboard.id, name, n), None)
    applied = st.session_state.get(applied_key(dashboard.id))
    if isinstance(applied, dict):
        applied.pop(name, None)


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
    nonce = _nonce(dashboard.id)
    held_key = value_key(dashboard.id, was, nonce)
    if held_key in st.session_state:
        st.session_state[value_key(dashboard.id, now, nonce)] = \
            st.session_state.pop(held_key)
    applied = st.session_state.get(applied_key(dashboard.id))
    if isinstance(applied, dict) and was in applied:
        applied[now] = applied.pop(was)


def _reset(dashboard: Dashboard) -> None:
    """Put every control back to its default.

    By moving the widgets to fresh keys rather than by writing to the ones on
    screen: Streamlit refuses to have a widget's state assigned after that
    widget has been created in the same run, and a Reset that has to happen on
    the *next* run is a Reset that visibly lags a click.
    """
    st.session_state[nonce_key(dashboard.id)] = _nonce(dashboard.id) + 1
    st.session_state[applied_key(dashboard.id)] = defaults(dashboard)
    st.session_state.pop(problems_key(dashboard.id), None)


def current_values(dashboard: Dashboard) -> dict[str, str]:
    """What the controls are showing right now, applied or not."""
    nonce = _nonce(dashboard.id)
    out = defaults(dashboard)
    for p in dashboard.parameters:
        held = st.session_state.get(value_key(dashboard.id, p.name, nonce))
        if held is not None:
            out[p.name] = _as_text(held)
    return out


def render(dashboard: Dashboard, choices: dict, on_change,
           *, on_apply=None, today: date | None = None) -> bool:
    """One row of controls above the dashboard. True if it may run.

    ``choices`` comes from the last run's results, so a picker offers what the
    data actually held rather than what it held when the dashboard was saved.
    ``on_change`` drops the *derived* frames, for parameters that only reach a
    transform; ``on_apply`` is called when a form submission changes a value a
    query reads, and is what goes back to the server.
    """
    if not dashboard.parameters:
        return True
    today = today or date.today()
    gated = bool(query_params(dashboard))
    if not gated:
        with st.container(border=True):
            _controls(dashboard, choices, on_change)
        return True
    return _form(dashboard, choices, on_apply, today)


def _form(dashboard: Dashboard, choices: dict, on_apply, today: date) -> bool:
    reads = query_params(dashboard)
    problems = st.session_state.get(problems_key(dashboard.id)) or {}

    with st.container(border=True):
        with st.form(key=f"dash_form_{dashboard.id}_{_nonce(dashboard.id)}"):
            st.caption(":material/tune: Set these, then Apply — the query runs "
                       "with the values you applied.")
            _controls(dashboard, choices, None, problems=problems)
            buttons = st.columns([1.1, 1.1, 5.8], vertical_alignment="center")
            applied = buttons[0].form_submit_button(
                "Apply", type="primary", icon=":material/play_arrow:",
                use_container_width=True)
            reset = buttons[1].form_submit_button(
                "Reset", icon=":material/restart_alt:",
                use_container_width=True,
                help="Put every control back to its default")

    if reset:
        _reset(dashboard)
        if on_apply is not None:
            on_apply()
        st.rerun()

    if applied:
        showing = current_values(dashboard)
        found = paramrules.check_all(dashboard.parameters, showing, today=today)
        st.session_state[problems_key(dashboard.id)] = found
        if not found:
            was = st.session_state.get(applied_key(dashboard.id))
            st.session_state[applied_key(dashboard.id)] = showing
            # Only a value a *query* reads is worth a round trip; changing one
            # that only feeds a transform re-shapes what is already in hand.
            requeries = was is None or any(
                (was or {}).get(name) != showing.get(name) for name in reads)
            if requeries and on_apply is not None:
                on_apply()
        st.rerun()

    # Held to the applied values, which is what the dashboard is running on.
    standing = paramrules.check_all(dashboard.parameters,
                                    chosen_values(dashboard), today=today,
                                    only=reads)
    if standing:
        for message in standing.values():
            st.warning(message, icon=":material/rule:")
        st.info("The query has not run — fix the form above and press Apply.",
                icon=":material/pending_actions:")
        return False
    return True


def _controls(dashboard: Dashboard, choices: dict, on_change,
              problems: dict | None = None) -> None:
    cols = st.columns(min(len(dashboard.parameters), 4))
    for i, p in enumerate(dashboard.parameters):
        _control(cols[i % len(cols)], dashboard, p,
                 choices.get(p.name) or [], on_change,
                 (problems or {}).get(p.name))


def _control(container, dashboard: Dashboard, p: Parameter,
             options: list, on_change, problem: str | None = None) -> None:
    key = value_key(dashboard.id, p.name, _nonce(dashboard.id))
    label = p.label or p.name
    # A form's widgets cannot carry a callback, and do not need one: nothing
    # happens until Apply.
    hook = {} if on_change is None else {"on_change": on_change}
    helping = p.help or None

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
        container.selectbox(label, offered, key=key, help=helping, **hook)
    elif p.kind == "toggle":
        st.session_state.setdefault(key, str(p.default).strip().lower()
                                    in ("true", "1", "yes"))
        container.checkbox(label, key=key, help=helping, **hook)
    elif p.kind == "number":
        try:
            start = float(p.default)
        except (TypeError, ValueError):
            start = 0.0
        st.session_state.setdefault(key, start)
        container.number_input(label, key=key, help=helping, **hook)
    elif p.kind == "date":
        # An out-of-range default is left for the rules to report by name,
        # rather than being clamped by the picker into a date nobody chose.
        st.session_state.setdefault(key, paramrules.as_date(p.default))
        container.date_input(label, key=key, help=helping, format="YYYY-MM-DD",
                             **hook)
    else:
        st.session_state.setdefault(key, p.default)
        container.text_input(label, key=key, help=helping, **hook)

    if problem:
        container.markdown(f":red[:material/error: {problem}]")
