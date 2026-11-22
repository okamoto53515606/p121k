"""Poll until Claude Haiku 5.5 (JP inference profile) becomes invokable.

Bedrock returns two different messages while a newly agreed Marketplace model is
still being provisioned for the account:
  - "Your account is currently being verified. Verification normally takes less than 2 hours."
  - "anthropic.claude-haiku-5-5 is not available for this account." (generic, most regions)

Usage: python tools/probe_haiku55.py [--interval 60] [--max-attempts 180]
Exit code 0 as soon as one region answers, 1 if the budget runs out.
"""
import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

import boto3

JST = timezone(timedelta(hours=9))
TARGETS = [("ap-northeast-1", "jp.anthropic.claude-haiku-5-5"),
           ("ap-northeast-3", "jp.anthropic.claude-haiku-5-5")]


def probe(region: str, model_id: str) -> tuple[bool, str]:
    try:
        client = boto3.client("bedrock-runtime", region_name=region)
        client.converse(modelId=model_id, messages=[{"role": "user", "content": [{"text": "hi"}]}],
                        inferenceConfig={"maxTokens": 16})
        return True, "OK"
    except Exception as e:  # noqa: BLE001 - any error means "not ready yet"
        return False, str(e).split("operation:")[-1].strip()[:120]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=60, help="seconds between rounds")
    ap.add_argument("--max-attempts", type=int, default=180)
    a = ap.parse_args()
    for i in range(a.max_attempts):
        stamp = datetime.now(JST).strftime("%F %T")
        for region, model_id in TARGETS:
            ok, msg = probe(region, model_id)
            print(f"{stamp} {region} {'OK' if ok else 'NG'} {msg}", flush=True)
            if ok:
                print(f"{stamp} RESULT invokable via {region} {model_id}", flush=True)
                return 0
        if i + 1 < a.max_attempts:
            time.sleep(a.interval)
    print("RESULT timeout: still not invokable", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
