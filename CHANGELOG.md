# Changelog

All notable changes to **google-workspace-mcp** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Plugin releases are driven by the `version` field in
[`.claude-plugin/plugin.json`](.claude-plugin/plugin.json): users receive an update
only when it is bumped.

## [Unreleased]

## [0.6.0] - 2026-07-06

### Added

- `drive_file_link_access` — toggle "anyone with the link" access on a file the
  account owns (create/delete the `anyone` permission). Built for
  zero-bandwidth flows where an external API fetches a Drive file server-side
  (e.g. ElevenLabs `source_url` transcription): enable, hand off the returned
  `direct_download_url`, revoke immediately after. Driven by the Hengam
  meeting-processor's Meet recordings — 0.7–1.8GB per meeting that no longer
  needs a local download.

## [0.5.0] - 2026-07-04

Two additions to the Gmail tool surface, driven by the first real
email-triage run in the personal-assistant workspace (both gaps blocked it).

### Added

- `gmail_label_create` — create a label (nested via `Parent/Child` names,
  parents first). Idempotent: on a 409 the existing label is returned with
  `already_existed: true`.
- `gmail_attachment_download` — download one attachment to a local path,
  given the `attachment_id` from a `format=full` message payload.

## [0.4.0] - 2026-06-09

A professional overhaul of the repository. No change to the tool surface or to
how the server is configured.

### Added

- `CHANGELOG.md` — this file.
- `SECURITY.md` — private vulnerability reporting policy and component scope.
- `CODE_OF_CONDUCT.md` — Contributor Covenant 3.0.
- `CONTRIBUTING.md` — development setup, checks, and repo-wide rules.
- Continuous integration (`.github/workflows/ci.yml`): Ruff, a byte-compile pass,
  an install + import smoke test, JSON manifest checks, and
  `claude plugin validate --strict`.
- Issue forms and a pull-request template under `.github/`, plus a Dependabot
  config for GitHub Actions.
- `.editorconfig` and `.gitattributes` for consistent formatting and line endings.
- `$schema` in the plugin and marketplace manifests for editor validation.

### Changed

- Rewrote `README.md` into a scannable, badge-topped reference — install,
  setup, the full 38-tool catalog, configuration, scopes, and security notes.
- Git history squashed to a single public release commit; the version history
  lives in this changelog.
- Plugin, marketplace, and package descriptions now mention **Tasks**
  (added in 0.3.0 but never reflected in the manifests).
- `.gitignore` now also excludes `.env` and `.claude/settings.local.json`.

### Removed

- The author email in `.claude-plugin/plugin.json` — manifests now carry
  name + GitHub URL only, matching the rest of the toolkit family.

### Fixed

- Removed an unused `import os` in `server.py` (flagged by Ruff).
- A leftover personal comment in `auth.py` is now generic.

## [0.3.0] - 2026-06-09

### Added

- **Google Tasks** support — 10 new tools: `tasklist_list`, `tasklist_create`,
  `tasklist_delete`, `task_list`, `task_get`, `task_create`, `task_update`,
  `task_complete`, `task_delete`, `task_move`.
- The `https://www.googleapis.com/auth/tasks` OAuth scope. **Re-run
  `google-workspace-authorize <slug>` for each account** to pick it up.

## [0.2.0] - 2026-06-09

### Added

- Initial public release: a multi-account Google Workspace MCP server for
  Claude Code, packaged as a plugin that is also its own marketplace.
- **Gmail** (12 tools): send (with reply threading), drafts
  (create/update/send/delete/list — including draft deletion, which the default
  connector can't do), search, message/thread fetch, label management, trash.
- **Calendar** (6 tools): list calendars, list/get/create/update/delete events —
  attendees, timezones, invitation emails, optional Google Meet links.
- **Drive** (9 tools): search (shared drives included), metadata, download with
  Google-format export, upload with optional conversion to Google formats,
  move/rename/trash/share, folder creation.
- Runtime account registry (`accounts.json` / `GWM_ACCOUNTS`) with per-project
  scoping, and the `google-workspace-authorize` OAuth CLI.
- The `/google-workspace-setup` guided-setup command.

[Unreleased]: https://github.com/rajool/google-workspace-mcp/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/rajool/google-workspace-mcp/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/rajool/google-workspace-mcp/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/rajool/google-workspace-mcp/releases/tag/v0.4.0
