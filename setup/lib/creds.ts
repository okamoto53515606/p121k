/**
 * AWS 認証情報は「このプロセス（サーバ）のメモリ」にだけ置く。
 *
 * why 保存しない: 画面はデプロイ権限を持つ。ディスクに置くと、リポジトリへの
 *                 誤コミット・バックアップ・共有 PC の別ユーザーから読まれうる。
 *                 サーバ再起動で消えるので、その都度入力してもらう（入力は 1 分）。
 * why globalThis: Next.js はルートごとにバンドルを分けるため、モジュール変数では
 *                 ルート間で共有されない。globalThis なら同一プロセス内で共有できる。
 */
export interface AwsSession {
  accessKeyId: string;
  secretAccessKey: string;
  sessionToken?: string;
  region: string;
  accountId: string;
  arn: string;
  connectedAt: string;
}

const KEY = "__p121k_aws_session__";

type Holder = { [KEY]?: AwsSession };
const holder = globalThis as unknown as Holder;

export function getSession(): AwsSession | undefined {
  return holder[KEY];
}

export function setSession(s: AwsSession): void {
  holder[KEY] = s;
}

export function clearSession(): void {
  delete holder[KEY];
}

/** 子プロセスに渡す env（キーはここでしか展開しない） */
export function awsEnv(s: AwsSession, extra: Record<string, string> = {}): NodeJS.ProcessEnv {
  return {
    ...process.env,
    AWS_ACCESS_KEY_ID: s.accessKeyId,
    AWS_SECRET_ACCESS_KEY: s.secretAccessKey,
    ...(s.sessionToken ? { AWS_SESSION_TOKEN: s.sessionToken } : {}),
    AWS_REGION: s.region,
    AWS_DEFAULT_REGION: s.region,
    AWS_PAGER: "",
    ...extra,
  };
}
