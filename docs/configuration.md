# Configuration and Agent Providers

E2ETestMAF reads `.env` and environment variables prefixed with `MAF_E2E_`. Agent settings are required by authoring and Agent-assisted investigation. They are not loaded by `regression`.

Start from the example file:

```bash
cp .env.example .env
```

## Provider matrix

| Provider | `MAF_E2E_MODEL_PROVIDER` | Authentication | Runtime |
|---|---|---|---|
| Azure OpenAI | `azure_openai` | `entra_id` or `api_key` | Local, container, Azure |
| Gemini Developer API | `gemini` | `api_key` | Local, container, Azure |
| Vertex AI | `vertex_ai` | `adc` or `api_key` | Local, container, Azure |
| GitHub Copilot CLI | `github_copilot` | `subscription` | Local only |
| Codex CLI | `codex_cli` | `subscription` | Local only |

Unsupported provider/authentication combinations fail instead of falling back to another credential type.

## Azure OpenAI

Use Entra ID after authenticating with Azure CLI, Managed Identity, or another `DefaultAzureCredential` source:

```dotenv
MAF_E2E_MODEL_PROVIDER=azure_openai
MAF_E2E_MODEL_AUTH=entra_id
MAF_E2E_AZURE_OPENAI_ENDPOINT=https://YOUR-RESOURCE.openai.azure.com
MAF_E2E_AZURE_OPENAI_DEPLOYMENT=YOUR_DEPLOYMENT
```

For API-key authentication:

```dotenv
MAF_E2E_MODEL_PROVIDER=azure_openai
MAF_E2E_MODEL_AUTH=api_key
MAF_E2E_AZURE_OPENAI_ENDPOINT=https://YOUR-RESOURCE.openai.azure.com
MAF_E2E_AZURE_OPENAI_DEPLOYMENT=YOUR_DEPLOYMENT
MAF_E2E_AZURE_OPENAI_API_KEY=YOUR_API_KEY
```

## Gemini Developer API

```dotenv
MAF_E2E_MODEL_PROVIDER=gemini
MAF_E2E_MODEL_AUTH=api_key
MAF_E2E_GEMINI_API_KEY=YOUR_API_KEY
MAF_E2E_GEMINI_MODEL=gemini-2.5-flash-lite
```

## Vertex AI

For Application Default Credentials:

```dotenv
MAF_E2E_MODEL_PROVIDER=vertex_ai
MAF_E2E_MODEL_AUTH=adc
MAF_E2E_GEMINI_MODEL=gemini-2.5-flash-lite
MAF_E2E_GEMINI_VERTEX_PROJECT=YOUR_PROJECT_ID
MAF_E2E_GEMINI_VERTEX_LOCATION=global
```

Run `gcloud auth application-default login` for local ADC. Vertex AI API-key mode uses `MAF_E2E_MODEL_AUTH=api_key` and `MAF_E2E_GEMINI_API_KEY`.

## Subscription CLI providers

Install the optional integrations:

```bash
uv sync --extra cli-providers --group dev
```

GitHub Copilot CLI uses the current OS user's Copilot login:

```dotenv
MAF_E2E_MODEL_PROVIDER=github_copilot
MAF_E2E_MODEL_AUTH=subscription
MAF_E2E_RUNTIME_ENVIRONMENT=local
MAF_E2E_GITHUB_COPILOT_CLI_PATH=copilot
MAF_E2E_GITHUB_COPILOT_TIMEOUT_SECONDS=300
```

Codex CLI reuses the current OS user's `codex login` session:

```dotenv
MAF_E2E_MODEL_PROVIDER=codex_cli
MAF_E2E_MODEL_AUTH=subscription
MAF_E2E_RUNTIME_ENVIRONMENT=local
MAF_E2E_CODEX_CLI_PATH=codex
MAF_E2E_CODEX_TIMEOUT_SECONDS=300
MAF_E2E_CODEX_MAX_TOOL_ROUNDS=8
```

These providers are rejected in Docker and Azure runtime modes and never fall back to API keys.

## Browser and authentication

Authoring and investigation use Microsoft Playwright MCP in an isolated browser context:

```dotenv
MAF_E2E_PLAYWRIGHT_BROWSER=chrome
MAF_E2E_PLAYWRIGHT_HEADLESS=true
MAF_E2E_PLAYWRIGHT_ALLOWED_ORIGINS=https://staging.example.com
MAF_E2E_CODEACT_ALLOW_FILE_UPLOAD=false
MAF_E2E_CODEACT_ALLOW_DESTRUCTIVE_ACTIONS=false
```

Place a reusable Playwright storage state at `auth/user.json`. The file is excluded from Git. If it does not exist, E2ETestMAF starts without saved authentication.

`--allowed-origin` can be repeated on `author` and Agent-assisted failure investigation. If no explicit origin is supplied, the target URL is used as the default boundary.

## CodeAct and Hyperlight

```dotenv
MAF_E2E_CODEACT_MODE=auto
MAF_E2E_CODEACT_MAX_CODE_BYTES=32768
MAF_E2E_CODEACT_MAX_INVOCATIONS=6
MAF_E2E_CODEACT_REQUIRE_KVM=true
```

Modes:

- `required`: require Hyperlight preflight and fail if it is unavailable.
- `auto`: use Hyperlight when available and otherwise use the audited direct-MCP path.
- `disabled`: use the direct-MCP path.

Hyperlight requires Linux x86_64, glibc 2.34 or later, and read/write access to `/dev/kvm`. macOS cannot run Hyperlight. See [Security and execution boundaries](security.md).

## Agent definitions and Skills

The five declarative Agent definitions live under `agents/`. Provider credentials, tools, MCP configuration, output schemas, and PowerFx expressions cannot be injected through those YAML files.

Application-specific read-only guidance can be supplied as Skill directories:

```dotenv
MAF_E2E_SKILL_PATHS=skills/sample-app,/path/to/another-skill
```

Each Skill may contain `SKILL.md` and `references/`. Skills containing `scripts/` are rejected.

## DevUI

DevUI is for local investigation only:

```bash
uv sync --extra devui --group dev
MAF_E2E_DEVUI_AUTH_TOKEN=change-me uv run maf-e2e-devui
```

It binds to loopback by default and requires a bearer token. Prompt and response bodies are not exported through OpenTelemetry unless trace content is explicitly enabled.

## Telemetry and artifact upload

```dotenv
MAF_E2E_OTLP_ENDPOINT=
MAF_E2E_APPLICATIONINSIGHTS_CONNECTION_STRING=
MAF_E2E_BLOB_ACCOUNT_URL=
MAF_E2E_BLOB_CONTAINER=e2e-artifacts
```

Blob upload uses Azure identity. Leave these values empty for local-only artifact storage.
