# API 仕様

IAM（SigV4）認証のみ。ブラウザから直接呼ぶ経路は無い。

- ベース URL: `cdk deploy` の出力 `ApiUrl`（例 `https://xxxx.execute-api.ap-northeast-1.amazonaws.com/v1`）
- 必要な権限: `karte-api-invoke` を呼び出し元の IAM ロール／ユーザーに付与
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

```bash
curl -sS --aws-sigv4 "aws:amz:ap-northeast-1:execute-api" \
  --user "$AWS_ACCESS_KEY_ID:$AWS_SECRET_ACCESS_KEY" \
  "$API/karte/$MEMBER_ID"
```
