import { NextResponse } from "next/server";
import { getSession } from "@/lib/creds";
import { describeStack, latestExecution } from "@/lib/aws";
import { logText, syncDeployResult } from "@/lib/cdk";
import { readState } from "@/lib/state";

/** 画面が一定間隔で叩く状態 API（デプロイ結果・スタック・実行状況・ログ末尾） */
export async function GET() {
  syncDeployResult();
  const state = readState();
  const session = getSession();
  if (!session) {
    return NextResponse.json({ connected: false, settings: state.settings, deploy: state.deploy, log: logText().slice(-8000) });
  }

  let stack = null;
  let execution = null;
  let error: string | undefined;
  try {
    stack = await describeStack("KarteStack", session);
    const arn = stack?.outputs.PipelineArn;
    if (arn) execution = await latestExecution(arn, session);
  } catch (e) {
    error = (e as Error).message;
  }
  return NextResponse.json({
    connected: true,
    accountId: session.accountId,
    region: session.region,
    settings: state.settings,
    deploy: state.deploy,
    stack,
    execution,
    error,
    log: logText().slice(-8000),
  });
}
