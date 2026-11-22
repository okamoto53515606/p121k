# 自社サーバから AWS を呼ぶための IAM 設計

## 何を決めるのか

「自社サーバ」が AWS を呼ぶ手段を決める、という意味。決めることは4つだけ。

1. **どこで動くか**（AWS の EC2 / ECS / EKS か、自社データセンターか）
2. **長期アクセスキーを置けるか**（置かないなら「一時的な認証情報」を取得する方式にする）
3. **何を許可するか**（`karte-inbound-upload` のみ / `karte-api-invoke` のみ / 両方）
4. **誰がどのくらいの期間持てるか**（ローテーション、退職時の失効）

このシステムは **呼び出し元の IAM ロールにマネージドポリシーを付けるだけ**で使える。
アプリ側に認証情報を書く必要はない（SigV4 は SDK が解決する）。

## 選択肢

| 呼び出し元の環境 | 推奨方式 | 長期キー | 備考 |
|---|---|---|---|
| AWS 上の EC2 | インスタンスプロファイル（IAM ロール） | 不要 | 最も簡単。IMDSv2 を必須にする |
| AWS 上の ECS / Fargate | タスクロール | 不要 | コンテナごとに権限を分けられる |
| AWS 上の EKS | IRSA / EKS Pod Identity（OIDC） | 不要 | サービスアカウント単位で権限を分ける |
| GitHub Actions など CI | OIDC フェデレーション | 不要 | リポジトリ・ブランチ条件を付けられる |
| 自社データセンター / オンプレ | **IAM Roles Anywhere**（X.509 証明書） | 不要 | 社内 CA の証明書で一時認証情報を得る |
| どうしても上記が無理な場合 | IAM ユーザーのアクセスキー | 必要 | 最終手段。ローテーションと監視が必須 |

補足: 「OIDC」は AWS 側に OIDC プロバイダを登録し、外部の ID プロバイダ（Kubernetes、GitHub、Auth0 など）が発行するトークンと引き換えに一時認証情報を得る方式。
「Roles Anywhere」は AWS の外（オンプレ）で動くサーバ向けで、X.509 証明書を信頼アンカーとして登録する。

## このシステムで付けるポリシー（2つだけ）

| ポリシー | できること | 付ける相手 |
|---|---|---|
| `karte-inbound-upload` | `s3:PutObject`（`inbound/*`）+ その KMS キーの `GenerateDataKey` / `Encrypt` | データを PUT するバッチ |
| `karte-api-invoke` | `execute-api:Invoke`（`/karte/*`） | カルテ・レコメンド・文案を取得するアプリ |

**付けてはいけないもの**: S3 の読み取り（`GetObject`）、DynamoDB、`iam:*`、`kms:Decrypt`（カルテの復号は Lambda の役割）。

## 決めたあとにやること

- 呼び出し元のロールに上記ポリシーをアタッチする（`data/outputs.json` の `UploadPolicyArn` / `ApiInvokePolicyArn`）。
- 可能なら `allowedCidrs` で API の送信元 IP を絞る（`-c allowedCidrs=203.0.113.0/24`）。
- アクセスキーを使う場合だけ、90日以内のローテーションと「未使用キーの検出」を運用に入れる。
- CloudTrail（`-c auditTrail=true`）で `inbound/` への PUT 実行者を記録する。
