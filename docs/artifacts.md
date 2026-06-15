# Artifacts and Asset Lifecycle

E2ETestMAF keeps authoring drafts, approved assets, and execution evidence separate.

## Draft workspace

Each target repository receives a private workspace:

```text
.maf-e2e/
  drafts/<scenario-id>/
    specification.yaml
    generated.spec.ts
    metadata.json
    validation-result.json
    trial-result.json
    artifacts/<trial-run-id>/
  rejected/
  expired/
  regression/<regression-run-id>/
```

Writes to draft metadata are atomic. Scenario identifiers and filenames are validated so they cannot escape the target repository workspace.

Trial artifact directories can contain:

- Playwright JSON report (`report.json`)
- JUnit report (`junit.xml`)
- HTML report
- screenshots
- `trace.zip`
- test result attachments
- collected console and network errors

## Approval identity

The specification hash is SHA-256 over normalized model JSON. The code hash is SHA-256 over the UTF-8 TypeScript source after removing the informational `// generated_at:` header line. This keeps approvals deterministic while still recording generation time in the source header and asset metadata.

Approval records include the scenario ID, specification and code versions, action, reviewer, timestamp, and both hashes. Approval is invalidated when either reviewed input changes.

## Published layout

`publish` writes only beneath the target repository's `e2e` directory:

```text
e2e/
  generated/<feature>/<scenario-id>.spec.ts
  specs/<feature>/<scenario-id>.v<version>.yaml
  metadata/<feature>/<scenario-id>.json
```

The metadata status becomes `ACTIVE`. The publisher recalculates both hashes immediately before writing and rejects path traversal or repository-external paths.

## Rejection and retention

`reject` removes the scenario from the active draft location and moves its audit record to `.maf-e2e/rejected/<scenario-id>-<timestamp>/`. Draft-retention processing moves expired, non-active drafts to `.maf-e2e/expired/`.

## Regression artifacts

Each regression run is written to `.maf-e2e/regression/<run-id>/`. It contains one artifact tree per scenario and `regression.json` with the repository revision, environment, timestamps, and scenario outcomes.

The legacy one-shot workflow stores its run output under `artifacts/<run-id>/` and checkpoints under `checkpoints/<run-id>/`. When `MAF_E2E_BLOB_ACCOUNT_URL` is configured, legacy artifact bundles and RAMPART reports can also be uploaded to Azure Blob Storage.
