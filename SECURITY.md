# Security Policy

`google-workspace-mcp` is a local MCP server that holds OAuth refresh tokens
for the user's own Google accounts and can read and send mail, manage
calendars, move and share Drive files, and manage tasks on their behalf.
Because of that reach, security reports are taken seriously.

## Supported versions

Only the latest released version receives security fixes. The plugin is
distributed through its own marketplace, so a fix ships in the next version
bump.

| Version        | Supported          |
| -------------- | ------------------ |
| 0.x (latest)   | :white_check_mark: |
| older releases | :x:                |

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability):

1. Open the repository's **Security** tab.
2. Click **Report a vulnerability**.
3. Describe the issue, the affected component, and a reproduction if you have one.

You can expect an initial response within a few days. When a fix is ready it is
released in a new version and noted in [`CHANGELOG.md`](CHANGELOG.md).

## Scope

The areas most worth scrutiny:

- **Account scoping** (`accounts.py`) — any way a server instance can act as an
  account outside its `GWM_ACCOUNTS` allowlist would defeat the per-project
  isolation model.
- **Token handling** (`auth.py`) — token files are written `0600` under the
  config home and refreshed in place; anything that widens permissions, logs a
  token, or writes one elsewhere is a bug.
- **The OAuth flow** (`authorize.py`) — a localhost (`127.0.0.1`) HTTP listener
  that exists only for the duration of one consent redirect; anything that
  binds wider, lingers, or leaks the authorization code matters.
- **Outbound actions** (`server.py`) — tools that send mail, share files, or
  email invitations act as the user; header handling and recipient handling
  deserve care.

## Trust model

The server runs locally over stdio and talks only to Google's APIs with the
user's own OAuth client. There is no telemetry and no third-party service in
the path. The broad OAuth scopes are a deliberate, documented trade-off
([README — Scopes](README.md#scopes)): the tokens belong to the user's own
accounts, and the config directory should be treated like any other secret
store. The model driving the tools is the same one the user is already
trusting with their session — the security boundary is the machine and the
config home, not the server.
