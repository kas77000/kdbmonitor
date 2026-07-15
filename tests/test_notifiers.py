# tests/test_notifiers.py
from kdbmonitor.core.models import Channels
from kdbmonitor.core.notifiers import dispatch, InAppSink


def test_dispatch_selects_enabled_channels():
    sink = InAppSink()
    sent_webhooks = []
    sent_emails = []

    channels = Channels(in_app=True, sound=True, email_to=["me@x.com"],
                        webhook_urls=["http://hook"])

    dispatch(
        channels, message="AAPL bid>100",
        in_app_sink=sink,
        email_fn=lambda to, msg: sent_emails.append((to, msg)),
        webhook_fn=lambda url, msg: sent_webhooks.append((url, msg)),
    )

    assert sink.messages == ["AAPL bid>100"]
    assert sent_emails == [(["me@x.com"], "AAPL bid>100")]
    assert sent_webhooks == [("http://hook", "AAPL bid>100")]


def test_dispatch_skips_disabled_channels():
    sink = InAppSink()
    sent = []
    channels = Channels(in_app=False, sound=False, email_to=[], webhook_urls=[])
    dispatch(channels, "m", in_app_sink=sink,
             email_fn=lambda to, msg: sent.append("email"),
             webhook_fn=lambda url, msg: sent.append("hook"))
    assert sink.messages == [] and sent == []
