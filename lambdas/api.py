"""IAM-protected API (API Gateway REST, AWS_IAM). Only the company's own servers call this.

A member without a karte (never existed, withdrawn, or deleted) is reported as 404 with contact NG.
"""
import json
import logging
import secrets
import time
import boto3
from boto3.dynamodb.conditions import Key

import common as c
import llm
import policy
from pii import mask

log = logging.getLogger()
log.setLevel(logging.INFO)

RECO_N = 10
SEARCH_LIMIT = 200

# カルテ一覧の検索。GSI は「entity=karte」の固定パーティションキー＋数値のソートキー。
SEARCH_INDEXES = {
    "first_purchase": ("karte-first-purchase", "first_purchase_ts"),
    "last_purchase": ("karte-last-purchase", "last_purchase_ts"),
    "first_view": ("karte-first-view", "first_view_ts"),
    "last_view": ("karte-last-view", "last_view_ts"),
    "purchase_count_90d": ("karte-purchase-count-90d", "purchase_count_90d"),
    "view_count_90d": ("karte-view-count-90d", "view_count_90d"),
}
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
    key = f"inbound/events/api-delete_{now}_{secrets.token_hex(4)}.tsv"
    body = f"member_id\titem_id\tevent_type\tevent_ts\treason\n{member_id}\t\twithdraw\t{c.iso_jst(now)}\tAPI_DELETE\n"
    c.s3().put_object(Bucket=c.env("BUCKET"), Key=key, Body=body.encode("utf-8"))
    c.karte_table().delete_item(Key={"member_id": member_id})
    return {"member_id": member_id, "deleted": True, "withdraw_recorded": key}


def _search(qs: dict) -> dict:
    """DynamoDB のインデックスで絞り込む（全件スキャンしない）。"""
    by = qs.get("by", "last_purchase")
    if by not in SEARCH_INDEXES:
        return {"error": "INVALID_BY", "allowed": sorted(SEARCH_INDEXES)}
    index, attr = SEARCH_INDEXES[by]
    limit = min(int(qs.get("limit", "50")), SEARCH_LIMIT)
    now = int(time.time())
    # one condition per key: members without the event have value 0, so zero is filtered
    # (a bare 0 sort key would otherwise flood the head of every index).
    kwargs = {"IndexName": index, "Limit": limit, "ScanIndexForward": bool(qs.get("asc"))}
    if qs.get("start"):
        kwargs["ExclusiveStartKey"] = {"entity": "karte", attr: int(qs["start"])}
    part = Key("entity").eq("karte")  # fixed GSI partition key
    if by.endswith("_count_90d"):
        kwargs["KeyConditionExpression"] = part & Key(attr).gte(max(int(qs.get("min", "1")), 1))
    elif qs.get("within_days"):
        kwargs["KeyConditionExpression"] = part & Key(attr).between(now - int(qs["within_days"]) * 86400, now)
    elif qs.get("older_than_days"):
        # between(1, cutoff) also excludes members that never had the event (value 0)
        kwargs["KeyConditionExpression"] = part & Key(attr).between(1, now - int(qs["older_than_days"]) * 86400)
    else:
        kwargs["KeyConditionExpression"] = part & Key(attr).gt(0)
    r = c.karte_table().query(**kwargs)
    return {"by": by, "count": len(r["Items"]),
            "members": [{"member_id": i["member_id"], "value": int(i.get(attr, 0))} for i in r["Items"]],
            "next": r.get("LastEvaluatedKey", {}).get(attr)}


def handler(event, _ctx):
    method, path = event["httpMethod"], event["resource"]
    if path == "/karte/search" and method == "GET":
        log.info(json.dumps({"op": "GET /karte/search", "by": (event.get("queryStringParameters") or {}).get("by")}))
        return _resp(200, _search(event.get("queryStringParameters") or {}))
    mid = (event.get("pathParameters") or {}).get("memberId", "")
    if not c.MEMBER_ID_RE.match(mid):
        return _resp(400, {"error": "INVALID_MEMBER_ID"})
    log.info(json.dumps({"op": f"{method} {path}", "member_tag": c.member_log_tag(mid), "caller": event["requestContext"].get("identity", {}).get("userArn")}))

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
        direction = mask(str(body.get("direction", "")))[:200]
        ctx = {
            "channel": body.get("channel", "generic"),
            "tone": body.get("tone", "丁寧で親しみやすい"),
            **({"direction": direction} if direction else {}),
            "interest_categories": [x[0] for x in sorted(k.get("categories", {}).items(), key=lambda kv: -int(kv[1]))[:3]],
            "purchase_count": int(k["counts"].get("purchase", 0)),
            "recommended_items": [{"name": i["item_name"], "category": i["category"], "price_jpy": int(i["price"])} for i in reco[:3]],
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
