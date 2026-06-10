"""Account registry — resolved at runtime from config, never hardcoded.

Which accounts a given server instance exposes is resolved in this order:

  1. $GWM_ACCOUNTS
       - a JSON object: {"slug": {"email": "...", "name": "..."}, ...}
       - OR a comma-separated list of slugs ("work,personal") that selects
         a subset of the registry (below). This is how a per-project
         .mcp.json limits a project to specific accounts.
  2. The registry file: $GWM_ACCOUNTS_FILE, else <config-home>/accounts.json
       shape: {"accounts": {"slug": {"email", "name"}, ...}}
       (the bare mapping without the "accounts" wrapper also works).
  3. Nothing configured -> empty (accounts_list reports it).

Config home: $GWM_HOME, else $XDG_CONFIG_HOME/google-workspace-mcp, else
~/.config/google-workspace-mcp. Tokens and the OAuth client live there too.

Slugs are user-defined, so the `account` argument is a plain string,
validated against the configured accounts at call time.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

AccountSlug = str


def config_home() -> Path:
    """Directory holding credentials.json, accounts.json, and tokens/."""
    env = os.environ.get("GWM_HOME")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "google-workspace-mcp"


def _registry_path() -> Path:
    p = os.environ.get("GWM_ACCOUNTS_FILE")
    return Path(p).expanduser() if p else config_home() / "accounts.json"


def _clean(mapping: dict) -> dict[str, dict]:
    """Keep only well-formed {slug: {email, name?}} entries."""
    out: dict[str, dict] = {}
    for slug, info in (mapping or {}).items():
        if isinstance(info, dict) and info.get("email"):
            out[slug] = {"email": info["email"], "name": info.get("name", "")}
    return out


def _read_registry() -> dict[str, dict]:
    p = _registry_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except Exception as e:  # noqa: BLE001
        print(f"google-workspace-mcp: bad accounts file {p}: {e}", file=sys.stderr)
        return {}
    if isinstance(data, dict):
        return _clean(data.get("accounts", data))
    return {}


def _resolve() -> dict[str, dict]:
    raw = (os.environ.get("GWM_ACCOUNTS") or "").strip()
    if raw.startswith("{"):
        try:
            return _clean(json.loads(raw))
        except Exception as e:  # noqa: BLE001
            print(f"google-workspace-mcp: bad GWM_ACCOUNTS json: {e}", file=sys.stderr)
            return {}
    registry = _read_registry()
    if raw:
        wanted = [s.strip() for s in raw.split(",") if s.strip()]
        missing = [s for s in wanted if s not in registry]
        if missing:
            print(
                f"google-workspace-mcp: GWM_ACCOUNTS lists unknown slug(s) "
                f"{missing}; known in registry: {sorted(registry) or '(none)'}",
                file=sys.stderr,
            )
        return {s: registry[s] for s in wanted if s in registry}
    return registry


# Resolved once at import. Slugs/accounts are static for a server's life;
# restart the server after changing config.
ACCOUNTS: dict[str, dict] = _resolve()


def _entry(slug: str) -> dict:
    if slug not in ACCOUNTS:
        known = sorted(ACCOUNTS) or "(none configured — see README setup)"
        raise ValueError(f"Unknown account '{slug}'. Configured: {known}.")
    return ACCOUNTS[slug]


def email_for(slug: str) -> str:
    return _entry(slug)["email"]


def name_for(slug: str) -> str:
    return _entry(slug).get("name", "")


def upsert_account(slug: str, email: str, name: str = "") -> Path:
    """Add or update a slug in the registry file. Used by the authorize CLI."""
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc: dict = {}
    if path.exists():
        try:
            doc = json.loads(path.read_text())
        except Exception:  # noqa: BLE001
            doc = {}
    if not isinstance(doc, dict):
        doc = {}
    accounts = doc.get("accounts")
    if not isinstance(accounts, dict):
        accounts = {} if "accounts" in doc else doc  # tolerate bare mapping
        doc = {"accounts": accounts}
    accounts[slug] = {"email": email, "name": name}
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    return path
