"""Reference implementation of the *customer side* (the company's own server).

Everything PII-adjacent happens here, before anything reaches AWS:
  * member IDs are pseudonymised with HMAC-SHA256 and a secret salt;
  * inquiry text is turned into a category code (and, only for `other`, a <=40 char
    masked summary). The body itself is never uploaded;
  * withdrawals are written as `withdraw` rows so the next FULL run drops the member.

Usage (demo):
    python tools/ingest_side.py --out data/inbound --salt-file data/hmac.key

The stack never sees a real name, address, phone number, or free-text inquiry.
"""
import argparse
import csv
import hashlib
import hmac
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas"))
from pii import mask  # noqa: E402

NOTE_MAX = 40
CATEGORIES = ("complaint", "question", "request", "other")

_COMPLAINT = ("壊れ", "不良", "違う商品", "届かない", "返金", "二重", "傷", "クレーム", "最悪", "詐欺")
_REQUEST = ("交換", "返品", "変更", "キャンセル", "再送", "登録", "解約")
_QUESTION = ("ですか", "ますか", "教えて", "どこ", "いつ", "いくら", "確認")


def pseudonymize(member_id: str, key: bytes) -> str:
    """Stable, non-reversible member ID (32 hex chars, safe for DynamoDB/Personalize)."""
    return hmac.new(key, member_id.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def classify(text: str) -> tuple[str, str]:
    """Rule-based on purpose: no inquiry text leaves the customer's infrastructure."""
    t = text.strip()
    if any(w in t for w in _COMPLAINT):
        return "complaint", ""
    if any(w in t for w in _REQUEST):
        return "request", ""
    if any(w in t for w in _QUESTION):
        return "question", ""
    note = mask(re.sub(r"\s+", " ", t))[:NOTE_MAX]
    return "other", note


def withdraw_row(member_id: str, event_ts: str, reason: str = "USER_REQUEST") -> list[str]:
    return [member_id, "", "withdraw", event_ts, reason]


def _write(path: str, header: list[str], rows: list[list[str]]) -> None:
    """TSV: item names and inquiry summaries may contain commas."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(header)
        w.writerows(rows)
    print(f"  wrote {path} ({len(rows)} rows)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/inbound", help="output directory")
    ap.add_argument("--salt-file", required=True, help="file holding the HMAC secret (never commit)")
    a = ap.parse_args()
    key = open(a.salt_file, "rb").read().strip()

    # ---- replace this demo block with your own source system queries ----
    members = ["member-0001", "member-0002"]
    members = [pseudonymize(m, key) for m in members]
    now = "2026-11-22T12:00:00+09:00"
    events = [
        [members[0], "item-1", "view", now, ""],
        [members[0], "item-1", "purchase", now, ""],
        [members[1], "item-2", "view", now, ""],
        withdraw_row(members[1], now, "USER_REQUEST"),
    ]
    inquiries = []
    raw_inquiries = [(members[0], "商品が壊れていました。交換してください"), (members[0], "配送はいつですか")]
    for n, (mid, text) in enumerate(raw_inquiries, start=1):
        cat, note = classify(text)
        assert cat in CATEGORIES
        inquiries.append([f"Q{n:07d}", mid, now, cat, note])
    # --------------------------------------------------------------------

    _write(os.path.join(a.out, "events", "events.tsv"), ["member_id", "item_id", "event_type", "event_ts", "reason"], events)
    _write(os.path.join(a.out, "inquiries", "inquiries.tsv"), ["inquiry_id", "member_id", "received_ts", "category", "note"], inquiries)
    print("next: aws s3 cp <dir> s3://<KarteBucketName>/inbound/ --recursive --sse aws:kms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
