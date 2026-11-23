# スクリーンショット置き場

`docs/quickstart.md` が参照する画像をここに置く。ファイル名は固定。

| ファイル | 撮る画面 |
|---|---|
| `01-start.png` | `bash setup/start.sh` の起動メッセージ（URL・ユーザー・パスワード） |
| `02-connect.png` | セットアップ画面「0. AWS 接続」で接続が成功した状態 |
| `03-settings.png` | 「1. 設定」の入力欄 |
| `04-deploy.png` | 「2. デプロイ」で `✅ KarteStack` と Outputs が出た状態 |
| `05-put.png` | 「3. データのPUT」と、PUT 後の S3 の様子 |
| `06-full.png` | 「4. FULL の実行」で SUCCEEDED になった状態 |
| `07-api.png` | 「5. 社内システム連携」のロール ARN とサンプル、セルフテスト結果 |

- 幅は 1000〜1400px 程度、PNG。
- 会員ID・アカウントID・ARN などが出る場合は、撮影前にダミーへ置き換えるか、該当箇所をモザイクにする。
- 画像を追加したら `git add docs/screenshot/*.png` してコミットすれば、quickstart にそのまま表示される。
