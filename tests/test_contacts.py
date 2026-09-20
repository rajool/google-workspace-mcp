"""Contacts tools (0.12.0): contacts_search / contacts_lookup resolve a bare
address to the name to address someone by, without touching Google."""

from __future__ import annotations

import pytest

from google_workspace_mcp import server


def _person(name, *emails, org=None):
    person = {"emailAddresses": [{"value": e} for e in emails]}
    if name:
        person["names"] = [{"displayName": name}]
    if org:
        person["organizations"] = [{"name": org}]
    return {"person": person}


class _Call:
    def __init__(self, log, name, resp, **kw):
        log.append((name, kw))
        self._resp = resp

    def execute(self):
        return self._resp


class _FakePeopleService:
    """Just enough of the People API client, and it records every query."""

    def __init__(self, saved=None, other=None, cold=False):
        self.log = []
        self._saved, self._other = saved or [], other or []
        # cold=True: the first non-empty search returns nothing until a
        # warm-up request with an empty query lands — Google's documented
        # behaviour for the search cache
        self._cold = cold
        self._warmed = False

    def _rows(self, query, warm):
        if self._cold and not self._warmed and query != "":
            return []
        return warm

    def people(self):
        svc = self

        class _People:
            def searchContacts(self, query, pageSize, readMask):
                if query == "":
                    svc._warmed = True
                return _Call(svc.log, "searchContacts",
                             {"results": svc._rows(query, svc._saved)},
                             query=query, pageSize=pageSize, readMask=readMask)
        return _People()

    def otherContacts(self):
        svc = self

        class _Other:
            def search(self, query, pageSize, readMask):
                if query == "":
                    svc._warmed = True
                return _Call(svc.log, "otherContacts.search",
                             {"results": svc._rows(query, svc._other)},
                             query=query, pageSize=pageSize, readMask=readMask)
        return _Other()


class _FakeGmailService:
    """Enough Gmail to answer `from:<addr>` with one message's From header."""

    def __init__(self, from_header=None):
        self._from = from_header
        self.log = []

    def users(self):
        svc = self

        class _Messages:
            def list(self, userId, q, maxResults):
                hits = [{"id": "m1"}] if svc._from else []
                return _Call(svc.log, "messages.list", {"messages": hits}, q=q)

            def get(self, userId, id, format, metadataHeaders):
                return _Call(
                    svc.log, "messages.get",
                    {"payload": {"headers": [{"name": "From", "value": svc._from}]}},
                    id=id)

        class _Users:
            def messages(self):
                return _Messages()
        return _Users()


def _wire(monkeypatch, people, gmail=None):
    monkeypatch.setattr(server.auth, "people", lambda account: people)
    monkeypatch.setattr(server.auth, "gmail",
                        lambda account: gmail or _FakeGmailService())


def test_search_merges_saved_and_other_contacts(monkeypatch):
    svc = _FakePeopleService(
        saved=[_person("Maryam Aghaee Tabrizi", "aghaee.m@gmail.com", org="Acme")],
        other=[_person("Sayed Mehdi Naji Esfahani", "mehdinaji@gmail.com")],
    )
    _wire(monkeypatch, svc)
    rows = server.contacts_search("personal", "a")["contacts"]
    assert [r["name"] for r in rows] == [
        "Maryam Aghaee Tabrizi", "Sayed Mehdi Naji Esfahani"]
    assert rows[0]["organization"] == "Acme"
    assert rows[1]["emails"] == ["mehdinaji@gmail.com"]


def test_search_deduplicates_a_person_in_both_books(monkeypatch):
    dup = _person("Maryam Aghaee Tabrizi", "aghaee.m@gmail.com")
    _wire(monkeypatch, _FakePeopleService(saved=[dup], other=[dup]))
    assert len(server.contacts_search("personal", "maryam")["contacts"]) == 1


def test_search_caps_page_size_at_googles_limit(monkeypatch):
    svc = _FakePeopleService()
    _wire(monkeypatch, svc)
    server.contacts_search("personal", "x", max_results=500)
    assert all(kw["pageSize"] == 30 for _, kw in svc.log)


def test_search_warms_the_cache_and_retries_when_empty(monkeypatch):
    svc = _FakePeopleService(
        saved=[_person("Vahid Amintabar", "vahid@example.com")], cold=True)
    _wire(monkeypatch, svc)
    rows = server.contacts_search("personal", "vahid")["contacts"]
    assert [r["name"] for r in rows] == ["Vahid Amintabar"]
    assert ("searchContacts", {"query": "", "pageSize": 10,
                               "readMask": server._CONTACT_READ_MASK}) in svc.log
    assert ("otherContacts.search", {"query": "", "pageSize": 10,
                                     "readMask": server._OTHER_READ_MASK}) in svc.log


def test_lookup_prefers_a_contact_whose_address_matches_exactly(monkeypatch):
    svc = _FakePeopleService(saved=[
        _person("Someone Else", "other@example.com"),
        _person("Maryam Aghaee Tabrizi", "aghaee.m@gmail.com"),
    ])
    _wire(monkeypatch, svc)
    assert server.contacts_lookup("personal", "aghaee.m@gmail.com") == {
        "email": "aghaee.m@gmail.com",
        "name": "Maryam Aghaee Tabrizi",
        "source": "contacts",
    }


def test_lookup_matches_the_address_case_insensitively(monkeypatch):
    _wire(monkeypatch, _FakePeopleService(
        other=[_person("Ehsan Rajooldezfooli", "EhsanRajool@gmail.com")]))
    got = server.contacts_lookup("personal", "ehsanrajool@gmail.com")
    assert got["name"] == "Ehsan Rajooldezfooli"


def test_lookup_falls_back_to_the_name_on_real_mail(monkeypatch):
    _wire(monkeypatch, _FakePeopleService(),
          _FakeGmailService("Deion Mudaliar <deion@panlegal.ca>"))
    assert server.contacts_lookup("personal", "deion@panlegal.ca") == {
        "email": "deion@panlegal.ca",
        "name": "Deion Mudaliar",
        "source": "sent_mail",
    }


def test_lookup_returns_no_name_when_nobody_knows_the_address(monkeypatch):
    _wire(monkeypatch, _FakePeopleService(), _FakeGmailService(None))
    assert server.contacts_lookup("personal", "info@cleardental.ca") == {
        "email": "info@cleardental.ca", "name": None, "source": None,
    }


def test_lookup_ignores_a_from_header_for_a_different_address(monkeypatch):
    """A `from:` search can match on more than the From header, so only an
    exact address match may supply the name."""
    _wire(monkeypatch, _FakePeopleService(),
          _FakeGmailService("Mailer Daemon <daemon@example.com>"))
    assert server.contacts_lookup("personal", "someone@example.org")["name"] is None


def test_lookup_ignores_a_contact_that_has_no_name(monkeypatch):
    _wire(monkeypatch, _FakePeopleService(saved=[_person(None, "x@example.com")]),
          _FakeGmailService(None))
    assert server.contacts_lookup("personal", "x@example.com")["name"] is None


@pytest.mark.parametrize("scope", [
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/contacts.other.readonly",
])
def test_the_read_only_contact_scopes_are_requested(scope):
    from google_workspace_mcp import auth
    assert scope in auth.SCOPES
