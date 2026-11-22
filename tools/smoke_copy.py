"""Generate a handful of copies with a chosen model (small smoke test).

    python tools/smoke_copy.py --model jp.anthropic.claude-haiku-4-5-20251001-v1:0 --count 10

Reads the deployed stack outputs, picks contact-OK members and calls the copy
prompt directly (not through API Gateway), so it can run before the API is warm.
"""
import argparse
import json
import os
import sys

import boto3

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "lambdas"))


def outputs() -> dict:
    merged = {}
    paths = [p for p in (os.path.join(ROOT, "data", "outputs.json"), os.path.join(ROOT, "setup", "state-outputs.json")) if os.path.exists(p)]
    for p in sorted(paths, key=os.path.getmtime):
        merged.update(json.load(open(p)).get("KarteStack", {}))
    return merged


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="jp.anthropic.claude-haiku-4-5-20251001-v1:0")
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--direction", default="")
    a = ap.parse_args()

    os.environ.setdefault("AWS_REGION", "ap-northeast-1")
    os.environ["MODEL_ID"] = a.model
    out = outputs()
    table = boto3.resource("dynamodb").Table(out["KarteTable"])

    import llm
    import policy

    picked, kw = [], {}
    while len(picked) < a.count:
        r = table.scan(**kw)
        for it in r["Items"]:
            if policy.evaluate(it)["ok"] and it.get("purchases"):
                picked.append(it)
                if len(picked) >= a.count:
                    break
        if "LastEvaluatedKey" not in r:
            break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]

    print(f"model={a.model} members={len(picked)}")
    for k in picked:
        ctx = {"channel": "mail", "tone": "丁寧で親しみやすい",
               "interest_categories": [c for c, _ in sorted(k.get("categories", {}).items(), key=lambda kv: -int(kv[1]))[:3]],
               "purchase_count": int(k["counts"].get("purchase", 0)),
               "recommended_items": [{"name": i["item_name"], "category": i["category"], "price_jpy": int(i["price"])}
                                     for i in k.get("reco", {}).get("items", [])[:3]]}
        if a.direction:
            ctx["direction"] = a.direction
        copy = llm.generate_copy(ctx)
        print(f"- {k['member_id'][:10]} [{copy['model']}] {copy['subject']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
