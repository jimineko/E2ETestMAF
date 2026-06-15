# Docker, CI, and Azure Deployment

## Docker Compose

Copy the environment template and run the launcher:

```bash
cp .env.example .env
./scripts/e2e-compose
```

Pass CLI arguments after the launcher when using the legacy flow or another command supported by the container image:

```bash
./scripts/e2e-compose --target-url https://example.com
```

When the target application runs on the host, use `host.docker.internal` from the container:

```dotenv
MAF_E2E_TARGET_URL=http://host.docker.internal:3000
MAF_E2E_PLAYWRIGHT_ALLOWED_ORIGINS=http://host.docker.internal:3000
```

The launcher checks whether both the host and Docker daemon can expose `/dev/kvm`. It applies `docker-compose.kvm.yml` only when KVM is available. The container drops Linux capabilities and is not privileged.

Require Hyperlight and fail before execution when KVM is unavailable:

```bash
MAF_E2E_CODEACT_MODE=required \
MAF_E2E_COMPOSE_KVM=required \
./scripts/e2e-compose
```

At shutdown, the launcher copies container output into local `artifacts/` and `checkpoints/` directories.

### macOS

Docker Desktop runs the `linux/amd64` image, including on Apple Silicon. Hyperlight cannot use macOS Hypervisor.framework or KVM through Docker Desktop, so `auto` uses the audited direct-MCP path. CPU emulation can make the container slower.

### Windows and WSL2

Keep the repository and commands inside WSL2. Standard E2E operation works without KVM. Hyperlight additionally requires nested virtualization, a KVM-capable WSL2 kernel, a readable and writable `/dev/kvm`, and Docker Engine running in the same distribution.

```bash
test -c /dev/kvm && test -r /dev/kvm && test -w /dev/kvm
docker info
```

Docker Desktop cannot expose `/dev/kvm` in every WSL2 configuration. The launcher detects that condition and follows the selected `auto` or `required` policy.

## Agent-free nightly regression

`templates/github-actions/e2e-nightly.yml` is a starting point for a target application repository. It installs Node dependencies and Chromium, runs `maf-e2e regression`, and uploads `.maf-e2e/regression/` plus Playwright reports.

The target repository must install E2ETestMAF before invoking the command. Adapt the template to the project's package or checkout strategy. No model provider or Agent secret is required for regression.

## Hyperlight and RAMPART workflow

`.github/workflows/hyperlight-rampart.yml` runs the managed safety fixture, Hyperlight integration test, and RAMPART suite on an Ubuntu runner with KVM. It requires Azure identity and model configuration secrets. This workflow is a safety gate, not the normal application regression path.

## Azure VM deployment

The infrastructure under `infra/` provisions a private KVM-capable VM, outbound NAT, identity, and a systemd timer.

1. Copy `infra/main.bicepparam.example` to `infra/main.bicepparam` and set the required values, including the SSH public key.
2. Deploy the Bicep template.
3. Push a `linux/amd64` `maf-playwright-e2e:latest` image to the provisioned Azure Container Registry.
4. Wait for `maf-e2e.timer` or start `maf-e2e.service` manually.

```bash
az deployment group create \
  --resource-group YOUR_RESOURCE_GROUP \
  --parameters infra/main.bicepparam
```

The VM has no public IP. Outbound traffic uses NAT Gateway, and the container receives `/dev/kvm` without privileged mode. The default timer runs at 18:00 UTC.
