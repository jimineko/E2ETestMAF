# E2ETestMAF 要件準拠状況

最終更新: 2026-06-16

## 判定

`E2ETestMAF_requirements.md` のMVPに対し、Agent-assisted Playwright regression
test lifecycle の主要経路は実装済みです。承認対象は生成済みTypeScriptと構造化仕様であり、
Trial、Publish、Regressionは標準Playwright Runnerを使います。CodeAct/MCPは探索、診断、
調査に限定され、通常Regressionのブラウザ操作経路には入りません。

ただし、探索上限の厳密な実行時強制や、あらゆるPlaywright API差分を自動生成する修復器など、
一部はMVPとしての安全な部分実装に留めています。管理UI、production、Visual Regression、
複数ブラウザ、自動マージは対象外です。

## 実行経路

| 経路 | ブラウザ操作 | 用途 | 承認・Publish可否 |
|---|---|---|---|
| `maf-e2e author` | 探索はCodeAct/MCP、Trialは生成済みTypeScript | 回帰資産作成 | 同一HashのTrial成功後のみ可 |
| `maf-e2e review/approve/publish` | ブラウザ操作なし | 人間レビューと正式保存 | Hash再検証後のみ可 |
| `maf-e2e regression` | 標準Playwright Runner | ACTIVE資産の定期実行 | Agent・LLM・MCP不要 |
| `maf-e2e analyze-failure --investigate` | CodeAct/MCP | 失敗後の現在UI調査 | 調査結果だけでは承認不可 |
| `maf-e2e repair --create-pr` | 静的検証と標準Playwright Runner | 限定修復PR作成 | 自動マージなし |
| サブコマンドなしCLI | Browser Executor + MCP | 単発QA、後方互換 | 回帰資産としては承認不可 |

## 機能要件対応表

| 要件 | 状況 | 実装状況 |
|---|---|---|
| FR-01 テスト要求登録 | Implemented | `author` CLI、`E2ETestRequest`、期待結果、禁止操作、探索上限、対象環境を保持 |
| FR-02 対象アプリ探索 | Partial | FR-02証跡フィールドと探索上限をschema/promptに追加。上限はAgent promptで制約し、実行時の厳密カウンタ強制は未実装 |
| FR-03 構造化仕様生成 | Implemented | `SpecificationGeneratorExecutor` と `domain/specification.py` が仕様、Locator、操作、Assertionを型で制限 |
| FR-04 Playwrightコード生成 | Implemented | 決定論的Generator、許可Locator/操作/Assertion、`generated_at`監査ヘッダー、`spec_hash`/`generator_version`ヘッダー |
| FR-05 検証・試行実行 | Implemented | format/lint/type-check/discovery、標準Playwright Trial、JSON/JUnit/HTML、Screenshot、Trace、Console/Network、Step/Assertion証跡 |
| FR-06 シナリオレビュー | Implemented | `review`、`approve`、`request-changes`、`reject`、レビュー履歴とrequest-changesコメント表示 |
| FR-07 正式保存 | Implemented | `publish` が `e2e/generated`、`e2e/specs`、`e2e/metadata` に限定し、承認Hashを再検証 |
| FR-08 ライフサイクル | Implemented | `disable`、`retire`、`new-version`、ACTIVE/DISABLED/RETIRED選択、spec/code version管理 |
| FR-09 定期回帰実行 | Implemented | ACTIVE metadataのみを標準Playwright Runnerで実行。GitHub Actions、Docker、Azure例は`regression`経路 |
| FR-10 失敗分類 | Implemented | 7カテゴリ分類、前回成功Regression履歴参照、Agent調査の再実行、exit code `2`連携 |
| FR-11 修復案生成 | Partial | `TEST_MAINTENANCE`限定、expected result/semantic guard、Locator診断修復、code-only安全差分。全API種別の自動修復生成は限定的 |
| FR-12 修復PR作成 | Implemented | Repository Publisher Protocol、branch/commit/push/PR作成、Hash/diff/証跡入りPR本文、自動マージなし |

## 重要な保証

- `regression` はAgent設定、モデルProvider、MCPサーバ、Agent認証を必要としません。
- 承認後の仕様・コード変更は `spec_hash` / `code_hash` 不一致で拒否されます。
- `code_hash` は `// generated_at:` 監査ヘッダーを除外したTypeScriptソースのSHA-256です。
- `production` はCLI入力検証で拒否され、許可環境は `local`、`development`、`staging` です。
- 修復は期待結果やシナリオ目的を変更できません。意味変更が必要な場合は `new-version` で再Trial/再承認します。
- RejectはDraftを正式領域から除去し、監査記録を `.maf-e2e/rejected` に残します。

## 既知の制限

- 管理UIは未実装です。CLIとJSON review packageで代替しています。
- Discoveryの `max_pages`、`max_actions`、`max_duration_seconds` はAgent promptへ渡しますが、CodeAct/MCP呼び出し回数の統一的なハードリミットとしては未完成です。
- 修復生成はLocator診断と安全判定可能なcode-only差分が中心です。iframe/dialog/waitなどの修復は、承認済み仕様の意味を変えない範囲での手動提案または将来拡張です。
- production、複数ブラウザ、モバイル、Visual Regression、PR自動マージはMVP対象外です。

## 検証コマンド

各改修Issue完了時に以下を実行しています。

```bash
uv run ruff check .
uv run mypy
uv run pytest
```
