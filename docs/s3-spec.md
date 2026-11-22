# S3 ソースデータ仕様（受付フォーマット）

自社システムが S3 に置くファイルの仕様。**TSV（タブ区切り）**、UTF-8（BOM可）、ヘッダ行あり。
商品名や問い合わせ概要にカンマが入り得るため、CSV は受け付けない。

| 種別 | 置き場所 | 扱い |
|---|---|---|
| アイテムマスタ | `s3://<Bucket>/inbound/items/<任意名>.tsv` | 最新ファイルが**全件スナップショット**（古いファイルは無視） |
| 行動履歴 | `s3://<Bucket>/inbound/events/<任意名>.tsv` | **追記**。同じ行が重複しても1回だけ数える |
| 問い合わせ | `s3://<Bucket>/inbound/inquiries/<任意名>.tsv` | 追記。分類コードのみ |
| （API が自動で置く） | `s3://<Bucket>/inbound/events/api-delete_*.tsv` | `DELETE /karte/{id}` が書く withdraw 行 |

拡張子は `.tsv` のみ。`.csv` は無視される（取り込み事故を防ぐため）。

## 1. アイテムマスタ

| 列 | 必須 | 型・制約 |
|---|---|---|
| `item_id` | ○ | `[A-Za-z0-9_.:-]{1,64}` |
| `item_name` | ○ | 200文字まで。カンマ・タブ以外は自由 |
| `item_description` | 任意 | 2000文字まで |
| `category` | ○ | 100文字まで（例 `electronics.phone`） |
| `price` | ○ | 0以上の整数（円） |

## 2. 行動履歴

| 列 | 必須 | 型・制約 |
|---|---|---|
| `member_id` | ○ | **HMAC-SHA256 の16進（32桁または64桁）のみ**。生の会員IDは拒否される |
| `item_id` | ○（withdraw 以外） | 上記と同じ形式 |
| `event_type` | ○ | `view` / `like` / `cart` / `purchase` / `return` / `withdraw` |
| `event_ts` | ○ | ISO8601＋タイムゾーン（例 `2026-11-22T12:00:00+09:00`）。オフセット必須 |
| `reason` | 任意 | 返品・退会の理由コード（60文字まで） |

- `withdraw` は**消さない**。1行でもある会員は、その会員の全履歴がカルテ・学習データから除外される。
- 同じ `(member_id, item_id, event_type, event_ts)` は重複排除される（再送してよい）。

## 3. 問い合わせ

| 列 | 必須 | 型・制約 |
|---|---|---|
| `inquiry_id` | ○ | 一意。重複は1件だけ採用 |
| `member_id` | ○ | ハッシュ化済み |
| `received_ts` | ○ | ISO8601＋タイムゾーン |
| `category` | ○ | `complaint` / `question` / `request` / `other` |
| `note` | 任意 | **`other` のときだけ** 40文字以内の概要（個人情報を書かない） |

- 本文は送らない。`category` が未知、`note` が40文字超、`body` などの想定外の列があると**ファイルごと拒否**される。
- 分類コードの意味:
  - `complaint`: クレーム。30日以内にあれば接触NG（`RECENT_COMPLAINT`）
  - `question`: 質問（商品・配送・支払いなど）
  - `request`: 依頼（交換・返品・変更・解約など）
  - `other`: 上記に当たらない相談。`note` に40文字以内で概要

## 4. 会員IDの仮名化（HMAC）

```
member_id = lower(hex(HMAC-SHA256(key = 自社の秘密鍵, msg = 生の会員ID)))[:32]
```

- 鍵は自社で生成・保管し、**AWS には置かない**（KMS も使わない。AWS 側で再現できないことが重要）。
- 出力は 32桁（先頭32文字）または 64桁（全部）。どちらでも受け付けるが、**運用中は混ぜない**。
- 同じ会員IDからは常に同じ値が出る（ハッシュなので衝突は事実上ない）。
- 生の会員ID・メール・電話・氏名は**送らない**。ハッシュ化していない値は行単位で拒否され、`reports/<run>/rejects.csv` に記録される。
- 参考実装: `tools/ingest_side.py` の `pseudonymize()`。

## 5. 拒否されたとき

- 行単位の不備 → その行だけ捨てる。`reports/<run>/rejects.csv` に `file, line, reason` が残る。
- 列の過不足 → **ファイルごと**スキップ（`FILE_SKIPPED`）。
- 拒否行が50件を超えると SNS にアラートが飛ぶ（`RejectedRows`）。スキップされたファイルがあれば即アラート（`SkippedFiles`）。

## 6. サイズと保持

- S3 に置いた履歴は `retentionYears`（既定3年）で自動削除される。FULL も期限切れの行を無視する。
- 1会員あたりのカルテは DynamoDB の 400KB 制限に近づくと build が**明示的に失敗**する（黙って履歴を捨てない）。
