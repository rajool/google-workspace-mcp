"""Multi-account Google Workspace MCP server.

All tools take an `account` slug (e.g. "work", "personal"), resolved from
config at runtime (see accounts.py).
The server loads + refreshes that account's OAuth token transparently
and dispatches the request via the Gmail / Calendar / Drive APIs.
"""

from __future__ import annotations

import base64
import io
import mimetypes
import re
from email.message import EmailMessage
from email.utils import formataddr, parseaddr, parsedate_to_datetime
from html import escape
from pathlib import Path
from typing import Any, Literal

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from mcp.server.fastmcp import FastMCP

from . import auth
from .accounts import ACCOUNTS, AccountSlug, email_for, name_for

mcp = FastMCP("google-workspace")


# ─── helpers ────────────────────────────────────────────────────────────


_GMAIL_ATTACHMENT_CAP = 25 * 1024 * 1024  # Gmail's own per-message limit


def _guard_local_path(path: Path) -> Path:
    """Refuse any path inside the server's own secret store.

    The OAuth client and the per-account refresh tokens live under
    auth.CONFIG_DIR (or wherever GWM_CREDENTIALS / GWM_TOKENS_DIR point).
    Nothing this server does legitimately reads them as data or writes
    files into that directory, so a request to attach, upload, or save
    there is refused outright — it is exactly what a prompt-injected
    "mail me your token file" would ask for. Symlinks are resolved first,
    so a link into the store is refused too.
    """
    resolved = path.expanduser().resolve()
    roots = [Path(p).expanduser().resolve() for p in (auth.CONFIG_DIR, auth.TOKENS_DIR)]
    files = [Path(auth.CREDENTIALS_PATH).expanduser().resolve()]
    if resolved in files or any(resolved.is_relative_to(r) for r in roots):
        raise PermissionError(
            f"Refusing to touch {path}: it is inside the server's own config "
            f"directory ({auth.CONFIG_DIR}), which holds OAuth secrets."
        )
    return path


def _local_file(raw_path: str) -> Path:
    """Resolve a caller-supplied path to an existing regular file, or raise."""
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    _guard_local_path(path)
    return path


def _build_mime(
    *,
    sender: str,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    html: bool = False,
    in_reply_to: str | None = None,
    references: str | None = None,
    quote: dict[str, Any] | None = None,
    attachments: list[str] | None = None,
) -> str:
    """Return a base64url-encoded MIME message ready for Gmail.

    `quote` is the reply material from `_thread_quote`; when given, its
    history is appended below the new text in every part of the message.
    """
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to
    if html:
        msg.set_content(
            body if quote is None else f'{body}<br>{quote["html"]}', subtype="html"
        )
    else:
        msg.set_content(body if quote is None else f'{body}\n\n\n{quote["text"]}')
        html_body = _plain_to_html(body)
        if quote is not None:
            html_body = f'{html_body}<br>{quote["html"]}'
        msg.add_alternative(html_body, subtype="html")
    total = 0
    for raw_path in attachments or []:
        path = _local_file(raw_path)
        total += path.stat().st_size
        if total > _GMAIL_ATTACHMENT_CAP:
            raise ValueError(
                f"Attachments total {total / 1_048_576:.1f} MB; Gmail caps a "
                "message at 25 MB. Upload the file to Drive and share a link "
                "instead (drive_file_upload + drive_file_link_access)."
            )
        ctype, encoding = mimetypes.guess_type(path.name)
        # No type, a compressed type (.gz), or a container type (message/*,
        # multipart/*) all go as opaque bytes: RFC 2046 forbids base64 on
        # message/rfc822, so a base64'd .eml is unreadable to the recipient.
        container = ctype is not None and ctype.split("/")[0] in ("message", "multipart")
        if ctype is None or encoding is not None or container:
            ctype = "application/octet-stream"
        maintype, _, subtype = ctype.partition("/")
        msg.add_attachment(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


_BULLET_RE = re.compile(r"^[-*\u2022]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^([0-9\u06f0-\u06f9\u0660-\u0669]{1,3})[.)]\s+(.*)$")
_DIGITS = str.maketrans("\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9"
                        "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669",
                        "01234567890123456789")


def _plain_to_html(body: str) -> str:
    """Render a plain-text body the way Gmail's own composer does.

    Regular lines become one <div> per line (blank lines <div><br></div>);
    runs of "- " / "* " / "\u2022 " lines become a real <ul>, and runs of
    "1. " / "1) " lines (ASCII, Persian, or Arabic-Indic digits) a real <ol>
    with the list marker Gmail itself would draw. dir="auto" throughout so
    LTR and RTL lines each lay out correctly, markers included.

    Attached as the text/html alternative of every plain-text message so a
    draft opened in the Gmail web UI keeps its rich-text shape. Without it
    Gmail treats the draft as plain-text-only and, on Send, rewrites the body
    with hard line breaks at ~70 columns — visibly broken paragraphs for the
    recipient. Gmail-composed mail never shows this because it is always
    multipart/alternative and clients display the HTML part.
    """
    out: list[str] = []
    lines = body.split("\n")
    i = 0
    while i < len(lines):
        if _BULLET_RE.match(lines[i]):
            items = []
            while i < len(lines) and (m := _BULLET_RE.match(lines[i])):
                items.append(f'<li dir="auto">{escape(m.group(1))}</li>')
                i += 1
            out.append(f'<ul dir="auto">{"".join(items)}</ul>')
        elif m := _NUMBERED_RE.match(lines[i]):
            start = int(m.group(1).translate(_DIGITS))
            items = []
            while i < len(lines) and (m := _NUMBERED_RE.match(lines[i])):
                items.append(f'<li dir="auto">{escape(m.group(2))}</li>')
                i += 1
            attr = f' start="{start}"' if start != 1 else ""
            out.append(f'<ol dir="auto"{attr}>{"".join(items)}</ol>')
        else:
            line = lines[i]
            out.append(
                f'<div dir="auto">{escape(line)}</div>' if line.strip() else '<div dir="auto"><br></div>'
            )
            i += 1
    return "".join(out)


_QUOTE_BQ_STYLE = (
    "margin:0px 0px 0px 0.8ex;border-left:1px solid rgb(204,204,204);padding-left:1ex"
)


def _extract_html_body(part: dict) -> str | None:
    """Return the first text/html body in a Gmail payload tree, decoded."""
    if part.get("mimeType") == "text/html":
        data = (part.get("body") or {}).get("data")
        if data:
            return base64.urlsafe_b64decode(data + "===").decode(
                "utf-8", errors="replace"
            )
    for sub in part.get("parts", []) or []:
        html_body = _extract_html_body(sub)
        if html_body:
            return html_body
    return None


def _quote_when(date_header: str) -> str:
    """Format a Date header the way Gmail writes it in an attribution line.

    "Mon, Aug 24, 2026 at 12:24 PM", with the narrow no-break space (U+202F)
    Gmail puts before AM/PM. Rendered in the message's own UTC offset, which
    is what the header carries; falls back to the raw header if unparseable.
    """
    try:
        dt = parsedate_to_datetime(date_header)
    except (TypeError, ValueError):
        return date_header
    hour = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt:%a}, {dt:%b} {dt.day}, {dt.year} at {hour}:{dt.minute:02d}\u202f{ampm}"


def _quote_prefix(text: str) -> str:
    """Prefix each line with Gmail's "> ", deepening already-quoted lines."""
    return "\n".join(
        (">" + line) if line.startswith(">") else f"> {line}"
        for line in text.split("\n")
    )


def _thread_quote(account: str, thread_id: str) -> dict[str, Any] | None:
    """Build reply material from the newest sent or received message of a thread.

    Returns the RFC822 Message-Id and References chain needed for correct
    threading, plus that message quoted in both flavours: "> "-prefixed text
    and a Gmail-style nested <blockquote class="gmail_quote">.

    Quoting only the newest message is enough to reproduce the whole thread:
    it already carries every earlier message nested inside it, exactly as
    Gmail's web Reply builds it. Drafts are skipped: they are not part of the
    conversation, and a reply draft is itself the newest message of its thread.
    Returns None when the thread cannot be read (bad id, deleted, missing
    scope) or holds nothing but drafts, so a send degrades to an unquoted reply
    instead of failing outright.
    """
    try:
        thread = (
            auth.gmail(account)
            .users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
    except HttpError:
        return None
    # Quoting a draft sent the recipient an unsent text and pointed In-Reply-To
    # at a message they never received: gmail_draft_update with thread_id
    # re-quoted the draft's own earlier version under the new body.
    messages = [
        m
        for m in thread.get("messages") or []
        if "DRAFT" not in (m.get("labelIds") or [])
    ]
    if not messages:
        return None
    payload = messages[-1].get("payload") or {}
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    name, addr = parseaddr(headers.get("from", ""))
    when = _quote_when(headers.get("date", ""))
    who = f"{name} " if name else ""
    prev_text = _extract_plain_body(payload) or ""
    prev_html = _extract_html_body(payload) or _plain_to_html(prev_text)
    message_id = headers.get("message-id")
    references = " ".join(
        ref for ref in (headers.get("references"), message_id) if ref
    )
    attr_text = f"On {when} {who}<{addr}> wrote:"
    attr_html = (
        f'<div dir="ltr" class="gmail_attr">On {escape(when)} {escape(who)}'
        f'&lt;<a href="mailto:{escape(addr, quote=True)}">{escape(addr)}</a>&gt;'
        " wrote:<br></div>"
    )
    return {
        "message_id": message_id,
        "references": references or None,
        "text": f"{attr_text}\n{_quote_prefix(prev_text)}" if prev_text else attr_text,
        "html": (
            '<div class="gmail_quote gmail_quote_container">'
            f'{attr_html}<blockquote class="gmail_quote" style="{_QUOTE_BQ_STYLE}">'
            f"{prev_html}</blockquote></div>"
        ),
    }


def _from_header(slug: str) -> str:
    """Build an RFC-5322 From header, e.g. 'Your Name <you@example.com>'.

    Falls back to the bare address if the account has no display name.
    """
    name = name_for(slug)
    email = email_for(slug)
    return formataddr((name, email)) if name else email


def _msg_summary(m: dict) -> dict:
    """Trim a Gmail message to what callers actually need."""
    headers = {
        h["name"].lower(): h["value"]
        for h in (m.get("payload") or {}).get("headers", [])
    }
    return {
        "id": m.get("id"),
        "threadId": m.get("threadId"),
        "labelIds": m.get("labelIds", []),
        "snippet": m.get("snippet"),
        "from": headers.get("from"),
        "to": headers.get("to"),
        "subject": headers.get("subject"),
        "date": headers.get("date"),
    }


# ─── meta ───────────────────────────────────────────────────────────────


@mcp.tool()
def accounts_list() -> dict:
    """List the configured Google accounts and whether each token still works.

    `authorized` is proven by refreshing each token against Google (null
    if Google could not be reached), not by the token file existing; a
    dead account carries a `status` and a `detail` naming the fix.
    """
    out: dict[str, dict] = {}
    for slug, info in ACCOUNTS.items():
        out[slug] = {
            "email": info["email"],
            "name": info.get("name", ""),
            **auth.token_status(slug),
        }
    if not out:
        return {
            "accounts": {},
            "hint": (
                "No accounts configured. Set GWM_ACCOUNTS (a JSON map or a "
                "comma-list of slugs from your registry) or create "
                f"{auth.CONFIG_DIR / 'accounts.json'}, then run "
                "google-workspace-authorize <slug> <email>. See the README."
            ),
        }
    return {"accounts": out}


# ─── Gmail ──────────────────────────────────────────────────────────────


@mcp.tool()
def gmail_send(
    account: AccountSlug,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    html: bool = False,
    thread_id: str | None = None,
    in_reply_to_message_id: str | None = None,
    quote_history: bool = True,
    attachments: list[str] | None = None,
) -> dict:
    """Send an email immediately from the given account.

    Replies: pass thread_id and nothing else. The server reads the thread and
    quotes its history below your text exactly as Gmail's web Reply does, and
    derives the In-Reply-To / References headers itself. So write `body` as
    ONLY the new message — never paste earlier messages into it by hand, or
    the recipient gets the history twice. (`in_reply_to_message_id` overrides
    the derived header when you already hold the RFC822 Message-Id;
    `quote_history=false` sends into the thread with no quote.)

    Body format: write `body` as plain text — blank-line paragraphs,
    "- " bullets, "1." / "1)" numbered lines (ASCII or Persian digits).
    It goes out as multipart/alternative with a Gmail-composer-style HTML
    part, so lists arrive as Gmail's real bullets/numbering and the draft
    can be opened and sent from the Gmail web UI safely. Never hard-wrap
    lines yourself. Set html=true only for a body that is already HTML.

    `attachments` are paths on the machine running this server. Each is
    attached under its own file name, with the MIME type guessed from that
    name and `application/octet-stream` as the fallback.
    """
    ctx = _thread_quote(account, thread_id) if thread_id else None
    raw = _build_mime(
        sender=_from_header(account),
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
        html=html,
        in_reply_to=in_reply_to_message_id or (ctx or {}).get("message_id"),
        references=(ctx or {}).get("references"),
        quote=ctx if quote_history else None,
        attachments=attachments,
    )
    payload: dict[str, Any] = {"raw": raw}
    if thread_id:
        payload["threadId"] = thread_id
    sent = (
        auth.gmail(account)
        .users()
        .messages()
        .send(userId="me", body=payload)
        .execute()
    )
    return _msg_summary(sent)


@mcp.tool()
def gmail_draft_create(
    account: AccountSlug,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    html: bool = False,
    thread_id: str | None = None,
    quote_history: bool = True,
    attachments: list[str] | None = None,
) -> dict:
    """Create a Gmail draft. Returns {id, message: {...}}.

    Replies: pass thread_id and nothing else. The server reads the thread and
    quotes its history below your text exactly as Gmail's web Reply does, and
    derives the In-Reply-To / References headers itself. So write `body` as
    ONLY the new message — never paste earlier messages into it by hand, or
    the recipient gets the history twice. (`in_reply_to_message_id` overrides
    the derived header when you already hold the RFC822 Message-Id;
    `quote_history=false` sends into the thread with no quote.)

    Body format: write `body` as plain text — blank-line paragraphs,
    "- " bullets, "1." / "1)" numbered lines (ASCII or Persian digits).
    It goes out as multipart/alternative with a Gmail-composer-style HTML
    part, so lists arrive as Gmail's real bullets/numbering and the draft
    can be opened and sent from the Gmail web UI safely. Never hard-wrap
    lines yourself. Set html=true only for a body that is already HTML.

    `attachments` are paths on the machine running this server. Each is
    attached under its own file name, with the MIME type guessed from that
    name and `application/octet-stream` as the fallback.
    """
    ctx = _thread_quote(account, thread_id) if thread_id else None
    raw = _build_mime(
        sender=_from_header(account),
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
        html=html,
        in_reply_to=(ctx or {}).get("message_id"),
        references=(ctx or {}).get("references"),
        quote=ctx if quote_history else None,
        attachments=attachments,
    )
    msg: dict[str, Any] = {"raw": raw}
    if thread_id:
        msg["threadId"] = thread_id
    draft = (
        auth.gmail(account)
        .users()
        .drafts()
        .create(userId="me", body={"message": msg})
        .execute()
    )
    return {"id": draft.get("id"), "message": _msg_summary(draft.get("message", {}))}


@mcp.tool()
def gmail_draft_update(
    account: AccountSlug,
    draft_id: str,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    html: bool = False,
    thread_id: str | None = None,
    quote_history: bool = True,
    attachments: list[str] | None = None,
) -> dict:
    """Overwrite an existing draft's contents.

    Pass thread_id when the draft is a reply — it keeps the draft attached to
    that thread (an update without it detaches the draft) and re-quotes the
    thread's history, so `body` stays just the new text.

    Body format: write `body` as plain text — blank-line paragraphs,
    "- " bullets, "1." / "1)" numbered lines (ASCII or Persian digits).
    It goes out as multipart/alternative with a Gmail-composer-style HTML
    part, so lists arrive as Gmail's real bullets/numbering and the draft
    can be opened and sent from the Gmail web UI safely. Never hard-wrap
    lines yourself. Set html=true only for a body that is already HTML.

    `attachments` are paths on the machine running this server. Each is
    attached under its own file name, with the MIME type guessed from that
    name and `application/octet-stream` as the fallback.
    """
    ctx = _thread_quote(account, thread_id) if thread_id else None
    raw = _build_mime(
        sender=_from_header(account),
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
        html=html,
        in_reply_to=(ctx or {}).get("message_id"),
        references=(ctx or {}).get("references"),
        quote=ctx if quote_history else None,
        attachments=attachments,
    )
    msg: dict[str, Any] = {"raw": raw}
    if thread_id:
        msg["threadId"] = thread_id
    draft = (
        auth.gmail(account)
        .users()
        .drafts()
        .update(userId="me", id=draft_id, body={"message": msg})
        .execute()
    )
    return {"id": draft.get("id"), "message": _msg_summary(draft.get("message", {}))}


@mcp.tool()
def gmail_draft_send(account: AccountSlug, draft_id: str) -> dict:
    """Send an existing draft."""
    sent = (
        auth.gmail(account)
        .users()
        .drafts()
        .send(userId="me", body={"id": draft_id})
        .execute()
    )
    return _msg_summary(sent)


@mcp.tool()
def gmail_draft_delete(account: AccountSlug, draft_id: str) -> dict:
    """Permanently delete a draft (the thing the default connector can't do)."""
    auth.gmail(account).users().drafts().delete(userId="me", id=draft_id).execute()
    return {"deleted": draft_id}


@mcp.tool()
def gmail_drafts_list(
    account: AccountSlug,
    max_results: int = 20,
    query: str | None = None,
) -> dict:
    """List drafts. `query` uses standard Gmail search syntax."""
    resp = (
        auth.gmail(account)
        .users()
        .drafts()
        .list(userId="me", maxResults=max_results, q=query)
        .execute()
    )
    drafts = resp.get("drafts", []) or []
    return {"drafts": drafts, "next_page_token": resp.get("nextPageToken")}


@mcp.tool()
def gmail_search(
    account: AccountSlug,
    query: str,
    max_results: int = 20,
    label_ids: list[str] | None = None,
    include_spam_trash: bool = False,
) -> dict:
    """Search messages with Gmail's query syntax (e.g. 'from:foo subject:bar')."""
    svc = auth.gmail(account).users().messages()
    resp = svc.list(
        userId="me",
        q=query,
        maxResults=max_results,
        labelIds=label_ids,
        includeSpamTrash=include_spam_trash,
    ).execute()
    ids = [m["id"] for m in (resp.get("messages") or [])]
    # Bulk-fetch metadata so the caller can see who/what/when without
    # a second round trip per message.
    out = []
    for mid in ids:
        m = svc.get(userId="me", id=mid, format="metadata").execute()
        out.append(_msg_summary(m))
    return {"messages": out, "next_page_token": resp.get("nextPageToken")}


@mcp.tool()
def gmail_message_get(
    account: AccountSlug,
    message_id: str,
    format: Literal["full", "metadata", "minimal", "raw"] = "full",
) -> dict:
    """Fetch one message. `format=full` includes the body."""
    m = (
        auth.gmail(account)
        .users()
        .messages()
        .get(userId="me", id=message_id, format=format)
        .execute()
    )
    if format == "full":
        # Decode the plain-text body if present, for convenience.
        body_text = _extract_plain_body(m.get("payload") or {})
        return {**_msg_summary(m), "body_text": body_text, "raw": m}
    return {**_msg_summary(m), "raw": m}


def _extract_plain_body(part: dict) -> str | None:
    if part.get("mimeType") == "text/plain":
        data = (part.get("body") or {}).get("data")
        if data:
            return base64.urlsafe_b64decode(data + "===").decode(
                "utf-8", errors="replace"
            )
    for sub in part.get("parts", []) or []:
        text = _extract_plain_body(sub)
        if text:
            return text
    return None


@mcp.tool()
def gmail_message_trash(account: AccountSlug, message_id: str) -> dict:
    """Move a message to Trash (reversible for 30 days)."""
    m = (
        auth.gmail(account)
        .users()
        .messages()
        .trash(userId="me", id=message_id)
        .execute()
    )
    return _msg_summary(m)


@mcp.tool()
def gmail_message_modify(
    account: AccountSlug,
    message_id: str,
    add_label_ids: list[str] | None = None,
    remove_label_ids: list[str] | None = None,
) -> dict:
    """Add/remove labels on a message (e.g. mark read by removing UNREAD)."""
    body = {
        "addLabelIds": add_label_ids or [],
        "removeLabelIds": remove_label_ids or [],
    }
    m = (
        auth.gmail(account)
        .users()
        .messages()
        .modify(userId="me", id=message_id, body=body)
        .execute()
    )
    return _msg_summary(m)


@mcp.tool()
def gmail_labels_list(account: AccountSlug) -> dict:
    """List all labels for the account."""
    resp = auth.gmail(account).users().labels().list(userId="me").execute()
    return {"labels": resp.get("labels", [])}


@mcp.tool()
def gmail_label_create(
    account: AccountSlug,
    name: str,
    label_list_visibility: Literal[
        "labelShow", "labelShowIfUnread", "labelHide"
    ] = "labelShow",
    message_list_visibility: Literal["show", "hide"] = "show",
) -> dict:
    """Create a label. Nested labels use 'Parent/Child' names; create each
    parent level first. Idempotent: an existing label is returned as-is."""
    svc = auth.gmail(account).users().labels()
    body = {
        "name": name,
        "labelListVisibility": label_list_visibility,
        "messageListVisibility": message_list_visibility,
    }
    try:
        label = svc.create(userId="me", body=body).execute()
    except HttpError as e:
        if e.resp.status != 409:
            raise
        existing = svc.list(userId="me").execute().get("labels", [])
        label = next(lb for lb in existing if lb.get("name") == name)
        return {**label, "already_existed": True}
    return label


@mcp.tool()
def gmail_thread_get(account: AccountSlug, thread_id: str) -> dict:
    """Fetch a whole thread (all messages)."""
    t = (
        auth.gmail(account)
        .users()
        .threads()
        .get(userId="me", id=thread_id, format="full")
        .execute()
    )
    return {
        "id": t.get("id"),
        "messages": [_msg_summary(m) for m in t.get("messages", [])],
    }


@mcp.tool()
def gmail_attachment_download(
    account: AccountSlug,
    message_id: str,
    attachment_id: str,
    save_to: str,
) -> dict:
    """Download one attachment to a local path. Get `attachment_id` from
    the message payload parts (gmail_message_get with format=full)."""
    path = _guard_local_path(Path(save_to).expanduser())
    att = (
        auth.gmail(account)
        .users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )
    data = base64.urlsafe_b64decode(att["data"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"saved_to": str(path), "size_bytes": len(data)}


# ─── Calendar ───────────────────────────────────────────────────────────


@mcp.tool()
def calendar_list(account: AccountSlug) -> dict:
    """List all calendars the account can access."""
    resp = auth.calendar(account).calendarList().list().execute()
    cals = [
        {
            "id": c.get("id"),
            "summary": c.get("summary"),
            "primary": c.get("primary", False),
            "accessRole": c.get("accessRole"),
            "timeZone": c.get("timeZone"),
        }
        for c in resp.get("items", [])
    ]
    return {"calendars": cals}


@mcp.tool()
def calendar_events_list(
    account: AccountSlug,
    calendar_id: str = "primary",
    time_min: str | None = None,
    time_max: str | None = None,
    query: str | None = None,
    max_results: int = 25,
    single_events: bool = True,
) -> dict:
    """List events. `time_min`/`time_max` are RFC3339 (e.g. '2026-06-05T00:00:00-07:00')."""
    resp = (
        auth.calendar(account)
        .events()
        .list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            q=query,
            maxResults=max_results,
            singleEvents=single_events,
            orderBy="startTime" if single_events else None,
        )
        .execute()
    )
    return {
        "events": resp.get("items", []),
        "next_page_token": resp.get("nextPageToken"),
    }


@mcp.tool()
def calendar_event_get(
    account: AccountSlug,
    event_id: str,
    calendar_id: str = "primary",
) -> dict:
    """Fetch one event."""
    return (
        auth.calendar(account)
        .events()
        .get(calendarId=calendar_id, eventId=event_id)
        .execute()
    )


@mcp.tool()
def calendar_event_create(
    account: AccountSlug,
    summary: str,
    start: str,
    end: str,
    calendar_id: str = "primary",
    description: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,
    timezone: str | None = None,
    send_updates: Literal["all", "externalOnly", "none"] = "none",
    add_google_meet: bool = False,
) -> dict:
    """Create an event. `start`/`end` are RFC3339 datetimes; date-only ('2026-06-10') makes an all-day event."""
    def time_obj(s: str) -> dict:
        if "T" in s:
            obj = {"dateTime": s}
            if timezone:
                obj["timeZone"] = timezone
            return obj
        return {"date": s}

    body: dict[str, Any] = {
        "summary": summary,
        "start": time_obj(start),
        "end": time_obj(end),
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees]

    kwargs: dict[str, Any] = {
        "calendarId": calendar_id,
        "body": body,
        "sendUpdates": send_updates,
    }
    if add_google_meet:
        import uuid

        body["conferenceData"] = {
            "createRequest": {
                "requestId": str(uuid.uuid4()),
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }
        kwargs["conferenceDataVersion"] = 1

    return auth.calendar(account).events().insert(**kwargs).execute()


@mcp.tool()
def calendar_event_update(
    account: AccountSlug,
    event_id: str,
    calendar_id: str = "primary",
    summary: str | None = None,
    description: str | None = None,
    location: str | None = None,
    start: str | None = None,
    end: str | None = None,
    timezone: str | None = None,
    attendees: list[str] | None = None,
    send_updates: Literal["all", "externalOnly", "none"] = "none",
) -> dict:
    """Patch an existing event — only the fields you pass are changed."""
    patch: dict[str, Any] = {}
    if summary is not None:
        patch["summary"] = summary
    if description is not None:
        patch["description"] = description
    if location is not None:
        patch["location"] = location
    if start is not None:
        patch["start"] = (
            {"dateTime": start, **({"timeZone": timezone} if timezone else {})}
            if "T" in start
            else {"date": start}
        )
    if end is not None:
        patch["end"] = (
            {"dateTime": end, **({"timeZone": timezone} if timezone else {})}
            if "T" in end
            else {"date": end}
        )
    if attendees is not None:
        patch["attendees"] = [{"email": a} for a in attendees]
    return (
        auth.calendar(account)
        .events()
        .patch(
            calendarId=calendar_id,
            eventId=event_id,
            body=patch,
            sendUpdates=send_updates,
        )
        .execute()
    )


@mcp.tool()
def calendar_event_delete(
    account: AccountSlug,
    event_id: str,
    calendar_id: str = "primary",
    send_updates: Literal["all", "externalOnly", "none"] = "none",
) -> dict:
    """Delete an event."""
    auth.calendar(account).events().delete(
        calendarId=calendar_id, eventId=event_id, sendUpdates=send_updates
    ).execute()
    return {"deleted": event_id}


@mcp.tool()
def calendar_create(
    account: AccountSlug,
    summary: str,
    description: str | None = None,
    timezone: str | None = None,
) -> dict:
    """Create a new secondary calendar owned by the account (a school, project, or family calendar).

    Returns the new calendar's id — pass it as `calendar_id` to the event tools and to `calendar_share`.
    """
    body: dict[str, Any] = {"summary": summary}
    if description:
        body["description"] = description
    if timezone:
        body["timeZone"] = timezone
    cal = auth.calendar(account).calendars().insert(body=body).execute()
    return {
        "id": cal.get("id"),
        "summary": cal.get("summary"),
        "description": cal.get("description"),
        "timeZone": cal.get("timeZone"),
    }


_SHARE_ROLES = ("freeBusyReader", "reader", "writer")


def _acl_rule_id(email: str) -> str:
    """Google names a per-user sharing rule `user:<email>`."""
    return f"user:{email.strip().lower()}"


@mcp.tool()
def calendar_acl_list(account: AccountSlug, calendar_id: str) -> dict:
    """Who can see a calendar — one row per sharing rule (a user, a group, a domain, or the public)."""
    resp = auth.calendar(account).acl().list(calendarId=calendar_id).execute()
    rules = [
        {
            "id": r.get("id"),
            "role": r.get("role"),
            "scope_type": r.get("scope", {}).get("type"),
            "scope_value": r.get("scope", {}).get("value"),
        }
        for r in resp.get("items", [])
    ]
    return {"calendar_id": calendar_id, "rules": rules}


@mcp.tool()
def calendar_share(
    account: AccountSlug,
    calendar_id: str,
    email: str,
    role: Literal["freeBusyReader", "reader", "writer"] = "reader",
    send_notifications: bool = True,
) -> dict:
    """Share a calendar with one person (or Google group) by email.

    Roles: `freeBusyReader` (busy/free only), `reader` (see all event details),
    `writer` (also add and edit events). `owner` is deliberately not offered —
    hand ownership over in the Calendar UI. Sharing an address that already has
    access updates its role. By default Google emails the person an invitation.
    """
    if role not in _SHARE_ROLES:
        raise ValueError(f"role must be one of {_SHARE_ROLES}, got {role!r}")
    rule = (
        auth.calendar(account)
        .acl()
        .insert(
            calendarId=calendar_id,
            body={"role": role, "scope": {"type": "user", "value": email.strip()}},
            sendNotifications=send_notifications,
        )
        .execute()
    )
    return {
        "calendar_id": calendar_id,
        "rule_id": rule.get("id"),
        "email": rule.get("scope", {}).get("value", email.strip()),
        "role": rule.get("role", role),
    }


@mcp.tool()
def calendar_unshare(account: AccountSlug, calendar_id: str, email: str) -> dict:
    """Remove one person's access to a calendar.

    Looks the address up in the calendar's sharing rules (case-insensitively) and
    deletes that rule; falls back to the conventional `user:<email>` rule id.
    """
    svc = auth.calendar(account)
    want = email.strip().lower()
    rules = svc.acl().list(calendarId=calendar_id).execute().get("items", [])
    match = next(
        (
            r
            for r in rules
            if r.get("scope", {}).get("type") == "user"
            and (r.get("scope", {}).get("value") or "").lower() == want
        ),
        None,
    )
    rule_id = match["id"] if match else _acl_rule_id(email)
    svc.acl().delete(calendarId=calendar_id, ruleId=rule_id).execute()
    return {"calendar_id": calendar_id, "removed": rule_id, "found": match is not None}


# ─── Drive ──────────────────────────────────────────────────────────────


_DRIVE_FIELDS = "id, name, mimeType, parents, webViewLink, webContentLink, modifiedTime, size, owners(emailAddress, displayName)"

# Office formats Drive can convert into its own editable types. Used when
# creating a native Doc/Sheet/Slides from a local file, and when revising one.
_GOOGLE_NATIVE = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "application/vnd.google-apps.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "application/vnd.google-apps.spreadsheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "application/vnd.google-apps.presentation",
}


@mcp.tool()
def drive_search(
    account: AccountSlug,
    query: str | None = None,
    max_results: int = 20,
    order_by: str | None = "modifiedTime desc",
    include_trashed: bool = False,
) -> dict:
    """List/search files. `query` uses Drive query language; see
    https://developers.google.com/drive/api/guides/search-files
    Examples:
      "name contains 'budget'"
      "mimeType='application/vnd.google-apps.folder'"
      "'<parent-id>' in parents"
    """
    q_parts = []
    if query:
        q_parts.append(f"({query})")
    if not include_trashed:
        q_parts.append("trashed = false")
    q = " and ".join(q_parts) if q_parts else None

    resp = (
        auth.drive(account)
        .files()
        .list(
            q=q,
            pageSize=max_results,
            orderBy=order_by,
            fields=f"nextPageToken, files({_DRIVE_FIELDS})",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        )
        .execute()
    )
    return {"files": resp.get("files", []), "next_page_token": resp.get("nextPageToken")}


@mcp.tool()
def drive_file_get(account: AccountSlug, file_id: str) -> dict:
    """Get a file's full metadata."""
    return (
        auth.drive(account)
        .files()
        .get(fileId=file_id, fields=_DRIVE_FIELDS, supportsAllDrives=True)
        .execute()
    )


@mcp.tool()
def drive_file_download(
    account: AccountSlug,
    file_id: str,
    save_to: str,
    export_mime_type: str | None = None,
) -> dict:
    """Download a file. For Google Docs/Sheets/Slides, pass `export_mime_type`
    (e.g. 'application/pdf', 'text/plain', 'text/csv')."""
    out_path = _guard_local_path(Path(save_to).expanduser())
    svc = auth.drive(account).files()
    if export_mime_type:
        req = svc.export_media(fileId=file_id, mimeType=export_mime_type)
    else:
        req = svc.get_media(fileId=file_id, supportsAllDrives=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with io.FileIO(out_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return {"saved_to": str(out_path), "bytes": out_path.stat().st_size}


@mcp.tool()
def drive_file_upload(
    account: AccountSlug,
    local_path: str,
    name: str | None = None,
    parent_folder_id: str | None = None,
    mime_type: str | None = None,
    convert_to_google_doc: bool = False,
) -> dict:
    """Upload a local file to Drive. `convert_to_google_doc=True` converts
    .docx/.xlsx/.pptx to native Google Docs/Sheets/Slides."""
    path = _local_file(local_path)

    metadata: dict[str, Any] = {"name": name or path.name}
    if parent_folder_id:
        metadata["parents"] = [parent_folder_id]

    if mime_type is None:
        mime_type, _ = mimetypes.guess_type(str(path))
        mime_type = mime_type or "application/octet-stream"

    if convert_to_google_doc:
        # Drive treats the destination mimeType in metadata as the
        # target format; the source mimeType comes from the media body.
        target = _GOOGLE_NATIVE.get(mime_type)
        if target:
            metadata["mimeType"] = target

    media = MediaFileUpload(str(path), mimetype=mime_type, resumable=True)
    file = (
        auth.drive(account)
        .files()
        .create(
            body=metadata,
            media_body=media,
            fields=_DRIVE_FIELDS,
            supportsAllDrives=True,
        )
        .execute()
    )
    return file


@mcp.tool()
def drive_file_update_content(
    account: AccountSlug,
    file_id: str,
    local_path: str,
    name: str | None = None,
    mime_type: str | None = None,
    convert_to_google_doc: bool = False,
    keep_revision_forever: bool = False,
) -> dict:
    """Replace an existing file's contents in place. The file keeps its ID,
    link and sharing, and the previous contents stay in Drive's revision
    history. Use this rather than `drive_file_upload` to revise something
    already in Drive: uploading again under the same name creates a second
    file, it does not version the first. `convert_to_google_doc=True` revises
    a native Doc/Sheet/Slides from a local .docx/.xlsx/.pptx. Google
    Docs/Sheets/Slides keep full version history; for binary files Drive
    drops old revisions after 30 days or 100 revisions unless
    `keep_revision_forever=True` (at most 200 pinned per file)."""
    path = _local_file(local_path)

    if mime_type is None:
        mime_type, _ = mimetypes.guess_type(str(path))
        mime_type = mime_type or "application/octet-stream"

    metadata: dict[str, Any] = {}
    if name:
        metadata["name"] = name
    if convert_to_google_doc:
        target = _GOOGLE_NATIVE.get(mime_type)
        if target:
            metadata["mimeType"] = target

    media = MediaFileUpload(str(path), mimetype=mime_type, resumable=True)
    file = (
        auth.drive(account)
        .files()
        .update(
            fileId=file_id,
            body=metadata,
            media_body=media,
            fields=_DRIVE_FIELDS,
            supportsAllDrives=True,
            keepRevisionForever=keep_revision_forever,
        )
        .execute()
    )
    return file


@mcp.tool()
def drive_file_move(
    account: AccountSlug,
    file_id: str,
    new_parent_id: str,
    remove_old_parents: bool = True,
) -> dict:
    """Move a file to a new folder."""
    svc = auth.drive(account).files()
    file = svc.get(
        fileId=file_id, fields="parents", supportsAllDrives=True
    ).execute()
    prev_parents = ",".join(file.get("parents", [])) if remove_old_parents else None
    updated = svc.update(
        fileId=file_id,
        addParents=new_parent_id,
        removeParents=prev_parents,
        fields=_DRIVE_FIELDS,
        supportsAllDrives=True,
    ).execute()
    return updated


@mcp.tool()
def drive_file_rename(account: AccountSlug, file_id: str, new_name: str) -> dict:
    """Rename a file."""
    return (
        auth.drive(account)
        .files()
        .update(
            fileId=file_id,
            body={"name": new_name},
            fields=_DRIVE_FIELDS,
            supportsAllDrives=True,
        )
        .execute()
    )


@mcp.tool()
def drive_file_trash(account: AccountSlug, file_id: str) -> dict:
    """Move a file to trash (reversible)."""
    return (
        auth.drive(account)
        .files()
        .update(
            fileId=file_id,
            body={"trashed": True},
            fields=_DRIVE_FIELDS,
            supportsAllDrives=True,
        )
        .execute()
    )


@mcp.tool()
def drive_folder_create(
    account: AccountSlug,
    name: str,
    parent_folder_id: str | None = None,
) -> dict:
    """Create a new folder."""
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_folder_id:
        metadata["parents"] = [parent_folder_id]
    return (
        auth.drive(account)
        .files()
        .create(body=metadata, fields=_DRIVE_FIELDS, supportsAllDrives=True)
        .execute()
    )


@mcp.tool()
def drive_file_share(
    account: AccountSlug,
    file_id: str,
    email: str,
    role: Literal["reader", "commenter", "writer", "fileOrganizer", "organizer"] = "reader",
    send_notification: bool = False,
    message: str | None = None,
) -> dict:
    """Share a file with someone by email."""
    permission = {
        "type": "user",
        "role": role,
        "emailAddress": email,
    }
    kwargs: dict[str, Any] = {
        "fileId": file_id,
        "body": permission,
        "sendNotificationEmail": send_notification,
        "supportsAllDrives": True,
    }
    if send_notification and message:
        kwargs["emailMessage"] = message
    return auth.drive(account).permissions().create(**kwargs).execute()


@mcp.tool()
def drive_file_link_access(
    account: AccountSlug,
    file_id: str,
    enabled: bool,
    role: Literal["reader", "commenter"] = "reader",
) -> dict:
    """Toggle "anyone with the link" access on a file the account owns.

    Built for zero-bandwidth server-side fetches: some APIs (e.g. a
    transcription service's source_url parameter) can download a Drive file
    themselves — but only while it is link-accessible. Flow: enable, hand the
    returned direct_download_url to the fetching service, then IMMEDIATELY
    call again with enabled=false to revoke. Never leave link access on.
    """
    service = auth.drive(account)
    if enabled:
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": role},
            supportsAllDrives=True,
        ).execute()
        return {
            "file_id": file_id,
            "link_access": role,
            "direct_download_url": (
                "https://drive.usercontent.google.com/download"
                f"?id={file_id}&export=download&confirm=t"
            ),
            "reminder": "Revoke when done: call again with enabled=false.",
        }
    service.permissions().delete(
        fileId=file_id,
        permissionId="anyoneWithLink",
        supportsAllDrives=True,
    ).execute()
    return {"file_id": file_id, "link_access": "off"}


# ─── Tasks (Google Tasks) ────────────────────────────────────────────────


def _task_summary(t: dict) -> dict:
    keys = ("id", "title", "status", "due", "notes", "parent",
            "position", "completed", "updated")
    return {k: t[k] for k in keys if k in t}


# ─── contacts ───────────────────────────────────────────────────────────
#
# Why this exists: Gmail shows the display name the SENDER supplies, so a
# recipient written as a bare address arrives as a raw address. The name for
# someone the user has only corresponded with lives in "other contacts", not
# in saved contacts, so both are searched. Read-only by scope.


_CONTACT_READ_MASK = "names,emailAddresses,organizations"
_OTHER_READ_MASK = "names,emailAddresses"


def _contact_rows(res: dict) -> list[dict]:
    """Flatten a People search response to {name, emails, organization}."""
    rows = []
    for hit in res.get("results") or []:
        person = hit.get("person") or {}
        names = person.get("names") or []
        emails = person.get("emailAddresses") or []
        orgs = person.get("organizations") or []
        rows.append({
            "name": names[0].get("displayName") if names else None,
            "emails": [e["value"] for e in emails if e.get("value")],
            "organization": orgs[0].get("name") if orgs else None,
        })
    return rows


def _people_search(account: str, query: str, page_size: int) -> list[dict]:
    """Search saved contacts, then other contacts. Warms the server-side cache.

    People search runs off a cache Google builds per session; the documented
    way to prime it is a request with an empty query. Rather than pay for that
    on every call, only warm up and retry when a search comes back empty.
    """
    svc = auth.people(account)

    def saved(q):
        return svc.people().searchContacts(
            query=q, pageSize=page_size, readMask=_CONTACT_READ_MASK).execute()

    def other(q):
        return svc.otherContacts().search(
            query=q, pageSize=page_size, readMask=_OTHER_READ_MASK).execute()

    rows = _contact_rows(saved(query)) + _contact_rows(other(query))
    if not rows:
        saved("")
        other("")
        rows = _contact_rows(saved(query)) + _contact_rows(other(query))

    seen, merged = set(), []
    for row in rows:
        key = (row["name"], tuple(row["emails"]))
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)
    return merged[:page_size]


@mcp.tool()
def contacts_search(
    account: AccountSlug,
    query: str,
    max_results: int = 10,
) -> dict:
    """Search the account's contacts by name, email address or company.

    Covers BOTH saved contacts and "other contacts" — the people Gmail
    recorded from correspondence but the user never saved — so someone who
    has only ever been emailed is still found.
    """
    page_size = max(1, min(int(max_results), 30))
    return {"contacts": _people_search(account, query, page_size)}


@mcp.tool()
def contacts_lookup(account: AccountSlug, email: str) -> dict:
    """Resolve ONE email address to the name to address that person by.

    Returns {"email", "name", "source"}. `source` is "contacts" (saved or
    other contacts) or "sent_mail" — the display name on a real message from
    that address, which is what Google itself shows — or null when nothing
    here knows the address, in which case the bare address is the correct
    form (a role mailbox like info@ usually lands here).

    Use it before writing a recipient: an address the account can name is
    addressed as "Firstname Lastname <addr@host>", never bare.
    """
    target = email.strip().lower()

    for row in _people_search(account, target, 30):
        if row["name"] and any(e.lower() == target for e in row["emails"]):
            return {"email": email, "name": row["name"], "source": "contacts"}

    # Fall back to what the address itself has signed mail as.
    res = auth.gmail(account).users().messages().list(
        userId="me", q=f"from:{target}", maxResults=1).execute()
    for ref in res.get("messages") or []:
        msg = auth.gmail(account).users().messages().get(
            userId="me", id=ref["id"], format="metadata",
            metadataHeaders=["From"]).execute()
        for h in msg.get("payload", {}).get("headers", []):
            if h.get("name", "").lower() != "from":
                continue
            name, addr = parseaddr(h.get("value", ""))
            if name and addr.lower() == target:
                return {"email": email, "name": name, "source": "sent_mail"}

    return {"email": email, "name": None, "source": None}


@mcp.tool()
def tasklist_list(account: AccountSlug) -> dict:
    """List the account's task lists (each has an id + title)."""
    res = auth.tasks(account).tasklists().list(maxResults=100).execute()
    return {"lists": [{"id": x["id"], "title": x.get("title", "")}
                      for x in res.get("items", [])]}


@mcp.tool()
def tasklist_create(account: AccountSlug, title: str) -> dict:
    """Create a new task list."""
    return auth.tasks(account).tasklists().insert(body={"title": title}).execute()


@mcp.tool()
def tasklist_delete(account: AccountSlug, tasklist_id: str) -> dict:
    """Delete a task list and all its tasks. Irreversible."""
    auth.tasks(account).tasklists().delete(tasklist=tasklist_id).execute()
    return {"deleted": tasklist_id}


@mcp.tool()
def task_list(
    account: AccountSlug,
    tasklist_id: str = "@default",
    show_completed: bool = False,
    show_hidden: bool = False,
    max_results: int = 100,
) -> dict:
    """List tasks in a list. Find ids via tasklist_list; '@default' is the account's default list."""
    res = (
        auth.tasks(account)
        .tasks()
        .list(
            tasklist=tasklist_id,
            showCompleted=show_completed,
            showHidden=show_hidden,
            maxResults=max_results,
        )
        .execute()
    )
    return {"tasks": [_task_summary(t) for t in res.get("items", [])]}


@mcp.tool()
def task_get(account: AccountSlug, task_id: str, tasklist_id: str = "@default") -> dict:
    """Fetch a single task."""
    return auth.tasks(account).tasks().get(tasklist=tasklist_id, task=task_id).execute()


@mcp.tool()
def task_create(
    account: AccountSlug,
    title: str,
    tasklist_id: str = "@default",
    notes: str | None = None,
    due: str | None = None,
    parent: str | None = None,
    previous: str | None = None,
) -> dict:
    """Create a task. `due` is RFC3339 (e.g. '2026-06-15T00:00:00Z') — Google Tasks
    keeps only the DATE part. `parent` makes it a subtask of that task id;
    `previous` orders it after that task id."""
    body: dict[str, Any] = {"title": title}
    if notes is not None:
        body["notes"] = notes
    if due is not None:
        body["due"] = due
    kwargs: dict[str, Any] = {"tasklist": tasklist_id, "body": body}
    if parent:
        kwargs["parent"] = parent
    if previous:
        kwargs["previous"] = previous
    return auth.tasks(account).tasks().insert(**kwargs).execute()


@mcp.tool()
def task_update(
    account: AccountSlug,
    task_id: str,
    tasklist_id: str = "@default",
    title: str | None = None,
    notes: str | None = None,
    due: str | None = None,
    status: Literal["needsAction", "completed"] | None = None,
) -> dict:
    """Patch a task's fields. status='completed' completes it (or use task_complete)."""
    body: dict[str, Any] = {}
    if title is not None:
        body["title"] = title
    if notes is not None:
        body["notes"] = notes
    if due is not None:
        body["due"] = due
    if status is not None:
        body["status"] = status
    return (
        auth.tasks(account)
        .tasks()
        .patch(tasklist=tasklist_id, task=task_id, body=body)
        .execute()
    )


@mcp.tool()
def task_complete(account: AccountSlug, task_id: str, tasklist_id: str = "@default") -> dict:
    """Mark a task completed (shortcut for status='completed')."""
    return (
        auth.tasks(account)
        .tasks()
        .patch(tasklist=tasklist_id, task=task_id, body={"status": "completed"})
        .execute()
    )


@mcp.tool()
def task_delete(account: AccountSlug, task_id: str, tasklist_id: str = "@default") -> dict:
    """Delete a task. Irreversible."""
    auth.tasks(account).tasks().delete(tasklist=tasklist_id, task=task_id).execute()
    return {"deleted": task_id}


@mcp.tool()
def task_move(
    account: AccountSlug,
    task_id: str,
    tasklist_id: str = "@default",
    parent: str | None = None,
    previous: str | None = None,
) -> dict:
    """Reposition a task: under `parent` (as a subtask) and/or after `previous` in the same list."""
    kwargs: dict[str, Any] = {"tasklist": tasklist_id, "task": task_id}
    if parent:
        kwargs["parent"] = parent
    if previous:
        kwargs["previous"] = previous
    return auth.tasks(account).tasks().move(**kwargs).execute()


# ─── entry ──────────────────────────────────────────────────────────────


def main() -> None:
    mcp.run()  # stdio transport — what Claude Code expects


if __name__ == "__main__":
    main()
