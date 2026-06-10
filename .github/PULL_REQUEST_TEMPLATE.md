<!-- Thanks for contributing! Keep changes generic, public, and English-only. -->

## What & why

<!-- What does this change, and why? Link any issue, e.g. "Closes #123". -->

## Type of change

- [ ] `feat` — new tool / capability
- [ ] `fix` — bug fix
- [ ] `docs` — documentation only
- [ ] `refactor` / `chore` — no user-facing behavior change

## Checklist

- [ ] `uvx ruff check .` is clean
- [ ] `claude plugin validate . --strict` passes
- [ ] Content is generic, public, and English-only (no secrets, tokens, real account slugs, or personal data)
- [ ] Bumped `version` in `.claude-plugin/plugin.json`, `pyproject.toml`, and `__init__.py`, ran `uv lock`, and updated `CHANGELOG.md` (for user-facing changes)
- [ ] New tools follow the house pattern (`account` slug first, tight docstring, trimmed return shape) and are listed in `README.md`
- [ ] Commits follow [Conventional Commits](https://www.conventionalcommits.org/)
