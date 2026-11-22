"""FULL rebuild: S3 source history -> karte (DynamoDB) + Personalize input files.

Source of truth is S3 (inbound/*). Every run re-reads all retained files, so:
  * a member with any `withdraw` event is dropped entirely (karte and training data) – no tombstones;
  * rows older than RETENTION_YEARS are ignored (S3 lifecycle removes the old files too);
  * duplicate rows (same file re-sent, overlapping files) are collapsed.
"""
import csv
import io
import json
import logging
import os
import tempfile
import time
from collections import defaultdict

import common as c
import policy
from pii import mask

log = logging.getLogger()
log.setLevel(logging.INFO)

EVENT_TYPES = {"view", "like", "cart", "purchase", "return", "withdraw"}
TRAIN_TYPES = {"view", "like", "cart", "purchase"}
INQUIRY_CATEGORIES = {"complaint", "question", "request", "other"}
NOTE_MAX = 40

EVENT_COLS = ({"member_id", "item_id", "event_type", "event_ts"}, {"reason"})
ITEM_COLS = ({"item_id", "item_name", "category", "price"}, {"item_description"})
INQUIRY_COLS = ({"inquiry_id", "member_id", "received_ts", "category"}, {"note"})

MAX_RECENT, MAX_CATEGORIES, MAX_RETURNS, MAX_INQUIRIES = 30, 30, 20, 10
MAX_REJECT_ROWS = 20000
API_DELETE_PREFIX = "inbound/events/api-delete_"


class SchemaError(ValueError):
    pass


def _open_rows(bucket: str, key: str, cols: tuple[set, set]):
    """Strict header: required columns present, nothing unexpected (e.g. a free-text `body` column)."""
    body = c.s3().get_object(Bucket=bucket, Key=key)["Body"]
    reader = csv.DictReader(io.TextIOWrapper(body, encoding="utf-8-sig", newline=""))
    names = {n.strip() for n in (reader.fieldnames or [])}
    required, optional = cols
    if required - names:
        raise SchemaError(f"missing columns: {sorted(required - names)}")
    if names - required - optional:
        raise SchemaError(f"unexpected columns: {sorted(names - required - optional)}")
    return reader


def _list_csv(bucket: str, prefix: str) -> list[dict]:
    out = []
    for page in c.s3().get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        out.extend(o for o in page.get("Contents", []) if o["Key"].lower().endswith(".csv"))
    return sorted(out, key=lambda o: o["Key"])


class Report:
    def __init__(self):
        self.skipped_files: list[dict] = []
        self.rejects: list[tuple] = []
        self.reject_total = 0

    def reject(self, key: str, line: int, reason: str):
        self.reject_total += 1
        if len(self.rejects) < MAX_REJECT_ROWS:
            self.rejects.append((key, line, reason))

    def skip(self, key: str, reason: str):
        self.skipped_files.append({"key": key, "reason": reason})

    def write(self, bucket: str, run: str):
        if not (self.rejects or self.skipped_files):
            return
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["file", "line", "reason"])
        w.writerows(self.rejects)
        for s in self.skipped_files:
            w.writerow([s["key"], 0, "FILE_SKIPPED: " + s["reason"]])
        c.s3().put_object(Bucket=bucket, Key=f"reports/{run}/rejects.csv", Body=buf.getvalue().encode("utf-8"))


def load_items(bucket: str, report: Report | None = None) -> dict:
    """The newest items file is a full snapshot."""
    files = _list_csv(bucket, "inbound/items/")
    if not files:
        return {}
    key = max(files, key=lambda o: (o["LastModified"], o["Key"]))["Key"]
    items: dict[str, dict] = {}
    try:
        reader = _open_rows(bucket, key, ITEM_COLS)
    except SchemaError as e:
        if report:
            report.skip(key, str(e))
        return {}
    for n, row in enumerate(reader, start=2):
        try:
            iid = row["item_id"].strip()
            if not c.ITEM_ID_RE.match(iid):
                raise ValueError("bad item_id")
            price = float(str(row["price"]).strip())
            if price < 0:
                raise ValueError("negative price")
            items[iid] = {"item_id": iid, "item_name": (row["item_name"] or "")[:200],
                          "category": (row["category"] or "").strip()[:100] or "unknown", "price": int(round(price))}
        except Exception as e:
            if report:
                report.reject(key, n, f"ITEM: {e}")
    return items


def _pass_withdraw(bucket: str, files: list[dict], report: Report) -> tuple[set[str], int, list[dict]]:
    """Pass 1: who has withdrawn (at any time) and the newest event timestamp."""
    withdrawn: set[str] = set()
    max_ts = 0
    good = []
    for o in files:
        try:
            reader = _open_rows(bucket, o["Key"], EVENT_COLS)
        except SchemaError as e:
            report.skip(o["Key"], str(e))
            continue
        good.append(o)
        for row in reader:
            try:
                mid, typ = row["member_id"].strip(), row["event_type"].strip()
                if not c.MEMBER_ID_RE.match(mid) or typ not in EVENT_TYPES:
                    continue
                ts = c.parse_ts(row["event_ts"])
                if typ == "withdraw":
                    withdrawn.add(mid)
                else:
                    max_ts = max(max_ts, ts)  # withdraw rows (e.g. API deletes stamped "now") must not move the replay reference
            except Exception:
                continue  # reported in pass 2
    return withdrawn, max_ts, good


def _apply(k: dict, typ: str, ts: int, item_id: str, cat: str) -> None:
    k["last_event_ts"] = max(int(k["last_event_ts"]), ts)
    k["counts"][typ] += 1
    k["last_ts"][typ] = max(int(k["last_ts"].get(typ, 0)), ts)
    if typ == "return":
        k["returns_ts"].append(ts)
        if len(k["returns_ts"]) > 4 * MAX_RETURNS:
            k["returns_ts"] = sorted(k["returns_ts"])[-MAX_RETURNS:]
    else:
        if cat:
            k["categories"][cat] = k["categories"].get(cat, 0) + 1
    k["recent_events"].append({"ts": ts, "type": typ, "item_id": item_id})
    if len(k["recent_events"]) > 4 * MAX_RECENT:
        k["recent_events"] = sorted(k["recent_events"], key=lambda e: e["ts"])[-MAX_RECENT:]


def _finish(k: dict) -> None:
    k["recent_events"] = sorted(k["recent_events"], key=lambda e: e["ts"])[-MAX_RECENT:]
    k["returns_ts"] = sorted(k["returns_ts"])[-MAX_RETURNS:]
    if len(k["categories"]) > MAX_CATEGORIES:
        k["categories"] = dict(sorted(k["categories"].items(), key=lambda kv: -kv[1])[:MAX_CATEGORIES])
    k["inquiries"] = sorted(k["inquiries"], key=lambda q: q["ts"])[-MAX_INQUIRIES:]


def _scan_karte(**kw):
    t = c.karte_table()
    while True:
        r = t.scan(**kw)
        yield from r["Items"]
        if "LastEvaluatedKey" not in r:
            return
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def apply_api_deletes(bucket: str, since: float) -> int:
    """Members deleted through the API while a run was in flight must not be resurrected by that run."""
    n = 0
    for o in _list_csv(bucket, API_DELETE_PREFIX):
        if o["LastModified"].timestamp() < since:
            continue
        try:
            for row in _open_rows(bucket, o["Key"], EVENT_COLS):
                if row["event_type"].strip() == "withdraw" and c.MEMBER_ID_RE.match(row["member_id"].strip()):
                    c.karte_table().delete_item(Key={"member_id": row["member_id"].strip()})
                    n += 1
        except SchemaError:
            continue
    return n


def build(state: dict) -> dict:
    started = time.time()
    bucket = c.env("BUCKET")
    run = time.strftime("%Y%m%d%H%M%S")
    years = int(c.env("RETENTION_YEARS", "3"))
    report = Report()

    items = load_items(bucket, report)
    ev_files = _list_csv(bucket, "inbound/events/")
    withdrawn, max_ts, ev_files = _pass_withdraw(bucket, ev_files, report)
    ref = max_ts if c.env("POLICY_REFERENCE", "now") == "last_event" and max_ts else int(started)
    cutoff = ref - years * 365 * 86400

    members: dict[str, dict] = {}
    seen: set[int] = set()
    stats = defaultdict(int)
    tmp = tempfile.NamedTemporaryFile("w", newline="", suffix=".csv", delete=False, encoding="utf-8")
    inter = csv.writer(tmp)
    inter.writerow(["USER_ID", "ITEM_ID", "EVENT_TYPE", "TIMESTAMP"])
    with_inter: set[str] = set()

    for o in ev_files:
        key = o["Key"]
        for n, row in enumerate(_open_rows(bucket, key, EVENT_COLS), start=2):
            try:
                mid, typ = row["member_id"].strip(), row["event_type"].strip()
                if not c.MEMBER_ID_RE.match(mid):
                    raise ValueError("bad member_id")
                if typ not in EVENT_TYPES:
                    raise ValueError("bad event_type")
                ts = c.parse_ts(row["event_ts"])
                item_id = (row.get("item_id") or "").strip()
                if typ != "withdraw" and not c.ITEM_ID_RE.match(item_id):
                    raise ValueError("bad item_id")
            except Exception as e:
                report.reject(key, n, f"EVENT: {e}")
                continue
            if mid in withdrawn:
                stats["dropped_withdrawn_rows"] += 1
                continue
            if ts < cutoff:
                stats["dropped_expired_rows"] += 1
                continue
            h = hash((mid, item_id, typ, ts))
            if h in seen:
                stats["duplicate_rows"] += 1
                continue
            seen.add(h)
            k = members.get(mid) or members.setdefault(mid, c.new_karte(mid))
            _apply(k, typ, ts, item_id, items.get(item_id, {}).get("category", ""))
            if typ in TRAIN_TYPES:
                inter.writerow([mid, item_id, typ, ts])
                with_inter.add(mid)
            stats["event_rows"] += 1
    tmp.close()

    seen_inq: set[str] = set()
    for o in _list_csv(bucket, "inbound/inquiries/"):
        key = o["Key"]
        try:
            reader = _open_rows(bucket, key, INQUIRY_COLS)
        except SchemaError as e:
            report.skip(key, str(e))
            continue
        for n, row in enumerate(reader, start=2):
            try:
                mid, iid, cat = row["member_id"].strip(), row["inquiry_id"].strip(), row["category"].strip()
                if not c.MEMBER_ID_RE.match(mid) or not iid:
                    raise ValueError("bad member_id/inquiry_id")
                if cat not in INQUIRY_CATEGORIES:
                    raise ValueError("bad category")
                ts = c.parse_ts(row["received_ts"])
                note = (row.get("note") or "").strip() if cat == "other" else ""
                if len(note) > NOTE_MAX:
                    raise ValueError(f"note longer than {NOTE_MAX} chars (send codes, not text)")
            except Exception as e:
                report.reject(key, n, f"INQUIRY: {e}")
                continue
            if mid in withdrawn:
                stats["dropped_withdrawn_rows"] += 1
                continue
            if ts < cutoff or iid in seen_inq:
                stats["dropped_expired_rows" if ts < cutoff else "duplicate_rows"] += 1
                continue
            seen_inq.add(iid)
            k = members.get(mid) or members.setdefault(mid, c.new_karte(mid))
            q = {"id": iid, "ts": ts, "category": cat}
            if note:
                q["note"] = mask(note)
            k["inquiries"].append(q)
            k["inquiry_count"] += 1
            k["last_event_ts"] = max(int(k["last_event_ts"]), ts)
            stats["inquiry_rows"] += 1

    for k in members.values():
        _finish(k)

    # Personalize inputs
    s3 = c.s3()
    s3.upload_file(tmp.name, bucket, "personalize/interactions/interactions.csv")
    os.unlink(tmp.name)
    ibuf = io.StringIO()
    iw = csv.writer(ibuf)
    iw.writerow(["ITEM_ID", "CATEGORY", "PRICE"])
    for it in items.values():
        iw.writerow([it["item_id"], it["category"], it["price"]])
    s3.put_object(Bucket=bucket, Key="personalize/items/items.csv", Body=ibuf.getvalue().encode("utf-8"))
    eligible = sorted(m for m in with_inter if policy.evaluate(members[m])["ok"])
    s3.put_object(Bucket=bucket, Key=f"personalize/batch-input/{run}.json",
                  Body=("\n".join(json.dumps({"userId": m}) for m in eligible) + "\n").encode())

    # DynamoDB: replace everything. Reco/copy of members that still exist are carried over until the next load step.
    carry, existing = {}, set()
    for it in _scan_karte(ProjectionExpression="member_id, reco, #c", ExpressionAttributeNames={"#c": "copy"}):
        existing.add(it["member_id"])
        if "reco" in it or "copy" in it:
            carry[it["member_id"]] = {x: it[x] for x in ("reco", "copy") if x in it}
    t = c.karte_table()
    with t.batch_writer(overwrite_by_pkeys=["member_id"]) as bw:
        for mid, k in members.items():
            k.update(carry.get(mid, {}))
            bw.put_item(Item=k)
    stale = existing - set(members)
    with t.batch_writer() as bw:
        for mid in stale:
            bw.delete_item(Key={"member_id": mid})
    late = apply_api_deletes(bucket, started - 5)
    report.write(bucket, run)

    counts = {"members": len(members), "withdrawn_excluded": len(withdrawn), "stale_removed": len(stale),
              "batch_users": len(eligible), "items": len(items), "late_api_deletes": late,
              "rejected_rows": report.reject_total, "skipped_files": report.skipped_files,
              "retention_cutoff": c.iso_jst(cutoff), **stats}
    return {**state, "run": run, "run_started": int(started), "counts": counts}
