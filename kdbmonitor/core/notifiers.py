# kdbmonitor/core/notifiers.py
from __future__ import annotations

import smtplib
from email.mime.text import MIMEText
from typing import Callable, Optional

import requests

from kdbmonitor.core.models import Channels


class InAppSink:
    """Collects messages to render in the Streamlit UI."""
    def __init__(self):
        self.messages: list[str] = []

    def push(self, message: str) -> None:
        self.messages.append(message)


def send_email(smtp_host: str, smtp_port: int, sender: str,
               to: list[str], subject: str, body: str) -> None:
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    with smtplib.SMTP(smtp_host, smtp_port) as s:
        s.sendmail(sender, to, msg.as_string())


def post_webhook(url: str, message: str) -> None:
    requests.post(url, json={"text": message}, timeout=10)


def delivery_payload(name: str, channels: Channels, message: str,
                     key: str) -> Optional[dict]:
    """What the browser should do about one fired alert, or None for nothing.

    Kept here, away from Streamlit, because which deliveries an alert asked
    for is a property of the alert rather than of the page that happens to be
    open. ``ui/engine.py`` hands the result to the notification component.
    """
    if not (channels.browser or channels.sound or channels.focus):
        return None
    return {
        "key": key,
        "title": name,
        "body": message,
        "notify": bool(channels.browser),
        "sound": bool(channels.sound),
        "focus": bool(channels.focus),
    }


def dispatch(channels: Channels, message: str, in_app_sink: InAppSink,
             email_fn: Optional[Callable[[list[str], str], None]] = None,
             webhook_fn: Optional[Callable[[str, str], None]] = None) -> None:
    if channels.in_app:
        in_app_sink.push(message)
    if channels.email_to and email_fn is not None:
        email_fn(channels.email_to, message)
    if webhook_fn is not None:
        for url in channels.webhook_urls:
            webhook_fn(url, message)
