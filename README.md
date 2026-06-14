# MAF + Playwright MCP Autonomous E2E Testing

Microsoft Agent Framework (MAF) と Microsoft公式 Playwright MCP を使い、Webアプリケーションの探索、テスト生成、ブラウザ実行、判定、再試行、レポート作成を行う、型付きの自律型E2Eテスト製品です。Agent backendは Azure OpenAI / Google Gemini / Vertex AI / GitHub Copilot CLI / OpenAI Codex CLI を選択できます。Codex CLI以外はMAF標準プロバイダーを直接利用します。

旧 `maf_qa` Python API、`maf-qa` CLI、`MAF_QA_*` 環境変数との互換性はありません。保存済みの `autonomous-web-qa-v2` チェックポイントも再開できないため、新しい `maf_e2e` API、`maf-e2e` CLI、`MAF_E2E_*` 環境変数で実行し直してください。

## 実装したフロー

```text
Orchestrator -> Discovery -> Generator -> Browser(MCP)
                                      -> Judge  --+
                                      -> Safety --+-> Decision -> retry / complete / escalate
```

- MAF `WorkflowBuilder` による型付きデータフロー
- `agents/*.yaml` と `AgentFactory(safe_mode=True)` による宣言型Agent
- Discovery/Generatorへ接続できる参照専用Agent Skills
- モデルAPIの限定retry、構造化出力repair、Playwright非自動retry
- run単位に分離した `AgentSession` とファイルチェックポイント
- Safety high/critical・stage障害のBLOCKED化とDevUI HITL
- Playwright操作を `MCPStdioTool` + `@playwright/mcp` に限定
- Discovery/Browserを `HyperlightCodeActProvider` でCodeAct化
- Hyperlight micro-VM内から監査付きPlaywright MCP関数を一括呼び出し
- RAMPARTによる隔離済みXPIA/behavioral安全性回帰テスト
- `storageState` の再利用、`devtools` capabilityによるtrace開始/停止、成果物ZIP化
- Azure OpenAIのManaged Identity認証
- Gemini Developer APIのAPIキー認証、Vertex AIのADC認証
- GitHub Copilot CLIのサブスクリプション認証を使うMAF Agent
- ChatGPTサブスクリプション認証を再利用するCodex CLI AgentとMAF tool bridge
- Blob Storageへの成果物アップロード
- OpenTelemetryのOTLP/Application Insights出力
- macOS/Linux/Windows WSL2共通のDocker Compose実行経路とAzure VM用Bicep

## 重要な設計補正

2026年6月14日時点の正式PyPI名は `microsoft-agent-framework` ではなく `agent-framework` です。本リポジトリは `agent-framework-core==1.8.1`、`agent-framework-hyperlight==1.0.0b260521`、`rampart==0.1.0` を固定します。Hyperlight integrationはbeta、RAMPARTはalpha、Agent SkillsとHarness APIはexperimentalです。更新時は単体・Linux KVM統合・RAMPART試験をすべて通してください。

DiscoveryとBrowserは、対応環境ではMAF `HyperlightCodeActProvider`から公開される`execute_code`だけをモデルへ渡します。生成PythonはASTポリシーで検査され、Hyperlight内の`call_tool(...)`から許可済みPlaywright MCP関数を呼び出します。VM内ネットワークとホストファイルmountは無効です。RAMPARTの能動攻撃は通常のE2Eテスト実行には混ぜず、管理下fixtureまたは明示allowlist済みstagingだけを対象にします。

### Hyperlight実行条件

- Python 3.13
- Linux x86_64、glibc 2.34以上
- `/dev/kvm`を読み書きできること
- コンテナには`--device=/dev/kvm:/dev/kvm`だけを渡し、privilegedにしないこと

macOS arm64ではHyperlight自体とKVMを使用できません。Docker Compose内のLinux amd64コンテナでE2Eテストは実行できますが、`MAF_E2E_CODEACT_MODE=auto`により監査付き直接MCP経路へ明示的に切り替わります。`required`では起動を拒否します。本番とLinux KVM統合試験は`required`を使用します。

## ローカル実行

デフォルトの実行方法はDocker Composeです。

```bash
cp .env.example .env
./scripts/e2e-compose
```

対象アプリがホストのlocalhostで動いている場合、`.env`では`localhost`ではなく`host.docker.internal`を指定します。

```dotenv
MAF_E2E_TARGET_URL=http://host.docker.internal:3000
MAF_E2E_PLAYWRIGHT_ALLOWED_ORIGINS=http://host.docker.internal:3000
```

Python環境を直接使う方法は、実装・単体テスト向けです。

```bash
uv python install
uv sync --all-extras --group dev
npm ci
npx playwright install chrome
uv run maf-e2e
```

GitHub Copilot CLIまたはCodex CLIを使う場合はローカルPython実行を使用します。これらのbackendはDocker／Azureでは拒否され、APIキーへフォールバックしません。

ローカル環境は `venv + pip` ではなく `uv` 管理に統一しています。`.python-version`
で指定した Python 3.13 系の仮想環境を `uv sync` で作成し、Python 依存関係は
`pyproject.toml` を正として解決します。

Azure OpenAIを使う場合は事前に `az login`、Vertex AIのADCを使う場合は `gcloud auth application-default login` を実行します。Gemini Developer APIのAPIキー認証では、どちらのCLIログインも不要です。サブスクリプションbackendでは事前に `copilot` または `codex login` の認証を完了してください。

### 品質チェック

```bash
uv run ruff check .   # lint
uv run pytest         # テスト
uv run mypy           # 型チェック
```

### 依存管理

```bash
uv lock --upgrade     # ロックファイルを最新に更新
uv build              # ホイールビルド（リリース時）
```

### CI での品質チェック手順

CI プラットフォーム（GitHub Actions / Azure DevOps 等）に依らず、以下のコマンド列を実行します。
ブラウザインストール・Azure 認証・外部 API 呼び出しは不要です。

```bash
uv sync --group dev
uv run ruff check .
uv run pytest
uv run mypy
```

### Agent設定とSkills

5エージェントの名前、説明、instructions、モデルオプションは `agents/*.yaml` で管理します。provider、認証、tools、MCP、output schema、PowerFx式はYAMLから指定できません。
Discoveryは `next_step_hints`、Generatorは `execution_notes` と `handoff_hints`、Browserは `follow_up_hints` を返し、各段階の次の手が伝わるようにします。

Discovery/BrowserのCodeAct設定:

```dotenv
MAF_E2E_CODEACT_MODE=auto              # required | auto | disabled
MAF_E2E_CODEACT_MAX_CODE_BYTES=32768
MAF_E2E_CODEACT_MAX_INVOCATIONS=6
MAF_E2E_CODEACT_REQUIRE_KVM=true
MAF_E2E_CODEACT_ALLOW_FILE_UPLOAD=false
MAF_E2E_CODEACT_ALLOW_DESTRUCTIVE_ACTIONS=false
MAF_E2E_COMPOSE_KVM=auto               # auto | required | disabled
MAF_E2E_COMPOSE_HEADLESS=true           # Docker内のPlaywrightは既定でheadless
```

`required`でpreflightに失敗した場合、直接MCPへfallbackせず構成障害としてBLOCKEDになります。`auto`のfallbackはローカル開発用です。

チェックポイントから再開する場合は、保存済みplanを読み込み、新しいMCP接続とHyperlight SandboxでBrowserステージを先頭から再実行します。

```bash
uv run maf-e2e --resume-run-id RUN_ID
uv run maf-e2e --resume-run-id RUN_ID --checkpoint-id CHECKPOINT_ID
```

対象アプリ固有の操作規約は、`SKILL.md` と `references/` だけを含むSkillとして作成し、`MAF_E2E_SKILL_PATHS`へカンマ区切りで指定します。`scripts/` を含むSkillは起動時に拒否されます。

### DevUI

DevUIはローカル調査専用です。Bearer認証付きでloopbackへ起動し、既定ではプロンプトや応答本文をOpenTelemetryへ記録しません。

```bash
uv sync --extra devui --group dev
MAF_E2E_DEVUI_AUTH_TOKEN=change-me uv run maf-e2e-devui
```

DevUIでESCALATEした実行は人間の `retry` または `abort` 応答まで停止します。本番のVM batchでは同じ状態を`blocked`レポートと終了コード3へ変換します。

### モデルプロバイダー

モデル通信は`MAF_E2E_MODEL_PROVIDER`と`MAF_E2E_MODEL_AUTH`を必ず明示します。対応していない組み合わせや別認証方式へのfallbackは拒否されます。

Azure OpenAIをEntra ID（Azure CLI、Managed Identity、サービスプリンシパル等）で使う場合:

```dotenv
MAF_E2E_MODEL_PROVIDER=azure_openai
MAF_E2E_MODEL_AUTH=entra_id
MAF_E2E_AZURE_OPENAI_ENDPOINT=https://YOUR-RESOURCE.openai.azure.com
MAF_E2E_AZURE_OPENAI_DEPLOYMENT=gpt-4o
```

Windows、WSL2、Docker等からAzure OpenAI API Keyを使う場合:

```dotenv
MAF_E2E_MODEL_PROVIDER=azure_openai
MAF_E2E_MODEL_AUTH=api_key
MAF_E2E_AZURE_OPENAI_ENDPOINT=https://YOUR-RESOURCE.openai.azure.com
MAF_E2E_AZURE_OPENAI_DEPLOYMENT=gpt-4o
MAF_E2E_AZURE_OPENAI_API_KEY=YOUR_API_KEY
```

Gemini Developer APIを使う場合:

```dotenv
MAF_E2E_MODEL_PROVIDER=gemini
MAF_E2E_MODEL_AUTH=api_key
MAF_E2E_GEMINI_API_KEY=YOUR_API_KEY
MAF_E2E_GEMINI_MODEL=gemini-2.5-flash-lite
```

GitHub Copilot CLIを使う場合は、Copilot CLIへログインした同じOSユーザーで次を実行します。旧API token方式は使用しません。

```dotenv
MAF_E2E_MODEL_PROVIDER=github_copilot
MAF_E2E_MODEL_AUTH=subscription
MAF_E2E_RUNTIME_ENVIRONMENT=local
MAF_E2E_GITHUB_COPILOT_CLI_PATH=copilot
MAF_E2E_GITHUB_COPILOT_MODEL=
MAF_E2E_GITHUB_COPILOT_TIMEOUT_SECONDS=300
```

Codex CLIを使う場合は、`codex login`でChatGPTへログインした同じOSユーザーで次を設定します。Codexはread-only sandboxで動作し、ブラウザ操作はMAFから渡された監査付きtoolだけを利用します。

```dotenv
MAF_E2E_MODEL_PROVIDER=codex_cli
MAF_E2E_MODEL_AUTH=subscription
MAF_E2E_RUNTIME_ENVIRONMENT=local
MAF_E2E_CODEX_CLI_PATH=codex
MAF_E2E_CODEX_MODEL=
MAF_E2E_CODEX_TIMEOUT_SECONDS=300
MAF_E2E_CODEX_MAX_TOOL_ROUNDS=8
```

CLI backendの導入と認証確認:

```bash
uv sync --extra cli-providers --group dev
copilot --version
codex login status
```

Copilot／ChatGPTの契約上限と利用条件は各サービスに従います。保存したCodex checkpointは同じユーザーのローカルthreadを再利用し、threadが失われている場合は保存済みtranscriptから再構築します。

Vertex AIをADCで使う場合:

```dotenv
MAF_E2E_MODEL_PROVIDER=vertex_ai
MAF_E2E_MODEL_AUTH=adc
MAF_E2E_GEMINI_MODEL=gemini-2.5-flash-lite
MAF_E2E_GEMINI_VERTEX_PROJECT=YOUR_PROJECT_ID
MAF_E2E_GEMINI_VERTEX_LOCATION=global
```

Vertex AI Express Mode等のAPI Keyを使う場合は`MODEL_AUTH=api_key`と`MAF_E2E_GEMINI_API_KEY`を指定します。

CLI引数でも対象を指定できます。

```bash
uv run maf-e2e \
  --model-provider gemini \
  --model-auth api_key \
  --target-url https://example.com \
  --objective "主要導線を検証する" \
  --policy "コンソールに未処理エラーがない"
```

`--model-provider`は `azure_openai`、`gemini`、`vertex_ai`、`github_copilot`、`codex_cli`、`--model-auth`は`api_key`、`entra_id`、`adc`、`subscription`を受け付けます。

認証済み状態を再利用する場合は `auth/user.json` を配置します。このファイルはGit管理されません。

## Docker

```bash
./scripts/e2e-compose
./scripts/e2e-compose --target-url https://example.com
```

ランチャーはDockerを起動し、ホストとDocker daemonの両方から`/dev/kvm`を利用できるか検査します。成功時だけ`docker-compose.kvm.yml`を自動適用し、`e2e`コンテナへKVM deviceだけを渡します。privilegedモードは使用しません。利用できない場合はmacOSを含め`auto`の直接MCP経路で実行します。実行中はDocker管理のnamed volumeを使い、終了時に`artifacts/`と`checkpoints/`へ成果物を同期します。

KVMを必須にして、利用できなければ実行前に失敗させる場合:

```bash
MAF_E2E_CODEACT_MODE=required MAF_E2E_COMPOSE_KVM=required ./scripts/e2e-compose
```

### macOS

Docker Desktopを起動して`./scripts/e2e-compose`を実行します。Apple Siliconでも`linux/amd64`コンテナを使用するため動作しますが、CPUエミュレーションの分だけ遅くなります。HyperlightはmacOSのHypervisor.frameworkに未対応で、Docker Desktop越しにKVMを利用することもできないため、CodeActではなく直接MCP経路になります。

コンテナは非rootユーザーで実行し、Linux capabilityをすべて削除します。Chromium sandboxが必要とするuser namespaceを許可するためComposeではseccompを解除していますが、privilegedモードは使用しません。

### Windows WSL2

リポジトリとコマンド実行環境をWSL2側へ置き、WSL2のbashから`./scripts/e2e-compose`を実行します。通常のE2EテストはKVMなしでも動作します。実Hyperlightを使うにはWindows 11のnested virtualization、KVM対応WSL2 kernel、読み書き可能な`/dev/kvm`、および同じWSL2 distro内で動作するDocker Engineが必要です。

Azure OpenAI API Keyを使う場合は`.env`へ`MAF_E2E_MODEL_PROVIDER=azure_openai`、`MAF_E2E_MODEL_AUTH=api_key`、endpoint、deployment、API keyを設定します。Composeの`env_file`からコンテナへ渡されるため、Windows側の`az login`は不要です。

必要に応じて`%UserProfile%\.wslconfig`でnested virtualizationを明示します。Windows 11では既定値も`true`です。

```ini
[wsl2]
nestedVirtualization=true
```

```powershell
wsl --update
wsl --list --verbose
wsl --shutdown
```

```bash
test -c /dev/kvm && test -r /dev/kvm && test -w /dev/kvm
docker info
MAF_E2E_CODEACT_MODE=required MAF_E2E_COMPOSE_KVM=required ./scripts/e2e-compose
```

Docker DesktopのWSL2 custom kernel利用は公式サポート外です。Docker Desktop側daemonへ`/dev/kvm`を渡せない構成では、ランチャーが検出して直接MCPへ切り替えます。

## Azure

1. `infra/main.bicepparam.example` をコピーし、SSH公開鍵を含む値を設定します。
2. Bicepをデプロイします。
3. 出力されたACRへlinux/amd64の`maf-playwright-e2e:latest`をpushします。
4. KVM対応VM上の`maf-e2e.timer`を待つか、`systemctl start maf-e2e.service`で起動します。

```bash
az deployment group create \
  --resource-group YOUR_RG \
  --parameters infra/main.bicepparam
```

VMには公開IPを付与せず、NAT Gateway経由の外向き通信だけを許可します。既定のsystemd timerはUTC 18:00、日本時間03:00です。コンテナへは`/dev/kvm`だけを渡します。

## RAMPART

RAMPART試験は通常の`pytest`から分離して実行します。5試行中80%以上SAFEをゲートとし、結果は`artifacts/rampart/`と、設定時はBlob Storageの`rampart/`以下へ保存します。

```bash
MAF_E2E_CODEACT_MODE=required \
MAF_E2E_RAMPART_TARGET_URL=http://127.0.0.1:8765 \
uv run pytest tests/rampart -v
```

`.github/workflows/hyperlight-rampart.yml`はLinux KVM上で管理下fixtureを起動し、夜間または手動で安全性試験を実施します。任意の本番URLを指定して実行してはいけません。

## 成果物

各実行は `artifacts/<run_id>/` にPlaywright出力と `report.json` を保存し、同階層にZIPを作成します。`MAF_E2E_BLOB_ACCOUNT_URL` が設定されている場合はManaged IdentityでBlobへアップロードします。
