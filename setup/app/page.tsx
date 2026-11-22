"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type Settings = {
  retentionYears: number;
  weeklyFull: boolean;
  alertEmail: string;
  allowedCidrs: string;
  auditTrail: boolean;
  modelId: string;
  geoBlock: boolean;
  uploadCidrs: string;
  callerPrincipalArns: string;
};
type Deploy = { startedAt: string; finishedAt?: string; exitCode?: number; action: "deploy" | "destroy" };
type Stack = { status: string; outputs: Record<string, string>; updatedAt?: string } | null;
type Execution = { status: string; startedAt?: string; stoppedAt?: string; error?: string; cause?: string } | null;
type StateResp = {
  connected: boolean; accountId?: string; region?: string; settings: Settings; deploy?: Deploy;
  stack: Stack; execution: Execution; error?: string; log: string;
};

const EMPTY: StateResp = {
  connected: false,
  settings: { retentionYears: 3, weeklyFull: false, alertEmail: "", allowedCidrs: "", auditTrail: false,
    modelId: "jp.anthropic.claude-haiku-5-5", geoBlock: true, uploadCidrs: "", callerPrincipalArns: "" },
  stack: null, execution: null, log: "",
};

export default function Page() {
  const [st, setSt] = useState<StateResp>(EMPTY);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "ng" | "warn"; text: string } | null>(null);
  const [form, setForm] = useState({ accessKeyId: "", secretAccessKey: "", sessionToken: "", region: "ap-northeast-1" });
  const [settings, setSettings] = useState<Settings>(EMPTY.settings);
  const [selftest, setSelftest] = useState<null | { unsigned: number; signed: number; ok: boolean; note: string; target: string }>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await fetch("/api/state", { cache: "no-store" });
      const j = (await r.json()) as StateResp;
      setSt(j);
      setSettings((prev) => (prev === EMPTY.settings ? j.settings : prev));
    } catch { /* 画面を壊さない */ }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  useEffect(() => { setSettings(st.settings); }, [st.settings.retentionYears, st.settings.weeklyFull, st.settings.alertEmail, st.settings.allowedCidrs, st.settings.auditTrail, st.settings.modelId]); // eslint-disable-line react-hooks/exhaustive-deps

  const post = async (url: string, body?: unknown, method = "POST") => {
    setBusy(url); setMsg(null);
    try {
      const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { setMsg({ kind: "ng", text: j.error ?? `失敗しました (${r.status})` }); return null; }
      await refresh();
      return j;
    } catch (e) {
      setMsg({ kind: "ng", text: (e as Error).message });
      return null;
    } finally { setBusy(null); }
  };

  const outputs = st.stack?.outputs ?? {};
  const deployed = Boolean(outputs.ApiUrl);
  const deployRunning = Boolean(st.deploy && st.deploy.exitCode === undefined);
  const deployOk = st.deploy?.exitCode === 0;
  const stepDone = useMemo(() => ({
    connect: st.connected,
    settings: Boolean(st.settings),
    deploy: deployed && deployOk,
    ops: Boolean(st.execution),
  }), [st.connected, st.settings, deployed, deployOk, st.execution]);

  const apiBase = outputs.ApiUrl?.replace(/\/$/, "") ?? "https://<api-id>.execute-api.ap-northeast-1.amazonaws.com/v1";
  const bucket = outputs.BucketName ?? "<KarteBucketName>";

  return (
    <div className="layout">
      <nav className="side">
        <a href="#connect"><span className={`dot ${stepDone.connect ? "done" : ""}`} />0. AWS 接続</a>
        <a href="#settings"><span className={`dot ${stepDone.settings ? "done" : ""}`} />1. 設定</a>
        <a href="#deploy"><span className={`dot ${stepDone.deploy ? "done" : deployRunning ? "warn" : ""}`} />2. デプロイ</a>
        <a href="#put"><span className="dot" />3. データのPUT</a>
        <a href="#ops"><span className={`dot ${stepDone.ops ? "done" : ""}`} />4. FULL の実行</a>
        <a href="#guide"><span className={`dot ${deployed ? "done" : ""}`} />5. 社内システム連携</a>
        <a href="#destroy"><span className="dot" />6. 削除</a>
      </nav>

      <main className="content">
        {msg && <div className={`notice ${msg.kind === "ok" ? "" : msg.kind}`}>{msg.text}</div>}

        <section className="card" id="connect">
          <h2>0. AWS 接続</h2>
          <p className="lead">
            デプロイ専用の IAM ユーザーのアクセスキーを入力します。<strong>キーはこのサーバのメモリにだけ保持し、ディスクには書きません。</strong>
            画面を閉じてサーバを再起動すると消えるので、その都度入力してください。
          </p>
          {st.connected ? (
            <>
              <table className="kv">
                <tbody>
                  <tr><th>アカウント</th><td>{st.accountId}</td></tr>
                  <tr><th>呼び出し元</th><td><code>{st.region}</code> の IAM ユーザー</td></tr>
                </tbody>
              </table>
              <div className="actions">
                <button onClick={async () => { await post("/api/session", undefined, "DELETE"); setMsg({ kind: "ok", text: "接続を解除しました（キーは破棄済み）" }); }}>
                  接続を解除
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="row">
                <label className="field"><span>アクセスキーID</span>
                  <input type="text" value={form.accessKeyId} autoComplete="off" onChange={(e) => setForm({ ...form, accessKeyId: e.target.value })} placeholder="AKIA..." />
                </label>
                <label className="field"><span>シークレットアクセスキー</span>
                  <input type="password" value={form.secretAccessKey} autoComplete="off" onChange={(e) => setForm({ ...form, secretAccessKey: e.target.value })} />
                </label>
              </div>
              <div className="row">
                <label className="field"><span>セッショントークン（一時キーの場合のみ）</span>
                  <input type="password" value={form.sessionToken} autoComplete="off" onChange={(e) => setForm({ ...form, sessionToken: e.target.value })} />
                </label>
                <label className="field"><span>リージョン</span>
                  <input type="text" value={form.region} onChange={(e) => setForm({ ...form, region: e.target.value })} />
                </label>
              </div>
              <div className="actions">
                <button className="primary" disabled={busy !== null}
                  onClick={async () => {
                    const j = await post("/api/session", form);
                    if (j) { setMsg({ kind: "ok", text: `接続しました（アカウント ${j.accountId}）` }); setForm({ ...form, secretAccessKey: "", sessionToken: "" }); }
                  }}>接続して確認</button>
              </div>
              <p className="muted small" style={{ marginTop: 10 }}>
                推奨: この用途専用の IAM ユーザーを作り、`infra/` のデプロイに必要な権限だけを付けます（管理者権限の使い回しは避ける）。
              </p>
            </>
          )}
        </section>

        <section className="card" id="settings">
          <h2>1. 設定</h2>
          <p className="lead">ここで決めた値が CDK の context としてそのままデプロイに渡ります。</p>
          <div className="row">
            <label className="field"><span>ソース履歴の保持年数（S3 ライフサイクル）</span>
              <input type="number" min={1} max={10} value={settings.retentionYears}
                onChange={(e) => setSettings({ ...settings, retentionYears: Number(e.target.value) })} />
            </label>
            <label className="field"><span>障害通知メール（任意）</span>
              <input type="text" value={settings.alertEmail} placeholder="ops@example.co.jp"
                onChange={(e) => setSettings({ ...settings, alertEmail: e.target.value })} />
            </label>
          </div>
          <div className="row">
            <label className="field"><span>API の送信元IP制限（CIDR をカンマ区切り・任意）</span>
              <input type="text" value={settings.allowedCidrs} placeholder="203.0.113.0/24"
                onChange={(e) => setSettings({ ...settings, allowedCidrs: e.target.value })} />
            </label>
            <label className="field"><span>文案生成モデル</span>
              <input type="text" value={settings.modelId}
                onChange={(e) => setSettings({ ...settings, modelId: e.target.value })} />
            </label>
          </div>
          <label className="check">
            <input type="checkbox" checked={settings.weeklyFull} onChange={(e) => setSettings({ ...settings, weeklyFull: e.target.checked })} />
            <span>週次 FULL を自動実行する（毎週日曜 03:00 JST）</span>
          </label>
          <div className="row">
            <label className="field"><span>S3 の IP 許可リスト（CIDR・任意）</span>
              <input type="text" value={settings.uploadCidrs} placeholder="203.0.113.0/24"
                onChange={(e) => setSettings({ ...settings, uploadCidrs: e.target.value })} />
            </label>
            <label className="field"><span>ロールを引き受けさせる相手（ARN・任意）</span>
              <input type="text" value={settings.callerPrincipalArns} placeholder="arn:aws:iam::123456789012:role/my-batch"
                onChange={(e) => setSettings({ ...settings, callerPrincipalArns: e.target.value })} />
            </label>
          </div>
          <label className="check">
            <input type="checkbox" checked={settings.auditTrail} onChange={(e) => setSettings({ ...settings, auditTrail: e.target.checked })} />
            <span>CloudTrail のデータイベントを有効にする（S3 への読み書きを記録。課金あり）</span>
          </label>
          <label className="check">
            <input type="checkbox" checked={settings.geoBlock} onChange={(e) => setSettings({ ...settings, geoBlock: e.target.checked })} />
            <span>海外IPからの API 呼び出しを遮断する（WAF の国判定で日本以外をブロック。課金あり）</span>
          </label>

        </section>

        <section className="card" id="deploy">
          <h2>2. デプロイ / 更新</h2>
          <p className="lead">
            初回は約 6〜10 分（Personalize の作成を含む）。変更がなければ数分で終わります。
            実行中はこの画面を閉じても構いません（サーバ側で継続）。
          </p>
          <div className="actions">
            <button className="primary" disabled={!st.connected || busy !== null || deployRunning}
              onClick={async () => {
                const j = await post("/api/deploy", settings);
                if (j) setMsg({ kind: "warn", text: "デプロイを開始しました。下のログで進捗を確認できます。" });
              }}>{deployRunning ? "デプロイ実行中…" : "デプロイ / 更新"}</button>
            <span className={`status ${deployRunning ? "warn" : deployOk ? "ok" : st.deploy ? "ng" : ""}`}>
              {deployRunning ? "実行中" : st.deploy ? (deployOk ? "成功" : `失敗 (exit ${st.deploy.exitCode})`) : "未実行"}
            </span>
            {st.deploy && <span className="muted small">開始 {new Date(st.deploy.startedAt).toLocaleString("ja-JP")}</span>}
          </div>
          {st.stack && (
            <table className="kv">
              <tbody>
                <tr><th>スタック状態</th><td><code>{st.stack.status}</code></td></tr>
                <tr><th>API</th><td><code>{outputs.ApiUrl ?? "-"}</code></td></tr>
                <tr><th>バケット</th><td><code>{outputs.BucketName ?? "-"}</code></td></tr>
                <tr><th>パイプライン</th><td><code>{outputs.PipelineArn ?? "-"}</code></td></tr>
              </tbody>
            </table>
          )}
          <pre>{st.log || "（まだログはありません）"}</pre>
        </section>

        <section className="card" id="put">
          <h2>3. データのPUT</h2>
          <p className="lead">
            FULL を回す前に、自社システムから次の3種類を S3 に置きます。<strong>個人情報はここで捨てます</strong>（氏名・住所・電話・メール、
            問い合わせ本文は送らない）。会員IDは HMAC で仮名化したものを使います。
          </p>
          <table className="kv">
            <tbody>
              <tr><th>アイテムマスタ</th><td><code>s3://{bucket}/inbound/items/&lt;任意名&gt;.tsv</code><br />
                <span className="muted small">最新ファイルが全件スナップショット。列: <code>item_id, item_name, item_description, category, price</code></span></td></tr>
              <tr><th>行動履歴</th><td><code>s3://{bucket}/inbound/events/&lt;任意名&gt;.tsv</code><br />
                <span className="muted small">追記する（タブ区切り）。列: <code>member_id, item_id, event_type, event_ts, reason</code>（<code>event_type</code> は view / like / cart / purchase / return / withdraw）</span></td></tr>
              <tr><th>問い合わせ</th><td><code>s3://{bucket}/inbound/inquiries/&lt;任意名&gt;.tsv</code><br />
                <span className="muted small">分類コードのみ。列: <code>inquiry_id, member_id, received_ts, category, note</code>（<code>note</code> は <code>other</code> のときだけ40字以内）</span></td></tr>
              <tr><th>必要な IAM</th><td><code>{outputs.UploadPolicyArn ?? "karte-inbound-upload"}</code></td></tr>
            </tbody>
          </table>
          <ul className="tight">
            <li><strong>退会</strong>は <code>withdraw</code> 行を追記するだけ。その行は消さない（毎回の FULL でこの会員を丸ごと除外する根拠になる）。</li>
            <li>書式は TSV（タブ区切り）・UTF-8・ヘッダ付き。列の過不足はファイルごと拒否され、<code>reports/&lt;run&gt;/rejects.csv</code> に残る。</li>
            <li>仕様は <code>docs/s3-spec.md</code>、サンプルは <code>docs/samples/</code>、変換の参考実装は <code>tools/ingest_side.py</code>。</li>
          </ul>
        </section>

        <section className="card" id="ops">
          <h2>4. FULL の実行</h2>
          <p className="lead">
            FULL は「S3 の全履歴を読み直してカルテと学習データを作り直す」処理です。退会者はこのタイミングで完全に消えます。
          </p>
          <div className="actions">
            <button className="primary" disabled={!deployed || busy !== null}
              onClick={async () => {
                const j = await post("/api/full");
                if (j) setMsg({ kind: "ok", text: "FULL を開始しました。学習込みで 1 時間ほどかかります。" });
              }}>FULL を開始</button>
            {st.execution && (
              <span className={`status ${st.execution.status === "SUCCEEDED" ? "ok" : st.execution.status === "RUNNING" ? "warn" : "ng"}`}>
                {st.execution.status}
              </span>
            )}
          </div>
          {st.execution && (
            <table className="kv">
              <tbody>
                <tr><th>開始</th><td>{st.execution.startedAt ? new Date(st.execution.startedAt).toLocaleString("ja-JP") : "-"}</td></tr>
                <tr><th>終了</th><td>{st.execution.stoppedAt ? new Date(st.execution.stoppedAt).toLocaleString("ja-JP") : "-"}</td></tr>
                {st.execution.error && <tr><th>エラー</th><td>{st.execution.error}</td></tr>}
                {st.execution.cause && <tr><th>原因</th><td>{st.execution.cause}</td></tr>}
              </tbody>
            </table>
          )}
          <p className="muted small">失敗時は SNS の通知（設定していればメール）に届きます。拒否行は <code>s3://{bucket}/reports/</code> に残ります。</p>
        </section>

        <section className="card" id="guide">
          <h2>5. 社内システム連携（API）</h2>
          <p className="lead">取得系の API です。呼び出し元のロールに <code>karte-api-invoke</code> を付けて、SigV4 で署名します。</p>

          <h3 style={{ fontSize: 13, marginBottom: 4 }}>API（SigV4 署名が必須）</h3>
          <table className="kv">
            <tbody>
              <tr><th>エンドポイント</th><td><code>{apiBase}</code></td></tr>
              <tr><th>必要な IAM</th><td><code>{outputs.ApiInvokePolicyArn ?? "karte-api-invoke"}</code></td></tr>
            </tbody>
          </table>
          <pre>{`# カルテ取得
aws --region ap-northeast-1 apigateway … ではなく curl で署名する例:
curl -sS --aws-sigv4 "aws:amz:ap-northeast-1:execute-api" \\
  --user "$AWS_ACCESS_KEY_ID:$AWS_SECRET_ACCESS_KEY" \\
  "${apiBase}/karte/<memberId>"

# レコメンド / 文案 / 削除
curl … "${apiBase}/karte/<memberId>/reco"
curl -X POST … "${apiBase}/karte/<memberId>/copy" -d '{"channel":"mail"}'
curl -X DELETE … "${apiBase}/karte/<memberId>"`}</pre>

          <div className="actions">
            <button disabled={!deployed || busy !== null}
              onClick={async () => {
                const j = await post("/api/selftest");
                if (j) setSelftest(j);
              }}>API のセルフテスト（署名なし→403 / 署名あり→404）</button>
          </div>
          {selftest && (
            <div className={`notice ${selftest.ok ? "" : "ng"}`} style={{ marginTop: 10 }}>
              署名なし: <strong>{selftest.unsigned}</strong> / 署名あり: <strong>{selftest.signed}</strong>（{selftest.ok ? "正常" : "要確認"}）
              <div className="muted small">{selftest.note}</div>
            </div>
          )}
        </section>

        <section className="card" id="keys">
          <h3 style={{ fontSize: 13, marginBottom: 4 }}>自社サーバが引き受けるロール</h3>
          <p className="muted small">
            呼び出しは<strong>IAM ロール</strong>で行います（長期キーなし）。EC2 のインスタンスプロファイル、ECS タスクロール、
            EKS（IRSA）、OIDC、Roles Anywhere のいずれかで次のロールを引き受けてください。
          </p>
          <table className="kv">
            <tbody>
              <tr><th>S3 PUT 用</th><td><code>{outputs.UploadRoleArn ?? "（デプロイ後に表示）"}</code></td></tr>
              <tr><th>API 用</th><td><code>{outputs.ApiRoleArn ?? "（デプロイ後に表示）"}</code></td></tr>
            </tbody>
          </table>
          <p className="muted small">別アカウントのロールに許可する場合は、設定の「ロールを引き受けさせる相手」に ARN を追加して再デプロイします。</p>
          <p className="notice small" style={{ marginTop: 10 }}>
            長期アクセスキーの発行は行いません（<strong>禁止</strong>）。上のロールを使ってください。
          </p>
        </section>

        <section className="card" id="destroy">
          <h2>6. 削除</h2>
          <p className="lead">スタックを削除します（S3 のデータ、DynamoDB のカルテ、KMS キーも消えます）。Personalize の Solution は残るため、必要なら手動で削除してください。</p>
          <div className="actions">
            <button className="danger" disabled={!st.connected || busy !== null || deployRunning}
              onClick={async () => {
                if (!confirm("KarteStack を削除します。元に戻せません。よろしいですか？")) return;
                const j = await post("/api/deploy", undefined, "DELETE");
                if (j) setMsg({ kind: "warn", text: "削除を開始しました。" });
              }}>スタックを削除</button>
          </div>
        </section>
      </main>
    </div>
  );
}
