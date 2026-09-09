"""OAuth + Google service builders.

Each account has a token file at TOKENS_DIR/<slug>.json. The
authorize.py script writes these; the server reads + refreshes them.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from .accounts import config_home, email_for

# Config lives OUTSIDE the code tree so it survives the server being
# installed anywhere (uv tool venv, a Claude Code plugin cache, etc.)
# and so secrets never sit next to (or get committed with) the source.
# Honors $GWM_HOME / $XDG_CONFIG_HOME; override either path explicitly
# with $GWM_CREDENTIALS / $GWM_TOKENS_DIR.
CONFIG_DIR = config_home()

CREDENTIALS_PATH = Path(
    os.environ.get("GWM_CREDENTIALS", CONFIG_DIR / "credentials.json")
)
TOKENS_DIR = Path(os.environ.get("GWM_TOKENS_DIR", CONFIG_DIR / "tokens"))

# Broad scopes — these are the user's own accounts, full control.
# Narrower scopes would force re-auth every time a tool is added.
SCOPES = [
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/tasks",
]


def _token_path(slug: str) -> Path:
    return TOKENS_DIR / f"{slug}.json"


def _reauth_hint(slug: str) -> str:
    """Every reason a refresh token dies, cheapest cause first.

    Google issues 7-day refresh tokens to an External OAuth app left in
    "Testing" unless it asks only for name/email/profile — which these
    SCOPES are nowhere near — so an unpublished app is by far the most
    common cause of a weekly `invalid_grant`.
    """
    return (
        f"Re-run: google-workspace-authorize {slug}. If this recurs every "
        "~7 days, the OAuth app is still in 'Testing' — publish it "
        "(Google Auth Platform → Audience → Publish app), which needs no "
        "verification under 100 users. Other causes: unused for 6 months, "
        "the account's Google password changed (that revokes Gmail-scoped "
        "tokens), or consent/the OAuth client was revoked."
    )


def _load_credentials(slug: str) -> Credentials:
    path = _token_path(slug)
    if not path.exists():
        raise RuntimeError(
            f"No token for account '{slug}' ({email_for(slug)}). "
            f"Run: google-workspace-authorize {slug}"
        )
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise RuntimeError(
                f"Google rejected the refresh token for '{slug}' "
                f"({email_for(slug)}): {e}. {_reauth_hint(slug)}"
            ) from e
        path.write_text(creds.to_json())
    if not creds.valid:
        raise RuntimeError(
            f"Token for '{slug}' is invalid and cannot refresh. "
            f"{_reauth_hint(slug)}"
        )
    return creds


def token_status(slug: str) -> dict:
    """Prove an account's token still works by refreshing it against Google.

    A token file existing says nothing about whether Google still honors
    it, so this asks. `authorized` is None when the check itself could
    not run (offline, Google unreachable) — that is "unknown", not "no".
    """
    path = _token_path(slug)
    if not path.exists():
        return {
            "authorized": False,
            "status": "no_token",
            "detail": f"Never authorized. Run: google-workspace-authorize {slug}",
        }
    try:
        creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    except (ValueError, OSError) as e:
        return {
            "authorized": False,
            "status": "unreadable",
            "detail": (
                f"{path} is not a usable token file ({e}). "
                f"Re-run: google-workspace-authorize {slug}"
            ),
        }
    try:
        creds.refresh(Request())
    except RefreshError as e:
        return {
            "authorized": False,
            "status": "revoked",
            "detail": f"Google rejected the refresh token ({e}). {_reauth_hint(slug)}",
        }
    except TransportError as e:
        return {
            "authorized": None,
            "status": "unreachable",
            "detail": f"Could not reach Google to check this token ({e}).",
        }
    path.write_text(creds.to_json())
    return {"authorized": True, "status": "ok"}


# Cache built services per account+api so we don't rebuild on every call.
# The Credentials object refreshes itself in place, so this is safe.
@lru_cache(maxsize=32)
def _service(slug: str, api: str, version: str):
    creds = _load_credentials(slug)
    return build(api, version, credentials=creds, cache_discovery=False)


def gmail(slug: str):
    return _service(slug, "gmail", "v1")


def calendar(slug: str):
    return _service(slug, "calendar", "v3")


def drive(slug: str):
    return _service(slug, "drive", "v3")


def tasks(slug: str):
    return _service(slug, "tasks", "v1")
