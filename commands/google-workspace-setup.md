---
description: One-time setup for the google-workspace-mcp plugin — check prerequisites, create/locate the OAuth client, define accounts, authorize each in the right browser, and wire it into the current project.
---

# Google Workspace MCP — setup

Guide the user through standing up the multi-account Google Workspace MCP
server. Be concise; do the mechanical steps yourself. Never ask the user
to paste secrets into chat — only to place files or click Allow.

## 1. Prerequisites

- `uv --version` — if missing, point to https://docs.astral.sh/uv/ and stop.
- OAuth client secret at `~/.config/google-workspace-mcp/credentials.json`
  (honor `$GWM_HOME` / `$GWM_CREDENTIALS` / `$XDG_CONFIG_HOME`). If missing,
  walk them through Google Cloud Console (own project; enable **Gmail**,
  **Calendar**, **Drive** APIs; OAuth consent screen External + add their
  accounts as **test users**; create an **OAuth client → Desktop app**;
  download and save to that path).
- Server binary on PATH: `google-workspace-mcp --help`. If missing:
  `uv tool install git+https://github.com/rajool/google-workspace-mcp`
  (or `uv tool install .` from a local clone).

## 2. Define accounts

Pick a short slug per account. Either create
`~/.config/google-workspace-mcp/accounts.json` (see `accounts.example.json`
in the repo) or plan to pass them inline via `GWM_ACCOUNTS`.

## 3. Authorize each account

The authorize CLI prints a Google consent URL and waits on a localhost
redirect. The key rule: **open the URL in a browser signed into the account
being authorized.**

For each account:

1. Run in the background:
   `google-workspace-authorize <slug>` (account already in the registry),
   or `google-workspace-authorize <slug> <email> "<Display Name>"` to
   register a new one. It prints `AUTH_URL: <url>`.
2. If the **Claude in Chrome** extension is connected, call
   `list_connected_browsers`, pick the browser whose name matches that
   account's email, `select_browser` it, `navigate` to the AUTH_URL, and
   click through the unverified-app warning → Continue → Select all →
   Continue. Otherwise hand the user the URL for the right browser/profile.
3. The CLI writes `~/.config/google-workspace-mcp/tokens/<slug>.json`.

## 4. Wire it into this project

Create a `.mcp.json` at the project root scoping the project to the
intended accounts:

```json
{ "mcpServers": { "google-workspace": {
    "command": "google-workspace-mcp",
    "env": { "GWM_ACCOUNTS": "<comma-separated slugs>" } } } }
```

`GWM_ACCOUNTS` limits this project to those accounts (omit it to allow all
in the registry). Tell the user to restart Claude Code / reconnect MCP.

## 5. Verify

Call `accounts_list` and confirm the expected accounts show
`authorized: true`. Optionally send one test email per account to the
user's own address.
