# MAF + Playwright MCP Autonomous QA

Microsoft Agent Framework (MAF) と Microsoft公式 Playwright MCP を使う、型付きの自律QAワークフローです。チャットモデルは Azure OpenAI または Google Gemini を選択できます。

## 実装したフロー

```text
Orchestrator -> Discovery -> Generator -> Browser(MCP)
                                      -> Judge  --+
                                      -> Safety --+-> Refiner -> retry / Finalizer
```

- MAF `WorkflowBuilder` による型付きデータフロー
- MAF `AgentSession` のExecutor単位管理とファイルチェックポイント
- Playwright操作を `MCPStdioTool` + `@playwright/mcp` に限定
- `storageState` の再利用、`devtools` capabilityによるtrace開始/停止、成果物ZIP化
- Azure OpenAIのManaged Identity認証
- Gemini Developer APIのAPIキー認証、Vertex AIのADC認証
- Blob Storageへの成果物アップロード
- OpenTelemetryのOTLP/Application Insights出力
- Docker ComposeとAzure Container Apps Job用Bicep

## 重要な設計補正

2026年6月13日時点の正式PyPI名は `microsoft-agent-framework` ではなく `agent-framework` です。このリポジトリでは不要な全integrationを入れず、安定版 `agent-framework-core==1.8.1` と `agent-framework-openai==1.8.1`、公式Geminiコネクタ `agent-framework-gemini==1.0.0a260609` を使用します。Geminiコネクタは現時点ではalpha版です。

Hyperlight、CodeAct、RAMPARTはMAF Workflowの標準APIではありません。本実装は生成コードを実行せず、ブラウザExecutorがPlaywright MCPを直接利用します。Safety Agentは既定で受動的レビューのみを行い、許可のない能動攻撃は実施しません。

## ローカル実行

```bash
cp .env.example .env
make install
make browsers
make test
make run
```

Azure OpenAIを使う場合は事前に `az login`、Vertex AIのADCを使う場合は `gcloud auth application-default login` を実行します。Gemini Developer APIのAPIキー認証では、どちらのCLIログインも不要です。

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
.venv/bin/maf-qa \
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
