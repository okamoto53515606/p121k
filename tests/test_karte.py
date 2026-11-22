import json

import api
import common
import policy
import pipeline
from conftest import BUCKET, ITEMS, M1, M2, M3, M4, Q_HEADER, all_members, build, events, karte, put
from pii import mask


def call(method, resource, mid, body=None):
    ev = {"httpMethod": method, "resource": resource, "pathParameters": {"memberId": mid},
          "body": json.dumps(body) if body else None, "requestContext": {"identity": {}}}
    r = api.handler(ev, None)
    return r["statusCode"], json.loads(r["body"])


def test_mask_pii():
    t = mask("佐藤太郎です。電話は090-1234-5678、メールはa.b@example.com、〒150-0001")
    assert "090" not in t and "example.com" not in t and "佐藤太郎" not in t and "150-0001" not in t


def test_build_counts_categories_and_dedup(aws):
    put(aws, "inbound/items/i.csv", ITEMS)
    rows = [(M1, "1", "view", "2026-01-01T10:00:00+09:00", ""), (M1, "1", "purchase", "2026-01-01T10:05:00+09:00", ""),
            (M1, "2", "like", "2026-01-01T10:06:00+09:00", "")]
    put(aws, "inbound/events/e1.csv", events(*rows))
    put(aws, "inbound/events/e2_resent.csv", events(*rows))  # overlapping re-send must not double count
    c = build()
    assert c["members"] == 1 and c["duplicate_rows"] == 3 and c["event_rows"] == 3
    k = karte(M1)
    assert k["counts"]["purchase"] == 1 and k["counts"]["view"] == 1
    assert set(k["categories"]) == {"electronics.phone", "apparel.shoes"}
    assert call("GET", "/karte/{memberId}", M1)[1]["contact"]["ok"] is True


def test_build_is_idempotent_and_removes_members_no_longer_in_source(aws):
    put(aws, "inbound/events/a.csv", events((M1, "1", "view", "2026-01-01T10:00:00+09:00", ""), (M2, "1", "view", "2026-01-01T10:00:00+09:00", "")))
    build()
    assert all_members() == {M1, M2}
    aws.delete_object(Bucket=BUCKET, Key="inbound/events/a.csv")
    put(aws, "inbound/events/b.csv", events((M1, "1", "view", "2026-01-01T10:00:00+09:00", "")))
    c = build()
    assert all_members() == {M1} and c["stale_removed"] == 1
    assert build()["members"] == 1


def test_withdraw_removes_member_everywhere_regardless_of_order(aws):
    put(aws, "inbound/items/i.csv", ITEMS)
    # withdraw arrives in an *older* file than later events; the member still disappears completely
    put(aws, "inbound/events/a.csv", events((M2, "", "withdraw", "2026-01-02T10:00:00+09:00", "UNUSED"), (M1, "1", "view", "2026-01-01T10:00:00+09:00", "")))
    put(aws, "inbound/events/b.csv", events((M2, "1", "view", "2026-01-01T10:00:00+09:00", ""), (M2, "2", "purchase", "2026-02-01T10:00:00+09:00", "")))
    c = build()
    assert all_members() == {M1} and c["withdrawn_excluded"] == 1 and c["dropped_withdrawn_rows"] == 3  # incl. the withdraw row itself
    code, body = call("GET", "/karte/{memberId}", M2)
    assert code == 404 and body["contact"] == {"ok": False, "reasons": ["NO_KARTE"]}
    train = aws.get_object(Bucket=BUCKET, Key="personalize/interactions/interactions.csv")["Body"].read().decode()
    assert M2 not in train and M1 in train and train.startswith("USER_ID,ITEM_ID,EVENT_TYPE,TIMESTAMP")
    batch = aws.get_object(Bucket=BUCKET, Key=[o["Key"] for o in aws.list_objects_v2(Bucket=BUCKET, Prefix="personalize/batch-input/")["Contents"]][0])["Body"].read().decode()
    assert json.loads(batch.strip()) == {"userId": M1}
    assert call("POST", "/karte/{memberId}/copy", M2, {})[0] == 404


def test_retention_drops_old_rows(aws):
    put(aws, "inbound/events/a.csv", events((M1, "1", "view", "2020-01-01T10:00:00+09:00", ""), (M1, "1", "view", "2026-01-01T10:00:00+09:00", ""),
                                            (M3, "1", "view", "2020-01-01T10:00:00+09:00", "")))
    c = build()
    assert c["dropped_expired_rows"] == 2 and all_members() == {M1} and karte(M1)["counts"]["view"] == 1


def test_validation_rejects_rows_and_files(aws):
    put(aws, "inbound/events/a.csv", events(("short", "1", "view", "2026-01-01T10:00:00+09:00", ""), (M1, "1", "explode", "2026-01-01T10:00:00+09:00", ""),
                                            (M1, "1", "view", "not-a-date", ""), (M1, "1", "view", "2026-01-01T10:00:00+09:00", "")))
    put(aws, "inbound/events/nohdr.csv", "a,b\n1,2\n")
    c = build()
    assert c["rejected_rows"] == 3 and len(c["skipped_files"]) == 1 and c["members"] == 1
    rep = aws.list_objects_v2(Bucket=BUCKET, Prefix="reports/")["Contents"][0]["Key"]
    assert "FILE_SKIPPED" in aws.get_object(Bucket=BUCKET, Key=rep)["Body"].read().decode()


def test_return_spike_makes_contact_ng_and_blocks_copy_and_reco(aws):
    put(aws, "inbound/items/i.csv", ITEMS)
    put(aws, "inbound/events/a.csv", events((M1, "1", "purchase", "2026-01-01T10:00:00+09:00", ""), (M1, "1", "return", "2026-01-03T10:00:00+09:00", "DEFECT"),
                                            (M1, "2", "return", "2026-01-04T10:00:00+09:00", "SIZE")))
    build()
    assert call("GET", "/karte/{memberId}", M1)[1]["contact"] == {"ok": False, "reasons": ["RETURN_SPIKE"]}
    assert call("POST", "/karte/{memberId}/copy", M1, {})[0] == 409
    assert call("GET", "/karte/{memberId}/reco", M1)[1]["items"] == []


def test_inquiries_are_codes_only(aws):
    put(aws, "inbound/events/a.csv", events((M1, "1", "view", "2026-01-05T09:00:00+09:00", ""), (M2, "1", "view", "2026-01-05T09:00:00+09:00", "")))
    put(aws, "inbound/inquiries/q.csv", Q_HEADER +
        f"Q1,{M1},2026-01-05T10:00:00+09:00,complaint,ignored for non-other\n"
        f"Q2,{M2},2026-01-05T10:00:00+09:00,other,ポイント失効の相談 090-1111-2222\n"
        f"Q3,{M2},2026-01-05T11:00:00+09:00,other,{'長い自由記述' * 10}\n"
        f"Q4,{M2},2026-01-05T11:00:00+09:00,rage,x\n")
    c = build()
    assert c["inquiry_rows"] == 2 and c["rejected_rows"] == 2
    assert "note" not in karte(M1)["inquiries"][0]
    note = karte(M2)["inquiries"][0]["note"]
    assert "090" not in note and len(note) <= 40
    assert call("GET", "/karte/{memberId}", M1)[1]["contact"]["reasons"] == ["RECENT_COMPLAINT"]
    assert call("GET", "/karte/{memberId}", M2)[1]["contact"]["ok"] is True


def test_inquiry_file_with_free_text_column_is_refused(aws):
    put(aws, "inbound/inquiries/q.csv", "inquiry_id,member_id,received_ts,category,body\nQ1," + M1 + ",2026-01-05T10:00:00+09:00,complaint,全文テキスト\n")
    c = build()
    assert c["members"] == 0 and "unexpected columns" in c["skipped_files"][0]["reason"]


def test_api_delete_records_withdraw_and_survives_next_full(aws):
    put(aws, "inbound/events/a.csv", events((M3, "1", "view", "2026-01-01T10:00:00+09:00", ""), (M4, "1", "view", "2026-01-01T10:00:00+09:00", "")))
    build()
    code, body = call("DELETE", "/karte/{memberId}", M3)
    assert code == 200 and body["deleted"] and karte(M3) is None
    assert aws.get_object(Bucket=BUCKET, Key=body["withdraw_recorded"])["Body"].read().decode().count("withdraw") == 1
    assert call("GET", "/karte/{memberId}", M3)[0] == 404
    c = build()  # the source file still contains M3; the recorded withdraw keeps them out
    assert all_members() == {M4} and c["withdrawn_excluded"] == 1
    assert call("DELETE", "/karte/{memberId}", M3)[0] == 200  # idempotent


def test_delete_during_run_is_not_resurrected(aws):
    import full
    put(aws, "inbound/events/a.csv", events((M3, "1", "view", "2026-01-01T10:00:00+09:00", "")))
    st = full.build({})
    call("DELETE", "/karte/{memberId}", M3)
    common.karte_table().put_item(Item={"member_id": M3, "counts": {}})  # simulate a stale write landing afterwards
    assert full.apply_api_deletes(BUCKET, st["run_started"] - 5) == 1
    assert karte(M3) is None


def test_reco_carried_over_and_loaded_with_item_names(aws):
    put(aws, "inbound/items/i.csv", ITEMS)
    put(aws, "inbound/events/a.csv", events((M1, "1", "view", "2026-01-01T10:00:00+09:00", ""), (M2, "2", "view", "2026-01-01T10:00:00+09:00", "")))
    st = pipeline.handler({"action": "build"}, None)
    out = "\n".join(json.dumps({"input": {"userId": m}, "output": {"recommendedItems": ["1", "2", "999"], "scores": [0.9, 0.1, 0.05]}}) for m in (M1, M2, M3))
    aws.put_object(Bucket=BUCKET, Key=f"personalize/output/{st['run']}/x.json.out", Body=out.encode())
    res = pipeline.handler({"action": "load", "state": {**st, "solution_version_arn": "arn:sv"}}, None)
    assert res["loaded"]["updated"] == 2 and res["loaded"]["skipped_missing"] == 1
    _, reco = call("GET", "/karte/{memberId}/reco", M1)
    assert [i["item_id"] for i in reco["items"]] == ["1", "2"] and reco["items"][0]["item_name"] == "A"
    build()  # next FULL keeps the previous reco until the load step replaces it
    assert karte(M1)["reco"]["items"][0]["item_id"] == "1"


def test_status_checks_read_nested_responses(monkeypatch):
    class Fake:
        def describe_solution_version(self, solutionVersionArn):
            return {"solutionVersion": {"status": "ACTIVE"}}

        def describe_batch_inference_job(self, batchInferenceJobArn):
            return {"batchInferenceJob": {"status": "CREATE FAILED", "failureReason": "boom"}}

        def describe_dataset_import_job(self, datasetImportJobArn):
            return {"datasetImportJob": {"status": "CREATE IN_PROGRESS"}}

    monkeypatch.setattr(pipeline, "_p", Fake())
    assert pipeline.check_train({"solution_version_arn": "x"})["status"] == "ACTIVE"
    assert pipeline.check_batch({"batch_job_arn": "x"})["status"] == "FAILED"
    assert pipeline.check_imports({"import_jobs": ["x"]})["status"] == "PENDING"


def test_policy_window_and_missing_karte():
    k = {"last_event_ts": 10_000_000, "returns_ts": [10_000_000 - 40 * 86400, 10_000_000 - 1], "inquiries": []}
    assert policy.evaluate(k)["ok"] is True  # the old return falls outside the 30 day window (last_event reference)
    assert policy.evaluate(None) == {"ok": False, "reasons": ["NO_KARTE"]}


def test_replay_reference_ignores_withdraw_rows(aws):
    put(aws, "inbound/events/a.csv", events((M1, "1", "view", "2020-01-01T10:00:00+09:00", ""), (M2, "1", "view", "2020-01-01T10:00:00+09:00", "")))
    build()
    call("DELETE", "/karte/{memberId}", M2)  # stamps a withdraw row with the current time
    c = build()
    assert all_members() == {M1} and c.get("dropped_expired_rows", 0) == 0
