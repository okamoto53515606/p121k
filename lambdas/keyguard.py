"""Access-key hygiene: expire the two long-lived keys used for S3 PUT and the API.

IAM access keys never expire by themselves. This job makes the promise real:
keys older than ACCESS_KEY_MAX_AGE_DAYS are deactivated and deleted, and at most
two keys per user are kept (so rotation is possible without downtime).

The console issues the keys (and shows the expiry date); this job enforces it.
"""
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

MAX_KEYS = 2


def handler(_event, _ctx):
    iam = boto3.client("iam")
    users = [u.strip() for u in os.environ.get("KEY_USERS", "").split(",") if u.strip()]
    max_age_days = int(os.environ.get("ACCESS_KEY_MAX_AGE_DAYS", "180"))
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    deleted, kept = [], []
    for user in users:
        try:
            keys = iam.list_access_keys(UserName=user)["AccessKeyMetadata"]
        except iam.exceptions.NoSuchEntityException:
            log.warning("user %s not found", user)
            continue
        # oldest first: anything over the age limit goes, and only MAX_KEYS survive
        for i, k in enumerate(sorted(keys, key=lambda x: x["CreateDate"])):
            too_old = k["CreateDate"] < cutoff
            too_many = i < len(keys) - MAX_KEYS
            if not (too_old or too_many):
                kept.append({"user": user, "id": k["AccessKeyId"], "created": k["CreateDate"].isoformat()})
                continue
            iam.update_access_key(UserName=user, AccessKeyId=k["AccessKeyId"], Status="Inactive")
            iam.delete_access_key(UserName=user, AccessKeyId=k["AccessKeyId"])
            deleted.append({"user": user, "id": k["AccessKeyId"], "created": k["CreateDate"].isoformat(),
                            "reason": "expired" if too_old else "rotated"})
    log.info("deleted=%s kept=%s", deleted, kept)
    return {"deleted": deleted, "kept": kept, "max_age_days": max_age_days}
