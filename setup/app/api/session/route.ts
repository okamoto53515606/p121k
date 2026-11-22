import { NextResponse } from "next/server";
import { clearSession, getSession, setSession, type AwsSession } from "@/lib/creds";
import { whoAmI } from "@/lib/aws";

/** 現在の接続状態（キーそのものは返さない） */
export async function GET() {
  const s = getSession();
  if (!s) return NextResponse.json({ connected: false });
  return NextResponse.json({
    connected: true,
    accountId: s.accountId,
    arn: s.arn,
    region: s.region,
    connectedAt: s.connectedAt,
  });
}

/** アクセスキーで接続を検証し、メモリにだけ保持する */
export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
  const accessKeyId = String(body.accessKeyId ?? "").trim();
  const secretAccessKey = String(body.secretAccessKey ?? "").trim();
  const sessionToken = String(body.sessionToken ?? "").trim() || undefined;
  const region = String(body.region ?? "ap-northeast-1").trim();
  if (!accessKeyId || !secretAccessKey) {
    return NextResponse.json({ error: "アクセスキーIDとシークレットを入力してください" }, { status: 400 });
  }
  if (!/^[a-z]{2}-[a-z]+-\d$/.test(region)) {
    return NextResponse.json({ error: "リージョンは ap-northeast-1 の形式で指定してください" }, { status: 400 });
  }

  const candidate: AwsSession = {
    accessKeyId, secretAccessKey, sessionToken, region, accountId: "", arn: "",
    connectedAt: new Date().toISOString(),
  };
  try {
    const id = await whoAmI(candidate);
    candidate.accountId = id.Account;
    candidate.arn = id.Arn;
  } catch (e) {
    return NextResponse.json({ error: `接続できませんでした: ${(e as Error).message}` }, { status: 400 });
  }
  setSession(candidate);
  return NextResponse.json({ connected: true, accountId: candidate.accountId, arn: candidate.arn, region });
}

/** 接続を破棄する（キーはディスクに残っていない） */
export async function DELETE() {
  clearSession();
  return NextResponse.json({ connected: false });
}
