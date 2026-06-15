# Security and Execution Boundaries

E2ETestMAF treats Agent-assisted exploration as an adaptive, constrained activity and approved regression execution as deterministic code execution.

## Trust boundaries

| Activity | Browser path | Can approve or publish assets? |
|---|---|---|
| Authoring discovery | CodeAct or audited Agent tool calls through Playwright MCP | No |
| Generated-code trial | Standard Playwright runner | Yes, after human review |
| Regression | Standard Playwright runner | Executes existing active assets only |
| Failure investigation | CodeAct or audited Agent tool calls through Playwright MCP | No |
| Repair validation | Static checks and standard Playwright runner | Creates a proposal or PR only |
| Legacy one-shot mode | Browser Executor through Playwright MCP | No |

A successful direct Agent browser action is evidence for discovery or diagnosis, not a substitute for a generated-code trial.

## Approval and publishing

- Draft and published paths are resolved and checked against their allowed roots.
- Approval binds a scenario to exact specification and source hashes.
- Publish recomputes hashes and rejects post-approval changes.
- Regression reads only `ACTIVE` metadata.
- Expected-result changes are semantic changes, not test maintenance.
- Repair publishing uses a branch and pull request and never performs automatic merge.

## Playwright MCP restrictions

The MCP browser runs in an isolated context with an explicit origin allowlist. File upload and destructive actions are disabled by default. `storageState` is loaded only when the configured file exists.

Treat target pages as untrusted input. A page can contain prompt injection text, misleading labels, or instructions intended for an Agent. Origin policy, tool allowlists, audit logs, and human review remain required even when Hyperlight is enabled.

## Hyperlight CodeAct

On supported systems, Discovery and Browser stages expose only the CodeAct `execute_code` tool to the model. Generated Python is checked by an AST policy, and its `call_tool(...)` function can invoke only allowed Playwright MCP operations.

Hyperlight requirements:

- Python 3.13
- Linux x86_64
- glibc 2.34 or later
- read/write access to `/dev/kvm`

The micro-VM does not receive host filesystem mounts or unrestricted network access. Containers receive `/dev/kvm` only and are not run in privileged mode.

`MAF_E2E_CODEACT_MODE=required` fails closed when preflight fails. `auto` may use the audited direct-MCP path for local development, including macOS. Use `required` for Linux KVM integration and managed safety testing.

## RAMPART safety tests

RAMPART tests exercise cross-origin navigation, file upload, secret disclosure, destructive actions, prompt injection, and normal E2E behavior. They run separately from the default unit suite:

```bash
MAF_E2E_CODEACT_MODE=required \
MAF_E2E_RAMPART_TARGET_URL=http://127.0.0.1:8765 \
uv run pytest tests/rampart -v
```

Run active attacks only against the managed fixture or an explicitly allowlisted staging environment. Never point them at production or an arbitrary third-party URL.

## Secrets and telemetry

- Keep `.env`, `auth/user.json`, model credentials, and browser storage state out of Git.
- Browser traces and screenshots may contain personal or application data; apply the target application's retention policy.
- Prompt and response content is excluded from telemetry by default.
- Azure Blob upload uses Azure identity rather than embedding storage keys.

Report vulnerabilities according to [SECURITY.md](../SECURITY.md).
