import { spawn } from "child_process";
import { closeSync, existsSync, openSync, readFileSync, statSync, writeFileSync } from "fs";
import { resolve } from "path";
import { awsEnv, type AwsSession } from "./creds";
import { ensureStateDir, LOG_PATH, patchState, readState, type Settings } from "./state";

/** infra/ ディレクトリ（CDK プロジェクト） */
export function infraDir(): string {
  return resolve(process.cwd(), "..", "infra");
}

/** CDK に渡す context 引数。設定画面の値がそのままインフラに効く。 */
export function contextArgs(s: Settings): string[] {
  const args = [
    "-c", `retentionYears=${s.retentionYears}`,
    "-c", `weeklyFull=${s.weeklyFull ? "true" : "false"}`,
    "-c", `auditTrail=${s.auditTrail ? "true" : "false"}`,
    "-c", `modelId=${s.modelId}`,
    "-c", `policyReference=${s.policyReference}`,
  ];
  if (s.alertEmail) args.push("-c", `alertEmail=${s.alertEmail}`);
  if (s.allowedCidrs) args.push("-c", `allowedCidrs=${s.allowedCidrs.replace(/\s+/g, "")}`);
  return args;
}

function isRunning(): boolean {
  const st = readState().deploy;
  if (!st || st.exitCode !== undefined) return false;
  // If the log already has our exit marker, the process is finished.
  return !logText().includes("__EXIT__");
}

export function logText(): string {
  if (!existsSync(LOG_PATH)) return "";
  const size = statSync(LOG_PATH).size;
  // 末尾 200KB だけ読む（巨大ログで UI が固まらないように）
  const buf = readFileSync(LOG_PATH);
  return buf.subarray(Math.max(0, size - 200_000)).toString("utf-8");
}

/** CDK を非同期で起動し、ログを .state/deploy.log に流す */
export function startCdk(action: "deploy" | "destroy", session: AwsSession): { started: true } | { started: false; error: string } {
  if (isRunning()) return { started: false, error: "すでに実行中です（完了までお待ちください）" };
  ensureStateDir();
  writeFileSync(LOG_PATH, "");
  const s = readState().settings;
  const cmd = action === "deploy"
    ? `npx cdk deploy KarteStack ${contextArgs(s).join(" ")} --require-approval never --outputs-file ../setup/state-outputs.json`
    : `npx cdk destroy KarteStack --force`;
  const fd = openSync(LOG_PATH, "a", 0o600);
  const child = spawn("bash", ["-lc", `${cmd}; echo "__EXIT__$?"`], {
    cwd: infraDir(),
    env: awsEnv(session, { CI: "1" }),
    detached: true,
    stdio: ["ignore", fd, fd],
  });
  child.unref();
  closeSync(fd);
  patchState({
    deploy: { startedAt: new Date().toISOString(), action, logFile: LOG_PATH, exitCode: undefined },
  });
  return { started: true };
}

/** ログの __EXIT__ マーカーから終了コードを読み取って状態に反映する */
export function syncDeployResult(): void {
  const text = logText();
  const m = text.match(/__EXIT__(\d+)/);
  const st = readState();
  if (m && st.deploy && st.deploy.exitCode === undefined) {
    const code = Number(m[1]);
    patchState({ deploy: { ...st.deploy, exitCode: code, finishedAt: new Date().toISOString() } });
  }
}
