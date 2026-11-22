"""IAM-protected API (API Gateway REST, AWS_IAM). Only the company's own servers call this.

A member without a karte (never existed, withdrawn, or deleted) is reported as 404 with contact NG.
"""
import json
import logging
import secrets
import time

import common as c
import llm
import policy

log = logging.getLogger()
log.setLevel(logging.INFO)

RECO_N = 10
NO_KARTE = {"ok": False, "reasons": ["NO_KARTE"]}


def _resp(code: int, body: dict):
    return {"statusCode": code, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(policy.to_jsonable(body), ensure_ascii=False)}


def _view(k: dict) -> dict:
    out = {key: k.get(key) for key in (
        "member_id", "updated_at", "last_event_ts", "counts", "last_ts", "recent_events", "inquiry_count", "inquiries", "reco", "copy")}
    out["top_categories"] = [x[0] for x in sorted(k.get("categories", {}).items(), key=lambda kv: -int(kv[1]))[:5]]
    out["contact"] = policy.evaluate(k)
    return out


def _delete(member_id: str) -> dict:
    """Record a withdraw event in S3 first (so every later FULL run excludes the member), then drop the karte."""
    now = int(time.time())
    key = f"inbound/events/api-delete_{now}_{secrets.token_hex(4)}.csv"
    body = f"member_id,item_id,event_type,event_ts,reason\n{member_id},,withdraw,{c.iso_jst(now)},API_DELETE\n"
    c.s3().put_object(Bucket=c.env("BUCKET"), Key=key, Body=body.encode("utf-8"))
    c.karte_table().delete_item(Key={"member_id": member_id})
    return {"member_id": member_id, "deleted": True, "withdraw_recorded": key}


def handler(event, _ctx):
    method, path = event["httpMethod"], event["resource"]
    mid = (event.get("pathParameters") or {}).get("memberId", "")
    if not c.MEMBER_ID_RE.match(mid):
        return _resp(400, {"error": "INVALID_MEMBER_ID"})
    log.info(json.dumps({"op": f"{method} {path}", "member": mid, "caller": event["requestContext"].get("identity", {}).get("userArn")}))

    if method == "DELETE":
        return _resp(200, _delete(mid))

    k = c.karte_table().get_item(Key={"member_id": mid}).get("Item")
    if not k:
        return _resp(404, {"error": "NOT_FOUND", "contact": NO_KARTE})

    if path == "/karte/{memberId}" and method == "GET":
        return _resp(200, _view(k))

    contact = policy.evaluate(k)
    reco = (k.get("reco") or {}).get("items", [])[:RECO_N]
    if path == "/karte/{memberId}/reco" and method == "GET":
        return _resp(200, {"member_id": mid, "contact": contact, "items": reco if contact["ok"] else []})

    if path == "/karte/{memberId}/copy" and method == "POST":
        if not contact["ok"]:
            return _resp(409, {"error": "CONTACT_NG", "contact": contact})
        try:
            body = json.loads(event.get("body") or "{}")
        except ValueError:
            return _resp(400, {"error": "INVALID_JSON"})
        ctx = {
            "channel": body.get("channel", "generic"),
            "tone": body.get("tone", "丁寧で親しみやすい"),
            "interest_categories": [x[0] for x in sorted(k.get("categories", {}).items(), key=lambda kv: -int(kv[1]))[:3]],
            "purchase_count": int(k["counts"].get("purchase", 0)),
            "recommended_items": [{"name": i["item_name"], "category": i["category"], "price_jpy": i["price"]} for i in reco[:3]],
        }
        copy = llm.generate_copy(ctx)
        copy["generated_at"] = int(time.time())
        try:
            c.karte_table().update_item(Key={"member_id": mid}, UpdateExpression="SET #c = :c", ConditionExpression="attribute_exists(member_id)",
                                        ExpressionAttributeNames={"#c": "copy"}, ExpressionAttributeValues={":c": copy})
        except c.karte_table().meta.client.exceptions.ConditionalCheckFailedException:
            return _resp(404, {"error": "NOT_FOUND", "contact": NO_KARTE})
        return _resp(200, {"member_id": mid, "copy": copy})

    return _resp(404, {"error": "NOT_FOUND"})
