# Legacy Workflow and Migration

## Current primary workflow

New regression assets must use the subcommand lifecycle:

```text
author -> review -> approve -> publish -> regression
```

This path generates TypeScript, runs the exact code with the standard Playwright runner, binds approval to hashes, and executes active assets without an Agent.

## Legacy one-shot workflow

Calling `maf-e2e` without a subcommand retains the original autonomous workflow for investigation and one-off QA:

```text
Orchestrator -> Discovery -> Generator -> Browser via MCP
                                      -> Judge
                                      -> Safety
                                      -> retry / complete / escalate
```

The Browser Executor directly uses Playwright MCP. Results from this path cannot be approved or published as regression assets.

Resume a saved legacy browser stage with:

```bash
uv run maf-e2e --resume-run-id RUN_ID
uv run maf-e2e --resume-run-id RUN_ID --checkpoint-id CHECKPOINT_ID
```

Resumption reloads the saved plan but creates a new MCP connection and browser sandbox.

## Removed compatibility

The old `maf_qa` Python API, `maf-qa` CLI, and `MAF_QA_*` environment variables are not supported. Saved `autonomous-web-qa-v2` checkpoints cannot be resumed. Run the request again using the `maf_e2e` package, `maf-e2e` CLI, and `MAF_E2E_*` environment variables.

The current PyPI dependency family is `agent-framework-*`; it is not named `microsoft-agent-framework`.
