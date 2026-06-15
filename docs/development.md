# Development Guide

## Local setup

The repository uses uv rather than a hand-managed `venv` and `pip` workflow.

```bash
uv python install 3.13
uv sync --all-extras --group dev
npm ci
npx playwright install chrome chromium
```

Use `uv sync --group dev` instead of `--all-extras` when optional DevUI, subscription CLI, Hyperlight runtime, and RAMPART dependencies are not needed.

## Quality checks

```bash
uv run ruff check .
uv run mypy
uv run pytest
```

These checks do not require browser installation, Azure authentication, or external model calls. Integration and subscription tests are skipped unless their explicit environment gates are enabled.

## Dependency and build tasks

```bash
uv lock --upgrade
uv build
```

Python dependencies are declared in `pyproject.toml` and locked in `uv.lock`. JavaScript runtime dependencies are declared in `package.json` and locked in `package-lock.json`.

Agent Framework Hyperlight support is beta, RAMPART is alpha, and the Agent Skills and harness APIs are experimental. Dependency upgrades must pass the default suite, Linux KVM integration tests, and RAMPART tests before adoption.

## Integration tests

Hyperlight integration requires Linux x86_64 with KVM and the managed fixture URL:

```bash
MAF_E2E_RUN_HYPERLIGHT_INTEGRATION=1 \
MAF_E2E_CODEACT_MODE=required \
MAF_E2E_RAMPART_TARGET_URL=http://127.0.0.1:8765 \
uv run pytest tests/test_hyperlight_integration.py -v
```

RAMPART tests use the same controlled fixture:

```bash
MAF_E2E_CODEACT_MODE=required \
MAF_E2E_RAMPART_TARGET_URL=http://127.0.0.1:8765 \
uv run pytest tests/rampart -v
```

Subscription CLI integration tests consume the authenticated user's Copilot or ChatGPT quota and must be enabled intentionally.

## Documentation changes

Keep the English and Japanese READMEs aligned at the workflow and safety-guarantee level. Detailed operational material belongs under `docs/`; the top-level README should remain an entry point rather than a complete operator manual.
