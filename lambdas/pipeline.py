"""Weekly FULL pipeline steps (Step Functions):

build -> start_imports -> check_imports -> ensure_solution -> start_train -> check_train -> start_batch -> check_batch -> load

No real-time campaign is ever created (it would bill a minimum of 1 TPS around the clock).
"""
import json
import logging
import time

import boto3

import common as c
import full

log = logging.getLogger()
log.setLevel(logging.INFO)

_p = None
SOLUTION_NAME = "karte-up-v2"
RECIPE_ARN = "arn:aws:personalize:::recipe/aws-user-personalization-v2"
NUM_RESULTS = 10


def personalize():
    global _p
    if _p is None:
        _p = boto3.client("personalize")
    return _p


def build(state: dict) -> dict:
    return full.build(state)


def start_imports(state: dict) -> dict:
    jobs = []
    for kind, arn_env, key in (("interactions", "INTERACTIONS_DATASET_ARN", "personalize/interactions/interactions.csv"),
                               ("items", "ITEMS_DATASET_ARN", "personalize/items/items.csv")):
        r = personalize().create_dataset_import_job(
            jobName=f"karte-{kind}-{state['run']}", datasetArn=c.env(arn_env), importMode="FULL",
            dataSource={"dataLocation": f"s3://{c.env('BUCKET')}/{key}"}, roleArn=c.env("PERSONALIZE_ROLE_ARN"))
        jobs.append(r["datasetImportJobArn"])
    return {**state, "import_jobs": jobs}


def _status(statuses: list[str]) -> str:
    if any(s == "CREATE FAILED" for s in statuses):
        return "FAILED"
    return "ACTIVE" if all(s == "ACTIVE" for s in statuses) else "PENDING"


def check_imports(state: dict) -> dict:
    st, fails = [], []
    for arn in state["import_jobs"]:
        d = personalize().describe_dataset_import_job(datasetImportJobArn=arn)["datasetImportJob"]
        st.append(d["status"])
        if d.get("failureReason"):
            fails.append(d["failureReason"])
    return {**state, "status": _status(st), "failure": "; ".join(fails)}


def ensure_solution(state: dict) -> dict:
    """A solution needs an imported interactions dataset, so it cannot be created by CloudFormation."""
    dsg = c.env("DATASET_GROUP_ARN")
    for s in personalize().list_solutions(datasetGroupArn=dsg)["solutions"]:
        if s["name"] == SOLUTION_NAME:
            return {**state, "solution_arn": s["solutionArn"]}
    r = personalize().create_solution(name=SOLUTION_NAME, datasetGroupArn=dsg, recipeArn=RECIPE_ARN)
    return {**state, "solution_arn": r["solutionArn"]}


def start_train(state: dict) -> dict:
    r = personalize().create_solution_version(solutionArn=state["solution_arn"], trainingMode="FULL")
    return {**state, "solution_version_arn": r["solutionVersionArn"]}


def check_train(state: dict) -> dict:
    d = personalize().describe_solution_version(solutionVersionArn=state["solution_version_arn"])["solutionVersion"]
    return {**state, "status": _status([d["status"]]), "failure": d.get("failureReason", "")}


def start_batch(state: dict) -> dict:
    bucket = c.env("BUCKET")
    r = personalize().create_batch_inference_job(
        jobName=f"karte-batch-{state['run']}", solutionVersionArn=state["solution_version_arn"], numResults=NUM_RESULTS,
        jobInput={"s3DataSource": {"path": f"s3://{bucket}/personalize/batch-input/{state['run']}.json", "kmsKeyArn": c.env("KMS_KEY_ARN")}},
        jobOutput={"s3DataDestination": {"path": f"s3://{bucket}/personalize/output/{state['run']}/", "kmsKeyArn": c.env("KMS_KEY_ARN")}},
        roleArn=c.env("PERSONALIZE_ROLE_ARN"))
    return {**state, "batch_job_arn": r["batchInferenceJobArn"]}


def check_batch(state: dict) -> dict:
    d = personalize().describe_batch_inference_job(batchInferenceJobArn=state["batch_job_arn"])["batchInferenceJob"]
    return {**state, "status": _status([d["status"]]), "failure": d.get("failureReason", "")}


def load(state: dict) -> dict:
    bucket, prefix = c.env("BUCKET"), f"personalize/output/{state['run']}/"
    items = full.load_items(bucket)
    now = int(time.time())
    updated = skipped = errors = 0
    table = c.karte_table()
    missing = table.meta.client.exceptions.ConditionalCheckFailedException
    for page in c.s3().get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            if not o["Key"].endswith(".out"):
                continue
            body = c.s3().get_object(Bucket=bucket, Key=o["Key"])["Body"].read().decode("utf-8")
            for line in body.splitlines():
                rec = json.loads(line)
                if rec.get("error"):
                    errors += 1
                    continue
                out = rec["output"]
                reco = []
                for iid, score in zip(out["recommendedItems"], out.get("scores", [])):
                    it = items.get(iid)
                    if it:
                        reco.append({"item_id": iid, "item_name": it["item_name"], "category": it["category"],
                                     "price": it["price"], "score": str(score)})
                try:
                    table.update_item(
                        Key={"member_id": rec["input"]["userId"]}, UpdateExpression="SET reco = :r",
                        ConditionExpression="attribute_exists(member_id)",
                        ExpressionAttributeValues={":r": {"items": reco, "generated_at": now, "model": state["solution_version_arn"]}})
                    updated += 1
                except missing:
                    skipped += 1  # withdrawn/deleted while the pipeline ran
    late = full.apply_api_deletes(bucket, state.get("run_started", now) - 5)
    return {**state, "status": "DONE", "loaded": {"updated": updated, "skipped_missing": skipped, "errors": errors, "late_api_deletes": late}}


ACTIONS = {f.__name__: f for f in (build, start_imports, check_imports, ensure_solution, start_train, check_train, start_batch, check_batch, load)}


def handler(event, _ctx):
    action = event["action"]
    out = ACTIONS[action](event.get("state") or {})
    log.info(json.dumps({"action": action, **{k: v for k, v in out.items() if k in ("status", "counts", "failure", "loaded")}}, default=str))
    return out
