import base64
import email

from jarvis import mcp_google as g


def b64(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def test_extract_body_prefers_plain():
    payload = {"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/html", "body": {"data": b64("<p>HTML</p>")}},
        {"mimeType": "text/plain", "body": {"data": b64("Hallo Welt")}},
    ]}
    assert g.extract_body(payload) == "Hallo Welt"


def test_extract_body_html_fallback():
    payload = {"mimeType": "text/html", "body": {"data": b64("<style>x{}</style><p>Hi&amp;du</p><br>Zeile")}}
    assert g.extract_body(payload) .split() == ["Hi&du", "Zeile"]


def test_summarize_message():
    msg = {"id": "1", "threadId": "t", "labelIds": ["UNREAD", "INBOX"], "snippet": "a &amp; b",
           "payload": {"headers": [{"name": "Subject", "value": "Test"}, {"name": "From", "value": "x@y.de"}]}}
    s = g.summarize_message(msg)
    assert s["subject"] == "Test" and s["unread"] and s["snippet"] == "a & b"


def test_build_mime_reply_headers():
    raw = g.build_mime("a@b.de", "Re: Hi", "Text", in_reply_to="<m1>", references="<m0>")
    m = email.message_from_bytes(base64.urlsafe_b64decode(raw))
    assert m["To"] == "a@b.de" and m["In-Reply-To"] == "<m1>" and m["References"] == "<m0> <m1>"


def test_event_time_local_tz():
    assert g.event_time("2026-10-02", True) == {"date": "2026-10-02"}
    t = g.event_time("2026-10-02T14:00", False)
    assert t["dateTime"].startswith("2026-10-02T14:00:00+02:00")


def test_send_tool_disabled_by_default():
    assert not hasattr(g, "gmail_send")
