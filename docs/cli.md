# CLI Reference

Run `uv run maf-e2e --help` or `uv run maf-e2e <command> --help` for the authoritative argument list.

## Asset lifecycle

### `author`

Explores a target application, generates structured specifications and TypeScript, validates each draft, and executes the generated code with the standard Playwright runner.

```bash
uv run maf-e2e author \
  --target-repo /path/to/web-app \
  --target-url https://staging.example.com \
  --objective "A registered user can sign in" \
  --expected-result "The dashboard is visible" \
  --precondition "A test user exists" \
  --allowed-origin https://staging.example.com
```

`--expected-result`, `--precondition`, `--policy`, `--prohibited-action`, and `--allowed-origin` are repeatable. `--test-data` accepts a JSON object. Successful authoring exits `0` with `pending_approval`; a blocked workflow exits `3`.

### `review`

Prints JSON containing asset metadata, the structured specification, generated source, and trial result.

```bash
uv run maf-e2e review --target-repo /path/to/web-app
uv run maf-e2e review --target-repo /path/to/web-app --scenario-id login-page
```

### `approve`, `request-changes`, and `reject`

```bash
uv run maf-e2e approve \
  --target-repo /path/to/web-app \
  --scenario-id login-page \
  --reviewer reviewer@example.com \
  --comment "Specification and evidence reviewed"
```

Replace `approve` with `request-changes` or `reject`. Approval requires a successful trial whose source hash still matches the draft. Reject moves the draft to `.maf-e2e/rejected`.

### `publish`

```bash
uv run maf-e2e publish \
  --target-repo /path/to/web-app \
  --scenario-id login-page
```

Publish requires the latest review action to be `approve`. It recalculates the specification and code hashes and exits `4` for a configuration, path, or approval-integrity error.

## Regression

```bash
uv run maf-e2e regression \
  --target-repo /path/to/web-app \
  --environment staging
```

Use repeatable `--scenario-id` options to select scenarios. Only metadata with lifecycle status `ACTIVE` is executed.

Regression exit codes:

| Code | Meaning |
|---|---|
| `0` | All selected scenarios passed |
| `1` | At least one scenario failed |
| `3` | At least one scenario was blocked or timed out |
| `4` | Configuration or stored-asset error |

The command does not construct Agent settings and therefore does not require provider credentials.

## Failure analysis

Classify existing trial evidence without an Agent:

```bash
uv run maf-e2e analyze-failure \
  --trial-result /path/to/trial-result.json \
  --previous-passed \
  --diagnostic "The submit button locator no longer resolves"
```

Run a new Agent-assisted UI investigation:

```bash
uv run maf-e2e analyze-failure \
  --trial-result /path/to/trial-result.json \
  --investigate \
  --target-url https://staging.example.com \
  --allowed-origin https://staging.example.com
```

The result is saved as `failure-analysis.json` beside the trial result. Agent-assisted mode also saves `regression-diagnostic.json`. Exit `2` identifies `test_maintenance`; other classifications exit `1`.

Failure categories are `application_defect`, `test_maintenance`, `environment_failure`, `authentication_failure`, `test_data_failure`, `flaky_failure`, and `unknown`.

## Repair

Repair is accepted only for `test_maintenance`. Supply either reviewed TypeScript or a diagnostic containing locator replacements:

```bash
uv run maf-e2e repair \
  --target-repo /path/to/web-app \
  --scenario-id login-page \
  --analysis /path/to/failure-analysis.json \
  --diagnostic /path/to/regression-diagnostic.json
```

To create a branch, commit, push, and GitHub pull request:

```bash
uv run maf-e2e repair \
  --target-repo /path/to/web-app \
  --scenario-id login-page \
  --analysis /path/to/failure-analysis.json \
  --proposed-code /path/to/reviewed.spec.ts \
  --create-pr \
  --base-branch main
```

The GitHub CLI must be installed and authenticated. A failed repair trial exits `2`; configuration and publishing errors exit `4`. Pull requests are never merged automatically.

## Legacy one-shot mode

Calling `maf-e2e` without a subcommand runs the original autonomous browser workflow:

```bash
uv run maf-e2e \
  --target-url https://example.com \
  --objective "Validate the primary journey" \
  --policy "No uncaught console errors"
```

This mode uses Playwright MCP directly through the Browser Executor. Its result cannot be approved or published as a regression asset. Resume a saved run with `--resume-run-id` and optional `--checkpoint-id`.
