import { existsSync, mkdirSync, readFileSync, writeFileSync } from "fs";
import { resolve } from "path";

/**
 * 画面の状態（設定値・最終デプロイの結果）を保存する。
 * 認証情報は絶対に入れない（lib/creds.ts のメモリのみ）。
 */
export interface Settings {
  retentionYears: number;
  weeklyFull: boolean;
  alertEmail: string;
  allowedCidrs: string;
  auditTrail: boolean;
  modelId: string;
  geoBlock: boolean;
  uploadCidrs: string;
  accessKeyMaxAgeDays: number;
}

export interface DeployState {
  startedAt: string;
  finishedAt?: string;
  exitCode?: number;
  action: "deploy" | "destroy";
  logFile: string;
}

export interface SetupState {
  settings: Settings;
  deploy?: DeployState;
  outputs?: Record<string, string>;
  outputsAt?: string;
  lastExecutionArn?: string;
}

export const DEFAULT_SETTINGS: Settings = {
  retentionYears: 3,
  weeklyFull: false,
  alertEmail: "",
  allowedCidrs: "",
  auditTrail: false,
  modelId: "jp.anthropic.claude-haiku-5-5",
  geoBlock: true,
  uploadCidrs: "",
  accessKeyMaxAgeDays: 180,
};

const STATE_PATH = resolve(process.cwd(), "setup-state.json");
const STATE_DIR = resolve(process.cwd(), ".state");
export const LOG_PATH = resolve(STATE_DIR, "deploy.log");

export function ensureStateDir(): void {
  if (!existsSync(STATE_DIR)) mkdirSync(STATE_DIR, { recursive: true, mode: 0o700 });
}

export function readState(): SetupState {
  if (!existsSync(STATE_PATH)) return { settings: { ...DEFAULT_SETTINGS } };
  try {
    const raw = JSON.parse(readFileSync(STATE_PATH, "utf-8")) as Partial<SetupState>;
    return { ...raw, settings: { ...DEFAULT_SETTINGS, ...(raw.settings ?? {}) } };
  } catch {
    return { settings: { ...DEFAULT_SETTINGS } };
  }
}

export function writeState(next: SetupState): void {
  writeFileSync(STATE_PATH, JSON.stringify(next, null, 2) + "\n", { encoding: "utf-8", mode: 0o600 });
}

export function patchState(patch: Partial<SetupState>): SetupState {
  const next = { ...readState(), ...patch };
  writeState(next);
  return next;
}

/** 設定値を検証する（CDK の context に渡るため、型と範囲を厳密に見る） */
export function validateSettings(input: unknown): { ok: true; settings: Settings } | { ok: false; error: string } {
  const b = (input ?? {}) as Record<string, unknown>;
  const years = Number(b.retentionYears ?? DEFAULT_SETTINGS.retentionYears);
  if (!Number.isInteger(years) || years < 1 || years > 10) return { ok: false, error: "保持年数は1〜10の整数で指定してください" };
  const alertEmail = String(b.alertEmail ?? "").trim();
  if (alertEmail && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(alertEmail)) return { ok: false, error: "通知メールの形式が不正です" };
  const allowedCidrs = String(b.allowedCidrs ?? "").trim();
  if (allowedCidrs && !/^[0-9a-fA-F.:/,\s]+$/.test(allowedCidrs)) return { ok: false, error: "IP制限は CIDR をカンマ区切りで指定してください" };
  const modelId = String(b.modelId ?? DEFAULT_SETTINGS.modelId).trim();
  if (!/^[a-z0-9.-]+$/.test(modelId)) return { ok: false, error: "モデルIDの形式が不正です" };
  const keyDays = Number(b.accessKeyMaxAgeDays ?? DEFAULT_SETTINGS.accessKeyMaxAgeDays);
  if (!Number.isInteger(keyDays) || keyDays < 1 || keyDays > 365) return { ok: false, error: "アクセスキーの有効期間は1〜365日で指定してください" };
  const uploadCidrs = String(b.uploadCidrs ?? "").trim();
  if (uploadCidrs && !/^[0-9a-fA-F.:/,\s]+$/.test(uploadCidrs)) return { ok: false, error: "S3のIP制限は CIDR をカンマ区切りで指定してください" };
  return {
    ok: true,
    settings: {
      retentionYears: years,
      weeklyFull: Boolean(b.weeklyFull),
      alertEmail,
      allowedCidrs,
      auditTrail: Boolean(b.auditTrail),
      modelId,
      geoBlock: b.geoBlock !== false,
      uploadCidrs: String(b.uploadCidrs ?? "").trim(),
      accessKeyMaxAgeDays: Number(b.accessKeyMaxAgeDays ?? DEFAULT_SETTINGS.accessKeyMaxAgeDays),
    },
  };
}
