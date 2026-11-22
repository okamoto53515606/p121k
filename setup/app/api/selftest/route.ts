import { execFile } from "child_process";
import { NextResponse } from "next/server";
import { getSession } from "@/lib/creds";
import { describeStack } from "@/lib/aws";

function curl(args: string[], env: NodeJS.ProcessEnv): Promise<number> {
  return new Promise((resolve) => {
    execFile("curl", args, { env, timeout: 30_000 }, (err, stdout) => {
      if (err && !stdout) {
        resolve(-1);
        return;
      }
      resolve(Number(String(stdout).trim()) || -1);
    });
  });
}

/**
 * API のセルフテスト。
 * 署名なし → 403（IAM 保護が効いている）、署名あり → 404/200（署名が通る）。
 */
export async function POST() {
  const s = getSession();
  if (!s) return NextResponse.json({ error: "先に AWS へ接続してください" }, { status: 401 });
  const stack = await describeStack("KarteStack", s);
  const url = stack?.outputs.ApiUrl;
  if (!url) return NextResponse.json({ error: "API がまだありません（デプロイ未完了）" }, { status: 400 });

  const target = `${url.replace(/\/$/, "")}/karte/${"a".repeat(32)}`;
  const env = {
    ...process.env,
    AWS_ACCESS_KEY_ID: s.accessKeyId,
    AWS_SECRET_ACCESS_KEY: s.secretAccessKey,
    ...(s.sessionToken ? { AWS_SESSION_TOKEN: s.sessionToken } : {}),
  };
  const unsigned = await curl(["-s", "-o", "/dev/null", "-w", "%{http_code}", "-X", "GET", target], env);
  const signed = await curl([
    "-s", "-o", "/dev/null", "-w", "%{http_code}", "-X", "GET", target,
    "--aws-sigv4", `aws:amz:${s.region}:execute-api`, "--user", `${s.accessKeyId}:${s.secretAccessKey}`,
    ...(s.sessionToken ? ["-H", `x-amz-security-token: ${s.sessionToken}`] : []),
  ], env);

  return NextResponse.json({
    target,
    unsigned,
    signed,
    ok: unsigned === 403 && (signed === 404 || signed === 200),
    note: "署名前: 403 が正常。署名後: 404（その会員がいない）または 200 が正常。",
  });
}
