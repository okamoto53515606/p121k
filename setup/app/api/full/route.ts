import { NextResponse } from "next/server";
import { getSession } from "@/lib/creds";
import { aws, describeStack } from "@/lib/aws";
import { patchState } from "@/lib/state";

/** FULL パイプラインを手動で開始する */
export async function POST() {
  const session = getSession();
  if (!session) return NextResponse.json({ error: "先に AWS へ接続してください" }, { status: 401 });

  const stack = await describeStack("KarteStack", session);
  const arn = stack?.outputs.PipelineArn;
  if (!arn) return NextResponse.json({ error: "スタックがありません。先にデプロイしてください" }, { status: 400 });

  const out = await aws(["stepfunctions", "start-execution", "--state-machine-arn", arn, "--input", "{}",
    "--query", "executionArn", "--output", "text"], session, 30_000);
  const executionArn = out.trim();
  patchState({ lastExecutionArn: executionArn });
  return NextResponse.json({ started: true, executionArn });
}
