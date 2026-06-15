# E2ETestMAF プロダクト要件定義書

## 1. 文書目的

本書は、E2ETestMAF のプロダクト目的、利用者、処理ワークフロー、機能要件、非機能要件、ドメインモデル、MVP 範囲および実装優先順位を定義する。

本プロダクトは、開発中の Web アプリケーションに対する回帰試験の作成・承認・定期実行・保守を支援し、開発者の試験工数を削減するとともに、開発中ソフトウェアの品質を一定水準まで継続的に担保することを目的とする。

---

## 2. プロダクト定義

本プロダクトは、自然言語で与えられたテスト要求に対して対象 Web アプリケーションを探索し、構造化テスト仕様と実行可能な Playwright テストコードを生成する。

生成コードを対象環境で試行実行し、期待結果、実行結果、生成コード、スクリーンショット、Trace 等の証跡をシナリオ単位で人間へ提示する。

人間が承認したシナリオだけを対象アプリケーションリポジトリ内の `e2e/**` へ正式保存し、以後の定期回帰テストとして有効化する。

定期実行で失敗した場合は、Agent が原因を分析し、アプリケーション不具合、テスト保守、環境障害、認証障害、テストデータ障害、Flaky failure、判定不能に分類する。

テスト保守が妥当と判断された場合、Agent は修復ブランチと Pull Request を作成し、人間のレビュー後に反映する。

期待結果の変更はテスト修復として扱わず、新しいテスト仕様バージョンを作成して再承認する。

---

## 3. 対象利用者

### 3.1 Web アプリケーション開発者

- 自然言語によるテスト要求の登録
- 生成されたシナリオ、コード、試行結果の確認
- シナリオ承認、修正依頼、却下
- 定期実行結果の確認
- 修復 Pull Request のレビュー

### 3.2 開発管理者

- テストスイート全体の進捗確認
- 定期実行設定
- シナリオの有効化、無効化
- 品質状況、失敗傾向の確認
- 修復 Pull Request のレビュー

### 3.3 品質管理部門

- テスト目的、前提条件、期待結果の確認
- シナリオ単位の承認
- 実行証跡、監査履歴の確認
- 不具合候補の確認

---

## 4. コア設計原則

1. 承認単位はシナリオ単位とする。
2. 人間は、仕様だけでなく、生成コードと実際の試行結果を確認して承認する。
3. 承認前のコードは一時成果物として扱い、正式な `e2e/**` には保存しない。
4. 承認されたコードだけを対象アプリケーションの `e2e/**` へ保存する。
5. 通常の定期回帰実行では LLM をブラウザ操作経路に含めず、固定された Playwright コードを実行する。
6. Agent は生成、分析、修復提案を担当する。
7. 人間は仕様承認、期待結果承認、修復 Pull Request のレビューを担当する。
8. Agent は承認済み期待結果を自動変更してはならない。
9. 期待結果の変更は新しい仕様バージョンとして扱い、再承認を必須とする。
10. Agent による修復はブランチと Pull Request を介して反映し、自動マージしない。

---

## 5. テスト作成・承認ワークフロー

```text
自然言語によるテスト要求
  ↓
対象 Web アプリケーション探索
  ↓
構造化テスト仕様生成
  ↓
Playwright コード生成
  ↓
コード事前検証
  ├─ 構文チェック
  ├─ format
  ├─ lint
  ├─ type-check
  └─ Playwright test discovery
  ↓
対象環境で試行実行
  ↓
実行結果・期待結果・証跡の収集
  ↓
実行後検証
  ├─ Assertion 判定
  ├─ Console error 確認
  ├─ Network error 確認
  ├─ Trace 確認
  └─ 再実行可能性確認
  ↓
シナリオ単位の人手レビュー
  ├─ 承認
  │    ↓
  │  対象アプリケーションの e2e/** へ保存
  │    ↓
  │  承認済み回帰テストとして有効化
  │
  ├─ 修正依頼
  │    ↓
  │  仕様またはコードを修正
  │    ↓
  │  コード検証・試行実行を再実施
  │    ↓
  │  再レビュー
  │
  └─ 却下
       ↓
     正式資産へ昇格せず破棄
```

### 5.1 利用者視点の簡略フロー

```text
自然言語で試験内容を依頼
  ↓
Agent が実際に動く Playwright コードを作成して実行
  ↓
人間がコードと実行結果を確認
  ├─ OK → 回帰テストとして登録
  ├─ 修正 → 再生成・再実行・再確認
  └─ NG → 破棄
```

---

## 6. 定期回帰テストワークフロー

```text
スケジュール起動
  ↓
ACTIVE 状態の承認済み Playwright テストを取得
  ↓
標準 Playwright Test Runner で実行
  ↓
結果・証跡を収集
  ├─ 成功
  │    └─ 結果保存・通知
  │
  └─ 失敗
       ↓
     失敗分析
       ├─ APPLICATION_DEFECT
       ├─ TEST_MAINTENANCE
       ├─ ENVIRONMENT_FAILURE
       ├─ AUTHENTICATION_FAILURE
       ├─ TEST_DATA_FAILURE
       ├─ FLAKY_FAILURE
       └─ UNKNOWN
```

通常の回帰試験は、固定された Playwright コードを決定論的に実行する。Agent は、失敗後の分析が必要な場合にだけ起動する。

---

## 7. テスト修復ワークフロー

```text
回帰テスト失敗
  ↓
失敗原因分析
  ↓
TEST_MAINTENANCE と判定
  ↓
修復可能範囲を検査
  ├─ Locator
  ├─ DOM 探索方法
  ├─ iframe / dialog 操作
  ├─ wait 条件
  ├─ timeout
  ├─ 軽微な表示文言変更
  └─ Playwright API 利用方法
  ↓
期待結果・Assertion の意味が不変であることを確認
  ↓
修復ブランチ作成
  ↓
コード修正
  ↓
対象シナリオと関連シナリオを再実行
  ↓
Pull Request 作成
  ↓
人間によるレビュー
  ├─ Approve and Merge
  ├─ Request Changes
  └─ Close
```

以下の変更が必要な場合は修復を中止し、仕様変更フローへ移行する。

- expected result
- Assertion の意味
- 業務ルール
- 正常・異常判定条件
- 権限要件
- 遷移先の業務的意味
- 計算結果
- 入力必須条件
- シナリオ目的

---

## 8. 機能要件

### FR-01 テスト要求登録

利用者は自然言語または構造化フォームでテスト要求を登録できること。

```yaml
target_url:
objective:
business_context:
preconditions:
test_data:
expected_results:
policies:
allowed_origins:
prohibited_actions:
max_scenarios:
max_steps:
```

必須項目:

- 対象 URL
- テスト目的
- 期待結果

自然言語入力は Agent が構造化入力へ変換する。

### FR-02 対象アプリケーション探索

Agent はテスト目的の達成に必要な範囲で対象 Web アプリケーションを探索すること。

探索結果には以下を含める。

- 到達画面
- URL
- 操作可能要素
- 画面遷移
- 認証要否
- 必要なテストデータ
- 想定ユーザーフロー
- 破壊的操作の可能性
- 未確認領域
- Console / Network error

探索には上限を設ける。

```yaml
max_pages:
max_actions:
max_duration_seconds:
allowed_origins:
prohibited_actions:
```

### FR-03 構造化テスト仕様生成

探索結果と要求からシナリオ単位の構造化仕様を生成すること。

```yaml
scenario_id:
name:
objective:
priority:
preconditions:
test_data:
steps:
assertions:
cleanup:
prohibited_actions:
risk_level:
```

各操作は可能な限り構造化する。

```yaml
- step_id: login-step-01
  action: navigate
  target: /login
```

```yaml
- step_id: login-step-02
  action: fill
  locator:
    strategy: role
    role: textbox
    name: メールアドレス
  value_ref: test_users.standard.email
```

期待結果は機械判定可能な Assertion へ変換する。

```yaml
assertions:
  - assertion_id: login-assert-01
    type: visible
    locator:
      strategy: role
      role: heading
      name: ログイン
```

### FR-04 Playwright コード生成

構造化仕様から TypeScript の Playwright テストコードを生成すること。

承認前コードは正式な `e2e/**` に保存せず、一時作業領域へ保存する。

```text
.maf-e2e/
  drafts/
    login-page-display/
      specification.yaml
      login-page-display.spec.ts
      metadata.json
```

生成コードには以下のメタデータを保持する。

```yaml
scenario_id:
spec_version:
spec_hash:
generated_at:
generator_version:
```

### FR-05 コード検証・試行実行

生成コードに対し、以下を実行する。

- format
- lint
- type-check
- Playwright test discovery
- 対象環境での試行実行

試行実行では以下を記録する。

- ステップ単位の成否
- Assertion 単位の成否
- 実際値
- 期待値
- スクリーンショット
- Playwright Trace
- Console error
- Network error
- URL
- Locator
- 実行時間
- エラー詳細

### FR-06 シナリオ単位レビュー

レビュー対象は以下の一式とする。

- テスト目的
- 前提条件
- テストデータ
- 操作手順
- expected result
- Assertion
- 生成 Playwright コード
- 試行実行結果
- スクリーンショット
- Trace
- Console / Network error
- 副作用
- 禁止操作
- 未確認事項

レビュー操作:

- Approve
- Request Changes
- Reject

承認記録:

```yaml
scenario_id:
spec_version:
approved_by:
approved_at:
approval_comment:
spec_hash:
code_hash:
```

### FR-07 承認済み資産の正式保存

承認済みコードを対象アプリケーションリポジトリ内の `e2e/**` へ保存すること。

推奨構成:

```text
e2e/
  generated/
    login/
      login-page-display.spec.ts
  specs/
    login/
      login-page-display.v1.yaml
  metadata/
    login/
      login-page-display.json
```

承認時のコードと保存コードが同一であることを Hash で保証する。承認後にコード内容が変更された場合、その承認を無効とする。

### FR-08 テスト資産ライフサイクル管理

状態:

```text
DRAFT
GENERATED
VALIDATING
TRIAL_PASSED
PENDING_APPROVAL
APPROVED
ACTIVE
REPAIR_PENDING
DISABLED
RETIRED
REJECTED
```

同一仕様に対する Locator 修復は、仕様バージョンを変えずコードバージョンのみ更新できる。期待結果を変更する場合は仕様バージョンを更新し、再承認する。

### FR-09 定期回帰テスト実行

`ACTIVE` 状態のテストを定期実行できること。

対象環境:

- localhost
- 開発環境
- staging 環境

初期要件では production を対象外とする。

外部スケジューラーとして以下を利用可能とする。

- GitHub Actions
- Azure DevOps Pipelines
- cron
- Azure Container Apps Jobs
- Azure VM

CLI は以下を出力する。

- 終了コード
- JSON レポート
- JUnit XML
- HTML レポート
- Screenshot
- Trace
- Artifact

推奨終了コード:

```text
0: 全テスト成功
1: アプリケーション不具合候補
2: テスト修復候補
3: BLOCKED
4: 構成エラー
```

### FR-10 失敗分類

失敗を以下に分類する。

```text
APPLICATION_DEFECT
TEST_MAINTENANCE
ENVIRONMENT_FAILURE
AUTHENTICATION_FAILURE
TEST_DATA_FAILURE
FLAKY_FAILURE
UNKNOWN
```

### FR-11 修復案生成

`TEST_MAINTENANCE` と判断された場合だけ修復案を生成する。

```yaml
failure_reason:
affected_scenarios:
original_code:
proposed_code:
changed_locators:
semantic_change_detected:
expected_result_changed:
validation_results:
confidence:
artifacts:
```

`expected_result_changed=true` または `semantic_change_detected=true` の場合は、自動修復フローを停止する。

### FR-12 修復ブランチ・Pull Request 作成

Agent は修復用ブランチを作成する。

```text
agent/e2e-repair/<scenario-id>-<date>
```

以下を検証した後に Pull Request を作成する。

- lint
- type-check
- 対象シナリオ単独実行
- 関連シナリオ実行
- expected result 不変確認

初期リリースでは自動マージしない。

---

## 9. ドメインモデル

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

```python
class LocatorSpec(BaseModel):
    strategy: str
    role: str | None = None
    name: str | None = None
    value: str | None = None
```

```python
class StructuredStep(BaseModel):
    step_id: str
    action: str
    target: str | None = None
    locator: LocatorSpec | None = None
    value_ref: str | None = None
```

```python
class AssertionSpec(BaseModel):
    assertion_id: str
    type: str
    locator: LocatorSpec | None = None
    expected: Any
    source_expected_result: str
```

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

```python
class GeneratedTestAsset(BaseModel):
    scenario_id: str
    spec_version: int
    code_version: int
    draft_path: str
    published_path: str | None
    source_hash: str
    validated: bool
    generated_at: datetime
```

```python
class ScenarioApproval(BaseModel):
    scenario_id: str
    spec_version: int
    code_version: int
    approved_by: str
    approved_at: datetime
    approval_comment: str | None
    spec_hash: str
    code_hash: str
```

```python
class RegressionRun(BaseModel):
    run_id: str
    repository: str
    git_commit: str
    environment: str
    started_at: datetime
    completed_at: datetime | None
    scenario_results: list[ScenarioRunResult]
```

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

```python
class RepairProposal(BaseModel):
    proposal_id: str
    scenario_id: str
    spec_version: int
    base_code_version: int
    branch_name: str
    pull_request_url: str | None
    reason: str
    changed_files: list[str]
    semantic_change_detected: bool
    expected_result_changed: bool
    confidence: float
    validation_results: list[str]
```

---

## 10. 非機能要件

### 10.1 再現性

承認済みテストの通常実行経路では LLM に操作を判断させない。

### 10.2 監査性

以下を追跡可能とする。

- 承認者
- 承認仕様バージョン
- 承認コード Hash
- 生成モデル・Generator バージョン
- 実行 Git commit
- 実行結果
- 修復前後差分
- 修復理由
- Pull Request レビュー履歴

### 10.3 安全性

- localhost / 開発 / staging を対象とする
- production は初期要件では拒否する
- 許可 Origin 外への遷移を制限する
- 破壊的操作を禁止する
- 実課金を禁止する
- 認証情報をログやスクリーンショットへ出力しない
- Agent 生成コードは静的検査後に実行する

### 10.4 可用性

Agent API が利用できない場合でも、承認済み Playwright テストは実行可能であること。失敗分析は後から再実行可能とする。

### 10.5 利用性

非エンジニアが Playwright コードを理解しなくても、以下を確認できること。

- テスト目的
- 操作手順
- 期待結果
- 試行結果
- 承認状態
- 定期実行結果
- 不具合候補
- 修復内容
- Pull Request 状態

---

## 11. MVP 範囲

### 11.1 MVP 対象

- localhost
- 開発環境
- staging
- Chromium
- TypeScript Playwright
- 同一 Origin 中心
- storageState 認証
- 対象アプリリポジトリ内の `e2e/**`
- シナリオ単位承認
- GitHub Actions による nightly 実行
- GitHub Pull Request による修復レビュー

### 11.2 MVP で実装するもの

1. 自然言語テスト要求
2. 対象アプリ探索
3. 構造化テスト仕様生成
4. Playwright コード生成
5. コード検証
6. 試行実行
7. シナリオ単位レビュー
8. 承認後の `e2e/**` 保存
9. 定期実行
10. レポート
11. 失敗分類
12. Locator 修復案
13. 修復ブランチ作成
14. Pull Request 作成
15. 仕様・コードバージョン管理

### 11.3 MVP 対象外

- production 試験
- 自動マージ
- expected result の自動変更
- 完全自動テスト修復
- 複数ブラウザ
- モバイル
- Visual Regression
- DB 直接 Assertion
- 高度なテストデータ生成
- 複数 Git ホスティングサービス
- 多数モデル Provider の完全互換

---

## 12. 実装優先順位

### Phase 1: 構造化仕様と Draft 資産

- TestSpecification
- StructuredStep
- AssertionSpec
- LocatorSpec
- TestLifecycleStatus
- Draft 保存領域
- spec_hash / code_hash

### Phase 2: Playwright コード生成・検証・試行実行

- TypeScript Generator
- format / lint / type-check
- Playwright test discovery
- 試行実行
- Screenshot / Trace / Console / Network 収集

### Phase 3: シナリオ承認・正式保存

- Approve / Request Changes / Reject
- ScenarioApproval
- 承認前後 Hash 検証
- `e2e/**` への publish
- ACTIVE 化

### Phase 4: 定期回帰実行

- Regression CLI
- GitHub Actions workflow
- JSON / JUnit / HTML レポート
- Artifact
- 終了コード
- 実行履歴

### Phase 5: 失敗分類・修復

- FailureCategory
- 失敗分析
- semantic change guard
- expected result change guard
- 修復ブランチ
- Pull Request
- 再実行検証

### Phase 6: 管理 UI

- 承認待ち一覧
- シナリオ詳細
- コード・証跡表示
- 実行履歴
- 失敗詳細
- 修復 Pull Request 状態
