# Contributing to google-workspace-mcp

Thanks for contributing! google-workspace-mcp is a public, reusable Claude Code
plugin and MCP server. This guide covers how to set up, make a change, run the
checks, and the repo-wide rules that keep it shareable.

## Development setup

```bash
git clone https://github.com/rajool/google-workspace-mcp
cd google-workspace-mcp
uv sync                      # create .venv with the locked dependencies
claude --plugin-dir .        # load the plugin in Claude Code without installing it
```

To run the server from the checkout, point a project's `.mcp.json` at it:

```json
{
  "mcpServers": {
    "google-workspace": {
      "command": "uv",
      "args": ["--directory", "/path/to/google-workspace-mcp", "run", "google-workspace-mcp"],
      "env": { "GWM_ACCOUNTS": "personal" }
    }
  }
}
```

You'll need your own OAuth client and at least one authorized account to
exercise the tools end to end — see the [README setup](README.md#setup).
**Never commit `credentials.json`, `accounts.json` contents, or anything from
`tokens/`.**

## Making a change

- Work on a short-lived branch, keep `main` clean, and merge via pull request.
- **Commits** follow [Conventional Commits](https://www.conventionalcommits.org/):
  `type(scope): summary`, where `type` is one of `feat fix docs chore refactor`.
- **User-facing changes** bump `version` in three places —
  [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json),
  [`pyproject.toml`](pyproject.toml), and
  `src/google_workspace_mcp/__init__.py` — run `uv lock`, and add an entry to
  [`CHANGELOG.md`](CHANGELOG.md) (Keep a Changelog format).
- **Adding a tool?** Keep the pattern: an `account: AccountSlug` first
  parameter, a one-line docstring that tells the model when to use it, and a
  trimmed return shape (see `_msg_summary` / `_task_summary`). Update the
  catalog table in [`README.md`](README.md). If the tool needs a new OAuth
  scope, add it to `SCOPES` in `auth.py` and call out the required re-auth in
  the changelog.

## Running the checks

These mirror [`.github/workflows/ci.yml`](.github/workflows/ci.yml):

```bash
uvx ruff check .                                             # lint
uv run python -m py_compile src/google_workspace_mcp/*.py    # byte-compile
uv run python -c "from google_workspace_mcp import server, authorize"
claude plugin validate . --strict                            # manifests
```

The server's runtime dependencies are only the Google API client libraries and
the `mcp` SDK — keep it that way. Ruff is a development/CI tool only.

## Repo-wide rules

Everything here must be **generic, public, and English-only**. Do not commit
content tied to a specific person, company, or machine:

- personal email addresses (placeholders like `you@example.com` are fine)
- real account slugs or registry contents
- absolute home paths (write `~/.config/...`, not a real user's directory)
- secrets: OAuth client JSON, refresh tokens, API keys

## Code of Conduct

By participating, you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md).
