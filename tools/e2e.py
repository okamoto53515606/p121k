"""E2E against the deployed stack (FULL-rebuild design).

AWS_PROFILE=<profile> python tools/e2e.py --stage upload|build|verify|reco|all
  upload  clean inbound/, put items/events/inquiries
  build   run the FULL build step (Lambda) synchronously
  verify  compare with ground truth, IAM-only API, copy, API delete -> rebuild
  reco    run the whole Step Functions pipeline (Personalize) and check recommendations
"""
import argparse
import csv
import glob
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "lambdas"))
os.environ.setdefault("POLICY_REFERENCE", "last_event")
import policy  # noqa: E402

REGION = "ap-northeast-1"
OUT = json.load(open(os.path.join(ROOT, "data", "outputs.json")))["KarteStack"]
BUCKET, API = OUT["BucketName"], OUT["ApiUrl"].rstrip("/")
session = boto3.Session(region_name=REGION)
s3 = session.client("s3")
lam = session.client("lambda", config=Config(read_timeout=900, retries={"max_attempts": 0}))
sfn = session.client("stepfunctions")
karte_t = session.resource("dynamodb").Table(OUT["KarteTable"])
DATA = os.path.join(ROOT, "data", "out")
RUN = time.strftime("%Y%m%d%H%M%S")


def api(method: str, path: str, body=None, sign=True):
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    req = AWSRequest(method=method, url=url, data=data, headers={"Content-Type": "application/json"})
    if sign:
        SigV4Auth(session.get_credentials().get_frozen_credentials(), "execute-api", REGION).add_auth(req)
    r = urllib.request.Request(url, data=data, method=method, headers=dict(req.headers.items()))
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:200].decode(errors="replace")}


def scan_all():
    kw = {}
    while True:
        r = karte_t.scan(**kw)
        yield from r["Items"]
        if "LastEvaluatedKey" not in r:
            return
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def stage_upload():
    print("== clean bucket prefixes")
    for pre in ("inbound/", "processed/", "quarantine/", "reports/", "personalize/"):
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=pre):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                s3.delete_objects(Bucket=BUCKET, Delete={"Objects": keys})
    print("== upload source files")
    plan = [("items", os.path.join(DATA, "items.csv"))] + [("events", f) for f in sorted(glob.glob(os.path.join(DATA, "events_*.csv")))] + \
           [("inquiries", os.path.join(DATA, "inquiries.csv"))]
    for kind, f in plan:
        key = f"inbound/{kind}/{os.path.splitext(os.path.basename(f))[0]}_{RUN}.csv"
        s3.upload_file(f, BUCKET, key, ExtraArgs={"ServerSideEncryption": "aws:kms"})
        print("  ", key)


def run_build():
    t0 = time.time()
    r = lam.invoke(FunctionName=OUT["PipelineFunctionName"], Payload=json.dumps({"action": "build"}).encode())
    payload = json.loads(r["Payload"].read())
    if "errorMessage" in payload:
        print("  BUILD ERROR:", payload["errorMessage"][:500])
        return None
    print(f"  build done in {time.time() - t0:.0f}s:", json.dumps(payload["counts"], ensure_ascii=False))
    return payload


def stage_build():
    print("== FULL build")
    return run_build() is not None


def stage_verify():
    ok = True
    print("== anonymous access must be rejected")
    code, body = api("GET", "/karte/" + "a" * 32, sign=False)
    print("  unsigned GET ->", code, body)
    ok &= code in (401, 403)

    print("== compare with ground truth (withdrawn => no karte at all)")
    truth = {r["member_id"]: r for r in csv.DictReader(open(os.path.join(DATA, "truth.csv")))}
    got = {k["member_id"]: k for k in scan_all()}
    bad = []
    n_wd = n_ng = n_ok = 0
    for m, t in truth.items():
        reasons = set(filter(None, t["reasons"].split("|")))
        if "WITHDRAWN" in reasons:
            n_wd += 1
            if m in got:
                bad.append(("withdrawn-present", m))
        elif reasons:
            n_ng += 1
            if m not in got or set(policy.evaluate(got[m])["reasons"]) != reasons:
                bad.append(("ng-mismatch", m, reasons, policy.evaluate(got[m]) if m in got else None))
        else:
            n_ok += 1
            if m not in got or not policy.evaluate(got[m])["ok"]:
                bad.append(("ok-mismatch", m))
    print(f"  members truth={len(truth)} karte={len(got)} | withdrawn={n_wd} ng={n_ng} ok={n_ok} | mismatches={len(bad)} {bad[:3]}")
    ok &= not bad and len(got) == n_ng + n_ok

    print("== rejects report (expect exactly the 2 planted inquiry anomalies)")
    rep = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET, Prefix="reports/").get("Contents", [])]
    rows = list(csv.DictReader(io.StringIO(s3.get_object(Bucket=BUCKET, Key=sorted(rep)[-1])["Body"].read().decode()))) if rep else []
    print("  ", [(r["file"].split("/")[-1][:12], r["reason"][:50]) for r in rows])
    ok &= len(rows) == 2 and all("INQUIRY" in r["reason"] for r in rows)
    pii_leak = [k["member_id"] for k in got.values() if "090-" in json.dumps(k, default=str, ensure_ascii=False)]
    print("  karte rows containing the planted phone number:", len(pii_leak))
    ok &= not pii_leak

    print("== API (SigV4)")
    sample_ok = next(m for m, t in truth.items() if t["expected_contact"] == "ok" and m in got)
    sample_wd = next(m for m, t in truth.items() if "WITHDRAWN" in t["reasons"])
    sample_ng = next(m for m, t in truth.items() if "RETURN_SPIKE" in t["reasons"] and "WITHDRAWN" not in t["reasons"])
    c1, b1 = api("GET", f"/karte/{sample_ok}")
    c2, b2 = api("GET", f"/karte/{sample_wd}")
    c3, b3 = api("POST", f"/karte/{sample_ng}/copy", {})
    print("  GET ok-member ->", c1, b1.get("contact"), b1.get("counts"))
    print("  GET withdrawn ->", c2, b2)
    print("  POST copy NG  ->", c3, b3)
    ok &= c1 == 200 and c2 == 404 and b2["contact"]["reasons"] == ["NO_KARTE"] and c3 == 409

    print("== copy via Bedrock (jp.* profile)")
    t0 = time.time()
    c4, b4 = api("POST", f"/karte/{sample_ok}/copy", {"channel": "mail"})
    print(f"  -> {c4} in {time.time() - t0:.1f}s", json.dumps(b4, ensure_ascii=False)[:420])
    ok &= c4 == 200 and "{{customer_name}}" in b4["copy"]["body"]

    print("== DELETE API then FULL rebuild keeps the member out")
    n_before = len(got)
    c5, b5 = api("DELETE", f"/karte/{sample_ok}")
    c6, _ = api("GET", f"/karte/{sample_ok}")
    print("  delete ->", c5, b5, "| get after ->", c6)
    st = run_build()
    n_after = sum(1 for _ in scan_all())
    gone = karte_t.get_item(Key={"member_id": sample_ok}).get("Item") is None
    print(f"  members before={n_before} after_rebuild={n_after} deleted_member_absent={gone}")
    ok &= c5 == 200 and c6 == 404 and st is not None and gone and n_after == n_before - 1
    return ok


def stage_reco():
    print("== full pipeline (Step Functions: build -> Personalize import/train/batch -> load)")
    ex = sfn.start_execution(stateMachineArn=OUT["PipelineArn"], input="{}")["executionArn"]
    t0 = time.time()
    while True:
        d = sfn.describe_execution(executionArn=ex)
        if d["status"] != "RUNNING":
            break
        time.sleep(30)
    print(f"  {d['status']} after {(time.time() - t0) / 60:.1f} min")
    if d["status"] != "SUCCEEDED":
        print("  ", d.get("cause") or d.get("error"))
        return False
    out = json.loads(d["output"])
    print("  loaded:", out["loaded"])
    with_reco = [k for k in scan_all() if k.get("reco", {}).get("items")]
    print("  members with reco:", len(with_reco))
    if with_reco:
        m = with_reco[0]["member_id"]
        code, body = api("GET", f"/karte/{m}/reco")
        print("  sample reco ->", code, json.dumps(body, ensure_ascii=False)[:300])
        return code == 200 and len(with_reco) > 0
    return False


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all", choices=["upload", "build", "verify", "reco", "all"])
    a = ap.parse_args()
    ok = True
    if a.stage in ("upload", "all"):
        stage_upload()
    if a.stage in ("build", "all"):
        ok &= stage_build()
    if a.stage in ("verify", "all"):
        ok &= stage_verify()
    if a.stage in ("reco", "all"):
        ok &= stage_reco()
    print("\nRESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
