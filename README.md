# P121K — Personalized One-to-One Karte

自社AWS（東京）に顧客1人1人の「カルテ」を持ち、Amazon Personalize v2 でレコメンドを作り、Bedrock で汎用の案内文案（メール・手紙・LINEなど媒体を問わない）を作る基盤。
メール配信機能・CMSは作らない。結果は自社サーバが IAM 署名付きAPIで取得する。

残タスクは [TODO.md](./TODO.md)。

## 設計原則

原則は「○○を防ぐために、●●する」の形で書く。守る/守らないを判定できないものは原則にしない。

### アクセス
1. **外部からの直接アクセスを防ぐために、APIは IAM 認証（SigV4）のみにする。** ブラウザ向けの公開APIは作らない。署名なしは403。送信元IP制限も任意で付けられる（`allowedCidrs`）。
2. **認証情報の漏えいを防ぐために、長期アクセスキーを使わず、IAMロールだけで呼び出す。** 呼び出し側には最小権限のマネージドポリシー（`karte-api-invoke` / `karte-inbound-upload`）だけを付ける。
3. **他のデータとの混在を防ぐために、単一社・専用アカウント・専用KMSキーにする。** マルチテナントにしない。

### データの最小化
4. **個人情報の漏えい被害を防ぐために、個人情報を AWS に置かない。** 氏名・メール・住所・電話、年代・都道府県などの粗い属性も持たない。会員IDは自社側で HMAC により仮名化してから送る（鍵は自社保管）。
5. **自由記述の混入を防ぐために、問い合わせは分類コードだけを送る。** `complaint / question / request / other`、`other` のときだけ40字以内の概要。40字超・未知のカテゴリ・想定外の列（`body` など）は拒否する。
6. **Bedrock に個人情報を渡さないために、そもそも AWS に個人情報を置かない（原則4）。** プロンプトに載るのは商品名・カテゴリ・購入回数・興味カテゴリだけで、宛名は `{{customer_name}}` のプレースホルダ固定。実名の差し込みは自社サーバが行う。伏字化（電話・メール等の形式置換）は「万一混入したとき」の保険として残しているが、主防御は最小化。

### データの残存
7. **退会者のデータ残存を防ぐために、退会 = 存在しない、にする。** S3 の行動履歴に `withdraw` が1行でもある会員は、**全履歴ごと**カルテにも学習データにも入れない。退会状態のレコードや墓標を持たない。
8. **古いデータの残存を防ぐために、直近N年だけ保持する。** S3ライフサイクルで古いファイルを自動削除し、FULL再構築も期限切れの行を無視する（`retentionYears`、既定3年）。
9. **削除の復活を防ぐために、即時削除でも S3 に `withdraw` を記録する。** `DELETE /karte/{id}` はカルテを即時に消し、同時に `withdraw` 行を S3 に書く。次回以降のFULLでも復活しない。
10. **状態のずれ・差分バグを防ぐために、毎回FULLで作り直す。** 週1想定で S3 の全履歴を読み直し、DynamoDB のカルテと Personalize の入力を丸ごと入れ替える。差分管理・排他制御・状態遷移を持たない。

### 判定と生成
11. **説明できない接触判断を防ぐために、接触NGはルールだけで決める。** LLMは判定に使わない。NG理由は `RETURN_SPIKE`（30日内に返品2件以上）/ `RECENT_COMPLAINT`（30日内にクレーム）/ `NO_KARTE`（カルテなし）。
12. **NG会員への接触を防ぐために、404（カルテなし）もNGとして扱う。** NG会員にはレコメンドを返さず、文案生成は409で拒否する。
13. **不正データによるカルテ破損を防ぐために、厳密なスキーマ検証を行う。** 列の過不足はファイルごと、不正な値は行ごとに拒否し、`reports/<run>/rejects.csv` に記録する。同一行の重複は排除する。

### 暗号化・通信・コスト
14. **保管データの漏えいを防ぐために、S3・DynamoDB は専用KMSキー（CMK）で暗号化し、TLSのみ許可、パブリックアクセスを全ブロックする。**
15. **意図しない常時課金を防ぐために、Personalize はバッチ推論だけを使う。** リアルタイムCampaignは作らない（無リクエストでも最低1TPS分が課金される）。
16. **データの国外流出を防ぐために、東京リージョンに構築し、Bedrock は `jp.*` 推論プロファイル（東京・大阪のみ）を使う。** 「東京のみ」が必須の要件では Bedrock が使えない点に注意。
17. **構築と検証のずれを防ぐために、全リソースを CDK でコード化し、セキュリティ性質を CDK テストで検証する。**

## 構成

```
自社サーバ ──(S3 PUT, IAMロール)──> S3 inbound/{items|events|inquiries}/*.csv
                                       │（週1 or 手動で起動）
                                       ▼
              Step Functions「karte-reco-pipeline」
                build（S3全履歴 → DynamoDBカルテ + Personalize入力）
                → Personalize User-Personalization-v2（取込 → 学習 → バッチ推論）
                → load（Top-N をカルテへ）→ prune（旧 Solution Version を削除）
                                       ▼
自社サーバ ──(SigV4)──> API Gateway(AWS_IAM) → Lambda → DynamoDB / Bedrock
   GET /karte/{id}  GET /karte/{id}/reco  POST /karte/{id}/copy  DELETE /karte/{id}
```

- `infra/` CDK(TypeScript) 1スタック `KarteStack`（ap-northeast-1）
- `setup/` セットアップ / 運用画面（Next.js、localhost 専用、Basic 認証、キーはメモリのみ）
- `lambdas/` Python 3.12: `full.py`（FULL再構築）, `pipeline.py`（Step Functions各ステップ）, `api.py`, `llm.py`（文案）, `policy.py`（接触NGルール）, `pii.py`（伏字化）
- `tools/prepare_data.py` オープンデータ+合成データ → 仕様CSV、`tools/e2e.py` AWS上のE2E、`tools/ingest_side.py` 自社側の参考実装、`tools/probe_haiku55.py` モデル利用可否の確認
- `docs/` 運用手順（`operations.md`）、データ主権の代替案、CSVサンプル
- `tests/` pytest(moto)、`infra/test/` jest(CDK assertions)、`.github/workflows/ci.yml` で両方を自動実行

## ソースデータ仕様（S3 PUT、UTF-8 CSV、ヘッダ付き）

パス: `s3://<Bucket>/inbound/{items|events|inquiries}/<任意名>.csv`

| 種別 | 列（[ ]は任意） | 備考 |
|---|---|---|
| items | `item_id, item_name, [item_description], category, price` | 最新ファイルが全件スナップショット |
| events | `member_id, item_id, event_type, event_ts, [reason]` | `event_type`: `view/like/cart/purchase/return/withdraw`。`event_ts`はISO8601(タイムゾーン付き)。`withdraw`は`item_id`空欄。ファイルは**追記**し、`withdraw`行は消さない |
| inquiries | `inquiry_id, member_id, received_ts, category, [note]` | 上記原則5 |

Personalize には `view/like/cart/purchase` だけを渡す（`return` は学習に使わない）。最低 1,000 インタラクション / 25 ユーザー必要。

## 使い方

**セットアップ画面（推奨）** — ブラウザからデプロイ・運転・削除まで行えます。

```bash
bash setup/start.sh
# → http://127.0.0.1:3011（Basic 認証。ID / パスワードは起動時に表示）
```

画面の構成: 0. AWS 接続 → 1. 設定（保持年数・週次・通知先） → 2. デプロイ → 3. データのPUT（何をどのパスに置くか） → 4. FULL の実行 → 5. 社内システム連携（API） → 6. 削除。
アクセスキーは**サーバのメモリにのみ保持**し、ディスクには書きません（再起動時は再入力）。
詳細とセキュリティ設計は [setup/README.md](./setup/README.md)。

**CLI**

```bash
export AWS_PROFILE=<profile> AWS_REGION=ap-northeast-1
python3 -m venv .venv && .venv/bin/pip install boto3 pytest moto
.venv/bin/python -m pytest tests -q
cd infra && npm i && npx jest
npx cdk deploy KarteStack --outputs-file ../data/outputs.json
```

デプロイ時のオプション（`-c key=value`）:

| key | 既定 | 意味 |
|---|---|---|
| `retentionYears` | `3` | ソース履歴の保持年数 |
| `weeklyFull` | `false` | `true`で毎週日曜03:00(JST)にFULLパイプラインを自動起動 |
| `allowedCidrs` | なし | APIを送信元IPで制限（カンマ区切り） |
| `alertEmail` | なし | 指定すると障害通知メールを SNS `karte-alerts` へ購読 |
| `auditTrail` | `false` | `true`で CloudTrail のデータイベントを `inbound/` に有効化（課金あり） |
| `modelId` | `jp.anthropic.claude-haiku-5-5` | 文案生成モデル。利用不可ならエラーにする（代替モデルへの切替はしない） |
| `policyReference` | `now` | 過去日付のデモデータを流すときだけ `last_event` |

## 運用

1. **PUT**: 自社サーバのIAMロールに `karte-inbound-upload` を付与して上記パスへ。
2. **FULL実行**: Step Functions `karte-reco-pipeline` を起動（学習込みで実証規模で約1時間）。buildだけ試すときは Lambda `Pipeline` に `{"action":"build"}`。
3. **API呼び出し**: 呼び出し側ロールに `karte-api-invoke` を付与し、SigV4で署名する。
4. **退会**: events に `withdraw` を追記する。急ぐ場合は `DELETE /karte/{id}`。
5. **後片付け**: `npx cdk destroy KarteStack` のあと、Personalize の Solution / Solution Version を手動削除する（パイプラインが作るため。FULL成功時は旧バージョンを自動削除する）。

障害時の見方、監視項目、規模の切り替えは [docs/operations.md](./docs/operations.md) を参照。
自社側（PUTする側）の参考実装は `tools/ingest_side.py`（HMAC仮名化・問い合わせの分類コード化・withdraw出力）。
入出力CSVの実例は `docs/samples/` にある。

## 検証済みの内容（2026-11-22、ap-northeast-1、実証データ1.5万会員・約24万イベント）

- FULL build 約30秒。接触NG判定は正解データと全件一致。退会者469名はカルテにも学習データにも存在しない。
- `DELETE` 後にFULLを回しても復活しない。署名なしAPIは403。
- 不正行・不正ファイルは拒否され、電話番号などの全文はカルテに残らない。
- Personalize 全パイプライン成功（学習 約40分）。14,359名にTop-10を反映し、退会者・NG会員への反映は0。
- 署名付きでも `karte-api-invoke` を持たない IAM ユーザーは 403。APIアクセスログとLambdaログに会員IDは残らない（`member_tag` のみ）。
- 障害通知（SNS `karte-alerts`）とデータ品質アラーム（拒否行・スキップファイル）がデプロイされ、build のメトリクス（名前空間 `P121K`）が CloudWatch に届くことを確認。

## データ出典とライセンス

| データ | 扱い |
|---|---|
| [REES46 eCommerce behavior data](https://www.kaggle.com/datasets/mkechinov/ecommerce-behavior-data-from-multi-category-store)（Kaggle） | **採用**。作者が「無料で利用可。KaggleページとREES46へのリンクを出典として明記」と記載（`https://rees46.com`）。Kaggleのlicense欄は "Data files © Original Authors" で正式なOSSライセンスではないため、検証用に限定。データはリポジトリに含めない |
| Olist Brazilian E-Commerce / Retailrocket | **不採用**。CC BY-NC-SA 4.0（非商用） |
| Amazon Reviews 2023 | **不採用**。ライセンス記載が確認できない |
| 商品名・説明・like・return・withdraw・問い合わせコード | **合成**（`tools/prepare_data.py`、ルールベース） |

## モデルとコストの要点

- Claude Haiku 5.5 の東京単独推論は無く、日本向け推論プロファイル `jp.anthropic.claude-haiku-5-5`（東京・大阪）のみ。
- 単価（Bedrock の Marketplace オファーの料金表で確認済み）: 日本向けプロファイルは入力 $0.11 / 出力 $0.55 /100万トークン（グローバルは $0.10 / $0.50）。100Kトークン超は入力 $0.55 / 出力 $2.75。
- **Haiku 5.5 は Bedrock のバッチ推論に対応していない**（`CreateModelInvocationJob` が `Batch inference is not supported for the requested model` で拒否することを実機で確認）。文案生成は即時API（`POST /copy`）のみ。
- 利用には Marketplace の契約（従量課金・返金なし）への同意が必要。**モデルの代替切替は実装していない**。Haiku 5.5 が使えない場合は `POST /karte/{id}/copy` がエラーを返す（黙って別モデルに変わらない）。
- 契約は `aws bedrock create-foundation-model-agreement --model-id anthropic.claude-haiku-5-5 --offer-token <list-foundation-model-agreement-offers の token>` で作成できる（2026-11-22 に ap-northeast-1 で作成済み）。
- 契約直後は **アカウント検証待ち**で、推論は `AccessDeniedException` になる。ap-northeast-3 では「Your account is currently being verified. Verification normally takes less than 2 hours.」と明示されるが、ap-northeast-1 では「is not available for this account」という紛らわしい汎用文言になる。`tools/probe_haiku55.py` で利用可能になるまで確認できる。
- Personalize v2: 取込 $0.05/GB、学習 $2.00/100万インタラクション、推論 $0.15/1,000リクエスト。FULL再学習は毎回全件が学習課金の対象。

## 注意

- `data/`（元データ・HMAC鍵・正解ラベル・デプロイ出力）は git 管理外。アップロード・コミット禁止。
- Personalize Schema は**タグ変更で置換され、名前衝突して失敗**する。スタックのタグを変えない。

## ライセンス

このリポジトリのコードは [MIT License](./LICENSE)。

同梱しないデータの扱いは次のとおり。

| 対象 | ライセンス / 条件 |
|---|---|
| REES46 eCommerce behavior data | 作者の記載に従う（出典明記）。正式な OSS ライセンスではないため、検証用途に限定し、リポジトリには含めない |
| 生成したサンプル（`docs/samples/`） | 本リポジトリと同じ MIT（合成データのみ） |
| Claude Haiku 5.5 / Amazon Personalize | 各サービスの利用規約に従う（コードのライセンスとは別） |
