"""The server refuses to read from or write into its own secret store, and
attachments are capped and typed defensively (0.10.1)."""

from __future__ import annotations

import base64
import email

import pytest

from google_workspace_mcp import server


def _mime(**kw):
    raw = server._build_mime(
        sender="Me <me@example.com>", to=["them@example.com"], subject="s", body="b", **kw
    )
    return email.message_from_bytes(base64.urlsafe_b64decode(raw + "==="))


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the secret store at a temp dir and make any Google call an error."""
    cfg = tmp_path / "config"
    tokens = cfg / "tokens"
    tokens.mkdir(parents=True)
    (tokens / "work.json").write_text("{}")
    creds_elsewhere = tmp_path / "elsewhere" / "client.json"  # GWM_CREDENTIALS override
    creds_elsewhere.parent.mkdir()
    creds_elsewhere.write_text("{}")
    monkeypatch.setattr(server.auth, "CONFIG_DIR", cfg)
    monkeypatch.setattr(server.auth, "TOKENS_DIR", tokens)
    monkeypatch.setattr(server.auth, "CREDENTIALS_PATH", creds_elsewhere)

    def boom(*_args, **_kwargs):
        raise AssertionError("reached Google before the path check")

    monkeypatch.setattr(server.auth, "gmail", boom)
    monkeypatch.setattr(server.auth, "drive", boom)
    return cfg


def test_attachment_from_the_token_dir_is_refused(store):
    with pytest.raises(PermissionError):
        _mime(attachments=[str(store / "tokens" / "work.json")])


def test_symlink_into_the_store_is_refused(store, tmp_path):
    link = tmp_path / "innocent.json"
    link.symlink_to(store / "tokens" / "work.json")
    with pytest.raises(PermissionError):
        _mime(attachments=[str(link)])


def test_credentials_file_outside_config_dir_is_refused_too(store):
    with pytest.raises(PermissionError):
        _mime(attachments=[str(server.auth.CREDENTIALS_PATH)])


def test_upload_and_in_place_update_refuse_before_any_google_call(store):
    token = str(store / "tokens" / "work.json")
    with pytest.raises(PermissionError):
        server.drive_file_upload("acc", token)
    with pytest.raises(PermissionError):
        server.drive_file_update_content("acc", "FILE", token)


def test_downloads_cannot_write_into_the_store(store):
    with pytest.raises(PermissionError):
        server.gmail_attachment_download("acc", "m", "a", str(store / "credentials.json"))
    with pytest.raises(PermissionError):
        server.drive_file_download("acc", "FILE", str(store / "tokens" / "new.json"))


def test_ordinary_files_still_work(store, tmp_path):
    ok = tmp_path / "report.pdf"
    ok.write_bytes(b"%PDF")
    assert _mime(attachments=[str(ok)]).get_payload()[1].get_filename() == "report.pdf"


def test_eml_goes_as_opaque_bytes_not_message_rfc822(tmp_path):
    eml = tmp_path / "forwarded.eml"
    eml.write_bytes(b"From: a@x\nSubject: inner\n\nhi\n")
    part = _mime(attachments=[str(eml)]).get_payload()[1]
    assert part.get_content_type() == "application/octet-stream"
    assert part.get_filename() == "forwarded.eml"
    assert part.get_payload(decode=True) == eml.read_bytes()


def test_attachments_over_gmails_cap_fail_early(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_GMAIL_ATTACHMENT_CAP", 10)
    a = tmp_path / "a.bin"
    a.write_bytes(b"x" * 6)
    b = tmp_path / "b.bin"
    b.write_bytes(b"x" * 6)
    assert _mime(attachments=[str(a)]).get_content_type() == "multipart/mixed"
    with pytest.raises(ValueError, match="25 MB"):
        _mime(attachments=[str(a), str(b)])
