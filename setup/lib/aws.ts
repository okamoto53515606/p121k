import { execFile } from "child_process";
import { awsEnv, type AwsSession } from "./creds";

/** aws CLI を実行して stdout を返す（失敗時は stderr を含む Error） */
export function aws(args: string[], s: AwsSession, timeoutMs = 60_000): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile("aws", args, { env: awsEnv(s), timeout: timeoutMs, maxBuffer: 8 * 1024 * 1024 }, (err, stdout, stderr) => {
      if (err) {
        reject(new Error(`${err.message}\n${stderr}`.trim().slice(0, 1200)));
        return;
      }
      resolve(stdout);
    });
  });
}

export async function awsJson<T>(args: string[], s: AwsSession, timeoutMs = 60_000): Promise<T> {
  return JSON.parse(await aws([...args, "--output", "json"], s, timeoutMs)) as T;
}

/** STS で認証情報を検証し、アカウント情報を返す */
export async function whoAmI(s: AwsSession): Promise<{ Account: string; Arn: string }> {
  return awsJson<{ Account: string; Arn: string }>(["sts", "get-caller-identity"], s, 20_000);
}

export interface StackInfo {
  status: string;
  outputs: Record<string, string>;
  updatedAt?: string;
}

/** スタックの状態と Output を取得（存在しなければ null） */
export async function describeStack(name: string, s: AwsSession): Promise<StackInfo | null> {
  try {
    const res = await awsJson<{ Stacks: { StackStatus: string; Outputs?: { OutputKey: string; OutputValue: string }[]; LastUpdatedTime?: string }[] }>(
      ["cloudformation", "describe-stacks", "--stack-name", name],
      s,
    );
    const st = res.Stacks[0];
    const outputs: Record<string, string> = {};
    for (const o of st.Outputs ?? []) outputs[o.OutputKey] = o.OutputValue;
    return { status: st.StackStatus, outputs, updatedAt: st.LastUpdatedTime };
  } catch (e) {
    const msg = (e as Error).message;
    if (msg.includes("does not exist")) return null;
    throw e;
  }
}

export interface ExecutionInfo {
  status: string;
  startedAt?: string;
  stoppedAt?: string;
  error?: string;
  cause?: string;
}

/** 直近の Step Functions 実行を取得 */
export async function latestExecution(stateMachineArn: string, s: AwsSession): Promise<ExecutionInfo | null> {
  const res = await awsJson<{ executions: { executionArn: string; status: string; startDate: string; stopDate?: string }[] }>(
    ["stepfunctions", "list-executions", "--state-machine-arn", stateMachineArn, "--max-items", "1"],
    s,
  );
  const ex = res.executions?.[0];
  if (!ex) return null;
  const d = await awsJson<{ status: string; startDate: string; stopDate?: string; error?: string; cause?: string }>(
    ["stepfunctions", "describe-execution", "--execution-arn", ex.executionArn],
    s,
  );
  return { status: d.status, startedAt: d.startDate, stoppedAt: d.stopDate, error: d.error, cause: d.cause };
}
