# P121K セットアップ / 運用画面

`infra/` の CDK スタックを、ブラウザから構築・運用するための**ローカル専用**画面。
`newcleus/setup` と同じ方式（Next.js のローカルアプリ + フェーズ型の画面 + CDK 実行）。

## 起動

```bash
bash setup/start.sh
# → http://127.0.0.1:3011 に Basic 認証つきで表示（ID/パスワードは起動時に表示）
```

初回は `npm install` が走る。以降は `setup/.next` があれば `next start`（高速）、
無ければ `next dev`。開発時は `P121K_SETUP_DEV=1`。

## 画面でできること

| フェーズ | 内容 |
|---|---|
| 0. AWS 接続 | アクセスキーで STS 疎通確認。**キーはプロセスのメモリにのみ保持**（ディスク・リポジトリに書かない） |
| 1. 設定 | 保持年数 N、週次 FULL、障害通知メール、IP 制限、モデル、監査（CloudTrail） |
| 2. デプロイ | `cdk deploy` を非同期実行。ログと状態を 5 秒間隔で表示。出力（API URL・バケット名等）を表示 |
| 3. データのPUT | 置くべき3種類（アイテムマスタ / 行動履歴 / 問い合わせ）のS3パスと列、必要IAM、注意（withdraw 行は消さない等） |
| 4. FULL の実行 | FULL パイプラインの手動実行と、実行ステータスの確認 |
| 5. 社内システム連携 | 引き受ける IAM ロール（`p121k-upload` / `p121k-api`）の ARN、API エンドポイント、curl 例、セルフテスト。長期キーは非推奨のオプトイン |
| 6. 削除 | `cdk destroy` |

## セキュリティ上の設計（重要）

| 守るもの | どう守るか |
|---|---|
| 画面への外部到達 | `127.0.0.1` にのみバインド。`middleware.ts` が Host を検査し、localhost 以外は 403（DNS リバインディング対策） |
| 画面の不正操作 | Basic 認証。パスワードは未設定なら起動時に生成し `setup/.state/pass`（0600）へ |
| アクセスキーの漏洩 | メモリのみ。`setup-state.json` には設定値とデプロイ結果だけを書く（`grep` しても鍵は出ない） |
| 権限の過大化 | 専用 IAM ユーザーを推奨（管理者のキーを使い回さない）。使い終わったらキーを無効化 |

`setup/.state/` と `setup/setup-state.json` は `.gitignore` 済み。

## 仕組み

- `lib/creds.ts` — `globalThis` にセッションを保持（Next はルートごとにバンドルが分かれるためモジュール変数では共有されない）
- `lib/aws.ts` — `aws` CLI を `execFile` で実行（AWS SDK を追加しない）
- `lib/cdk.ts` — `npx cdk deploy|destroy` を detached で起動し、`.state/deploy.log` に流す
- `app/api/*` — session / state / deploy / full / selftest
- `middleware.ts` — Host 検査 + Basic 認証

## 既知の制約

- サーバを再起動すると AWS 接続は切れる（設計どおり。再入力する）。
- 同時実行は 1 つまで（デプロイ中の再実行は 409）。
- CloudTrail のデータイベントは課金対象のため既定オフ。画面のチェックで有効化できる。
- 画面からのデプロイは常に本番動作（`policyReference=now`）。過去日付のデモデータを流すときだけ CLI から `-c policyReference=last_event` を付ける。
