"""Attachments on send / draft create / draft update.

They are added after the body, so a plain-text message keeps its
multipart/alternative (text + Gmail-style html) inside multipart/mixed, and a
reply keeps the quoted history that test_gmail_reply_quote.py covers.
"""

from __future__ import annotations

import base64
import email

import pytest

from google_workspace_mcp import server

SENDER = "Me <me@example.com>"
TO = ["them@example.com"]


def _parse(raw: str) -> email.message.Message:
    return email.message_from_bytes(base64.urlsafe_b64decode(raw + "==="))


def test_plain_body_keeps_alternative_shape_under_mixed(tmp_path):
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4 not really")

    msg = _parse(
        server._build_mime(
            sender=SENDER,
            to=TO,
            subject="Report",
            body="Hi,\n\n- one\n- two",
            attachments=[str(pdf)],
        )
    )

    assert msg.get_content_type() == "multipart/mixed"
    alternative, attachment = msg.get_payload()
    assert alternative.get_content_type() == "multipart/alternative"
    plain, html = alternative.get_payload()
    assert plain.get_content_type() == "text/plain"
    assert html.get_content_type() == "text/html"
    assert "<ul" in html.get_payload(decode=True).decode()  # list rendering survives
    assert attachment.get_content_type() == "application/pdf"
    assert attachment.get_filename() == "report.pdf"
    assert attachment.get_payload(decode=True) == pdf.read_bytes()


def test_html_body_with_two_attachments(tmp_path):
    csv = tmp_path / "data.csv"
    csv.write_text("a,b\n1,2\n")
    blob = tmp_path / "payload.unknownext"
    blob.write_bytes(b"\x00\x01")

    msg = _parse(
        server._build_mime(
            sender=SENDER,
            to=TO,
            subject="Files",
            body="<p>See attached.</p>",
            html=True,
            attachments=[str(csv), str(blob)],
        )
    )

    assert msg.get_content_type() == "multipart/mixed"
    body, first, second = msg.get_payload()
    assert body.get_content_type() == "text/html"
    assert (first.get_content_type(), first.get_filename()) == ("text/csv", "data.csv")
    assert second.get_content_type() == "application/octet-stream"
    assert second.get_filename() == "payload.unknownext"


def test_reply_quote_survives_attachment(tmp_path):
    note = tmp_path / "note.txt"
    note.write_text("x")
    quote = {
        "text": "On Mon, Aug 24, 2026 Someone wrote:\n> older text",
        "html": '<blockquote class="gmail_quote">older text</blockquote>',
    }

    msg = _parse(
        server._build_mime(
            sender=SENDER,
            to=TO,
            subject="Re: x",
            body="New text.",
            quote=quote,
            attachments=[str(note)],
        )
    )

    alternative = msg.get_payload()[0]
    plain, html = (p.get_payload(decode=True).decode() for p in alternative.get_payload())
    assert plain.index("New text.") < plain.index("> older text")
    assert 'class="gmail_quote"' in html


def test_missing_attachment_raises_instead_of_sending_without_it(tmp_path):
    with pytest.raises(FileNotFoundError):
        server._build_mime(
            sender=SENDER,
            to=TO,
            subject="x",
            body="x",
            attachments=[str(tmp_path / "missing.pdf")],
        )


def test_no_attachments_leaves_shape_unchanged():
    msg = _parse(server._build_mime(sender=SENDER, to=TO, subject="x", body="Just this."))
    assert msg.get_content_type() == "multipart/alternative"
