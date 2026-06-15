# Security Policy

## Supported versions

E2ETestMAF is currently experimental. Security fixes are applied to the latest revision of the default branch; no released version is guaranteed to receive backports.

## Reporting a vulnerability

Do not disclose vulnerabilities, credentials, prompt-injection bypasses, origin-boundary bypasses, or path-traversal issues in a public issue.

Use GitHub's private vulnerability reporting for this repository when available. Include:

- the affected revision;
- the execution mode and operating system;
- minimal reproduction steps;
- the expected and observed security boundary;
- logs with secrets removed.

If private vulnerability reporting is unavailable, contact the repository owner privately before publishing details.

## Sensitive data

Never attach model credentials, Playwright storage state, browser traces containing personal data, or full environment files to an issue. Redact secrets and application data from logs and artifacts.
