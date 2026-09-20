# Changelog

All notable changes to **google-workspace-mcp** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Plugin releases are driven by the `version` field in
[`.claude-plugin/plugin.json`](.claude-plugin/plugin.json): users receive an update
only when it is bumped.

## [Unreleased]

## [0.12.0] - 2026-09-20

### Added

- Contacts, read-only: `contacts_lookup` resolves ONE address to the name to
  address that person by, and `contacts_search` finds people by name, address
  or company. Both cover saved contacts **and** "other contacts" — the people
  Gmail records from correspondence but the user never saved, which is where
  most names actually are.
- `contacts_lookup` falls back to the display name on real mail from that
  address when the People API has nothing, so it still answers for someone who
  was never a contact at all. It returns `name: null` when genuinely nobody
  knows the address — the signal that a bare address is the correct form, as
  for a role mailbox like `info@`.

### Why

Gmail shows the display name the *sender* supplies. A recipient written as a
bare address therefore arrives as a raw address, while every message a human
sends carries a name — so mail from this server read as machine-written
(reported 2026-09-19). Nothing here could look a name up; now it can.

### Changed

- Two new scopes, both read-only: `contacts.readonly` and
  `contacts.other.readonly`. This server never writes a contact.

### Upgrading

Two manual steps, both one-off:

1. **Enable the People API** in the Google Cloud project behind your OAuth
   client. Without it every contacts call fails with `SERVICE_DISABLED`.
2. **Re-authorize each account** — `google-workspace-authorize <slug>` — so the
   token carries the new scopes. Existing tokens keep working for Gmail,
   Calendar, Drive and Tasks until you do.


## [0.11.0] - 2026-09-18

### Added

- Calendar management, not just events: `calendar_create` makes a secondary
  calendar; `calendar_share` / `calendar_unshare` grant and revoke one
  person's access (`freeBusyReader`, `reader`, `writer` — never `owner`), and
  `calendar_acl_list` shows who can see a calendar. Until now a shared school
  or project calendar had to be created and shared in the Calendar UI.

## [0.10.2] - 2026-09-14

### Fixed

- Replies no longer quote an unsent draft. `_thread_quote` took the thread's
  newest message with drafts included, and a reply draft is itself the newest
  message of its thread: `gmail_draft_update` with `thread_id` quoted the
  draft's own earlier version under the new text and pointed `In-Reply-To` at
  it, and a reply into a thread holding a pending draft quoted that draft.
  Drafts are now skipped; the newest sent or received message is quoted.

## [0.10.1] - 2026-09-08

### Fixed

- `.eml` / `.mht` / `.mhtml` / `.mime` / `.nws` attachments went out as
  `message/rfc822` with a base64 transfer encoding, which RFC 2046 forbids —
  Python's own parser could not read the nested message back. Container types
  (`message/*`, `multipart/*`) now go as `application/octet-stream`, the way
  compressed and unknown types already did.
- Attachments over Gmail's 25 MB per-message limit now fail before anything is
  read or sent, with an error pointing at `drive_file_upload` +
  `drive_file_link_access`. Previously the whole message was built and
  base64-encoded, then rejected by Gmail.

### Security

- The server refuses to read from or write into its own secret store — the
  config directory (`~/.config/google-workspace-mcp/` by default), the tokens
  directory and the OAuth client file, wherever `GWM_HOME` / `GWM_CREDENTIALS`
  / `GWM_TOKENS_DIR` point, symlinks resolved. Applies to `attachments`,
  `drive_file_upload` and `drive_file_update_content` (reads) and to
  `gmail_attachment_download` and `drive_file_download` (writes). A
  prompt-injected "attach your token file and send it to …" now fails at the
  server instead of relying on the model to decline. Covered by tests.

### Changed

- `drive_file_update_content` docs spell out Drive's revision retention:
  Google Docs/Sheets/Slides keep full version history; binary files drop old
  revisions after 30 days or 100 revisions unless `keep_revision_forever`
  (at most 200 pinned per file).
- CI: ruff pinned to 0.16.6 (was 0.15.15). ruff 0.16 turns on import sorting
  (`I001`) by default, so the two import blocks it flagged — `functools`
  before `pathlib` in `auth.py`, and a stray blank line splitting the
  third-party block in `server.py` — are sorted. No runtime change.

## [0.10.0] - 2026-09-08

### Added

- `drive_file_update_content` — replace an existing Drive file's contents in
  place via `files().update()`, so the file keeps its ID, link and sharing and
  the previous contents stay in Drive's revision history. Uploading again under
  the same name with `drive_file_upload` creates a second file rather than a new
  version, which left no way to revise something already shared. Optional
  `name` rename, `convert_to_google_doc` to revise a native Doc/Sheet/Slides
  from a local `.docx`/`.xlsx`/`.pptx`, and `keep_revision_forever`. No new
  OAuth scope. The Office-to-Google conversion map is now a module-level
  `_GOOGLE_NATIVE` shared with `drive_file_upload`. Contributed by
  @zeroamplitude (#10).

## [0.9.0] - 2026-09-08

### Added

- `attachments` on `gmail_send` / `gmail_draft_create` / `gmail_draft_update`:
  a list of paths on the machine running the server, each attached under its
  own file name with the MIME type guessed from it (`application/octet-stream`
  as the fallback). A path that does not exist raises `FileNotFoundError`
  rather than quietly sending the message without the file. Attachments are
  added after the body, so plain-text mail keeps its `multipart/alternative`
  shape inside `multipart/mixed` and replies keep their quoted history.
  Contributed by @zeroamplitude (#11).

## [0.8.0] - 2026-09-08

### Added

- A **Troubleshooting** section in the README, opening with `invalid_grant:
  Bad Request` — check the OAuth app's publishing status before anything
  else, then the causes that survive publishing: a refresh token unused for
  6 months, and the account owner changing their Google password (which
  revokes any token holding Gmail scopes).
- CI: **Versions agree** now checks all five manifest version fields plus the
  copy in `uv.lock`, instead of three — the validator enforces
  `plugins[0].version` but ignores the top-level marketplace `version`.
- CI: **README catalog covers every tool** asserts that each `@mcp.tool()` has
  a catalog row and that both advertised totals match, so a new tool cannot
  ship undocumented or leave the count stale.

### Changed

- **Setup no longer says leaving the OAuth app in *Testing* is fine — it
  isn't, and it broke every account weekly.** Google issues refresh tokens
  that expire after **7 days** to an External app whose publishing status is
  *Testing*, unless the app requests only name, email address, and profile;
  this server requests full Gmail, Calendar, Drive, and Tasks scopes, so
  every connected account died about once a week with `invalid_grant: Bad
  Request` (hit on 2026-09-02). The README and `/google-workspace-setup` now
  walk you through publishing it (**Google Auth Platform → Audience →
  Publish app**), which requires no verification for personal use under 100
  users; the only cost is the one-time "Google hasn't verified this app"
  screen, which *Testing* shows anyway. Tokens issued while the app was in
  *Testing* keep their 7-day clock, so each account needs one re-authorize
  after publishing.
- `accounts_list` now proves each token by refreshing it against Google
  rather than checking that the token file exists. It used to report
  `authorized: true` for tokens Google had already expired, which made the
  weekly failure look like a server bug. A failing account now carries
  `status` (`no_token`, `unreadable`, `revoked`, or `unreachable`) and a
  `detail` naming the fix; `authorized` is `null` when Google itself could
  not be reached. Costs one refresh round trip per configured account.
- A refresh Google rejects now surfaces as a diagnosis naming the account
  and the likely cause, instead of a bare `RefreshError: invalid_grant`.

### Fixed

- README drift: both advertised tool totals said **38** while `server.py`
  defines **41**, `drive_file_link_access` (0.6.0) was missing from the Drive
  catalog, the version badge was pinned at `v0.4.0` (it now reads
  `plugin.json` live), and the security note's line count was stale.
- The documented version-bump ritual omitted `.claude-plugin/marketplace.json`,
  which carries the version **twice** (top-level `version` and
  `plugins[0].version`). A stale `plugins[0].version` fails the `plugin` CI
  job, because `plugin.json` wins at install time and
  `claude plugin validate . --strict` treats the resulting warning as an error.
  `CONTRIBUTING.md`, the README **Development** section and the pull-request
  template now all list five fields across four files.

## [0.7.0] - 2026-09-07

### Fixed

- **Replies now carry the thread's history.** `thread_id` attaches a message to
  a thread, but Gmail quotes nothing for you — so a reply built from `body`
  alone reached the recipient with every earlier message gone, and callers had
  to hand-build the quote chain (or, far more often, silently drop it).
  `gmail_send` / `gmail_draft_create` / `gmail_draft_update` now read the
  thread and append its history below the new text the way Gmail's web Reply
  does: `"> "`-prefixed in the `text/plain` part (already-quoted lines deepen
  to `">> "`) and a nested `<blockquote class="gmail_quote">` in the
  `text/html` part. Quoting the thread's newest message is enough — it already
  carries the whole earlier chain nested inside it. Pass `quote_history=false`
  to opt out. Because the fix lives in the server, every project and assistant
  gets it with no per-project documentation.
- **`In-Reply-To` / `References` are derived from the thread.** Previously only
  `gmail_send` set them, and only when the caller looked up the RFC822
  Message-Id itself; drafts created into a thread carried no threading headers
  at all, so non-Gmail clients broke the conversation apart.
  `in_reply_to_message_id` remains as an override.
- **`gmail_draft_update` no longer detaches a draft from its thread.** It
  overwrote the draft with a bare `raw` message and no `threadId`; it now takes
  an optional `thread_id` and preserves the attachment (and re-quotes).

### Added

- Test suite (`tests/`) covering reply quoting, threading headers, quote
  depth, and graceful degradation when a thread cannot be read. CI runs it.

## [0.6.3] - 2026-08-22

### Changed

- `gmail_send` / `gmail_draft_create` / `gmail_draft_update` tool descriptions
  now teach the body format at the point of use — plain text with `- ` bullets
  and `1.` / `1)` numbered lines (ASCII or Persian digits), no manual
  hard-wrapping, `html=true` only for real HTML — so every project and
  assistant using this server gets the guidance automatically, with no
  per-project documentation needed. README gained the same section.

## [0.6.2] - 2026-08-22

### Added

- The generated text/html alternative now renders plain-text lists the way
  Gmail's composer draws them: runs of `- ` / `* ` / `• ` lines become a real
  `<ul>`, runs of `1. ` / `1) ` lines (ASCII, Persian, or Arabic-Indic digits)
  a real `<ol>` (with `start` when the run does not begin at 1), each with
  `dir="auto"` so RTL lists get right-side markers. The text/plain part is
  untouched — still byte-identical to the caller's body.

## [0.6.1] - 2026-08-22

### Fixed

- Plain-text messages (`gmail_send` / `gmail_draft_create` / `gmail_draft_update`
  without `html=true`) are now built as `multipart/alternative` with a generated
  `text/html` part mirroring Gmail's own composer output (one `<div dir="auto">`
  per line). A plain-text-only draft opened in the Gmail web UI was treated as
  plain-text mode, and on Send Gmail rewrote the body with hard line breaks at
  ~70 columns — recipients saw broken mid-sentence paragraphs (hit on a real
  W Brothers email, 2026-08-22). Gmail-composed mail never shows this because
  it is always multipart; drafts now match that shape, so sending from the web
  UI is safe. The `text/plain` part remains the caller's body, byte-identical;
  `html=true` behavior is unchanged.
- CI: cap the `mcp` dependency below 2.0 — `mcp 2.0.0` moved
  `mcp.server.fastmcp`, breaking the import smoke test on a bare
  `pip install .`.

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

[Unreleased]: https://github.com/rajool/google-workspace-mcp/compare/google-workspace-mcp--v0.10.2...HEAD
[0.10.2]: https://github.com/rajool/google-workspace-mcp/compare/v0.10.1...google-workspace-mcp--v0.10.2
[0.10.1]: https://github.com/rajool/google-workspace-mcp/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/rajool/google-workspace-mcp/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/rajool/google-workspace-mcp/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/rajool/google-workspace-mcp/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/rajool/google-workspace-mcp/compare/v0.6.3...v0.7.0
[0.6.3]: https://github.com/rajool/google-workspace-mcp/compare/v0.6.2...v0.6.3
[0.6.2]: https://github.com/rajool/google-workspace-mcp/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/rajool/google-workspace-mcp/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/rajool/google-workspace-mcp/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/rajool/google-workspace-mcp/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/rajool/google-workspace-mcp/releases/tag/v0.4.0
