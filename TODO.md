# TODO（残タスク）

凡例: `[ ]` 未着手 / `[x]` 完了。優先度は上から。

## 開発

- [x] **Claude Haiku 5.5 の Marketplace 契約**: 2026-11-22 10:47 UTC（19:47 JST）に承諾済み。契約ID `agmt-b398skur7br2wqwqzvp1aovgm` / オファー `offer-u3aih6zr7uw5u` / 製品 `prod-6cyn7tgqazjhu`（Anthropic, PBC「Claude Haiku 5.5 (Amazon Bedrock Edition)」）。`GetFoundationModelAvailability` は `agreementAvailability=AVAILABLE`
- [ ] **モデルアクセスの付与待ち（AWS 側・利用者のアカウント課題）**: 契約から3.5時間（2026-11-22 14:15 UTC 時点）経過しても推論は `AccessDeniedException`。原因は Service Quotas の `Cross-region model inference tokens per minute for Anthropic Claude Haiku 5.5` が **0**（既定は1,000万なので quota 引き上げではなく、アクセス自体が未付与）。`tools/probe_haiku55.py` で監視し、付与されたら Playground と `POST /karte/{id}/copy` の `model` が 5.5 になることを確認。AWS サポート窓口への起票が必要（下の「連絡用メモ」）
- [x] Haiku 5.5 のバッチ推論の可否を確認 → **非対応**（実機で拒否を確認。文案生成は即時APIのみ）
- [x] **代替モデルへの自動フォールバックを削除**（黙って別モデルに切り替わらないようにする。使えなければエラー）
- [x] **退会データが残りうる場所の棚卸しと対処**（原則7に関わる）
  - [x] CloudWatch Logs: 会員IDは `member_tag`（SHA-256 先頭10桁）のみ。APIアクセスログは `resourcePath`（テンプレート）だけを記録。E2E で 0 件を検証
  - [x] DynamoDB の PITR 明示無効（`pointInTimeRecoverySpecification: false`）と運用の明文化（docs/operations.md）
  - [x] Personalize の学習モデルの残存対策: **Solution Version 単体の削除 API は存在しない**ため、FULL 成功後に Solution ごと削除し、次回は最新データで作り直す（`reset_solution`）
- [x] **セットアップ画面**（`setup/`、`newcleus/setup` 方式）
  - [x] デプロイ / 更新 / 削除（CDK 実行、非同期＋ログ表示）
  - [x] FULL の手動起動と実行状況表示
  - [x] 保持年数 N・週次スケジュール・通知先・IP制限・監査の設定
  - [x] PUT先S3パス・ファイル仕様・APIエンドポイント・curl 例の案内、APIセルフテスト
  - [x] ローカル限定（127.0.0.1 + Host検査）/ Basic認証 / キーはメモリのみ（ディスクに書かない）
- [x] **障害通知**: Step Functions の失敗（FAILED/TIMED_OUT/ABORTED）と Lambda エラーを SNS `karte-alerts` へ。メール購読は `-c alertEmail=`
- [x] 拒否行・`skipped_files` の**急増**を検知するアラーム（build が CloudWatch 名前空間 `P121K` に `RejectedRows` / `SkippedFiles` / `Members` / `BatchUsers` を発行し、しきい値超過で SNS 通知。実機で `RejectedRows=2` の受信を確認）
- [x] build 失敗時に中途半端な状態にならないことの確認（「読み切ってから書く」順序。docs/operations.md に明記）
- [x] 自社側ツールの参考実装: `tools/ingest_side.py`（HMAC仮名化・問い合わせの分類コード化・`withdraw` 出力）
- [x] **自社サーバ用 IAM**: **IAM ロール方式を既定**にした（`p121k-upload` / `p121k-api` の2ロール、長期キーなし）。EC2/ECS/EKS/OIDC/Roles Anywhere/別アカウントの手順は `docs/iam-for-callers.md`。**長期アクセスキーは禁止**（IAM ユーザーもキーも作らない）。運用者のデプロイ資格情報のみ例外
- [x] 監査: CloudTrail データイベント（`-c auditTrail=true`）と API Gateway アクセスログ
- [ ] AWS Config ルール（暗号化・公開禁止・保持期間の継続監視）
- [x] スケール: build を Glue / Fargate に置き換える閾値と設計（docs/operations.md §6。目安は build 10分超）
- [x] **LICENSE の決定**: **MIT**（`LICENSE`、`setup/package.json` / `infra/package.json` の `license`、README の「ライセンス」節）

## テスト

- [x] GitHub Actions で `pytest` と `npx jest` を自動実行
- [ ] Personalize のオフライン評価（precision@k / coverage、時系列分割）
- [x] 退会 → FULL → Personalize 入力 CSV に当該会員が含まれないことの自動検査（E2E）
- [ ] S3 ライフサイクルによる古いファイル削除の実機確認（保持年数を短くして検証。反映に日数が必要）
- [x] 権限テスト: 署名なし 403、`karte-api-invoke` を持たない IAM ユーザー（正しい署名）403。E2E に組込み
- [ ] 別アカウントからの呼び出し拒否の確認
- [ ] `allowedCidrs`（IP制限）と `weeklyFull=true`（週次起動）の実機確認
- [ ] Bedrock 呼び出しが `jp.*` 経由（東京・大阪のみ）であることを CloudTrail で確認（モデル有効化後）
- [x] 日本語の伏字化のテスト拡充（`tests/test_pii.py`: 誤検知しないこと・名前は文脈のみ）
- [x] 伏字化の位置づけを整理: **個人情報は PUT しない**（原則4）ため主防御ではない。万一の混入に備えた保険としてのみ維持し、精度評価は対象外とする
- [ ] 10万会員規模の負荷試験（build の所要時間とメモリ、Lambda 15分上限の余裕）

## ドキュメント

- [ ] 法務確認: 仮名ID運用（個人情報保護法）、退会後の取扱い、再入会は新IDで別人扱いとする運用
- [ ] REES46 へ利用目的を確認（「無料・出典明記」が正式な許諾文でないため）
- [x] Haiku 5.5 の単価を確認（Marketplace オファーの料金表。JPプロファイル $0.11/$0.55）
- [x] 運用手順書: `docs/operations.md`（PUT、FULL、拒否レポート、障害対応、監査、後片付け、規模）
- [x] データ仕様のサンプルCSV: `docs/samples/`
- [x] データ主権の方針: **AWS の日本リージョン（東京・大阪）で完結**。東京単独にはこだわらないため、代替案の資料は不要と判断して削除

## 連絡用メモ（AWS サポートへ）

```
アカウント: 210387976006
リージョン: ap-northeast-1（ap-northeast-3 でも同じ）
モデル: anthropic.claude-haiku-5-5（推論プロファイル jp.anthropic.claude-haiku-5-5）
事象: Marketplace オファー offer-u3aih6zr7uw5u を 2026-11-22T10:47Z に承諾し、
      契約 agmt-b398skur7br2wqwqzvp1aovgm が作成済み。
      GetFoundationModelAvailability は agreementAvailability=AVAILABLE,
      entitlementAvailability=AVAILABLE, authorizationStatus=AUTHORIZED。
      しかし Converse / InvokeModel は
      "anthropic.claude-haiku-5-5 is not available for this account."
2時間以上経過しても解消しない。
Service Quotas では Haiku 5.5 の TPM が 0（既定は 10,000,000）、
Haiku 4.5 は 5,000,000 で正常に利用できる。
モデルアクセスの付与をお願いしたい。
```

## Haiku の扱い（決定）

- 実装は **Haiku 5.5 前提**のまま（代替モデルへの自動切替はしない）。
- 利用者の動作確認だけ **`jp.anthropic.claude-haiku-4-5-20251001-v1:0` で10件**行う:
  `python tools/smoke_copy.py --count 10 --model jp.anthropic.claude-haiku-4-5-20251001-v1:0`
- 文案は**リアルタイム生成**（`POST /karte/{id}/copy`）。`direction` で訴求点・文体・長さを指示できる。
