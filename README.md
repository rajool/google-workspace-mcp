<div align="center">

# google-workspace-mcp

**One MCP server, all your Google accounts.**

A **multi-account** Google Workspace MCP server for [Claude Code](https://code.claude.com) — **Gmail**, **Calendar**, **Drive**, and **Tasks** across any number of Google accounts *in parallel*. Every tool takes an `account` slug, so a single Claude session can email from one account, file in another's Drive, and book an event in a third. Ships as a Claude Code **plugin** that is also its own **marketplace**.

[![CI](https://github.com/rajool/google-workspace-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/rajool/google-workspace-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Plugin version](https://img.shields.io/badge/plugin-v0.4.0-5b8cff.svg)](.claude-plugin/plugin.json)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-d97757)](https://code.claude.com/docs/en/plugins)
[![Changelog](https://img.shields.io/badge/changelog-keep%20a%20changelog-orange)](CHANGELOG.md)

</div>

---

## Why google-workspace-mcp

- **One server, every account.** Each tool call passes an `account` slug (e.g. `work`, `personal`). The server loads and refreshes that account's OAuth token transparently — no switching, no separate servers.
- **Per-project access control.** Accounts are configured at *runtime*, never baked into code. Each project's `.mcp.json` scopes it to a subset, so a personal project never even sees your work account.
- **Your own OAuth client.** You bring a (free) Google Cloud OAuth client, so you own the access and get the full tool surface — including things the default `claude.ai` connector can't do, like deleting a draft. Nothing is routed through anyone else's infrastructure.
- **Secrets stay out of the tree.** The OAuth client and per-account refresh tokens live under `~/.config/google-workspace-mcp/` (written `0600`), never next to code.
- **38 tools across four services** — see the [catalog](#tools) below.

## Table of contents

- [Install](#install)
- [Setup](#setup)
- [Per-project access control](#per-project-access-control)
- [Tools](#tools)
- [Configuration & storage](#configuration--storage)
- [Scopes](#scopes)
- [Trust & security](#trust--security)
- [Repository layout](#repository-layout)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

## Install

> `/plugin` commands run **inside** Claude Code — type them at the prompt.

```text
# 1) Register the marketplace (once per machine)
/plugin marketplace add rajool/google-workspace-mcp

# 2) Install the plugin
/plugin install google-workspace-mcp@google-workspace-mcp
```

This gives you the **`/google-workspace-setup`** command, which walks you through everything in [Setup](#setup) — checking prerequisites, creating the OAuth client, defining accounts, authorizing each one in the right browser, and wiring the server into your project.

<details>
<summary><strong>No <code>/plugin</code> command?</strong> (Cowork, web, the Agent SDK) — enable it declaratively</summary>

Some environments don't expose the `/plugin` slash command. Add the block below to `.claude/settings.json` at **user level** (`~/.claude/settings.json`, applies everywhere) or **project level** (committed, so teammates get it on trust). The runtime reconciles `enabledPlugins` at startup and fetches the marketplace automatically:

```json
{
  "extraKnownMarketplaces": {
    "google-workspace-mcp": {
      "source": { "source": "github", "repo": "rajool/google-workspace-mcp" }
    }
  },
  "enabledPlugins": {
    "google-workspace-mcp@google-workspace-mcp": true
  }
}
```

</details>

## Setup

Requires [`uv`](https://docs.astral.sh/uv/) on your PATH. The `/google-workspace-setup` command automates these steps; here is the manual path.

### 1. Create your own Google Cloud OAuth client

Each user creates their own client (free):

1. Go to the [Google Cloud Console](https://console.cloud.google.com) and create a project (or pick one).
2. **APIs & Services → Enabled APIs & services → + Enable APIs** — enable the **Gmail**, **Google Calendar**, **Google Drive**, and **Google Tasks** APIs.
3. **APIs & Services → OAuth consent screen** — User type **External**. Fill the required fields. Under **Test users**, add every Google account you intend to connect. (Leaving the app in *Testing* is fine.)
4. **APIs & Services → Credentials → + Create credentials → OAuth client ID → Application type: Desktop app**. Download the JSON.
5. Save that JSON as:

   ```text
   ~/.config/google-workspace-mcp/credentials.json
   ```

### 2. Install the server binary

So a project's `.mcp.json` can launch it by name:

```bash
# from GitHub:
uv tool install git+https://github.com/rajool/google-workspace-mcp

# …or from a local clone:
uv tool install .
```

This installs two commands: `google-workspace-mcp` (the server) and `google-workspace-authorize` (the OAuth helper).

### 3. Define your accounts

Give each account a short **slug**. Two ways:

**a) A registry file** (recommended) — `~/.config/google-workspace-mcp/accounts.json` (template: [`accounts.example.json`](accounts.example.json)):

```json
{
  "accounts": {
    "work":     { "email": "you@company.com", "name": "Your Name" },
    "personal": { "email": "you@gmail.com",   "name": "Your Name" }
  }
}
```

`name` is the display name shown in the `From:` header on sent mail (set it to `""` to send with the bare address).

**b) Or inline per project** via the `GWM_ACCOUNTS` env var (see [step 5](#5-wire-it-into-a-project)) — handy if you don't want a shared registry file.

### 4. Authorize each account

This prints a Google consent URL — open it in a browser signed into *that* account and click **Allow**:

```bash
# account already in accounts.json — just the slug:
google-workspace-authorize work

# or register a brand-new account inline (adds it to accounts.json):
google-workspace-authorize personal you@gmail.com "Your Name"
```

You'll click through the "Google hasn't verified this app" warning (it's your own app) → Continue → Select all → Continue. The refresh token is written to `~/.config/google-workspace-mcp/tokens/<slug>.json`.

### 5. Wire it into a project

Add a `.mcp.json` at the project root. **`GWM_ACCOUNTS` decides which accounts this project may use:**

```json
{
  "mcpServers": {
    "google-workspace": {
      "command": "google-workspace-mcp",
      "env": { "GWM_ACCOUNTS": "work,personal" }
    }
  }
}
```

- `GWM_ACCOUNTS` can be a **comma-list of slugs** from your registry, or an **inline JSON map** (`{"work":{"email":"…","name":"…"}}`) if you skipped the registry.
- Omit `GWM_ACCOUNTS` to expose every account in the registry.

Restart Claude Code (or reconnect MCP) to pick it up, then ask Claude to list accounts to confirm.

## Per-project access control

The point of `GWM_ACCOUNTS` is isolation: a project literally cannot act as an account that isn't in its list — the server refuses the slug. Tokens are stored centrally (authorize once), but each project's `.mcp.json` chooses its own slice:

| Project | `.mcp.json` `GWM_ACCOUNTS` | Can use |
|---|---|---|
| personal site | `personal` | personal only |
| work app | `work,support` | work + support |

## Tools

Every call requires an `account` slug. `accounts_list` shows the configured accounts and which have valid tokens.

### Gmail

| Tool | What it does |
|---|---|
| `gmail_send` | Send immediately — `to`/`cc`/`bcc`, plain or HTML, and correct **reply threading** via `thread_id` + `in_reply_to_message_id`. |
| `gmail_draft_create` / `gmail_draft_update` | Create a draft / overwrite its contents. |
| `gmail_draft_send` / `gmail_draft_delete` | Send a draft / permanently delete one (the thing the default connector can't do). |
| `gmail_drafts_list` | List drafts, with Gmail query syntax. |
| `gmail_search` | Search messages (`from:foo subject:bar`), returns sender/subject/date metadata in one round trip. |
| `gmail_message_get` / `gmail_thread_get` | Fetch one message (with decoded plain-text body) / a whole thread. |
| `gmail_message_modify` | Add/remove labels (e.g. mark read by removing `UNREAD`). |
| `gmail_message_trash` | Move to Trash (reversible for 30 days). |
| `gmail_labels_list` | List all labels. |
| `gmail_label_create` | Create a label (nested via `Parent/Child` names). Idempotent — an existing label is returned as-is. |
| `gmail_attachment_download` | Download one attachment to a local path (`attachment_id` from a `format=full` message). |

### Calendar

| Tool | What it does |
|---|---|
| `calendar_list` | All calendars the account can access. |
| `calendar_events_list` | Events in a time range, with search and recurring-event expansion. |
| `calendar_event_get` | Fetch one event. |
| `calendar_event_create` | Timed or all-day events — attendees, location, timezone, invitation emails (`send_updates`), optional **Google Meet** link. |
| `calendar_event_update` | Patch only the fields you pass. |
| `calendar_event_delete` | Delete an event. |

### Drive

| Tool | What it does |
|---|---|
| `drive_search` | List/search with the Drive query language; shared drives included. |
| `drive_file_get` | Full metadata for one file. |
| `drive_file_download` | Download any file; **exports** Google Docs/Sheets/Slides to e.g. PDF or CSV. |
| `drive_file_upload` | Upload a local file; optionally **convert** `.docx`/`.xlsx`/`.pptx` to native Google formats. |
| `drive_file_move` / `drive_file_rename` | Move between folders / rename. |
| `drive_file_trash` | Move to trash (reversible). |
| `drive_folder_create` | Create a folder. |
| `drive_file_share` | Share with someone by email — role from `reader` to `organizer`, optional notification message. |

### Tasks

| Tool | What it does |
|---|---|
| `tasklist_list` / `tasklist_create` / `tasklist_delete` | Manage task lists. |
| `task_list` / `task_get` | List tasks (incl. completed/hidden) / fetch one. |
| `task_create` | New task — notes, due date, as a subtask (`parent`), ordered (`previous`). |
| `task_update` / `task_complete` | Patch fields / mark completed. |
| `task_move` | Reposition: under a parent and/or after a sibling. |
| `task_delete` | Delete a task. |

## Configuration & storage

Everything lives under the config home — `$GWM_HOME`, else `$XDG_CONFIG_HOME/google-workspace-mcp`, else `~/.config/google-workspace-mcp/`:

| What | Path | Override |
|---|---|---|
| OAuth client secret | `<config-home>/credentials.json` | `$GWM_CREDENTIALS` |
| Account registry | `<config-home>/accounts.json` | `$GWM_ACCOUNTS_FILE` |
| Per-account tokens | `<config-home>/tokens/<slug>.json` | `$GWM_TOKENS_DIR` |
| Per-instance account scope | — | `$GWM_ACCOUNTS` |

Token files are written `0600`.

## Scopes

Broad on purpose — these are your own accounts; narrower scopes would force a re-auth every time a tool is added:

- `https://mail.google.com/`
- `https://www.googleapis.com/auth/calendar`
- `https://www.googleapis.com/auth/drive`
- `https://www.googleapis.com/auth/tasks`

> Adding a scope (as v0.3.0 did for Tasks) requires re-running `google-workspace-authorize <slug>` for each account.

## Trust & security

- **Local only.** The server speaks stdio to Claude Code and talks only to Google's APIs. The one listener it ever opens is a temporary `127.0.0.1` redirect during `google-workspace-authorize`, which closes as soon as consent lands.
- **Your client, your tokens.** The OAuth client and tokens stay on your machine, outside this repo. Never commit `credentials.json` or `tokens/` (the bundled [`.gitignore`](.gitignore) refuses both).
- **Nothing shared between users.** Each teammate runs their own OAuth client and authorizes their own accounts.
- **Treat the config dir as a secret store.** Tokens grant broad access to your mail/calendar/drive/tasks — `~/.config/google-workspace-mcp/` deserves the same care as `~/.ssh/`.
- An MCP server that can send email and share files deserves review before you enable it — the whole surface is ~1,300 lines of Python in [`src/google_workspace_mcp/`](src/google_workspace_mcp/). See [SECURITY.md](SECURITY.md) to report a vulnerability.

## Repository layout

```text
google-workspace-mcp/
├── .claude-plugin/
│   ├── plugin.json                  # the plugin manifest
│   └── marketplace.json             # single-repo marketplace (source: "./")
├── commands/
│   └── google-workspace-setup.md    # /google-workspace-setup — guided setup
├── src/google_workspace_mcp/
│   ├── server.py                    # the MCP server — all 38 tools
│   ├── auth.py                      # token load/refresh + Google service builders
│   ├── accounts.py                  # runtime account registry + GWM_ACCOUNTS scoping
│   └── authorize.py                 # standalone OAuth consent flow (CLI)
├── accounts.example.json            # template for ~/.config/google-workspace-mcp/accounts.json
├── pyproject.toml                   # uv/hatchling package — the two console scripts
└── .github/workflows/ci.yml         # lint, import smoke test, manifest validation
```

## Development

```bash
git clone https://github.com/rajool/google-workspace-mcp
cd google-workspace-mcp
uv sync                                   # create .venv with locked deps
claude --plugin-dir .                     # load the plugin without installing

# Quality gates (CI runs the same — see .github/workflows/ci.yml)
uvx ruff check .                          # lint
uv run python -m py_compile src/google_workspace_mcp/*.py
uv run python -c "from google_workspace_mcp import server"   # import smoke test
claude plugin validate . --strict         # validate plugin + marketplace manifests
```

A user-facing change bumps `version` in [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json), [`pyproject.toml`](pyproject.toml), and `src/google_workspace_mcp/__init__.py`, and adds a [`CHANGELOG.md`](CHANGELOG.md) entry — installed projects pick it up on `/plugin marketplace update`.

## Contributing

Contributions are welcome. The repo is **public, generic, and English-only** — no personal emails, real account slugs, home paths, or secrets. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup and checks, and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community expectations.

## License

[MIT](LICENSE) © Ali Rajool. Part of the same toolkit family as [`yar`](https://github.com/rajool/yar) and [`boote`](https://github.com/hengam-io/boote).
