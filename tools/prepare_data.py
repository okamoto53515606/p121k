"""Build inbound CSVs (items / events / inquiries) from the REES46 open dataset + synthetic data.

REES46 eCommerce behavior data (Kaggle): free to use with attribution to
https://www.kaggle.com/datasets/mkechinov/ecommerce-behavior-data-from-multi-category-store and https://rees46.com

Synthetic parts: item names/descriptions, like, return, withdraw, inquiries (category codes only).
Member IDs are HMAC-SHA256 pseudonyms; the key never leaves this machine.
`truth.csv` (expected contact policy) is for verification only and must NOT be uploaded.
"""
import argparse
import csv
import hashlib
import hmac
import os
import random
import secrets
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
USD_JPY = 150
EVENT_FILE_ROWS = 100_000

LAST = ["佐藤", "鈴木", "高橋", "田中", "伊藤", "渡辺", "山本", "中村", "小林", "加藤"]
FIRST = ["太郎", "花子", "健一", "美咲", "翔太", "陽子", "大輔", "由美", "拓也", "恵"]
RETURN_REASONS = ["DEFECT", "NOT_AS_DESCRIBED", "SIZE", "CHANGED_MIND"]
WITHDRAW_REASONS = ["UNUSED", "TOO_MANY_MAILS", "PRIVACY", "MOVED", "OTHER"]

OTHER_NOTES = ["ポイント失効についての相談", "配送業者の変更希望", "会員情報の確認依頼", "クーポン利用条件の確認"]


def pseudonym(key: bytes, raw_id: str) -> str:
    return hmac.new(key, raw_id.encode(), hashlib.sha256).hexdigest()[:32]


def jst_iso(dt: datetime) -> str:
    return dt.astimezone(JST).isoformat(timespec="seconds")


def parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S %Z").replace(tzinfo=timezone.utc)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="data/raw/rees46_2019-Oct_head4M.csv")
    ap.add_argument("--out", default="data/out")
    ap.add_argument("--members", type=int, default=15000)
    ap.add_argument("--min-events", type=int, default=5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--key-file", default="data/hmac.key")
    ap.add_argument("--shift-to-now", dest="shift", action="store_true", default=True,
                    help="最新イベントが「--lag-days 日前」になるよう全時刻を平行移動（既定）")
    ap.add_argument("--keep-dates", dest="shift", action="store_false",
                    help="元データの日付（REES46 なら 2019 年）をそのまま使う")
    ap.add_argument("--lag-days", type=int, default=1,
                    help="--shift-to-now のとき、最新イベントを何日前に置くか（既定 1）")
    a = ap.parse_args()

    rng = random.Random(a.seed)
    os.makedirs(a.out, exist_ok=True)
    if not os.path.exists(a.key_file):
        with open(a.key_file, "wb") as f:
            f.write(secrets.token_bytes(32))
        os.chmod(a.key_file, 0o600)
    key = open(a.key_file, "rb").read()

    # pass 1: choose members
    counts: Counter = Counter()
    with open(a.src, newline="") as f:
        for row in csv.DictReader(f):
            counts[row["user_id"]] += 1
    cands = sorted(u for u, c in counts.items() if c >= a.min_events)
    rng.shuffle(cands)
    chosen = set(cands[: a.members])

    # pass 2: collect events + items
    events = []  # (dt, member, item, type, reason)
    items = {}
    with open(a.src, newline="") as f:
        for row in csv.DictReader(f):
            if row["user_id"] not in chosen or row["event_type"] not in ("view", "cart", "purchase"):
                continue
            pid = row["product_id"]
            m = pseudonym(key, row["user_id"])
            events.append([parse_ts(row["event_time"]), m, pid, row["event_type"], ""])
            if pid not in items:
                cat = row["category_code"] or "unknown"
                brand = row["brand"] or "noname"
                leaf = cat.split(".")[-1]
                price = float(row["price"] or 0)
                items[pid] = {
                    "item_id": pid,
                    "item_name": f"{brand} {leaf} #{pid}",
                    "item_description": f"{brand}の{leaf}。カテゴリ: {cat.replace('.', ' > ')}。",
                    "category": cat,
                    "price": max(1, round(price * USD_JPY)),
                }

    by_member = defaultdict(list)
    for e in events:
        by_member[e[1]].append(e)
    members = sorted(by_member)

    purchasers = [m for m in members if any(e[3] == "purchase" for e in by_member[m])]
    problem = set(rng.sample(purchasers, max(1, int(len(purchasers) * 0.04))))  # multi-return members
    complainers = set(rng.sample(purchasers, max(1, int(len(purchasers) * 0.04))))
    withdrawers = set(rng.sample(members, max(1, int(len(members) * 0.03))))
    # make sure some withdrawers also complained/returned
    withdrawers |= set(rng.sample(sorted(complainers), max(1, len(complainers) // 5)))
    questioners = set(rng.sample(members, max(1, int(len(members) * 0.05))))

    synthetic = []
    inquiries = []
    returns_n = Counter()
    inq_id = 0

    for m in members:
        evs = sorted(by_member[m], key=lambda x: x[0])
        # like: ~3% of views
        for e in [x for x in evs if x[3] == "view"]:
            if rng.random() < 0.03:
                synthetic.append([e[0] + timedelta(seconds=rng.randint(5, 120)), m, e[2], "like", ""])
        # returns
        buys = [x for x in evs if x[3] == "purchase"]
        for b in buys:
            p = 0.9 if m in problem else 0.05
            if rng.random() < p:
                synthetic.append([b[0] + timedelta(days=rng.randint(1, 8), hours=rng.randint(0, 20)), m, b[2], "return", rng.choice(RETURN_REASONS)])
                returns_n[m] += 1
        if m in problem and returns_n[m] < 2 and buys:
            b = buys[0]
            synthetic.append([b[0] + timedelta(days=2), m, b[2], "return", "DEFECT"])
            synthetic.append([b[0] + timedelta(days=3), m, b[2], "return", "NOT_AS_DESCRIBED"])
            returns_n[m] += 2
        last_ts = max([evs[-1][0]] + [s[0] for s in synthetic[-6:] if s[1] == m])
        # inquiries (category codes only; the real text never leaves the customer's side)
        if m in complainers:
            inq_id += 1
            inquiries.append([f"Q{inq_id:07d}", m, last_ts - timedelta(hours=rng.randint(1, 20)), "complaint", ""])
        if m in questioners and m not in complainers:
            inq_id += 1
            cat = rng.choices(["question", "request", "other"], [6, 3, 1])[0]
            inquiries.append([f"Q{inq_id:07d}", m, last_ts - timedelta(hours=rng.randint(1, 20)), cat, rng.choice(OTHER_NOTES) if cat == "other" else ""])
        # withdraw
        if m in withdrawers:
            wts = last_ts + timedelta(hours=rng.randint(1, 48))
            synthetic.append([wts, m, "", "withdraw", rng.choice(WITHDRAW_REASONS)])
            if rng.random() < 0.15:  # event after withdraw: must be rejected by ingest
                synthetic.append([wts + timedelta(hours=3), m, evs[0][2], "view", ""])
                returns_n[("late", m)] += 1

    # deliberate anomalies to exercise rejection: free text over 40 chars, unknown category
    members_l = sorted(members)
    for n, (cat, note) in enumerate([("other", "これは四十文字を超える自由記述の問い合わせ本文を誤って送った想定のテキストです。電話は090-1234-5678"), ("rage", "")]):
        inq_id += 1
        inquiries.append([f"Q{inq_id:07d}", members_l[n], datetime(2019, 10, 4, tzinfo=timezone.utc), cat, note])

    all_events = sorted(events + synthetic, key=lambda x: x[0])

    if a.shift:
        # why: 画面からデプロイすると policyReference は常に now。2019 年のデモデータを
        #   そのまま流すと全行が保持期間（既定 3 年）の外になり、カルテが空になる。
        #   最新イベントが「lag_days 日前」になるよう全時刻を平行移動し、直近の
        #   30/90/180 日集計が自然に効くようにする。
        latest = max(e[0] for e in all_events)
        target = datetime.now(timezone.utc) - timedelta(days=a.lag_days)
        shift = target - latest
        for e in all_events:
            e[0] = e[0] + shift
        for q in inquiries:
            q[2] = q[2] + shift
        print(f"shifted {shift.days} days: latest event -> {jst_iso(target)}")

    # expected contact policy (verification only)
    ret_count = Counter(e[1] for e in all_events if e[3] == "return")
    wd = {e[1] for e in all_events if e[3] == "withdraw"}
    comp = {q[1] for q in inquiries if q[3] == "complaint"}
    # why lineterminator="\n": csv の既定は CRLF。TSV に \r が混ざると
    #   awk/jq などで扱いにくく、git の改行設定でも揺れるため LF に固定する。
    with open(os.path.join(a.out, "truth.csv"), "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["member_id", "expected_contact", "reasons"])
        for m in members:
            reasons = []
            if m in wd:
                reasons.append("WITHDRAWN")
            if ret_count[m] >= 2:
                reasons.append("RETURN_SPIKE")
            if m in comp:
                reasons.append("RECENT_COMPLAINT")
            w.writerow([m, "ng" if reasons else "ok", "|".join(reasons)])

    with open(os.path.join(a.out, "items.tsv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["item_id", "item_name", "item_description", "category", "price"],
                           delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(items.values())

    for i in range(0, len(all_events), EVENT_FILE_ROWS):
        with open(os.path.join(a.out, f"events_{i // EVENT_FILE_ROWS:03d}.tsv"), "w", newline="") as f:
            w = csv.writer(f, delimiter="\t", lineterminator="\n")
            w.writerow(["member_id", "item_id", "event_type", "event_ts", "reason"])
            for e in all_events[i:i + EVENT_FILE_ROWS]:
                w.writerow([e[1], e[2], e[3], jst_iso(e[0]), e[4]])

    # deliberate anomalies are already appended above (they must be shifted too)
    with open(os.path.join(a.out, "inquiries.tsv"), "w", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["inquiry_id", "member_id", "received_ts", "category", "note"])
        for q in inquiries:
            w.writerow([q[0], q[1], jst_iso(q[2]), q[3], q[4]])

    print(f"members={len(members)} items={len(items)} events={len(all_events)} inquiries={len(inquiries)}")
    print("event types:", dict(Counter(e[3] for e in all_events)))
    print("expected NG:", sum(1 for m in members if m in wd or ret_count[m] >= 2 or m in comp))


if __name__ == "__main__":
    main()
