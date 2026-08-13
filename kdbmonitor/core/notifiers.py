# kdbmonitor/core/notifiers.py
from __future__ import annotations

import re
import smtplib
from email.message import EmailMessage
from email.mime.text import MIMEText
from typing import Callable, Optional, Sequence

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


_SEPARATORS = re.compile(r"[,;]")


def parse_address_list(text: Optional[str]) -> list[str]:
    """Addresses from one typed field.

    Comma or semicolon, because Outlook puts semicolons on the clipboard and
    refusing a pasted list for its separator is being wrong about something
    that does not matter. Blank entries and a trailing separator are dropped.
    """
    if not text:
        return []
    return [part.strip() for part in _SEPARATORS.split(text) if part.strip()]


def send_report_email(smtp_host: str, smtp_port: int, sender: str,
                      to: list[str], subject: str, body: str,
                      attachments: Sequence[tuple[str, bytes, str]] = ()
                      ) -> None:
    """A message that can carry a PDF.

    ``EmailMessage`` rather than the ``MIMEText`` :func:`send_email` uses,
    which cannot hold an attachment. The alert path keeps that function
    unchanged — it has no attachment to carry and no reason to be touched.

    ``attachments`` are ``(filename, data, mime_type)``.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg.set_content(body or "")

    for filename, data, mime in attachments:
        maintype, _, subtype = mime.partition("/")
        msg.add_attachment(data, maintype=maintype,
                           subtype=subtype or "octet-stream",
                           filename=filename)

    with smtplib.SMTP(smtp_host, smtp_port) as s:
        # Recipients passed explicitly rather than left to the header: one
        # envelope for the whole list, so three admins get one conversation
        # and can reply to each other rather than three separate threads.
        s.send_message(msg, from_addr=sender, to_addrs=list(to))


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
