# E2ETestMAF Codex 改修指示書 v2

## 1. 改修目的

現行 E2ETestMAF を、単発の自律型探索・実行フローから、回帰テスト資産の作成、試行、承認、保存、定期実行、失敗分析、修復提案までを管理する基盤へ改修する。

```text
自然言語要求
→ MAF + CodeAct による対象アプリ探索
→ 構造化テスト仕様生成
→ Playwright コード生成
→ コード検証
→ 生成コードを標準 Playwright Runner で試行実行
→ コード・期待結果・証跡をシナリオ単位でレビュー
→ 承認後に対象アプリの e2e/** へ保存
→ 定期回帰実行
→ 失敗時だけ MAF + CodeAct で診断・修復調査
→ 修復ブランチと Pull Request を作成
```

承認前のコードを Draft、承認後のコードを Active Asset として明確に分離する。

---

## 2. MAF / CodeAct / Playwright Runner の役割分担

### 2.1 基本原則

本プロダクトは MAF を全体オーケストレーション基盤として継続利用する。

MAF Workflow には、次の2種類の Executor を共存させる。

1. MAF Agent + Hyperlight CodeAct + Playwright MCP を利用する適応的 Executor
2. ローカル処理または subprocess を実行する決定論的 Executor

試行実行を標準 Playwright Runner へ変更することは、MAF / CodeAct を不使用にすることを意味しない。

### 2.2 CodeAct を利用する処理

以下では MAF Agent に `HyperlightCodeActProvider` を付与し、許可済み Playwright MCP 関数だけを公開する。

- 対象 Web アプリケーションの初期探索
- 画面遷移、操作候補、Locator 候補の取得
- 生成コードの試行失敗時の原因調査
- 回帰テスト失敗時の現在 UI 再探索
- UI 変更とアプリ不具合の切り分け
- Locator、wait、iframe、dialog 等の修復候補調査

想定 Executor:

```text
DiscoveryExecutor
TrialFailureDiagnosticExecutor
RegressionFailureDiagnosticExecutor
CurrentUiDiscoveryExecutor
RepairInvestigationExecutor
```

現行の以下は維持する。

- AST ポリシー
- CodeAct 実行回数制限
- Origin 制限
- 破壊的操作制限
- 機密値マスキング
- Tool audit log
- Hyperlight 隔離実行

### 2.3 CodeAct を利用しない処理

以下は決定論的処理として実装し、Agent に自由判断させない。

- 構造化仕様から TypeScript Playwright コードを生成する処理
- format
- lint
- type-check
- Playwright test discovery
- 生成済み `.spec.ts` の試行実行
- 承認済み `.spec.ts` の定期回帰実行
- Hash 計算
- Draft / Published Asset 保存
- Git diff 作成

### 2.4 承認同一性

人間が承認するコードは、標準 Playwright Test Runner で試行実行されたコードそのものとする。

以下は禁止する。

```text
CodeAct が直接行ったブラウザ操作は成功
→ 後から別の Playwright コードを生成
→ そのコードを未実行のまま承認済みとして保存
```

承認対象は以下の組み合わせとする。

- 構造化テスト仕様
- 生成済み Playwright コード
- `spec_hash`
- `code_hash`
- 標準 Runner の試行結果
- Assertion 結果
- Screenshot
- Trace
- Console error
- Network error

承認後の Publish 時に `spec_hash` と `code_hash` を再計算し、不一致なら Publish を拒否する。

---

## 3. 正式な処理ワークフロー

### 3.1 Authoring Workflow

```text
MAF Workflow
  ↓
OrchestratorExecutor
  ↓
DiscoveryExecutor
  └─ MAF Agent + Hyperlight CodeAct + Playwright MCP
  ↓
SpecificationGeneratorExecutor
  └─ MAF Agent が構造化仕様を生成
  ↓
PlaywrightCodeGeneratorExecutor
  └─ 構造化仕様から決定論的に TypeScript を生成
  ↓
CodeValidationExecutor
  ├─ format
  ├─ lint
  ├─ type-check
  └─ Playwright test discovery
  ↓
TrialRunExecutor
  └─ 標準 Playwright Test Runner
  ↓
成功
  ↓
TrialJudgeExecutor
  ↓
ScenarioApprovalExecutor
  ├─ Approve
  ├─ Request Changes
  └─ Reject
  ↓ Approve
PublisherExecutor
  ↓
対象アプリケーションの e2e/** へ保存
  ↓
ACTIVE 化
```

### 3.2 Authoring 試行失敗時

```text
TrialRunExecutor
  ↓ failed
TrialFailureDiagnosticExecutor
  └─ MAF Agent + Hyperlight CodeAct + Playwright MCP
  ↓
DraftCodeRepairExecutor
  ├─ Locator 修正
  ├─ wait 条件修正
  ├─ iframe / dialog 操作修正
  └─ Playwright API 利用修正
  ↓
SemanticChangeGuard
  ├─ expected result 変更なし
  ├─ Assertion の意味変更なし
  └─ シナリオ目的変更なし
  ↓
CodeValidationExecutor
  ↓
TrialRunExecutor
```

Draft 修復回数には上限を設ける。上限到達時は人間へエスカレーションする。

### 3.3 Regression Workflow

```text
外部スケジューラー
  ↓
RegressionRunner
  └─ 標準 Playwright Test Runner
  ↓
ResultCollector
  ├─ passed → 結果保存
  └─ failed → FailureAnalysis Workflow
```

通常の回帰実行では、MAF Agent、CodeAct、Playwright MCP をブラウザ操作経路に含めない。

### 3.4 Regression Failure Analysis Workflow

```text
Failed Regression Result
  ↓
FailureEvidenceCollector
  ↓
RegressionFailureDiagnosticExecutor
  └─ MAF Agent + Hyperlight CodeAct + Playwright MCP
  ↓
FailureClassifierExecutor
  ├─ APPLICATION_DEFECT
  ├─ TEST_MAINTENANCE
  ├─ ENVIRONMENT_FAILURE
  ├─ AUTHENTICATION_FAILURE
  ├─ TEST_DATA_FAILURE
  ├─ FLAKY_FAILURE
  └─ UNKNOWN
```

### 3.5 Repair Workflow

```text
TEST_MAINTENANCE
  ↓
ApprovedSpecAndCodeLoader
  ↓
CurrentUiDiscoveryExecutor
  └─ MAF Agent + Hyperlight CodeAct + Playwright MCP
  ↓
RepairProposalGeneratorExecutor
  ↓
SemanticChangeGuard
  ↓
CodeValidationExecutor
  ↓
RepairTrialRunExecutor
  └─ 標準 Playwright Test Runner
  ↓
GitBranchCreator
  ↓
CommitCreator
  ↓
PullRequestCreator
  ↓
人間レビュー
```

修復 Pull Request は自動マージしない。

---

## 4. 現状コードとの乖離

### GAP-01 入力モデル不足

現行 `E2ETestRequest`:

- `target_url`
- `objective`
- `policies`
- `max_refinements`

追加:

- `expected_results`
- `preconditions`
- `test_data`
- `business_context`
- `prohibited_actions`
- `allowed_origins`
- `max_scenarios`
- `max_steps`
- `target_repository_root`

### GAP-02 TestScenario が自然言語中心

現行 `steps: list[str]` と `expected_results: list[str]` はコード生成の正本として曖昧。

追加:

- `scenario_id`
- `spec_version`
- `StructuredStep`
- `LocatorSpec`
- `AssertionSpec`
- `spec_hash`
- lifecycle status

### GAP-03 Playwright コード生成がない

現行 `BrowserExecutor` は Agent が `TestPlan` を解釈し、MCP で直接操作している。

追加:

- TypeScript Playwright Generator
- Draft source file
- `code_version`
- `code_hash`
- Generator version

### GAP-04 試行対象が生成コードではない

現行は Agent による直接ブラウザ操作を評価している。

改修後は、生成された `.spec.ts` を標準 Runner で実行し、その結果を承認対象とする。

### GAP-05 Human Review の用途が異なる

現行 Human Review は Safety / Stage failure の `retry` / `abort` 用。

追加:

- `Approve`
- `Request Changes`
- `Reject`
- `ScenarioApproval`
- reviewer identity
- `spec_hash`
- `code_hash`

### GAP-06 Draft / Publish がない

追加:

- Draft workspace
- target application repository root
- `e2e/**` Publish
- atomic write
- Hash verification
- ACTIVE state

### GAP-07 定期実行モードがない

追加:

- Agent 非依存 Regression Runner
- scenario filter
- JUnit / HTML / JSON report
- CI exit code
- GitHub Actions template

### GAP-08 QA 向け失敗分類がない

追加:

```text
APPLICATION_DEFECT
TEST_MAINTENANCE
ENVIRONMENT_FAILURE
AUTHENTICATION_FAILURE
TEST_DATA_FAILURE
FLAKY_FAILURE
UNKNOWN
```

### GAP-09 Repair Proposal がない

現行 retry は Generator に戻り計画全体を再生成する。

追加:

- 承認済み仕様とコードを基準とする限定修復
- semantic change detection
- expected result change detection
- RepairProposal
- repair validation

### GAP-10 Git branch / Pull Request 作成がない

追加:

- branch creation
- commit
- push
- PR creation
- PR body
- validation evidence

### GAP-11 仕様とコードのバージョン管理がない

追加:

- `spec_version`
- `code_version`
- `spec_hash`
- `code_hash`
- approved hash
- approval invalidation

### GAP-12 CLI コマンド体系が不足

追加:

```text
maf-e2e author
maf-e2e review
maf-e2e approve
maf-e2e request-changes
maf-e2e reject
maf-e2e publish
maf-e2e regression
maf-e2e analyze-failure
maf-e2e repair
```

---

## 5. ドメインモデル

モデルは責務別モジュールへ分割する。

```text
src/maf_e2e/domain/
  requests.py
  specification.py
  assets.py
  approval.py
  regression.py
  failures.py
  repair.py
```

### 5.1 Lifecycle

```python
class TestLifecycleStatus(StrEnum):
    DRAFT = "draft"
    GENERATED = "generated"
    VALIDATING = "validating"
    TRIAL_PASSED = "trial_passed"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    ACTIVE = "active"
    REPAIR_PENDING = "repair_pending"
    DISABLED = "disabled"
    RETIRED = "retired"
    REJECTED = "rejected"
```

### 5.2 Locator

```python
class LocatorSpec(BaseModel):
    strategy: Literal["role", "label", "text", "test_id", "css", "xpath"]
    role: str | None = None
    name: str | None = None
    value: str | None = None
```

### 5.3 StructuredStep

```python
class StructuredStep(BaseModel):
    step_id: str
    action: Literal[
        "navigate", "click", "fill", "select", "press",
        "check", "uncheck", "upload", "wait"
    ]
    target: str | None = None
    locator: LocatorSpec | None = None
    value_ref: str | None = None
```

### 5.4 AssertionSpec

```python
class AssertionSpec(BaseModel):
    assertion_id: str
    type: Literal[
        "visible", "hidden", "enabled", "disabled",
        "text_equals", "text_contains", "url_matches",
        "value_equals", "count_equals"
    ]
    locator: LocatorSpec | None = None
    expected: Any
    source_expected_result: str
```

### 5.5 TestSpecification

```python
class TestSpecification(BaseModel):
    scenario_id: str
    version: int
    name: str
    objective: str
    preconditions: list[str]
    steps: list[StructuredStep]
    assertions: list[AssertionSpec]
    test_data: dict[str, Any]
    prohibited_actions: list[str]
    status: TestLifecycleStatus
```

### 5.6 GeneratedTestAsset

```python
class GeneratedTestAsset(BaseModel):
    scenario_id: str
    spec_version: int
    code_version: int
    draft_path: Path
    published_path: Path | None = None
    source_hash: str
    validated: bool
    generated_at: datetime
```

### 5.7 TrialRunResult

```python
class TrialRunResult(BaseModel):
    scenario_id: str
    status: Literal["passed", "failed", "blocked"]
    assertion_results: list[AssertionResult]
    screenshot_paths: list[str]
    trace_path: str | None
    console_errors: list[str]
    network_errors: list[str]
    report_path: str
```

### 5.8 ScenarioApproval

```python
class ScenarioApproval(BaseModel):
    scenario_id: str
    spec_version: int
    code_version: int
    action: Literal["approve", "request_changes", "reject"]
    reviewer: str
    comment: str | None
    spec_hash: str
    code_hash: str
    reviewed_at: datetime
```

### 5.9 FailureCategory

```python
class FailureCategory(StrEnum):
    APPLICATION_DEFECT = "application_defect"
    TEST_MAINTENANCE = "test_maintenance"
    ENVIRONMENT_FAILURE = "environment_failure"
    AUTHENTICATION_FAILURE = "authentication_failure"
    TEST_DATA_FAILURE = "test_data_failure"
    FLAKY_FAILURE = "flaky_failure"
    UNKNOWN = "unknown"
```

### 5.10 RepairProposal

```python
class RepairProposal(BaseModel):
    proposal_id: str
    scenario_id: str
    spec_version: int
    base_code_version: int
    reason: str
    changed_files: list[str]
    semantic_change_detected: bool
    expected_result_changed: bool
    confidence: float
    validation_results: list[str]
    branch_name: str | None = None
    pull_request_url: str | None = None
```

---

## 6. 新規コンポーネント

### 6.1 Specification Generator

新規:

```text
src/maf_e2e/specification_generator.py
```

責務:

- `DiscoveryFindings` から `TestSpecification` を生成
- expected result を `AssertionSpec` に変換
- stable `scenario_id` を生成
- unsupported assertion を明示

### 6.2 Playwright Code Generator

新規:

```text
src/maf_e2e/playwright_codegen.py
```

責務:

- `TestSpecification` から TypeScript を決定論的に生成
- Locator strategy を Playwright API に対応付け
- spec metadata header を付与
- source hash を計算

第一選択はテンプレートベースの決定論的生成とする。

```python
def generate_playwright_test(spec: TestSpecification) -> str:
    ...
```

LLM に TypeScript 全体を自由生成させる方式を第一選択にしない。

### 6.3 Draft Asset Store

新規:

```text
src/maf_e2e/asset_store.py
```

Draft 構成:

```text
<workspace>/.maf-e2e/drafts/<scenario-id>/
  specification.yaml
  generated.spec.ts
  metadata.json
  trial-result.json
  artifacts/
```

責務:

- atomic write
- Hash 検証
- Draft retention
- Reject 時の削除または監査保管

### 6.4 Code Validator

新規:

```text
src/maf_e2e/code_validation.py
```

責務:

- format
- lint
- type-check
- Playwright test discovery
- timeout
- stdout / stderr capture
- output size limit

package manager 検出順:

1. `pnpm-lock.yaml`
2. `yarn.lock`
3. `package-lock.json`
4. npm

既存 Playwright 設定を優先し、上書きしない。

### 6.5 Trial Runner

新規:

```text
src/maf_e2e/trial_runner.py
```

責務:

- MAF Workflow 内の決定論的 `TrialRunExecutor` から呼び出す
- 生成済み `.spec.ts` を標準 Playwright Test Runner で実行
- 1シナリオ単位で実行
- Screenshot / Trace / HTML / JSON / JUnit を収集
- `TrialRunResult` を生成
- subprocess timeout と出力上限を管理

試行実行では Browser Agent / CodeAct にシナリオを再解釈させない。

失敗時は `TrialFailureDiagnosticExecutor` へ遷移する。

### 6.6 Trial Failure Diagnostic

新規:

```text
src/maf_e2e/trial_diagnostics.py
```

責務:

- MAF Agent + Hyperlight CodeAct + Playwright MCP で失敗画面を再調査
- 現在 DOM、画面遷移、Locator 候補を取得
- アプリ不具合、生成コード問題、環境問題を切り分け
- 診断結果を構造化して返す

### 6.7 Approval Store

新規:

```text
src/maf_e2e/approval_store.py
```

責務:

- Approve
- Request Changes
- Reject
- reviewer identity
- approval history
- `spec_hash` / `code_hash`
- Publish eligibility 判定

### 6.8 Publisher

新規:

```text
src/maf_e2e/publisher.py
```

Published 構成:

```text
<target-repository>/e2e/
  generated/<feature>/<scenario-id>.spec.ts
  specs/<feature>/<scenario-id>.v<version>.yaml
  metadata/<feature>/<scenario-id>.json
```

責務:

- target repository 外への書き込み禁止
- path traversal 防止
- Hash 再検証
- atomic Publish
- ACTIVE state 更新

### 6.9 Regression Runner

新規:

```text
src/maf_e2e/regression_runner.py
```

責務:

- ACTIVE シナリオを標準 Playwright Runner で実行
- Agent backend 設定なしで実行可能
- JSON / JUnit / HTML
- CI exit code
- scenario filter

### 6.10 Failure Analyzer

新規:

```text
src/maf_e2e/failure_analysis.py
```

入力:

- Playwright result
- Screenshot
- Trace
- Console
- Network
- 前回成功結果
- CodeAct による現在 UI 調査結果

出力:

- `FailureCategory`
- confidence
- evidence
- recommended action

### 6.11 Repair Generator

新規:

```text
src/maf_e2e/repair.py
```

責務:

- 承認済み spec / code の読み込み
- CodeAct 調査結果の利用
- Locator / wait / iframe / dialog 等だけを修正
- expected result 不変検査
- semantic change 検査
- 修復案生成

### 6.12 GitHub Repair Publisher

新規:

```text
src/maf_e2e/github_repair.py
```

責務:

- branch create
- file update
- commit
- push
- Pull Request create
- PR body 生成

GitHub 実装は interface 化する。

```python
class RepositoryPublisher(Protocol):
    async def create_branch(...): ...
    async def commit_files(...): ...
    async def create_pull_request(...): ...
```

---

## 7. Workflow 改修

### 7.1 Workflow 分割

既存 `build_e2e_test_workflow` を肥大化させない。

追加:

```python
build_authoring_workflow(...)
build_regression_analysis_workflow(...)
build_repair_workflow(...)
```

### 7.2 Executor 構成

Authoring:

```text
OrchestratorExecutor
DiscoveryExecutor
SpecificationGeneratorExecutor
PlaywrightCodeGeneratorExecutor
CodeValidationExecutor
TrialRunExecutor
TrialJudgeExecutor
ScenarioApprovalExecutor
PublisherExecutor
```

試行失敗分岐:

```text
TrialRunExecutor
→ TrialFailureDiagnosticExecutor
→ DraftCodeRepairExecutor
→ SemanticChangeGuardExecutor
→ CodeValidationExecutor
→ TrialRunExecutor
```

### 7.3 既存 BrowserExecutor の扱い

既存 `BrowserExecutor` の責務を分割する。

残す責務:

- 未知画面探索
- 操作候補収集
- Locator 候補取得
- 失敗原因調査

移す責務:

- 承認対象コードの試行実行

正式な Trial Run は `TrialRunExecutor` が標準 Playwright Runner で行う。

### 7.4 Human Review の分離

現行 Human Review を次に分離する。

```text
OperationalHumanReviewExecutor
  - Safety
  - Stage failure
  - retry / abort

ScenarioApprovalExecutor
  - approve
  - request_changes
  - reject
```

### 7.5 Judge の扱い

一次判定:

- Playwright Assertion
- Exit code
- Test result

LLM Judge:

- 自由文 expected result の補助解釈
- 証跡要約
- 曖昧結果のレビュー補助

LLM Judge だけで passed にしてはならない。

---

## 8. CLI 改修

subcommand 形式へ変更する。

```text
maf-e2e author
maf-e2e review
maf-e2e approve
maf-e2e request-changes
maf-e2e reject
maf-e2e publish
maf-e2e regression
maf-e2e analyze-failure
maf-e2e repair
```

### 8.1 author

```bash
maf-e2e author \
  --target-repo /path/to/app \
  --target-url http://localhost:3000 \
  --objective "未認証状態でログイン画面が表示されること" \
  --expected-result "ログイン見出し、メールアドレス、パスワード、ログインボタンが表示される"
```

内部処理:

```text
MAF + CodeAct 探索
→ 仕様生成
→ コード生成
→ 標準 Runner 試行
→ Review package 作成
```

### 8.2 approve

```bash
maf-e2e approve \
  --scenario-id login-page-display \
  --reviewer user@example.com \
  --comment "期待どおり"
```

### 8.3 publish

```bash
maf-e2e publish --scenario-id login-page-display
```

Approve 済みかつ Hash 一致時のみ Publish する。

### 8.4 regression

```bash
maf-e2e regression \
  --target-repo /path/to/app \
  --environment staging
```

Agent backend 設定を要求しない。

### 8.5 repair

```bash
maf-e2e repair \
  --run-id <run-id> \
  --scenario-id login-page-display \
  --create-pr
```

---

## 9. Configuration 改修

追加設定:

```text
target_repository_root
draft_root
published_e2e_root
spec_root
metadata_root
playwright_config_path
package_manager
authoring_timeout_seconds
trial_timeout_seconds
regression_timeout_seconds
draft_retention_days
github_repository
github_base_branch
repair_branch_prefix
max_trial_repairs
```

Environment:

```python
class TargetEnvironment(StrEnum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
```

初期要件では production を拒否する。

---

## 10. テスト要件

### 10.1 単体テスト

追加:

```text
tests/test_specification_models.py
tests/test_playwright_codegen.py
tests/test_asset_store.py
tests/test_code_validation.py
tests/test_trial_runner.py
tests/test_trial_diagnostics.py
tests/test_approval_store.py
tests/test_publisher.py
tests/test_regression_runner.py
tests/test_failure_analysis.py
tests/test_repair.py
tests/test_github_repair.py
```

必須ケース:

- deterministic `scenario_id`
- deterministic `spec_hash`
- deterministic `code_hash`
- role locator generation
- label locator generation
- unsupported action rejection
- path traversal rejection
- approval hash mismatch rejection
- Publish before approval rejection
- Reject Draft cleanup
- expected result change detection
- semantic change detection
- Trial failure から CodeAct 診断への遷移
- CodeAct 操作成功だけでは承認不可
- Regression CLI が Agent provider なしで動作
- exit code mapping

### 10.2 統合テスト Fixture

```text
tests/fixtures/webapp/
```

最低シナリオ:

1. ログイン画面表示
2. 正常ログイン
3. 認証失敗
4. ボタン文言変更
5. DOM 階層変更
6. Locator 変更
7. expected result 不成立
8. 認証期限切れ
9. test data 不足
10. network failure

### 10.3 受入テスト

Authoring:

```text
自然言語要求
→ CodeAct 探索
→ Draft spec
→ TypeScript 生成
→ 標準 Runner 試行成功
→ Approval
→ e2e/** Publish
→ Regression 成功
```

試行修復:

```text
生成コード失敗
→ CodeAct 診断
→ Draft 修復
→ 標準 Runner 再実行
→ Approval
```

回帰修復:

```text
承認済みテスト
→ UI Locator 変更
→ Regression failed
→ CodeAct 再探索
→ TEST_MAINTENANCE
→ 修復 branch
→ Pull Request
→ expected result 不変
```

仕様変更:

```text
expected behavior 変更
→ Repair Guard で拒否
→ new spec version required
```

---

## 11. GitHub Actions

テンプレート:

```text
templates/github-actions/e2e-nightly.yml
```

```yaml
name: Nightly E2E

on:
  schedule:
    - cron: "0 18 * * *"
  workflow_dispatch:

jobs:
  e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
      - run: npm ci
      - run: npx playwright install --with-deps chromium
      - run: maf-e2e regression --target-repo . --environment staging
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: e2e-artifacts
          path: |
            playwright-report/
            test-results/
```

通常の nightly 実行では Agent 認証情報を不要とする。

失敗後の分析ジョブを別ジョブとして起動可能にする。

---

## 12. 実装順序

### PR-1 Domain models and lifecycle

- 新規モデル
- Lifecycle
- Hash utility
- tests

### PR-2 Deterministic Playwright generator

- Locator mapping
- Assertion mapping
- TypeScript template
- tests

### PR-3 Draft Asset Store and Code Validation

- Draft files
- validator
- subprocess abstraction
- tests

### PR-4 Trial Runner in MAF Workflow

- `TrialRunExecutor`
- 標準 Playwright Runner
- report parser
- Artifact collection
- tests

### PR-5 CodeAct Trial Diagnostics

- `TrialFailureDiagnosticExecutor`
- 現行 CodeAct provider 再利用
- Draft repair loop
- retry limit
- tests

### PR-6 Approval and Publish

- `ScenarioApproval`
- Approve / Request Changes / Reject
- Hash verification
- `e2e/**` Publish
- tests

### PR-7 CLI subcommands

- author
- review
- approve
- request-changes
- reject
- publish

### PR-8 Regression Runner and CI

- Agent 非依存 Runner
- exit code
- JUnit / HTML
- GitHub Actions template

### PR-9 Regression Failure Analysis

- CodeAct 再探索
- QA failure classification
- evidence
- tests

### PR-10 Repair Proposal

- approved asset loader
- semantic guard
- expected result guard
- repair validation

### PR-11 Git branch and Pull Request

- repository publisher abstraction
- branch
- commit
- PR
- integration test

---

## 13. 完了条件

1. 自然言語要求から MAF + CodeAct で対象画面を探索できる。
2. 探索結果から構造化シナリオを生成できる。
3. 構造化仕様から TypeScript Playwright コードを生成できる。
4. 生成コードを標準 Playwright Runner で試行実行できる。
5. 試行失敗時に MAF + CodeAct で診断できる。
6. Draft 修復後に標準 Runner で再検証できる。
7. コード、期待結果、証跡をシナリオ単位でレビューできる。
8. Approve されたコードだけを対象アプリの `e2e/**` へ保存できる。
9. Reject されたコードは正式資産へ残らない。
10. 承認時と Publish 時の Hash 不一致を拒否できる。
11. 承認済みテストを Agent 非依存で定期実行できる。
12. 回帰失敗時に MAF + CodeAct で現在 UI を再調査できる。
13. 失敗を QA 向けカテゴリへ分類できる。
14. Locator 変更に対する修復案を生成できる。
15. expected result 変更を自動修復として扱わない。
16. 修復ブランチと Pull Request を作成できる。
17. Agent は Pull Request を自動マージしない。
18. CodeAct の直接操作結果と承認対象コードの実行結果を混同しない。
19. 単体テスト、統合テスト、受入テストが成功する。
20. README が本指示書のフローと一致する。

---

## 14. Codex 実行時の制約

- 既存機能を一括削除しない。
- 大規模な1 PRにしない。
- 上記 PR 順に小さく実装する。
- 各 PR で `ruff`, `mypy`, `pytest` を通す。
- subprocess 実行には timeout と出力上限を設ける。
- target repository 外への書き込みを禁止する。
- path traversal を防止する。
- production 実行を追加しない。
- expected result の自動変更を実装しない。
- 修復 Pull Request の自動マージを実装しない。
- 構造化仕様からの決定論的コード生成を優先する。
- LLM に TypeScript 全体を自由生成させる方式を第一選択にしない。
- MAF / Hyperlight CodeAct / Playwright MCP を廃止しない。
- CodeAct は探索、診断、修復調査に限定して利用する。
- CodeAct が直接操作して成功しただけで生成コードを承認可能にしない。
- 承認前に、同一 `code_hash` のコードを標準 Playwright Runner で必ず実行する。
- 通常の Regression Runner は Agent provider なしで動作可能にする。
