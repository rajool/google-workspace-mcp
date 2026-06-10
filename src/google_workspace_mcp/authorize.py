"""Standalone OAuth flow — adds/refreshes a token for one account slug.

Usage:
    google-workspace-authorize <slug>

The script:
  1. Picks a free localhost port and prints `AUTH_URL: <url>` to stdout.
  2. Starts a tiny HTTP server that captures the OAuth redirect.
  3. Exchanges the code for an offline refresh token.
  4. Saves it to tokens/<slug>.json.

The caller is expected to open the AUTH_URL in the *right browser*
(the one logged into the account you're authorizing). When run from
Claude Code, the harness reads the URL, drives a named Chrome
instance to it, the user clicks Allow, and the script finalises.
"""

from __future__ import annotations

import socket
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from google_auth_oauthlib.flow import Flow

from .accounts import email_for, upsert_account
from .auth import CREDENTIALS_PATH, SCOPES, TOKENS_DIR

DONE_HTML = b"""<!doctype html><meta charset="utf-8">
<title>Authorized</title>
<style>body{font-family:system-ui;max-width:40em;margin:4em auto;padding:0 1em}</style>
<h1>Done</h1><p>You can close this tab.</p>"""


class _Handler(BaseHTTPRequestHandler):
    code: str | None = None
    error: str | None = None

    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        _Handler.code = qs.get("code", [None])[0]
        _Handler.error = qs.get("error", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(DONE_HTML)

    def log_message(self, *a, **k):
        pass  # silent — keep stdout clean for AUTH_URL marker


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(
            "usage: google-workspace-authorize <slug> [email] [display name]\n"
            "  new account  : pass the email (+ optional display name) to "
            "register it, e.g.\n"
            "                 google-workspace-authorize work me@company.com \"My Name\"\n"
            "  known account: just the slug (must already be in accounts.json)",
            file=sys.stderr,
        )
        return 2

    slug = args[0]
    email = args[1] if len(args) > 1 else None
    name = " ".join(args[2:]) if len(args) > 2 else ""

    if email:
        path = upsert_account(slug, email, name)
        print(f"registered '{slug}' ({email}) in {path}", flush=True)
    else:
        try:
            email = email_for(slug)  # from the registry
        except ValueError as e:
            print(
                f"ERROR: {e}\n"
                f"To register a new account, pass its email: "
                f"google-workspace-authorize {slug} <email> [display name]",
                file=sys.stderr,
            )
            return 1

    if not CREDENTIALS_PATH.exists():
        print(
            f"ERROR: OAuth client file not found at {CREDENTIALS_PATH}.\n"
            f"Download the OAuth client (Desktop app) from Google Cloud Console "
            f"and save it there, or set $GWM_CREDENTIALS to its path.",
            file=sys.stderr,
        )
        return 1

    port = _free_port()
    redirect_uri = f"http://127.0.0.1:{port}"

    flow = Flow.from_client_secrets_file(
        str(CREDENTIALS_PATH),
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )
    # login_hint nudges Google to pre-pick the right account in a
    # multi-account browser, though the named browser should only
    # have one account signed in anyway.
    auth_url, _state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        login_hint=email,
    )

    # Flush so the harness can read it immediately.
    print(f"AUTH_URL: {auth_url}", flush=True)
    print(f"AWAITING_REDIRECT_ON: {redirect_uri}", flush=True)

    httpd = HTTPServer(("127.0.0.1", port), _Handler)
    httpd.timeout = 600  # 10 min to consent
    httpd.handle_request()
    httpd.server_close()

    if _Handler.error:
        print(f"ERROR: oauth returned error: {_Handler.error}", file=sys.stderr)
        return 1
    if not _Handler.code:
        print("ERROR: no authorization code received (timeout?)", file=sys.stderr)
        return 1

    flow.fetch_token(code=_Handler.code)
    creds = flow.credentials

    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    out = TOKENS_DIR / f"{slug}.json"
    out.write_text(creds.to_json())
    out.chmod(0o600)
    print(f"OK: saved token for '{slug}' ({email}) -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
