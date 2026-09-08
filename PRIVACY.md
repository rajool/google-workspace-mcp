# Privacy Policy

**Project:** google-workspace-mcp
**Last updated:** 2 September 2026

google-workspace-mcp is free, open-source software that you run on your own computer. It connects [Claude Code](https://claude.com/claude-code) to your own Google accounts (Gmail, Calendar, Drive and Tasks) through Google's official APIs.

There is no service behind it. There is no server we operate, no account to create, and no company collecting anything. This document explains what that means in practice.

## We do not collect your data

The maintainers of this project receive **no data from you at all** — not your email, not your files, not your calendar, not usage statistics, not crash reports, not your OAuth tokens.

The software contains no analytics, no telemetry, and no third-party reporting of any kind.

## Where your data actually goes

When you run this software, data moves between exactly three places, all of which you control:

1. **Your computer** — where the server runs.
2. **Google's APIs** — reached directly over HTTPS, using the OAuth client *you* created in *your* Google Cloud project.
3. **Your Claude Code client** — the local program that asked for the data, over stdio on your own machine.

Nothing is routed through any intermediary. There is no proxy, no relay, and no hosted component.

## What the software accesses, and why

You grant access per account, and only to the scopes the tools need:

| Scope | Used for |
|---|---|
| `https://mail.google.com/` | Reading, searching, drafting, labelling and sending mail |
| `https://www.googleapis.com/auth/calendar` | Reading and managing calendar events |
| `https://www.googleapis.com/auth/drive` | Searching, reading, uploading, moving and sharing files |
| `https://www.googleapis.com/auth/tasks` | Reading and managing task lists |

Data is fetched only when a tool call asks for it, held in memory long enough to answer that request, and then discarded. Nothing is copied into a database or cache by this software.

## Credentials and tokens

- Your OAuth client (`credentials.json`) and your refresh tokens (`tokens/<account>.json`) are stored **only** on your own machine, by default under `~/.config/google-workspace-mcp/`.
- Token files are written with `0600` permissions (owner read/write only).
- These files are never transmitted anywhere, and the repository's `.gitignore` refuses to commit them.
- Treat that directory as a secret store. The tokens grant broad access to your mail, calendar, files and tasks — it deserves the same care as `~/.ssh/`.

Each person who uses this software registers their own OAuth client and authorizes their own accounts. Nothing is shared between users.

## Per-project scoping

A project can be restricted to a subset of your accounts using the `GWM_ACCOUNTS` environment variable. The server refuses any account slug that is not on that list, so a given project cannot act as an account you did not grant it.

## Revoking access

You can withdraw access at any time, and you do not need us to do it:

- Revoke the app at [Google Account → Third-party access](https://myaccount.google.com/connections), and/or
- Delete the token file for that account from `~/.config/google-workspace-mcp/tokens/`.

Access stops immediately. Because we never held your data, there is nothing for us to delete.

## Children

This is a developer tool and is not directed at children.

## Google API Services User Data Policy

Use of information received from Google APIs adheres to the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements.

## Changes

Any change to this policy will be committed to this repository, so the full history is public and auditable in git.

## Contact

Questions or security reports: open an issue at
<https://github.com/rajool/google-workspace-mcp/issues>, or see [SECURITY.md](SECURITY.md) for how to report a vulnerability privately.
