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
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any, Literal

from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from mcp.server.fastmcp import FastMCP

from . import auth
from .accounts import ACCOUNTS, AccountSlug, email_for, name_for

mcp = FastMCP("google-workspace")


# ─── helpers ────────────────────────────────────────────────────────────


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
) -> str:
    """Return a base64url-encoded MIME message ready for Gmail."""
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
        msg.set_content(body, subtype="html")
    else:
        msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


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
    """List the configured Google accounts and whether each has a valid token."""
    out: dict[str, dict] = {}
    for slug, info in ACCOUNTS.items():
        path = auth.TOKENS_DIR / f"{slug}.json"
        out[slug] = {
            "email": info["email"],
            "name": info.get("name", ""),
            "authorized": path.exists(),
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
) -> dict:
    """Send an email immediately from the given account.

    For replies, pass thread_id AND in_reply_to_message_id (the RFC822
    Message-Id header value, NOT the Gmail message id) so the reply
    threads correctly.
    """
    raw = _build_mime(
        sender=_from_header(account),
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
        html=html,
        in_reply_to=in_reply_to_message_id,
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
) -> dict:
    """Create a Gmail draft. Returns {id, message: {...}}."""
    raw = _build_mime(
        sender=_from_header(account),
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
        html=html,
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
) -> dict:
    """Overwrite an existing draft's contents."""
    raw = _build_mime(
        sender=_from_header(account),
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
        html=html,
    )
    draft = (
        auth.gmail(account)
        .users()
        .drafts()
        .update(userId="me", id=draft_id, body={"message": {"raw": raw}})
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


# ─── Drive ──────────────────────────────────────────────────────────────


_DRIVE_FIELDS = "id, name, mimeType, parents, webViewLink, webContentLink, modifiedTime, size, owners(emailAddress, displayName)"


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
    svc = auth.drive(account).files()
    if export_mime_type:
        req = svc.export_media(fileId=file_id, mimeType=export_mime_type)
    else:
        req = svc.get_media(fileId=file_id, supportsAllDrives=True)
    out_path = Path(save_to).expanduser()
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
    path = Path(local_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(local_path)

    metadata: dict[str, Any] = {"name": name or path.name}
    if parent_folder_id:
        metadata["parents"] = [parent_folder_id]

    if mime_type is None:
        mime_type, _ = mimetypes.guess_type(str(path))
        mime_type = mime_type or "application/octet-stream"

    if convert_to_google_doc:
        # Drive treats the destination mimeType in metadata as the
        # target format; the source mimeType comes from the media body.
        conv = {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "application/vnd.google-apps.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "application/vnd.google-apps.spreadsheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": "application/vnd.google-apps.presentation",
        }
        target = conv.get(mime_type)
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


# ─── Tasks (Google Tasks) ────────────────────────────────────────────────


def _task_summary(t: dict) -> dict:
    keys = ("id", "title", "status", "due", "notes", "parent",
            "position", "completed", "updated")
    return {k: t[k] for k in keys if k in t}


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
