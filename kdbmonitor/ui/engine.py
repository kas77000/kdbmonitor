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

from kdbmonitor.core import schedule
from kdbmonitor.core.evaluate import evaluate_alert
from kdbmonitor.core.notifiers import (
    InAppSink, delivery_payload, dispatch, send_email, post_webhook,
)
from kdbmonitor.core.qcache import QueryCache
from kdbmonitor.ui import popup
from kdbmonitor.ui.common import (
    INTERVAL_PRESETS, is_due, make_client_for, should_capture_result,
)

_RUNNING_KEY = "mon_running"
_GRAN_KEY = "mon_gran"
_DEFAULT_GRAN = "15s"
# Where the rows of steps with a TTL are held between ticks. One cache for every
# alert, because it is keyed by the query rather than by whoever asked: two
# alerts reading the same universe from the same server fetch it once between
# them. It lives in session state, so a restart starts empty — the TTL is what
# keeps it honest while the app is up (see models.Step.cache_secs).
_STEP_CACHE_KEY = "alert_step_cache"


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


# --- browser-side delivery: notification, sound, and window attention ------- #
#
# Notifications need a one-time permission grant (a user gesture), so an Enable
# button is shown until it is granted. Every payload is deduped by key through
# localStorage, which is also why the same payloads can be handed to the
# component on several ticks without firing twice.
#
# On "bring the window to the front": no browser lets a page raise its own
# window on its own — that permission was removed years ago precisely because
# it was abused, and there is no flag or API that gives it back. What a page
# *can* do, and what this does, is:
#
#   * post a notification that stays on screen until it is clicked
#     (requireInteraction), which on Windows also flashes the taskbar button;
#   * focus the window from inside the notification's click handler, where the
#     click counts as the user's own gesture — so one click on the toast brings
#     the tab forward, whatever the user was doing;
#   * flash the tab title until the window is looked at, so a window that is
#     merely behind another one still shouts.
#
# The flashing state lives on the parent window rather than in this iframe:
# Streamlit tears the iframe down whenever the payloads change, and a timer
# started in a dead frame dies with it. Each load re-adopts a flash that is
# still wanted, which is what makes it survive a rerun.
_NOTIFY_HTML = """
<div id="kdbn" style="font:13px sans-serif;color:#8b98a5;padding:2px 0"></div>
<script>
(function(){
  var payloads = __PAYLOADS__;
  var box = document.getElementById('kdbn');
  var W = (function(){ try{ window.parent.document.title; return window.parent; }
                       catch(e){ return window; } })();
  var G = W.__kdbmon = W.__kdbmon || {};
  var timer = null;

  function beep(){ try{
    var c = G.actx || (G.actx = new (window.AudioContext||window.webkitAudioContext)());
    if(c.state === 'suspended'){ c.resume(); }
    var o = c.createOscillator(), g = c.createGain();
    o.connect(g); g.connect(c.destination); o.frequency.value = 880;
    g.gain.setValueAtTime(0.15, c.currentTime);
    g.gain.exponentialRampToValueAtTime(0.0001, c.currentTime + 0.3);
    o.start(); o.stop(c.currentTime + 0.3);
  } catch(e){} }

  function stopFlash(){
    if(timer){ clearInterval(timer); timer = null; }
    G.flash = null;
    if(G.title){ try{ W.document.title = G.title; } catch(e){} }
  }
  function startFlash(label){
    if(!G.title){ G.title = W.document.title; }
    G.flash = label;
    if(timer){ return; }
    var on = false;
    timer = setInterval(function(){
      // The flag is the authority, not this timer: whoever calls off the
      // flashing (a click on the window, a click on the notification) only
      // has to clear it, and the next tick stops and puts the title back.
      if(!G.flash){ stopFlash(); return; }
      on = !on;
      try{ W.document.title = on ? ('\\u25CF ' + G.flash) : G.title; }
      catch(e){ clearInterval(timer); timer = null; }
    }, 900);
  }
  function bringToFront(){
    // Only ever reached from a notification click, i.e. with the user's own
    // gesture behind it - the one case in which a browser honours focus().
    try{ W.focus(); }catch(e){}
    try{ window.focus(); }catch(e){}
    stopFlash();
  }

  // Looking at the window is the end of the shouting, however it got there.
  // Wired once per window, and it only clears the flag - the timer above puts
  // the title back on its next tick, wherever that timer happens to live.
  if(!G.wired){
    G.wired = true;
    try{ W.addEventListener('focus', function(){ W.__kdbmon.flash = null; }); }
    catch(e){}
  }
  // Re-adopt a flash left running by an iframe Streamlit has since replaced.
  if(G.flash){ startFlash(G.flash); }

  function fire(){
    var done;
    try{ done = JSON.parse(localStorage.getItem('kdbmon_fired') || '[]'); }
    catch(e){ done = []; }
    var played = false;
    payloads.forEach(function(p){
      if(done.indexOf(p.key) !== -1){ return; }
      if(p.notify){
        try{
          var n = new Notification(p.title, {body: p.body, tag: p.key,
                                             requireInteraction: !!p.focus});
          n.onclick = function(){ bringToFront(); try{ n.close(); }catch(e){} };
        } catch(e){}
      }
      if(p.sound && !played){ beep(); played = true; }
      if(p.focus){ startFlash(p.title); }
      done.push(p.key);
    });
    try{ localStorage.setItem('kdbmon_fired', JSON.stringify(done.slice(-200))); }
    catch(e){}
  }

  var wanted = payloads.some(function(p){ return p.notify; });
  if(!('Notification' in window)){
    box.textContent = 'Browser notifications not supported here';
    fire();                                   // sound and title flash still work
    return;
  }
  if(Notification.permission === 'granted'){
    box.innerHTML = '\\uD83D\\uDD14 Alert notifications on';
    fire();
  }
  else if(Notification.permission === 'denied'){
    box.innerHTML = '\\uD83D\\uDD15 Notifications blocked \\u2014 enable them in your browser site settings';
    fire();
  }
  else {
    var b = document.createElement('button');
    b.textContent = '\\uD83D\\uDD14 Enable alert notifications';
    b.style.cssText = 'padding:4px 10px;border-radius:6px;border:1px solid #3b82f6;background:#141b24;color:#dfe7ef;cursor:pointer';
    b.onclick = function(){ Notification.requestPermission().then(function(perm){
      if(perm === 'granted'){ box.innerHTML = '\\uD83D\\uDD14 Alert notifications on'; fire(); }
      else if(perm === 'denied'){ box.innerHTML = '\\uD83D\\uDD15 Notifications blocked'; }
    }); };
    box.appendChild(b);
    if(wanted){ fire(); }                     // don't lose the sound while asking
  }
})();
</script>
"""

# How many recent payloads ride along on every render. They are deduped in the
# browser, so re-sending is free — and it is what stops a notification being
# lost when a rerun replaces the iframe before its script has run.
_PAYLOAD_MEMORY = 20


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


def _park_off_hours(store, alert, latest, now) -> None:
    """Record that an alert is outside its active hours — once, not every tick.

    The row is worth writing at all because it ends whatever state the alert
    was left in when its window closed: a trigger still standing at 18:00 would
    otherwise be the 'previous' state at tomorrow's open, and a 'transition'
    re-arm only notifies on a rising edge. Parking it disarms the alert so the
    next real trigger inside the window is a fresh one.
    """
    if latest is not None and latest["status"] == "off_hours":
        return
    store.record_run(alert.id, ts=now.isoformat(), status="off_hours",
                     triggered=False, notified=False, row_count=None,
                     message=f"{alert.name}: outside its active hours")


def run_tick(store, mgr) -> None:
    """One evaluation pass over every due alert (only while monitoring is on).

    Records each run, captures the result on a trigger, and dispatches
    notifications. Always renders the browser-notification component so OS
    notifications fire on whichever page the user is viewing. Safe to call on
    every rerun: it evaluates an alert only when its poll interval is due and
    only inside the alert's own active hours.
    """
    resolve = make_client_for(store, mgr)
    sink: InAppSink = st.session_state.setdefault("in_app_sink", InAppSink())
    now = datetime.now(timezone.utc)
    payloads: list[dict] = st.session_state.setdefault("notify_payloads", [])
    toasts: list[str] = []
    new_popups = False

    if monitoring_on(store):
        email_fn = _email_fn(store)
        step_cache: QueryCache = st.session_state.setdefault(_STEP_CACHE_KEY,
                                                             QueryCache())
        for a in store.list_alerts():
            if not a.enabled:
                continue
            latest = store.latest_run(a.id)
            if not schedule.is_active(a.schedule, now):
                _park_off_hours(store, a, latest, now)
                continue
            if not is_due(latest["ts"] if latest else None, a.poll_interval_secs, now):
                continue
            res = evaluate_alert(a, resolve, prev_run=latest, now=now,
                                 last_notified_ts=store.last_notified_at(a.id),
                                 last_triggered_hash=store.last_triggered_hash(a.id),
                                 cache=step_cache)
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
                payload = delivery_payload(a.name, a.channels, res.message,
                                           key=f"{a.id}-{now.isoformat()}")
                if payload is not None:
                    payloads.append(payload)
                if a.channels.in_app:
                    toasts.append(res.message)
                if a.channels.popup and popup.queue(a, res.message, now, res.row_count):
                    new_popups = True

    del payloads[:-_PAYLOAD_MEMORY]
    browser_notify(payloads)
    for message in toasts:
        st.toast(message, icon=":material/notifications_active:")
    if new_popups:
        # Last, because it doesn't return: the notification component and the
        # toasts above have to reach the browser first.
        popup.request_open()
