import os
import re
import time
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config

MEMBER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
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
        "inquiries": [],
        "inquiry_count": 0,
    }
