import { NextResponse } from "next/server";
import { getSession } from "@/lib/creds";
import { startCdk } from "@/lib/cdk";
import { patchState, readState, validateSettings } from "@/lib/state";

/** 設定を保存し、CDK deploy を非同期で開始する */
export async function POST(request: Request) {
  const session = getSession();
  if (!session) return NextResponse.json({ error: "先に AWS へ接続してください" }, { status: 401 });

  const body = await request.json().catch(() => ({}));
  const v = validateSettings(body);
  if (!v.ok) return NextResponse.json({ error: v.error }, { status: 400 });

  patchState({ settings: v.settings });
  const r = startCdk("deploy", session);
  if (!r.started) return NextResponse.json({ error: r.error }, { status: 409 });
  return NextResponse.json({ started: true, settings: readState().settings });
}

/** 削除（cdk destroy） */
export async function DELETE() {
  const session = getSession();
  if (!session) return NextResponse.json({ error: "先に AWS へ接続してください" }, { status: 401 });
  const r = startCdk("destroy", session);
  if (!r.started) return NextResponse.json({ error: r.error }, { status: 409 });
  return NextResponse.json({ started: true });
}
