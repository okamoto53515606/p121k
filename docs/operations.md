# 運用ガイド（P121K）

前提: デプロイは `AWS_PROFILE=<profile> AWS_REGION=ap-northeast-1`、スタック名 `KarteStack`。
値は `data/outputs.json`（`cdk deploy --outputs-file`）に保存したものを使う。

## 1. 日次・週次の運転

| 何を | いつ | 方法 |
|---|---|---|
| 行動履歴・問い合わせの PUT | 常時（追記） | 自社サーバから `s3://<Bucket>/inbound/{events,inquiries}/` へ。items は最新ファイルが全件スナップショット |
| FULL 再構築＋レコメンド | 週1（日曜 03:00 JST を推奨） | `-c weeklyFull=true` で自動。手動は Step Functions `karte-reco-pipeline` を `{}` で開始 |
| build だけ試す | 随時 | Lambda `Pipeline` を `{"action":"build"}` で実行 |
| 退会の即時反映 | 随時 | `DELETE /karte/{id}`（S3 に `withdraw` を記録） |

`withdraw` 行は**消さない**。S3 の履歴に残っている限り、毎回の FULL でその会員は丸ごと除外される。

## 2. 失敗したときの見方

1. **通知**: `karte-alerts`（SNS）に届く。メール購読は `-c alertEmail=...` で作成。
   - Step Functions の `FAILED / TIMED_OUT / ABORTED`
   - Lambda のエラー（pipeline / api）
2. **実行の内訳**: `aws stepfunctions get-execution-history --execution-arn <arn> --reverse-order`
   - 失敗ステップの `cause` に Personalize の失敗理由が入る。
3. **拒否行**: `s3://<Bucket>/reports/<run>/rejects.csv`
   - `line` と `reason`（`ITEM:` / `EVENT:` / `INQUIRY:` / `FILE_SKIPPED:`）。
   - 想定外の列（自由記述の本文など）は**ファイルごと**スキップされる。
4. **build の部分失敗**: build は「読み切ってから書く」順序なので、途中で落ちた場合は
   前回のカルテが残る（消えない）。次の FULL が成功すれば揃う。**中途半端に消えることはない**。

## 3. 監視項目（見るべき数字）

| 指標 | 正常 | 異常時の対応 |
|---|---|---|
| `counts.members` | 前週と同水準 | 急減 → S3 のファイル欠落・列変更を疑う |
| `counts.rejected_rows` | 0〜数件 | 急増 → PUT 側の仕様変更。rejects.csv を確認 |
| `counts.skipped_files` | 0 | 1以上 → 列名・文字コード・ヘッダを確認 |
| `counts.withdrawn_excluded` | 単調増加 | 減る → `withdraw` 行が消されている |
| `loaded.updated` | `counts.batch_users` とほぼ一致 | 大きく少ない → 実行中の退会/削除が多い |

## 4. 監査

- **API アクセスログ**: `/aws/.../ApiAccessLogs`（3か月保持、`resourcePath` のみで会員IDは残さない）。
- **Lambda ログ**: 会員IDは `member_tag`（SHA-256 の先頭10桁）で記録。1か月保持。
- **CloudTrail データイベント**（任意）: `-c auditTrail=true` で `inbound/` の読み書きを記録（90日）。
  既定はオフ（データイベントは課金対象）。

## 5. 全削除して作り直す場合

CDK のスタック削除では **Personalize のリソースは残る**ため、作り直す前に次を実行する。

```bash
export AWS_PROFILE=<profile> AWS_REGION=ap-northeast-1

# 1) スタック（S3 / DynamoDB / KMS / API / Lambda / ロール / アラーム）
cd infra && npx cdk destroy KarteStack --force

# 2) Personalize（学習モデルとデータセット）
DSG=$(aws personalize list-dataset-groups --query 'datasetGroups[0].datasetGroupArn' --output text)
SOL=$(aws personalize list-solutions --dataset-group-arn "$DSG" --query 'solutions[0].solutionArn' --output text)
for v in $(aws personalize list-solution-versions --solution-arn "$SOL" --query 'solutionVersions[].solutionVersionArn' --output text); do
  aws personalize delete-solution-version --solution-version-arn "$v"
done
aws personalize delete-solution --solution-arn "$SOL"
aws personalize delete-dataset-group --dataset-group-arn "$DSG"

# 3) 残っているログ（必要なら）
for g in $(aws logs describe-log-groups --query 'logGroups[?contains(logGroupName,`KarteStack`)].logGroupName' --output text); do
  aws logs delete-log-group --log-group-name "$g"
done
```

> Personalize の削除は非同期で、数分かかることがある。削除中に再作成すると名前衝突で失敗するため、
> `list-dataset-groups` から消えたことを確認してからデプロイする。

## 6. 後片付け

```
npx cdk destroy KarteStack
```
Personalize の Solution / Solution Version は CDK 管理外なので手動で削除する。
S3 のソース履歴（`inbound/`）を消すと、カルテも次回 FULL で消える。

## 7. 上級者向け: 画面を使わずにデプロイする

通常はセットアップ画面を使う。CI や検証で同じ設定をコマンドから渡す場合の一覧。

```bash
cd infra
npx cdk deploy KarteStack \
  -c retentionYears=3 \
  -c weeklyFull=false \
  -c geoBlock=true \
  -c modelId=jp.anthropic.claude-haiku-5-5 \
  -c policyReference=now \
  --require-approval never --outputs-file ../data/outputs.json
```

| key | 既定 | 意味 |
|---|---|---|
| `retentionYears` | `3` | ソース履歴の保持年数 |
| `weeklyFull` | `false` | 毎週日曜 03:00 (JST) の FULL を有効化 |
| `alertEmail` | なし | 障害通知のメール購読 |
| `allowedCidrs` | なし | API の送信元 IP 制限 |
| `uploadCidrs` | なし | S3 への PUT を許可する CIDR |
| `geoBlock` | `true` | WAF で日本国外からの API を遮断 |
| `auditTrail` | `false` | CloudTrail データイベント（課金あり） |
| `modelId` | `jp.anthropic.claude-haiku-5-5` | 文案生成モデル |
| `callerPrincipalArns` | なし | 別アカウントのロールに引き受けを許可 |
| `policyReference` | `now` | **検証用**。過去日付のデモデータを流すときだけ `last_event` |

> 画面からデプロイすると `policyReference` は常に `now` になる。過去データのデモだけ CLI を使う。

## 8. 規模の目安と切り替え

| 規模 | 方式 | 根拠 |
|---|---|---|
| 〜数万会員 / 数百万イベント | 現行（Lambda 1回で build） | 実測: 1.5万会員・24万イベントで約30秒、メモリ 3GB で余裕 |
| 数百万会員 / 数千万イベント | Glue（Spark）または Fargate に置換 | Lambda の 15分・10GB 上限に近づく。`full.build` を差し替えるだけで済むようにしてある |
| 週次より高頻度 | 差分ロードの追加が必要 | FULL 前提の設計なので、まずは頻度を上げない |

判断の目安: build の所要時間が **10分を超えたら**置き換えを検討する。
