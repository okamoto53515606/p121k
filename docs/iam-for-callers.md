# 自社サーバから AWS を呼ぶための IAM 設計

方針は **長期アクセスキーを使わない**（README 設計原則2）。呼び出し側は IAM ロールを引き受け、
必要なポリシーだけを持つ。このスタックは用途別に**2つのロール**を作る。

| ロール | 付くポリシー | できること |
|---|---|---|
| `p121k-upload` | `karte-inbound-upload` | `s3:PutObject`（`inbound/*`）+ その KMS キーの `GenerateDataKey` / `Encrypt` |
| `p121k-api` | `karte-api-invoke` | `execute-api:Invoke`（`/karte/*`） |

ARN は `cdk deploy` の出力（`UploadRoleArn` / `ApiRoleArn`）と、セットアップ画面の「社内システム連携」で確認できる。

## 呼び出し元ごとの使い方

| 呼び出し元の環境 | 方法 | 長期キー |
|---|---|---|
| AWS 上の EC2 | インスタンスプロファイルに `p121k-upload` / `p121k-api` を指定 | 不要 |
| ECS / Fargate | タスクロールとして指定 | 不要 |
| EKS | IRSA / EKS Pod Identity で `p121k-*` を引き受ける | 不要 |
| Lambda | 実行ロールに `sts:AssumeRole` を許可して `p121k-*` を引き受ける | 不要 |
| GitHub Actions など CI | OIDC フェデレーション → `p121k-*` を引き受ける | 不要 |
| オンプレ / 自社データセンター | **IAM Roles Anywhere**（社内 CA の X.509 証明書）で `p121k-*` を引き受ける | 不要 |
| 別アカウント | 呼び出し元ロールの ARN を `-c callerPrincipalArns=...` に追加して再デプロイ | 不要 |

別アカウントを許可する例:

```bash
npx cdk deploy KarteStack -c callerPrincipalArns=arn:aws:iam::999999999999:role/ec2-batch
```

> 同じアカウント内なら、アカウントのルートを信頼しているので追加設定は不要。

## 付けてはいけないもの

- `s3:GetObject`（ソース履歴の読み取り）、`dynamodb:*`、`iam:*`、`kms:Decrypt`（カルテの復号は Lambda の役割）。
- 1つのロールに両方のポリシーを付けること（S3 用と API 用は分ける）。

## 長期アクセスキーを使う場合（非推奨）

IAM ロールがどうしても使えない環境のためだけに、オプトインで IAM ユーザーを作れる。

```bash
npx cdk deploy KarteStack -c accessKeys=true -c accessKeyMaxAgeDays=180
```

- 作られるユーザー: `p121k-upload-key` / `p121k-api-key`（用途別）。
- キーはセットアップ画面の「アクセスキー」から発行する。秘密キーはその場で一度だけ表示し、保存しない。
- 期限を過ぎたキーは日次ジョブ（`keyguard`）が無効化→削除する。1ユーザー最大2本（ローテーション用）。
- 既定は無効。「長期キーを持たない」原則から外れることを理解して使う。

## 海外IPからのアクセスを止める

| 対象 | できること | 実装 |
|---|---|---|
| API | 国単位で遮断できる（既定で有効） | WAFv2 の GeoMatch で「日本以外」を Block（`-c geoBlock=true`） |
| S3 | 国単位の遮断は**できない**（S3 に国の概念がない） | 自社の固定グローバルIPを `-c uploadCidrs=203.0.113.0/24,...` で許可リスト化。それ以外は PutObject を Deny |

S3 を確実に閉じたい場合は、インターネット経由をやめて **VPC エンドポイント経由**にする。
