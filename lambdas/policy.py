"""Contact policy. Pure rules; no LLM involved. A member without a karte (withdrawn/deleted) is always NG."""
import os
import time
from decimal import Decimal

DAY = 86400


def _cfg(name: str, default: str) -> str:
    return os.environ.get(name, default)


def evaluate(karte: dict | None, now_ts: int | None = None) -> dict:
    """Return {"ok": bool, "reasons": [...]}."""
    if karte is None:
        return {"ok": False, "reasons": ["NO_KARTE"]}

    window = int(_cfg("POLICY_WINDOW_DAYS", "30")) * DAY
    threshold = int(_cfg("RETURN_SPIKE_THRESHOLD", "2"))
    if _cfg("POLICY_REFERENCE", "now") == "last_event":
        ref = int(karte.get("last_event_ts", 0))
    else:
        ref = int(now_ts if now_ts is not None else time.time())

    reasons = []
    if len([t for t in karte.get("returns_ts", []) if int(t) >= ref - window]) >= threshold:
        reasons.append("RETURN_SPIKE")
    if any(q.get("category") == "complaint" and int(q.get("ts", 0)) >= ref - window for q in karte.get("inquiries", [])):
        reasons.append("RECENT_COMPLAINT")
    return {"ok": not reasons, "reasons": reasons}


def to_jsonable(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    if isinstance(o, dict):
        return {k: to_jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [to_jsonable(v) for v in o]
    return o
