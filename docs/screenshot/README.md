# スクリーンショット置き場

`docs/quickstart.md` が参照する画像をここに置く。ファイル名は固定。

| ファイル | 撮る画面 |
|---|---|
| `01-start.png` | `bash setup/start.sh` の起動メッセージ（URL・ユーザー・パスワード・モード） |
| `01b-login.png` | ブラウザの Basic 認証ダイアログ（`http://127.0.0.1:3011`） |
| `02-connect.png` | 「0. AWS 接続」にアクセスキーを入力した状態 |
| `02b-connected.png` | 「0. AWS 接続」で接続が成功した状態 |
| `03-settings.png` | 「1. 設定」の入力欄 |
| `04a-deploy-running.png` | 「2. デプロイ」の実行中（スタック状態とイベントログ） |
| `04-deploy.png` | 「2. デプロイ」で `✅ KarteStack` と Outputs が出た状態 |
| `05-put.png` | 「3. データのPUT」と、PUT 後の S3 の様子 |
| `05-put-2.png` | S3 コンソールで `inbound/events/` にオブジェクトが並んだ状態 |
| `06-full.png` | 「4. FULL の実行」のカード（開始前） |
| `06b-full-done.png` | FULL が SUCCEEDED になり、カルテ件数などが出た状態 |
| `07-api.png` | 「5. 社内システム連携」のロール ARN とサンプル、セルフテスト結果 |

- 幅は 1000〜1400px 程度、PNG。
- **アカウントID・アクセスキー・会員ID・ARN などは、必ずマスクしてから保存する。** 画面のメッセージ本文にも出る（例: 「接続しました（アカウント …）」）ので、入力欄だけでなくメッセージも確認する。
- 画像を追加したら `git add docs/screenshot/*.png` してコミットすれば、quickstart にそのまま表示される。
