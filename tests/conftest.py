import os
import sys

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas"))

BUCKET = "karte-test"
M1, M2, M3, M4 = "a" * 32, "b" * 32, "c" * 32, "d" * 32
ITEMS = "item_id,item_name,item_description,category,price\n1,A,desc,electronics.phone,1000\n2,B,desc,apparel.shoes,2000\n"
EV_HEADER = "member_id,item_id,event_type,event_ts,reason\n"
Q_HEADER = "inquiry_id,member_id,received_ts,category,note\n"


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    monkeypatch.setenv("TABLE_KARTE", "karte")
    monkeypatch.setenv("BUCKET", BUCKET)
    monkeypatch.setenv("POLICY_REFERENCE", "last_event")
    monkeypatch.setenv("RETENTION_YEARS", "3")
    monkeypatch.delenv("MODEL_ID", raising=False)
    monkeypatch.delenv("FALLBACK_MODEL_ID", raising=False)
    with mock_aws():
        import common
        common._ddb = None
        boto3.client("dynamodb").create_table(
            TableName="karte", BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": "member_id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "member_id", "KeyType": "HASH"}])
        s3 = boto3.client("s3", region_name="ap-northeast-1")
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "ap-northeast-1"})
        yield s3


def put(s3, key: str, text: str):
    s3.put_object(Bucket=BUCKET, Key=key, Body=text.encode("utf-8"))


def events(*rows):
    return EV_HEADER + "".join(",".join(r) + "\n" for r in rows)


def build():
    import full
    return full.build({})["counts"]


def karte(mid):
    import common
    return common.karte_table().get_item(Key={"member_id": mid}).get("Item")


def all_members():
    import common
    return {i["member_id"] for i in common.karte_table().scan()["Items"]}
