# TODO（残タスク）

凡例: `[ ]` 未着手 / `[x]` 完了。優先度は上から。

## 開発

- [ ] **Claude Haiku 5.5 の有効化**: Marketplace の契約（usage-based）に同意して利用可能にし、実機で `jp.anthropic.claude-haiku-5-5` の応答を確認（現状は Haiku 4.5 へ自動フォールバック）
- [ ] **退会データが残りうる場所の棚卸しと対処**（原則7に関わる）
  - [ ] CloudWatch Logs（仮名 `member_id` を記録している。保持1か月）
  - [ ] DynamoDB の PITR / バックアップを有効にしない運用の明文化（有効化すると削除後も最大35日残る）
  - [ ] Personalize の古い Solution Version の自動削除（FULL成功後に旧バージョンを削除）
- [ ] **セットアップ画面**（`newcleus/setup` 方式）
  - [ ] デプロイ / 更新 / 削除ボタン（CDK実行）
  - [ ] FULL の手動起動と進捗表示
  - [ ] 保持年数 N・週次スケジュールの設定
  - [ ] PUT先S3パス・ファイル仕様・APIエンドポイント・呼び出し例の案内
  - [ ] **ローカル限定で動かす / 画面自体にも認証 / 最小権限のデプロイ専用IAM / キーは保存せず使用後に無効化を案内**
- [ ] **障害通知**: パイプライン失敗・`skipped_files`・拒否行の急増を SNS 等で通知
- [ ] build 失敗時に DynamoDB が中途半端な状態にならないことの確認（書き込み途中の失敗時の挙動を設計）
- [ ] 自社側ツールの参考実装（PUT側）: HMAC仮名化、問い合わせの分類コード化、`withdraw` の出力
- [ ] 自社サーバ用 IAM（Roles Anywhere / OIDC など）の構成例
- [ ] 監査: CloudTrail（Data Events）、API Gateway アクセスログ、AWS Config ルール
- [ ] スケール: 数千万イベント規模で build を Glue / Fargate に置き換える閾値と設計
- [ ] LICENSE の決定

## テスト

- [ ] GitHub Actions で `pytest` と `npx jest` を自動実行
- [ ] Personalize のオフライン評価（precision@k / coverage、時系列分割）
- [ ] 退会 → FULL → Personalize 入力 CSV に当該会員が含まれないことの自動検査（E2Eに追加）
- [ ] S3 ライフサイクルによる古いファイル削除の実機確認（保持年数を短くして検証）
- [ ] 権限テスト: 別ロール・別アカウント・署名なしでの API 呼び出しがすべて拒否される
- [ ] `allowedCidrs`（IP制限）と `weeklyFull=true`（週次起動）の実機確認
- [ ] Bedrock 呼び出しが `jp.*` 経由（東京・大阪のみ）であることを CloudTrail で確認
- [ ] 日本語の伏字化（`other` の概要、文案出力）の精度評価とテスト拡充
- [ ] 10万会員規模の負荷試験（build の所要時間とメモリ、Lambda 15分上限の余裕）

## ドキュメント

- [ ] 法務確認: 仮名ID運用（個人情報保護法）、退会後の取扱い、再入会は新IDで別人扱いとする運用
- [ ] REES46 へ利用目的を確認（「無料・出典明記」が正式な許諾文でないため）
- [ ] Haiku 5.5 の単価を AWS Pricing Calculator / 公式価格で再確認（現状は第三者サイトの値）
- [ ] 運用手順書: PUT 手順、FULL 実行、拒否レポートの見方、障害対応、後片付け
- [ ] データ仕様のサンプルCSV（`docs/samples/`）
- [ ] 「データ主権が東京のみ必須の場合」の代替案（Bedrock を使わない、他モデル）の整理
