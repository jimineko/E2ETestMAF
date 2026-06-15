# E2ETestMAF 要件準拠状況

## 判定

`E2ETestMAF_requirements.md` のMVP要件に対し、管理UIを除く回帰テスト資産ワークフローを
実装済みである。Playwright MCPの直接操作は単発自律実行、探索、失敗診断に限定され、
承認対象の試行と通常回帰は生成済みTypeScriptコードを標準Playwright Runnerで実行する。

## 実行経路

| 経路 | ブラウザ操作 | 用途 | 承認・Publish可否 |
|---|---|---|---|
| `maf-e2e author` | 探索はCodeAct/MCP、試行は生成済みTypeScript | 回帰資産作成 | 同一Hashの試行成功後のみ可 |
| `maf-e2e regression` | 標準Playwright Runner | ACTIVE資産の定期実行 | Agent・LLM・MCP不要 |
| `maf-e2e analyze-failure --investigate` | CodeAct/MCP | 失敗後の現在UI調査 | 調査結果だけでは承認不可 |
| サブコマンドなしCLI | Browser Executor + MCP | 単発QA、後方互換 | 回帰資産としては承認不可 |

## 機能要件対応表

| 要件 | 状況 | 主な実装 |
|---|---|---|
| FR-01 テスト要求登録 | 実装済み | `cli.py`, `domain/requests.py`, `models.py` |
| FR-02 対象アプリ探索 | 実装済み | `DiscoveryExecutor`, CodeAct/MCP監査境界 |
| FR-03 構造化仕様生成 | 実装済み | `SpecificationGeneratorExecutor`, `domain/specification.py` |
| FR-04 Playwrightコード生成 | 実装済み | `playwright_codegen.py`, Draft Asset Store |
| FR-05 検証・試行実行 | 実装済み | `code_validation.py`, `trial_runner.py` |
| FR-06 シナリオレビュー | 実装済み | `review/approve/request-changes/reject`, `approval_store.py` |
| FR-07 正式保存 | 実装済み | `publisher.py`、Hash再検証、`e2e/**` atomic publish |
| FR-08 ライフサイクル | 実装済み | `TestLifecycleStatus`, spec/code version、Draft retention |
| FR-09 定期回帰実行 | 実装済み | `regression_runner.py`, Agent非依存CLI、CIテンプレート |
| FR-10 失敗分類 | 実装済み | `failure_analysis.py`, MAF regression analysis workflow |
| FR-11 修復案生成 | 実装済み | Locator限定修復、expected result / semantic guard |
| FR-12 修復PR作成 | 実装済み | branch、commit、push、`gh pr create`、自動マージなし |

## 承認同一性

1. 構造化仕様から決定論的にTypeScriptコードを生成する。
2. 対象リポジトリのformatter、lint、type-check、Playwright discoveryを実行する。
3. 整形後コードの `code_hash` を計算し、標準Playwright Runnerで試行する。
4. `approve` は同一 `code_hash` の成功Trialがある場合だけ受け付ける。
5. `publish` は承認時の `spec_hash` と `code_hash` を再計算し、不一致を拒否する。
6. ACTIVE資産は標準Playwright Runnerで固定コードとして実行する。

## MVP対象外

- 管理UI
- production試験
- Pull Requestの自動マージ
- expected resultの自動変更
- 複数ブラウザ、モバイル、Visual Regression

管理UIは要件書でもPhase 6であり、現在はCLIとJSONレビューPackageを提供する。
