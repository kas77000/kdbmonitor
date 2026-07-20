# kdbmonitor/ui/monitor.py
from __future__ import annotations

import json
from datetime import datetime, timezone

import streamlit as st
import streamlit.components.v1 as components

from kdbmonitor.core.client import ConnectionManager
from kdbmonitor.core.evaluate import evaluate_alert
from kdbmonitor.core.notifiers import InAppSink, dispatch, send_email, post_webhook
from kdbmonitor.ui.common import (
    STATUS_META, INTERVAL_PRESETS, is_due, secs_until_due, humanize_secs,
    condition_summary, make_client_for, should_capture_result,
)


def _email_fn(store):
    smtp_host = store.get_setting("smtp_host", "")
    if not smtp_host:
        return None
    smtp_port = int(store.get_setting("smtp_port", "25"))
    smtp_sender = store.get_setting("smtp_sender", "")
    return lambda to, msg: send_email(
        smtp_host, smtp_port, smtp_sender, to, subject="KdbMonitor alert", body=msg
    )


# Browser (OS-level) notifications that show even when the tab is minimized.
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


def _browser_notify(payloads: list[dict]) -> None:
    components.html(_NOTIFY_HTML.replace("__PAYLOADS__", json.dumps(payloads)),
                    height=44)


def render(store, mgr: ConnectionManager) -> None:
    if st.session_state.pop("_open_result", False) and "_nav_pages" in st.session_state:
        st.switch_page(st.session_state["_nav_pages"]["result"])
    resolve = make_client_for(store, mgr)
    sink: InAppSink = st.session_state.setdefault("in_app_sink", InAppSink())

    st.subheader(":material/monitoring: Live monitor")

    ctl = st.columns([1.4, 2.4, 1.2], vertical_alignment="center")
    running = ctl[0].toggle("Monitoring", value=False, key="mon_running",
                            help="Checks run only while this is on and the page is open.")
    gran_label = ctl[1].segmented_control(
        "Check granularity", list(INTERVAL_PRESETS.keys()), default="15s",
        key="mon_gran", help="How often the loop wakes. Each alert still checks on its own interval.",
    )
    tick = INTERVAL_PRESETS.get(gran_label or "15s", 15)
    ctl[2].markdown(
        (":green-badge[:material/sensors: Active]" if running
         else ":gray-badge[:material/sensors_off: Paused]")
    )

    alerts = store.list_alerts()
    if not alerts:
        st.info("No alerts yet. Create one in the Builder.", icon=":material/build:")
        return

    @st.fragment(run_every=tick if running else None)
    def _tick() -> None:
        now = datetime.now(timezone.utc)
        email_fn = _email_fn(store)
        notify_payloads = []
        display = []

        for a in store.list_alerts():
            latest = store.latest_run(a.id)
            if not a.enabled:
                display.append({"a": a, "status": "disabled", "rows": None,
                                "checked": None, "next": None})
                continue

            # only evaluate / record / notify while monitoring is live; otherwise
            # just display the last known status (a page rerun must not fire alerts)
            due = running and is_due(latest["ts"] if latest else None,
                                     a.poll_interval_secs, now)
            if due:
                res = evaluate_alert(a, resolve, prev_run=latest, now=now,
                                     last_notified_ts=store.last_notified_at(a.id),
                                     last_notified_hash=store.last_notified_hash(a.id))
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
                # Persist the latest snapshot per day for historical reports.
                if res.triggered and res.df is not None:
                    store.save_result(a.id, now.isoformat(), res.df)
                if res.notify:
                    dispatch(a.channels, res.message, in_app_sink=sink,
                             email_fn=email_fn, webhook_fn=post_webhook)
                    if a.channels.in_app:
                        notify_payloads.append({
                            "key": f"{a.id}-{now.isoformat()}",
                            "title": a.name, "body": res.message,
                            "sound": bool(a.channels.sound)})
                display.append({"a": a, "status": res.status, "rows": res.row_count,
                                "checked": now, "next": a.poll_interval_secs})
            else:
                status = latest["status"] if latest else "pending"
                nxt = secs_until_due(latest["ts"] if latest else None,
                                     a.poll_interval_secs, now)
                display.append({"a": a, "status": status,
                                "rows": latest["row_count"] if latest else None,
                                "checked": latest["ts"] if latest else None, "next": nxt})

        # KPI row
        n_trig = sum(1 for d in display if d["status"] == "triggered")
        n_err = sum(1 for d in display if d["status"] == "error")
        n_armed = sum(1 for d in display if d["status"] == "armed")
        k = st.columns(4)
        k[0].metric("Alerts", len(display), border=True)
        k[1].metric("Armed", n_armed, border=True)
        k[2].metric("Triggered", n_trig, border=True)
        k[3].metric("Errors", n_err, border=True)

        # Active triggers surfaced first
        for d in display:
            if d["status"] == "triggered":
                st.error(f"**{d['a'].name}** — {condition_summary(d['a'].trigger)}  "
                         f"·  {d['rows']} row(s)", icon=":material/notifications_active:")

        # Browser notifications (fire on new triggers; also shows the enable button)
        _browser_notify(notify_payloads)

        # Per-alert status rows
        last_results = st.session_state.get("last_results", {})
        for d in display:
            a = d["a"]
            label, color, icon = STATUS_META[d["status"]]
            row = st.columns([1.4, 3.4, 1, 2.1, 1.1], vertical_alignment="center")
            row[0].badge(label, icon=icon, color=color)
            row[1].markdown(f"**{a.name}**  \n:gray[Triggers when {condition_summary(a.trigger)}]")
            rows_txt = "—" if d["rows"] is None else str(d["rows"])
            row[2].markdown(f"`{rows_txt}` rows")
            if d["status"] == "disabled":
                row[3].caption("disabled")
            elif d["status"] == "pending":
                row[3].caption("awaiting first check")
            else:
                nxt = d["next"] if isinstance(d["next"], int) else a.poll_interval_secs
                row[3].caption(f":material/schedule: next check in {humanize_secs(nxt)} "
                               f"· every {humanize_secs(a.poll_interval_secs)}")

            stored = last_results.get(a.id)
            if stored is not None and stored.get("df") is not None:
                if row[4].button("View", key=f"view_{a.id}",
                                 icon=":material/open_in_full:",
                                 help="Open the full result table"):
                    st.session_state["result_alert_id"] = a.id
                    st.session_state["_open_result"] = True
                    st.rerun()
            else:
                row[4].caption("—")

        st.caption(f"Last loop: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC"
                   + ("" if running else " · monitoring paused"))

    _tick()
