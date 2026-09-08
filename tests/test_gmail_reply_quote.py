"""Reply quoting: a reply must carry the thread's history, like Gmail's web UI.

The regression these guard against: `thread_id` attaches a message to a thread
but Gmail does not quote anything for you, so a reply built from `body` alone
reaches the recipient with every earlier message gone.
"""

from __future__ import annotations

import base64
import email

import pytest

from google_workspace_mcp import server


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


PREV_TEXT = "Hi,\n\nThanks for your reply.\n\n> an older quoted line"
PREV_HTML = '<div dir="ltr">Hi,<br><br>Thanks for your reply.</div>'

THREAD = {
    "messages": [
        {"payload": {"headers": [{"name": "Message-ID", "value": "<first@mail>"}]}},
        {
            "payload": {
                "mimeType": "multipart/alternative",
                "headers": [
                    {"name": "From", "value": "Ali Rajool <ali@example.com>"},
                    {"name": "Date", "value": "Mon, 24 Aug 2026 12:24:33 -0700"},
                    {"name": "Message-ID", "value": "<newest@mail>"},
                    {"name": "References", "value": "<first@mail>"},
                    {"name": "Subject", "value": "Re: hearing test"},
                ],
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64(PREV_TEXT)}},
                    {"mimeType": "text/html", "body": {"data": _b64(PREV_HTML)}},
                ],
            }
        },
    ]
}


class _Execute:
    def __init__(self, result):
        self._result = result

    def execute(self):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _Threads:
    def __init__(self, result):
        self._result = result

    def get(self, **_kwargs):
        return _Execute(self._result)


class _Users:
    def __init__(self, result):
        self._result = result

    def threads(self):
        return _Threads(self._result)


class _Service:
    def __init__(self, result):
        self._result = result

    def users(self):
        return _Users(self._result)


@pytest.fixture
def thread(monkeypatch):
    def _install(result):
        monkeypatch.setattr(server.auth, "gmail", lambda _account: _Service(result))

    return _install


def test_quote_carries_newest_message_in_both_flavours(thread):
    thread(THREAD)
    quote = server._thread_quote("personal", "t1")

    assert quote is not None
    # Gmail's attribution line, narrow no-break space before AM/PM included.
    assert quote["text"].startswith(
        "On Mon, Aug 24, 2026 at 12:24 PM Ali Rajool <ali@example.com> wrote:"
    )
    assert "> Thanks for your reply." in quote["text"]
    assert '<blockquote class="gmail_quote"' in quote["html"]
    assert PREV_HTML in quote["html"]
    assert 'class="gmail_attr"' in quote["html"]


def test_threading_headers_come_from_the_newest_message(thread):
    thread(THREAD)
    quote = server._thread_quote("personal", "t1")

    assert quote["message_id"] == "<newest@mail>"
    # References accumulates the chain, ending with the message replied to.
    assert quote["references"] == "<first@mail> <newest@mail>"


def test_already_quoted_lines_deepen_not_flatten():
    assert server._quote_prefix("a\n> b") == "> a\n>> b"


def test_unreadable_thread_degrades_to_no_quote(thread):
    from googleapiclient.errors import HttpError

    not_found = type("R", (), {"status": 404, "reason": "Not Found"})()
    thread(HttpError(not_found, b""))
    assert server._thread_quote("personal", "gone") is None

    thread({"messages": []})
    assert server._thread_quote("personal", "empty") is None


def test_plain_reply_keeps_history_in_plain_and_html_parts(thread):
    thread(THREAD)
    quote = server._thread_quote("personal", "t1")

    raw = server._build_mime(
        sender="Me <me@example.com>",
        to=["them@example.com"],
        subject="Re: hearing test",
        body="Following up on this.",
        in_reply_to=quote["message_id"],
        references=quote["references"],
        quote=quote,
    )
    msg = email.message_from_bytes(base64.urlsafe_b64decode(raw + "==="))
    parts = {
        p.get_content_type(): p.get_payload(decode=True).decode()
        for p in msg.walk()
        if p.get_content_type() in ("text/plain", "text/html")
    }

    assert msg["In-Reply-To"] == "<newest@mail>"
    assert msg["References"] == "<first@mail> <newest@mail>"

    # New text first, history under it — in BOTH alternatives.
    for body in parts.values():
        assert body.index("Following up on this.") < body.index("Thanks for your reply.")
    assert "> Thanks for your reply." in parts["text/plain"]
    assert '<blockquote class="gmail_quote"' in parts["text/html"]


def test_html_reply_keeps_history(thread):
    thread(THREAD)
    quote = server._thread_quote("personal", "t1")

    raw = server._build_mime(
        sender="Me <me@example.com>",
        to=["them@example.com"],
        subject="Re: hearing test",
        body="<div>Following up.</div>",
        html=True,
        quote=quote,
    )
    body = email.message_from_bytes(
        base64.urlsafe_b64decode(raw + "===")
    ).get_payload(decode=True).decode()

    assert body.index("Following up.") < body.index("Thanks for your reply.")
    assert '<blockquote class="gmail_quote"' in body


def test_no_quote_means_body_is_untouched():
    raw = server._build_mime(
        sender="Me <me@example.com>",
        to=["them@example.com"],
        subject="Hello",
        body="Just this.",
    )
    msg = email.message_from_bytes(base64.urlsafe_b64decode(raw + "==="))
    plain = next(
        p for p in msg.walk() if p.get_content_type() == "text/plain"
    ).get_payload(decode=True).decode()

    assert plain.rstrip("\n") == "Just this."
    assert "wrote:" not in plain
