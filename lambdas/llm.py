"""Bedrock (Claude Haiku via jp.* inference profile) – campaign copy generation only.

Inquiry classification is done on the customer's side; only category codes reach AWS.
"""
import json
import os
import re

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from pii import mask

_client = None
_FALLBACK_ERRORS = ("AccessDeniedException", "ResourceNotFoundException", "ValidationException", "ThrottlingException", "ModelNotReadyException")


def client():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", config=Config(retries={"max_attempts": 6, "mode": "adaptive"}, read_timeout=60))
    return _client


def _models() -> list[str]:
    return [m for m in (os.environ.get("MODEL_ID"), os.environ.get("FALLBACK_MODEL_ID")) if m]


def converse(system: str, user: str, max_tokens: int = 700) -> tuple[str, str]:
    """Try MODEL_ID then FALLBACK_MODEL_ID. Returns (text, model_used)."""
    last = None
    for model in _models():
        extra = {"thinking": {"type": "disabled"}} if "haiku-5-5" in model else None
        for use_extra in ([True, False] if extra else [False]):
            kwargs = dict(
                modelId=model,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": user}]}],
                inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
            )
            if use_extra:
                kwargs["additionalModelRequestFields"] = extra
            try:
                r = client().converse(**kwargs)
                return "".join(b.get("text", "") for b in r["output"]["message"]["content"]), model
            except ClientError as e:
                last = e
                if e.response["Error"]["Code"] not in _FALLBACK_ERRORS:
                    raise
    raise last or RuntimeError("no model configured")


def _json_from(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no json")
    return json.loads(m.group(0))


COPY_SYSTEM = (
    "あなたは日本のEC事業者のCRM担当コピーライターです。メール・手紙・LINEなど媒体を問わず使える汎用の案内文を作ります。"
    "宛名は必ず {{customer_name}} というプレースホルダのまま記載し、実在の氏名・電話・メール・住所は絶対に書かないこと。"
    "価格や在庫の断定、過度な煽りは避け、丁寧で簡潔に。"
    'JSONのみ出力: {"subject":"30字以内","body":"本文(300字以内)"}'
)


def generate_copy(context: dict) -> dict:
    text, model = converse(COPY_SYSTEM, json.dumps(context, ensure_ascii=False))
    d = _json_from(text)
    subject, body = mask(str(d["subject"])), mask(str(d["body"]))
    if "{{customer_name}}" not in body:
        body = "{{customer_name}} 様\n" + body
    return {"subject": subject, "body": body, "model": model}
