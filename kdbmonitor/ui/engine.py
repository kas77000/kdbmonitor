# kdbmonitor/ui/engine.py
"""The monitoring engine — the evaluation loop that used to live inside the
Monitor page.

It is deliberately page-independent so the app shell (``app.py``) can run it on
*every* tab: checks keep firing while you're on Builder/Admin/Reports, not only
while the Monitor page is showing. The on/off state and cadence are persisted in
the DB (settings table), so monitoring also auto-resumes after a restart —
alerts keep arming and triggering through the whole day without babysitting the
Monitor tab.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import streamlit as st
import streamlit.components.v1 as components

from kdbmonitor.core.evaluate import evaluate_alert
from kdbmonitor.core.notifiers import InAppSink, dispatch, send_email, post_webhook
from kdbmonitor.ui.common import (
    INTERVAL_PRESETS, is_due, make_client_for, should_capture_result,
)

_RUNNING_KEY = "mon_running"
_GRAN_KEY = "mon_gran"
_DEFAULT_GRAN = "15s"


# --- persisted monitoring state (survives page switches and restarts) ------- #
def monitoring_on(store) -> bool:
    return store.get_setting(_RUNNING_KEY, "0") == "1"


def set_monitoring(store, on: bool) -> None:
    store.set_setting(_RUNNING_KEY, "1" if on else "0")


def granularity_label(store) -> str:
    return store.get_setting(_GRAN_KEY, _DEFAULT_GRAN) or _DEFAULT_GRAN


def set_granularity(store, label: str) -> None:
    store.set_setting(_GRAN_KEY, label)


def tick_secs(store) -> int:
    return INTERVAL_PRESETS.get(granularity_label(store), 15)


# --- browser / OS notifications (fire even when the tab is backgrounded) ----- #
# Requires a one-time permission grant (a user gesture), so an Enable button is
# shown until granted. Each payload is deduped by key via localStorage.
_NOTIFY_HTML = """
<div id="kdbn" style="font:13px sans-serif;color:#8b98a5;padding:2px 0"></div>
<script>
(function(){
  var payloads = __PAYLOADS__;
  var box = document.getElementById('kdbn');
  function beep(){ try{
    var c = new (window.AudioContext||window.webkitAudioContext)();
    var o = c.createOscillator(), g = c.createGain();
    o.connect(g); g.connect(c.destination); o.frequency.value = 880;
    g.gain.setValueAtTime(0.15, c.currentTime);
    g.gain.exponentialRampToValueAtTime(0.0001, c.currentTime + 0.3);
    o.start(); o.stop(c.currentTime + 0.3);
  } catch(e){} }
  function fire(){
    var done = JSON.parse(localStorage.getItem('kdbmon_fired') || '[]');
    var played = false;
    payloads.forEach(function(p){
      if(done.indexOf(p.key) === -1){
        try{ new Notification(p.title, {body: p.body, tag: p.key}); } catch(e){}
        if(p.sound && !played){ beep(); played = true; }
        done.push(p.key);
      }
    });
    localStorage.setItem('kdbmon_fired', JSON.stringify(done.slice(-200)));
  }
  if(!('Notification' in window)){ box.textContent = 'Browser notifications not supported'; return; }
  if(Notification.permission === 'granted'){ box.innerHTML = '🔔 Alert notifications on'; fire(); }
  else if(Notification.permission === 'denied'){ box.innerHTML = '🔕 Notifications blocked — enable them in your browser site settings'; }
  else {
    var b = document.createElement('button');
    b.textContent = '🔔 Enable alert notifications';
    b.style.cssText = 'padding:4px 10px;border-radius:6px;border:1px solid #3b82f6;background:#141b24;color:#dfe7ef;cursor:pointer';
    b.onclick = function(){ Notification.requestPermission().then(function(perm){
      if(perm === 'granted'){ box.innerHTML = '🔔 Alert notifications on'; fire(); }
      else if(perm === 'denied'){ box.innerHTML = '🔕 Notifications blocked'; }
    }); };
    box.appendChild(b);
  }
})();
</script>
"""


def browser_notify(payloads: list[dict]) -> None:
    components.html(_NOTIFY_HTML.replace("__PAYLOADS__", json.dumps(payloads)),
                    height=44)


def _email_fn(store):
    smtp_host = store.get_setting("smtp_host", "")
    if not smtp_host:
        return None
    smtp_port = int(store.get_setting("smtp_port", "25"))
    smtp_sender = store.get_setting("smtp_sender", "")
    return lambda to, msg: send_email(
        smtp_host, smtp_port, smtp_sender, to, subject="KdbMonitor alert", body=msg
    )


def run_tick(store, mgr) -> None:
    """One evaluation pass over every due alert (only while monitoring is on).

    Records each run, captures the result on a trigger, and dispatches
    notifications. Always renders the browser-notification component so OS
    notifications fire on whichever page the user is viewing. Safe to call on
    every rerun: it evaluates an alert only when its poll interval is due.
    """
    resolve = make_client_for(store, mgr)
    sink: InAppSink = st.session_state.setdefault("in_app_sink", InAppSink())
    now = datetime.now(timezone.utc)
    payloads: list[dict] = []

    if monitoring_on(store):
        email_fn = _email_fn(store)
        for a in store.list_alerts():
            if not a.enabled:
                continue
            latest = store.latest_run(a.id)
            if not is_due(latest["ts"] if latest else None, a.poll_interval_secs, now):
                continue
            res = evaluate_alert(a, resolve, prev_run=latest, now=now,
                                 last_notified_ts=store.last_notified_at(a.id),
                                 last_triggered_hash=store.last_triggered_hash(a.id))
            store.record_run(a.id, ts=now.isoformat(), status=res.status,
                             triggered=res.triggered, notified=res.notify,
                             row_count=res.row_count, message=res.message,
                             result_hash=res.result_hash)
            prev_trig = bool(latest["triggered"]) if latest else False
            if res.df is not None and should_capture_result(
                    a.result_retention, res.triggered, prev_trig):
                st.session_state.setdefault("last_results", {})[a.id] = {
                    "df": res.df, "rows": res.row_count, "when": now,
                    "mode": a.result_retention}
            if res.triggered and res.df is not None:
                store.save_result(a.id, now.isoformat(), res.df)
            if res.notify:
                dispatch(a.channels, res.message, in_app_sink=sink,
                         email_fn=email_fn, webhook_fn=post_webhook)
                if a.channels.in_app:
                    payloads.append({
                        "key": f"{a.id}-{now.isoformat()}",
                        "title": a.name, "body": res.message,
                        "sound": bool(a.channels.sound)})

    browser_notify(payloads)
