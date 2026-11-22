import { NextResponse } from "next/server";
import { getSession } from "@/lib/creds";
import { aws, describeStack } from "@/lib/aws";
import { readState } from "@/lib/state";

/** S3 用 / API 用のどちらのキーか。IAM ユーザーはスタックが作成済み。 */
const KINDS = { upload: "UploadUserName", api: "ApiUserName" } as const;
type Kind = keyof typeof KINDS;

async function users(): Promise<Record<Kind, string> | null> {
  const s = getSession();
  if (!s) return null;
  const stack = await describeStack("KarteStack", s);
  if (!stack) return null;
  return { upload: stack.outputs.UploadUserName ?? "", api: stack.outputs.ApiUserName ?? "" };
}

/** 現在のキー（秘密は返さない）。有効期限の目安つき。 */
export async function GET() {
  const s = getSession();
  if (!s) return NextResponse.json({ error: "先に AWS へ接続してください" }, { status: 401 });
  const names = await users();
  if (!names) return NextResponse.json({ error: "スタックがありません" }, { status: 400 });
  const maxAge = readState().settings.accessKeyMaxAgeDays;
  const out: Record<string, unknown> = { maxAgeDays: maxAge };
  for (const [kind, user] of Object.entries(names)) {
    if (!user) continue;
    const res = JSON.parse(await aws(["iam", "list-access-keys", "--user-name", user, "--output", "json"], s));
    out[kind] = (res.AccessKeyMetadata ?? []).map((k: { AccessKeyId: string; Status: string; CreateDate: string }) => ({
      id: k.AccessKeyId, status: k.Status, created: k.CreateDate,
      expires: new Date(new Date(k.CreateDate).getTime() + maxAge * 86400000).toISOString(),
    }));
  }
  return NextResponse.json(out);
}

/** キーを発行する。秘密はこの応答にしか現れない（保存しない）。 */
export async function POST(request: Request) {
  const s = getSession();
  if (!s) return NextResponse.json({ error: "先に AWS へ接続してください" }, { status: 401 });
  const body = (await request.json().catch(() => ({}))) as { kind?: string };
  const kind = (body.kind ?? "upload") as Kind;
  if (!(kind in KINDS)) return NextResponse.json({ error: "kind は upload / api" }, { status: 400 });
  const names = await users();
  const user = names?.[kind];
  if (!user) return NextResponse.json({ error: "スタックに IAM ユーザーがありません" }, { status: 400 });

  const maxAge = readState().settings.accessKeyMaxAgeDays;
  const out = JSON.parse(await aws(["iam", "create-access-key", "--user-name", user, "--output", "json"], s));
  const k = out.AccessKey;
  return NextResponse.json({
    kind, user,
    accessKeyId: k.AccessKeyId,
    secretAccessKey: k.SecretAccessKey,
    expires: new Date(Date.now() + maxAge * 86400000).toISOString(),
    maxAgeDays: maxAge,
    warning: "この値は再表示できません。安全な場所に移し、画面を閉じてください。期限を過ぎたキーは日次ジョブが自動で削除します。",
  });
}
