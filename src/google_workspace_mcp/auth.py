"""OAuth + Google service builders.

Each account has a token file at TOKENS_DIR/<slug>.json. The
authorize.py script writes these; the server reads + refreshes them.
"""

from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache

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


def _load_credentials(slug: str) -> Credentials:
    path = _token_path(slug)
    if not path.exists():
        raise RuntimeError(
            f"No token for account '{slug}' ({email_for(slug)}). "
            f"Run: google-workspace-authorize {slug}"
        )
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json())
    if not creds.valid:
        raise RuntimeError(
            f"Token for '{slug}' is invalid and cannot refresh. "
            f"Re-run: google-workspace-authorize {slug}"
        )
    return creds


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
