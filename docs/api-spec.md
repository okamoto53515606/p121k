# API 仕様

IAM（SigV4）認証のみ。ブラウザから直接呼ぶ経路は無い。

- ベース URL: `cdk deploy` の出力 `ApiUrl`（例 `https://xxxx.execute-api.ap-northeast-1.amazonaws.com/v1`）
- 必要な権限: `karte-api-invoke` を**呼び出し元の IAM ロール**に付与（IAM ユーザー・長期キーは作らない）
- 文字コード: UTF-8、`Content-Type: application/json`
- 日本国外からの呼び出しは WAF の国判定で 403（`geoBlock=true`、既定）

## エンドポイント一覧

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/karte/{memberId}` | カルテ取得（履歴・接触NG判定つき） |
| GET | `/karte/{memberId}/reco` | レコメンド取得 |
| POST | `/karte/{memberId}/copy` | 文案生成（リアルタイム） |
| DELETE | `/karte/{memberId}` | 退会・削除（即時＋S3に withdraw 記録） |
| GET | `/karte/search` | カルテ一覧の検索（インデックス） |

`{memberId}` は HMAC-SHA256 の16進（32桁または64桁）のみ。それ以外は 400。

## GET /karte/{memberId}

```json
{
  "member_id": "…",
  "updated_at": 1795380000,
  "last_event_ts": 1795380000,
  "counts": {"view": 23, "like": 1, "cart": 0, "purchase": 2, "return": 0},
  "last_ts": {"view": 1795380000},
  "recent_events": [{"ts": 1795380000, "type": "view", "item_id": "…"}],
  "purchases": [{"ts": 1795370000, "item_id": "…", "price": 1980}],
  "returns":   [{"ts": 1795370000, "item_id": "…", "reason": "SIZE", "price": 1980}],
  "inquiries": [{"id": "Q0000001", "ts": 1795370000, "category": "complaint"}],
  "inquiry_count": 1,
  "top_categories": ["electronics.phone"],
  "reco": {"items": [{"item_id": "…", "item_name": "…", "price": 1980, "score": "0.42"}]},
  "copy": {"subject": "…", "body": "{{customer_name}} 様 …", "model": "jp.anthropic.claude-haiku-5-5"},
  "contact": {"ok": false, "reasons": ["RETURN_SPIKE"]}
}
```

- `purchases` / `returns` / `inquiries` は **S3 に置いた分をすべて**返す（省略しない）。`price` はアイテムマスタの**最新**単価。
- `copy` は **`POST /karte/{memberId}/copy` を呼ぶまで `null`**。呼ぶと生成結果がこの属性に保存され、以後の GET で返る（呼ぶたびに上書き、FULL 再構築でも保持）。
- `reco` は FULL（Personalize）完了後に埋まる。
- カルテが無い（未登録・退会・削除）→ **404** `{"error":"NOT_FOUND","contact":{"ok":false,"reasons":["NO_KARTE"]}}`。
  404 は「接触NG」として扱うこと。

## GET /karte/{memberId}/reco

```json
{"member_id":"…","contact":{"ok":true,"reasons":[]},"items":[{"item_id":"…","item_name":"…","category":"…","price":1980,"score":"0.42"}]}
```

接触NGなら `items` は空配列（200）。

## POST /karte/{memberId}/copy

リアルタイム生成。バッチではない。

```json
{"channel": "mail", "tone": "丁寧で親しみやすい", "direction": "再来店を促す。絵文字なし。200字程度"}
```

| 項目 | 既定 | 意味 |
|---|---|---|
| `channel` | `generic` | `mail` / `letter` / `line` / `generic` など（文面の体裁に使う） |
| `tone` | `丁寧で親しみやすい` | 文体 |
| `direction` | なし | 訴求点・長さ・避けたい表現などの指示（200文字まで、伏字化して渡す） |

- 応答: `{"member_id":"…","copy":{"subject":"…","body":"{{customer_name}} 様 …","model":"jp.anthropic.claude-haiku-5-5","generated_at":…}}`
- 本文の宛名は `{{customer_name}}` のまま返る。**実名の差し込みは自社側で行う**。
- 接触NGなら **409** `{"error":"CONTACT_NG","contact":{…}}`。
- モデルが使えない場合は **502**（代替モデルへの切替はしない）。
- 生成結果はカルテの `copy` に保存される。

## DELETE /karte/{memberId}

- カルテを即時削除し、S3 に `withdraw` 行を書く（次回 FULL でも復活しない）。
- 応答: `{"member_id":"…","deleted":true,"withdraw_recorded":"inbound/events/api-delete_….tsv"}`

## GET /karte/search

DynamoDB の GSI で絞り込む（全件スキャンしない）。カルテを作り直した直後から有効。

| パラメータ | 既定 | 意味 |
|---|---|---|
| `by` | `last_purchase` | `first_purchase` / `last_purchase` / `first_view` / `last_view` / `purchase_count_90d` / `view_count_90d` |
| `within_days` | — | 直近N日以内（時刻系のとき） |
| `older_than_days` | — | N日より前（時刻系のとき） |
| `min` | `1` | 回数系のときの下限 |
| `limit` | `50` | 最大200 |
| `asc` | なし | `true` で昇順（古い順） |

```json
{"by":"last_purchase","count":3,"members":[{"member_id":"…","value":1795380000}],"next":1795370000}
```

`next` が返れば、その値を `start` に渡して続きを取れる（同じ `by` を使う）。

例:

| したいこと | 呼び出し |
|---|---|
| 初回購入が古い会員 | `/karte/search?by=first_purchase&older_than_days=365&asc=true` |
| 最終購入が近い会員 | `/karte/search?by=last_purchase&within_days=30` |
| 直近90日の購入が3回以上 | `/karte/search?by=purchase_count_90d&min=3` |
| 最終閲覧が近い会員 | `/karte/search?by=last_view&within_days=14` |

## エラー

| コード | 意味 |
|---|---|
| 400 | `INVALID_MEMBER_ID` / `INVALID_JSON` |
| 401 / 403 | 署名なし、または `karte-api-invoke` が無い |
| 404 | カルテなし（＝接触NG） |
| 409 | 接触NG（文案生成） |
| 502 | 文案生成モデルが利用不可 |

## 呼び出し例

呼び出し元は **IAM ロール**を引き受けて動く（EC2 のインスタンスプロファイル、ECS タスクロール、
EKS の IRSA、Roles Anywhere など）。署名は SDK / CLI がロールの一時資格情報で自動的に行う。
**長期アクセスキーは使わない。**

```bash
# 方法1: awscurl（SigV4 を自動で付ける）
awscurl --service execute-api --region ap-northeast-1 "$API/karte/$MEMBER_ID"

# 方法2: ロールを引き受けて curl で署名前する（一時資格情報なのでキーは残らない）
CREDS=$(aws sts assume-role --role-arn arn:aws:iam::123456789012:role/p121k-api \
  --role-session-name p121k-call --query Credentials --output json)
export AWS_ACCESS_KEY_ID=$(echo "$CREDS" | jq -r .AccessKeyId)
export AWS_SECRET_ACCESS_KEY=$(echo "$CREDS" | jq -r .SecretAccessKey)
export AWS_SESSION_TOKEN=$(echo "$CREDS" | jq -r .SessionToken)

curl -sS --aws-sigv4 "aws:amz:ap-northeast-1:execute-api" \
  --user "$AWS_ACCESS_KEY_ID:$AWS_SECRET_ACCESS_KEY" \
  -H "x-amz-security-token: $AWS_SESSION_TOKEN" \
  "$API/karte/$MEMBER_ID"
```

## IAM 署名（SigV4）の実装例

サービス名は `execute-api`、リージョンは `ap-northeast-1`。
**資格情報はロールから取得する**（SDK の既定チェーンに任せる）。コードにキーを書かない・環境変数に置かない。

### PHP（`aws/aws-sdk-php`）

```bash
composer require aws/aws-sdk-php guzzlehttp/guzzle
```

```php
<?php
require __DIR__ . '/vendor/autoload.php';

use Aws\Credentials\CredentialProvider;
use Aws\Signature\SignatureV4;
use GuzzleHttp\Client;
use GuzzleHttp\Psr7\Request;

$base   = 'https://xxxxxxxxxx.execute-api.ap-northeast-1.amazonaws.com/v1';
$region = 'ap-northeast-1';
$member = 'ハッシュ化されたmember_id';

// 既定チェーン: インスタンスプロファイル / タスクロール / IRSA / Roles Anywhere を自動で解決する。
// 長期キー（AWS_ACCESS_KEY_ID 等）は設定しない。
$credentials = CredentialProvider::defaultProvider()()->wait();
$signer = new SignatureV4('execute-api', $region);
$client = new Client();

// --- GET /karte/{memberId} ---
$request  = new Request('GET', "{$base}/karte/{$member}", ['Accept' => 'application/json']);
$response = $client->send($signer->signRequest($request, $credentials));
$karte    = json_decode($response->getBody()->getContents(), true);

// --- POST /karte/{memberId}/copy（JSON ボディも署名対象） ---
$body = json_encode([
    'channel'   => 'mail',
    'direction' => '再来店を促す。絵文字なし。200字程度',
], JSON_UNESCAPED_UNICODE);

$request = new Request('POST', "{$base}/karte/{$member}/copy", [
    'Content-Type' => 'application/json',
    'Accept'       => 'application/json',
], $body);
$response = $client->send($signer->signRequest($request, $credentials));
echo $response->getBody();
```

### .NET（C#）

AWS SDK の既定チェーン（EC2/ECS/EKS のロール、または SSO）で資格情報を解決し、
必要なら `sts:AssumeRole` で `p121k-api` を引き受ける。

```bash
dotnet add package AWSSDK.SecurityToken
dotnet add package AWSSDK.Core
```

```csharp
using Amazon.Runtime;
using Amazon.SecurityToken;
using Amazon.SecurityToken.Model;

// 1) 実行環境のロール（インスタンスプロファイル等）で p121k-api を引き受ける
var sts = new AmazonSecurityTokenServiceClient(RegionEndpoint.APNortheast1);
var assumed = await sts.AssumeRoleAsync(new AssumeRoleRequest
{
    RoleArn         = "arn:aws:iam::123456789012:role/p121k-api",
    RoleSessionName = "p121k-call",
});
var credentials = assumed.Credentials;   // 一時資格情報（1時間で失効）

// 2) その資格情報で HttpClient に SigV4 を付ける（AWSSDK の AWS4Signer を利用）
var signer = new Amazon.Runtime.Internal.Auth.AWS4Signer();
// ...（リクエスト生成 → signer.Sign(...) → SendAsync）
```

> SDK を使えない環境（NuGet が使えない等）では、SigV4 を自前実装する必要がある。
> その場合も**資格情報の取得元はロール**（IMDS / Roles Anywhere / STS）に限る。
> 実装例は [YMSTTIME_DAT_API](https://github.com/okamoto53515606/YMSTTIME_DAT_API) の .NET サンプルを参照
> （サービス名を `execute-api` に読み替える）。

### つまずきやすい点

| 症状 | 原因 |
|---|---|
| 403 `Missing Authentication Token` | 署名していない（`Authorization` ヘッダが無い） |
| 403 `Signature expired` | 端末の時計がずれている（UTC で ±5分以内に） |
| 403 `The request signature we calculated does not match` | クエリの並び順・URL エンコード・`Content-Type` の不一致。ボディを署名していない |
| 403 `not authorized to perform: execute-api:Invoke` | 署名は正しいが、引き受けたロールに `karte-api-invoke` が無い（S3 用ロールを間違えている等） |
| 403（日本国外） | WAF の国判定。`geoBlock=true` のとき海外 IP は届かない |
| `Credentials not found`（SDK） | ロールが付いていない。インスタンスプロファイル／タスクロール／Roles Anywhere を確認 |
