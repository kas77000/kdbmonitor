"""Mailing a report, and reading the admin list somebody typed."""
import smtplib

import pytest

from kdbmonitor.core.notifiers import parse_address_list, send_report_email


class _FakeSMTP:
    """Records what would have been sent. Constructed as a context manager,
    the way smtplib.SMTP is used."""
    sent: list[dict] = []

    def __init__(self, host, port):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def send_message(self, msg, from_addr=None, to_addrs=None):
        type(self).sent.append({"host": self.host, "port": self.port,
                                "msg": msg, "from": from_addr, "to": to_addrs})


@pytest.fixture()
def sent(monkeypatch):
    _FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    return _FakeSMTP.sent


# --- the admin list ----------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("a@x.com", ["a@x.com"]),
    ("a@x.com,b@x.com", ["a@x.com", "b@x.com"]),
    ("a@x.com; b@x.com", ["a@x.com", "b@x.com"]),
    ("a@x.com; b@x.com, c@x.com", ["a@x.com", "b@x.com", "c@x.com"]),
    ("  a@x.com ,  b@x.com  ", ["a@x.com", "b@x.com"]),
    ("a@x.com,", ["a@x.com"]),
    ("a@x.com,,b@x.com", ["a@x.com", "b@x.com"]),
    ("", []),
    (None, []),
    ("   ", []),
])
def test_an_address_list_is_read_however_it_was_pasted(text, expected):
    """Outlook puts semicolons on the clipboard. Refusing a pasted list for
    its separator is being wrong about something that does not matter."""
    assert parse_address_list(text) == expected


# --- sending -----------------------------------------------------------------

def test_a_report_goes_out_with_its_pdf_attached(sent):
    send_report_email("smtp.corp.local", 25, "kdbmonitor@corp.local",
                      ["desk@corp.local", "risk@corp.local"],
                      "OMS Overview — 2026-08-14", "Attached.",
                      [("oms_overview_2026-08-14_0700.pdf", b"%PDF-1.4 fake",
                        "application/pdf")])
    assert len(sent) == 1
    call = sent[0]
    assert (call["host"], call["port"]) == ("smtp.corp.local", 25)
    assert call["to"] == ["desk@corp.local", "risk@corp.local"]
    assert call["from"] == "kdbmonitor@corp.local"

    msg = call["msg"]
    assert msg["Subject"] == "OMS Overview — 2026-08-14"
    assert msg["To"] == "desk@corp.local, risk@corp.local"

    parts = list(msg.iter_attachments())
    assert len(parts) == 1
    assert parts[0].get_filename() == "oms_overview_2026-08-14_0700.pdf"
    assert parts[0].get_content_type() == "application/pdf"
    assert parts[0].get_payload(decode=True) == b"%PDF-1.4 fake"


def test_the_body_is_readable_text(sent):
    send_report_email("h", 25, "s@x.com", ["a@x.com"], "S", "The body.")
    assert "The body." in sent[0]["msg"].get_content()


def test_a_notice_with_no_attachment_is_a_plain_message(sent):
    send_report_email("h", 25, "s@x.com", ["admin@x.com"], "[FAILED] J", "why")
    assert list(sent[0]["msg"].iter_attachments()) == []


def test_several_addresses_are_one_message_not_one_each(sent):
    """They are colleagues working the same failure; seeing who else was told,
    and being able to reply to all of them, is the point."""
    send_report_email("h", 25, "s@x.com",
                      ["a@x.com", "b@x.com", "c@x.com"], "S", "B")
    assert len(sent) == 1
    assert sent[0]["to"] == ["a@x.com", "b@x.com", "c@x.com"]
