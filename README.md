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
- `storageState` の再利用、`devtools` capabilityによるtrace開始/停止、成果物ZIP化
- Azure OpenAIのManaged Identity認証
- Gemini Developer APIのAPIキー認証、Vertex AIのADC認証
- GitHub CopilotのGitHubトークン認証（明示トークンまたは `gh auth token` フォールバック）
- Blob Storageへの成果物アップロード
- OpenTelemetryのOTLP/Application Insights出力
- Docker ComposeとAzure Container Apps Job用Bicep

## 重要な設計補正

2026年6月13日時点の正式PyPI名は `microsoft-agent-framework` ではなく `agent-framework` です。このリポジトリでは不要な全integrationを入れず、安定版 `agent-framework-core==1.8.1` と `agent-framework-openai==1.8.1`、公式Geminiコネクタ `agent-framework-gemini==1.0.0a260609` を使用します。Geminiコネクタはalpha版、宣言型AgentとDevUIは `1.0.0b260528` のbeta版、Agent Skillsはexperimentalです。

Hyperlight、CodeAct、RAMPARTはMAF Workflowの標準APIではありません。本実装は生成コードを実行せず、ブラウザExecutorがPlaywright MCPを直接利用します。Safety Agentは既定で受動的レビューのみを行い、許可のない能動攻撃は実施しません。

## ローカル実行

```bash
cp .env.example .env
make install
make browsers
make test
make run
```

ローカル環境は `venv + pip` ではなく `uv` 管理に統一しています。`.python-version`
で指定した Python 3.14 系の仮想環境を `uv sync` で作成し、Python 依存関係は
`pyproject.toml` を正として解決します。

Azure OpenAIを使う場合は事前に `az login`、Vertex AIのADCを使う場合は `gcloud auth application-default login` を実行します。Gemini Developer APIのAPIキー認証では、どちらのCLIログインも不要です。

### Agent設定とSkills

5エージェントの名前、説明、instructions、モデルオプションは `agents/*.yaml` で管理します。provider、認証、tools、MCP、output schema、PowerFx式はYAMLから指定できません。
Discoveryは `next_step_hints`、Generatorは `execution_notes` と `handoff_hints`、Browserは `follow_up_hints` を返し、各段階の次の手が伝わるようにします。

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
```

## Azure

1. `infra/main.bicepparam.example` をコピーして値を設定します。
2. Bicepをデプロイします。
3. 出力されたACRへ `maf-playwright-qa:latest` をpushします。
4. ACA Jobを手動起動するか、Cronを待ちます。

```bash
az deployment group create \
  --resource-group YOUR_RG \
  --parameters infra/main.bicepparam
```

ACA JobのCronはUTCです。`0 18 * * *` は日本時間では毎日03:00です。

## 成果物

各実行は `artifacts/<run_id>/` にPlaywright出力と `report.json` を保存し、同階層にZIPを作成します。`MAF_QA_BLOB_ACCOUNT_URL` が設定されている場合はManaged IdentityでBlobへアップロードします。
