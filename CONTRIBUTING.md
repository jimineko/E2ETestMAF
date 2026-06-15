# Contributing to E2ETestMAF

E2ETestMAF is experimental. Before starting a substantial change, open an issue to confirm the intended behavior and compatibility constraints.

## Development setup

```bash
uv python install 3.13
uv sync --all-extras --group dev
npm ci
npx playwright install chrome chromium
```

## Quality checks

Run all three checks before submitting a pull request:

```bash
uv run ruff check .
uv run mypy
uv run pytest
```

Linux KVM and RAMPART integration tests are intentionally separate from the default suite. See [docs/development.md](docs/development.md) for their prerequisites and commands.

## Pull requests

- Keep changes focused and include tests for behavior changes.
- Preserve the distinction between Agent-assisted discovery and deterministic Playwright execution.
- Do not weaken origin restrictions, path validation, approval hash checks, or repair semantic guards.
- Update both `README.md` and `README.ja.md` when changing the user-facing workflow.
- Do not commit credentials, `auth/user.json`, generated artifacts, or target-application drafts.

## License

This repository does not currently grant an open-source license. Contributions cannot be accepted under an open-source contribution model until the repository owner adds a license and contribution terms.
