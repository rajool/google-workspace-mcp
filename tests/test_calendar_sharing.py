"""Calendar management tools (0.11.0): the requests calendar_create /
calendar_share / calendar_unshare / calendar_acl_list send, without touching Google."""

from __future__ import annotations

import pytest

from google_workspace_mcp import server


class _Call:
    def __init__(self, log, name, resp=None, **kw):
        log.append((name, kw))
        self._resp = resp or {}

    def execute(self):
        return self._resp


class _FakeCalendarService:
    """Just enough of the Calendar API client: records every request it gets."""

    def __init__(self):
        self.log = []
        self.acl_rows = [
            {"id": "user:a@example.com", "role": "reader", "scope": {"type": "user", "value": "a@example.com"}},
            {"id": "user:me@example.com", "role": "owner", "scope": {"type": "user", "value": "me@example.com"}},
        ]

    def calendars(self):
        return self

    def acl(self):
        return self

    def insert(self, **kw):
        body = kw.get("body", {})
        return _Call(self.log, "insert", resp={"id": "new-id", **body}, **kw)

    def delete(self, **kw):
        return _Call(self.log, "delete", **kw)

    def list(self, **kw):
        return _Call(self.log, "list", resp={"items": self.acl_rows}, **kw)


@pytest.fixture
def fake(monkeypatch):
    svc = _FakeCalendarService()
    monkeypatch.setattr(server.auth, "calendar", lambda account: svc)
    return svc


def test_create_sends_summary_description_timezone(fake):
    out = server.calendar_create("personal", "School", description="d", timezone="America/Vancouver")
    name, kw = fake.log[-1]
    assert name == "insert"
    assert kw["body"] == {"summary": "School", "description": "d", "timeZone": "America/Vancouver"}
    assert out == {"id": "new-id", "summary": "School", "description": "d", "timeZone": "America/Vancouver"}


def test_create_omits_empty_optionals(fake):
    server.calendar_create("personal", "Bare")
    _, kw = fake.log[-1]
    assert kw["body"] == {"summary": "Bare"}


def test_share_defaults_to_reader_with_an_invitation(fake):
    out = server.calendar_share("personal", "cal@group.calendar.google.com", " p@example.com ")
    name, kw = fake.log[-1]
    assert name == "insert"
    assert kw["calendarId"] == "cal@group.calendar.google.com"
    assert kw["body"] == {"role": "reader", "scope": {"type": "user", "value": "p@example.com"}}
    assert kw["sendNotifications"] is True
    assert out["role"] == "reader" and out["email"] == "p@example.com" and out["rule_id"] == "new-id"


def test_share_refuses_owner_before_calling_google(fake):
    with pytest.raises(ValueError):
        server.calendar_share("personal", "cal", "p@example.com", role="owner")  # type: ignore[arg-type]
    assert fake.log == []


def test_unshare_deletes_the_matching_rule_case_insensitively(fake):
    out = server.calendar_unshare("personal", "cal", " A@Example.com ")
    name, kw = fake.log[-1]
    assert (name, kw) == ("delete", {"calendarId": "cal", "ruleId": "user:a@example.com"})
    assert out == {"calendar_id": "cal", "removed": "user:a@example.com", "found": True}


def test_unshare_falls_back_to_the_conventional_rule_id(fake):
    out = server.calendar_unshare("personal", "cal", "nobody@example.com")
    _, kw = fake.log[-1]
    assert kw["ruleId"] == "user:nobody@example.com"
    assert out["found"] is False


def test_acl_list_flattens_scope(fake):
    out = server.calendar_acl_list("personal", "cal")
    assert out["calendar_id"] == "cal"
    assert out["rules"][0] == {
        "id": "user:a@example.com",
        "role": "reader",
        "scope_type": "user",
        "scope_value": "a@example.com",
    }
