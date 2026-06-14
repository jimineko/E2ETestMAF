# MAF + Playwright MCP Autonomous QA

Microsoft Agent Framework (MAF) と Microsoft公式 Playwright MCP を使う、型付きの自律QAワークフローです。チャットモデルは Azure OpenAI / Google Gemini / GitHub Copilot (GitHub Models経由) を選択できます。

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
- GitHub CopilotのGitHubトークン認証（明示トークンまたは `gh auth token` フォールバック）
- Blob Storageへの成果物アップロード
- OpenTelemetryのOTLP/Application Insights出力
- Docker ComposeとAzure Container Apps Job用Bicep

## 重要な設計補正

2026年6月14日時点の正式PyPI名は `microsoft-agent-framework` ではなく `agent-framework` です。本リポジトリは `agent-framework-core==1.8.1`、`agent-framework-hyperlight==1.0.0b260521`、`rampart==0.1.0` を固定します。Hyperlight integrationはbeta、RAMPARTはalpha、Agent SkillsとHarness APIはexperimentalです。更新時は単体・Linux KVM統合・RAMPART試験をすべて通してください。

DiscoveryとBrowserは、対応環境ではMAF `HyperlightCodeActProvider`から公開される`execute_code`だけをモデルへ渡します。生成PythonはASTポリシーで検査され、Hyperlight内の`call_tool(...)`から許可済みPlaywright MCP関数を呼び出します。VM内ネットワークとホストファイルmountは無効です。RAMPARTの能動攻撃は通常QA実行には混ぜず、管理下fixtureまたは明示allowlist済みstagingだけを対象にします。

### Hyperlight実行条件

- Python 3.13
- Linux x86_64、glibc 2.34以上
- `/dev/kvm`を読み書きできること
- コンテナには`--device=/dev/kvm:/dev/kvm`だけを渡し、privilegedにしないこと

macOS arm64ではWasm backend wheelが提供されていません。`MAF_QA_CODEACT_MODE=auto`では直接MCP経路へ明示的に切り替わり、`required`ではBLOCKEDレポートを返します。本番とLinux統合試験は`required`を使用します。

## ローカル実行

```bash
cp .env.example .env
uv python install
uv sync --all-extras --group dev
npm ci
npx playwright install chrome
uv run maf-qa
```

ローカル環境は `venv + pip` ではなく `uv` 管理に統一しています。`.python-version`
で指定した Python 3.13 系の仮想環境を `uv sync` で作成し、Python 依存関係は
`pyproject.toml` を正として解決します。

Azure OpenAIを使う場合は事前に `az login`、Vertex AIのADCを使う場合は `gcloud auth application-default login` を実行します。Gemini Developer APIのAPIキー認証では、どちらのCLIログインも不要です。

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
MAF_QA_CODEACT_MODE=auto              # required | auto | disabled
MAF_QA_CODEACT_MAX_CODE_BYTES=32768
MAF_QA_CODEACT_MAX_INVOCATIONS=6
MAF_QA_CODEACT_REQUIRE_KVM=true
MAF_QA_CODEACT_ALLOW_FILE_UPLOAD=false
MAF_QA_CODEACT_ALLOW_DESTRUCTIVE_ACTIONS=false
```

`required`でpreflightに失敗した場合、直接MCPへfallbackせず構成障害としてBLOCKEDになります。`auto`のfallbackはローカル開発用です。

チェックポイントから再開する場合は、保存済みplanを読み込み、新しいMCP接続とHyperlight SandboxでBrowserステージを先頭から再実行します。

```bash
uv run maf-qa --resume-run-id RUN_ID
uv run maf-qa --resume-run-id RUN_ID --checkpoint-id CHECKPOINT_ID
```

対象アプリ固有の操作規約は、`SKILL.md` と `references/` だけを含むSkillとして作成し、`MAF_QA_SKILL_PATHS`へカンマ区切りで指定します。`scripts/` を含むSkillは起動時に拒否されます。

### DevUI

DevUIはローカル調査専用です。Bearer認証付きでloopbackへ起動し、既定ではプロンプトや応答本文をOpenTelemetryへ記録しません。

```bash
uv sync --extra devui --group dev
MAF_QA_DEVUI_AUTH_TOKEN=change-me uv run maf-qa-devui
```

DevUIでESCALATEした実行は人間の `retry` または `abort` 応答まで停止します。本番のbatch/ACA Jobでは同じ状態を`blocked`レポートと終了コード3へ変換します。

### モデルプロバイダー

Azure OpenAIを使う場合は次を設定します。

```dotenv
MAF_QA_MODEL_PROVIDER=azure_openai
MAF_QA_AZURE_OPENAI_ENDPOINT=https://YOUR-RESOURCE.openai.azure.com
MAF_QA_AZURE_OPENAI_DEPLOYMENT=gpt-4o
```

Gemini Developer APIを使う場合は次を設定します。

```dotenv
MAF_QA_MODEL_PROVIDER=gemini
MAF_QA_GEMINI_API_KEY=YOUR_API_KEY
MAF_QA_GEMINI_MODEL=gemini-2.5-flash-lite
```

GitHub Copilot（GitHub Models）を使う場合は次を設定します。`MAF_QA_GITHUB_COPILOT_TOKEN` を省略した場合、`MAF_QA_GITHUB_COPILOT_USE_GH_CLI_TOKEN=true` で `gh auth token` を使用します。

```dotenv
MAF_QA_MODEL_PROVIDER=github_copilot
MAF_QA_GITHUB_COPILOT_MODEL=gpt-4.1
MAF_QA_GITHUB_COPILOT_BASE_URL=https://models.inference.ai.azure.com
MAF_QA_GITHUB_COPILOT_TOKEN=YOUR_GITHUB_TOKEN
```

Vertex AIを使う場合は、ADCを構成したうえで次を設定します。APIキーを利用するVertex AI Express Modeにも対応します。

```dotenv
MAF_QA_MODEL_PROVIDER=gemini
MAF_QA_GEMINI_MODEL=gemini-2.5-flash-lite
MAF_QA_GEMINI_USE_VERTEX_AI=true
MAF_QA_GEMINI_VERTEX_PROJECT=YOUR_PROJECT_ID
MAF_QA_GEMINI_VERTEX_LOCATION=global
```

CLI引数でも対象を指定できます。

```bash
uv run maf-qa \
  --model-provider gemini \
  --target-url https://example.com \
  --objective "主要導線を検証する" \
  --policy "コンソールに未処理エラーがない"
```

認証済み状態を再利用する場合は `auth/user.json` を配置します。このファイルはGit管理されません。

## Docker

```bash
docker compose build
docker compose run --rm qa
# Linux KVM host
docker compose --profile hyperlight run --rm qa-hyperlight
```

Dockerは`linux/amd64`固定です。通常の`qa` serviceはKVM deviceを要求しません。実HyperlightはLinux KVMホストで`qa-hyperlight` serviceと`MAF_QA_CODEACT_MODE=required`を使用してください。

## Azure

1. `infra/main.bicepparam.example` をコピーし、SSH公開鍵を含む値を設定します。
2. Bicepをデプロイします。
3. 出力されたACRへlinux/amd64の`maf-playwright-qa:latest`をpushします。
4. KVM対応VM上の`maf-qa.timer`を待つか、`systemctl start maf-qa.service`で起動します。

```bash
az deployment group create \
  --resource-group YOUR_RG \
  --parameters infra/main.bicepparam
```

VMには公開IPを付与せず、NAT Gateway経由の外向き通信だけを許可します。既定のsystemd timerはUTC 18:00、日本時間03:00です。コンテナへは`/dev/kvm`だけを渡します。

## RAMPART

RAMPART試験は通常の`pytest`から分離して実行します。5試行中80%以上SAFEをゲートとし、結果は`artifacts/rampart/`と、設定時はBlob Storageの`rampart/`以下へ保存します。

```bash
MAF_QA_CODEACT_MODE=required \
MAF_QA_RAMPART_TARGET_URL=http://127.0.0.1:8765 \
uv run pytest tests/rampart -v
```

`.github/workflows/hyperlight-rampart.yml`はLinux KVM上で管理下fixtureを起動し、夜間または手動で安全性試験を実施します。任意の本番URLを指定して実行してはいけません。

## 成果物

各実行は `artifacts/<run_id>/` にPlaywright出力と `report.json` を保存し、同階層にZIPを作成します。`MAF_QA_BLOB_ACCOUNT_URL` が設定されている場合はManaged IdentityでBlobへアップロードします。
