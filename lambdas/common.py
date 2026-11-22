import hashlib
import os
import re
import time
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config

# member_id must already be pseudonymised on the customer side (HMAC-SHA256 hex, 32 or 64 chars).
# Raw customer IDs must never reach S3: that is the whole point of the design.
MEMBER_ID_RE = re.compile(r"^[0-9a-f]{32}(?:[0-9a-f]{32})?$")
ITEM_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
JST = timezone(timedelta(hours=9))

_cfg = Config(retries={"max_attempts": 8, "mode": "adaptive"}, max_pool_connections=50)
_ddb = None


def env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        raise KeyError(name)
    return v


def ddb():
    global _ddb
    if _ddb is None:
        _ddb = boto3.resource("dynamodb", config=_cfg)
    return _ddb


def s3():
    return boto3.client("s3", config=_cfg)


def member_log_tag(member_id: str) -> str:
    """Short stable tag for logs. The member ID itself is never logged, so a withdrawn member's
    pseudonym does not linger in CloudWatch Logs after the karte is deleted."""
    return hashlib.sha256(member_id.encode("utf-8")).hexdigest()[:10]


def karte_table():
    return ddb().Table(env("TABLE_KARTE"))


def parse_ts(s: str) -> int:
    """ISO8601 with offset (JST etc.) -> epoch seconds. Raises ValueError."""
    dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timezone offset required")
    return int(dt.timestamp())


def iso_jst(ts: int) -> str:
    return datetime.fromtimestamp(ts, JST).isoformat(timespec="seconds")


def new_karte(member_id: str) -> dict:
    now = int(time.time())
    return {
        "member_id": member_id,
        "updated_at": now,
        "last_event_ts": 0,
        "counts": {"view": 0, "like": 0, "cart": 0, "purchase": 0, "return": 0},
        "last_ts": {},
        "categories": {},
        "recent_events": [],
        "returns_ts": [],
        "purchases": [],   # full history from the TSV (not truncated)
        "returns": [],     # full history with reason and price
        "inquiries": [],   # full history (category codes only)
        "inquiry_count": 0,
        "first_purchase_ts": 0,
        "last_purchase_ts": 0,
        "first_view_ts": 0,
        "last_view_ts": 0,
        "windows": {},     # purchase/view counts per 30/90/180 days, flattened at finish
    }
