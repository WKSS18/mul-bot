"""Server-only DeepSeek and OSS adapters. No credentials are returned to clients."""
import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import anthropic
import oss2

from .config import settings


def model_ready() -> bool:
    return bool(settings.anthropic_auth_token)


async def ask_deepseek(prompt: str, system: str = "你是同频 AI 助手。") -> str:
    if not model_ready():
        raise RuntimeError("ANTHROPIC_AUTH_TOKEN is not configured")
    async with anthropic.AsyncAnthropic(
        api_key=settings.anthropic_auth_token,
        base_url=settings.anthropic_base_url,
        timeout=settings.api_timeout_ms / 1000,
    ) as client:
        response = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
    return "".join(block.text for block in response.content if getattr(block, "type", None) == "text")


def oss_ready() -> bool:
    return bool(settings.oss_access_key_id and settings.oss_access_key_secret)


def post_policy(object_key: str, max_bytes: int) -> dict:
    """OSS Signature V4 POST policy, fixed key and bounded size, valid five minutes."""
    if not oss_ready():
        raise RuntimeError("OSS credentials are not configured")
    now = datetime.now(timezone.utc)
    date = now.strftime("%Y%m%d")
    request_date = now.strftime("%Y%m%dT%H%M%SZ")
    credential = f"{settings.oss_access_key_id}/{date}/{settings.oss_region}/oss/aliyun_v4_request"
    policy = {
        "expiration": (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "conditions": [
            {"bucket": settings.oss_bucket},
            {"key": object_key},
            {"x-oss-signature-version": "OSS4-HMAC-SHA256"},
            {"x-oss-credential": credential},
            {"x-oss-date": request_date},
            ["content-length-range", 1, max_bytes],
        ],
    }
    encoded = base64.b64encode(json.dumps(policy, separators=(",", ":")).encode()).decode()
    secret = settings.oss_access_key_secret
    key = ("aliyun_v4" + secret).encode()
    for part in (date, settings.oss_region, "oss", "aliyun_v4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, encoded.encode(), hashlib.sha256).hexdigest()
    return {
        "url": f"https://{settings.oss_bucket}.oss-{settings.oss_region}.aliyuncs.com/",
        "fields": {
            "key": object_key,
            "policy": encoded,
            "x-oss-signature-version": "OSS4-HMAC-SHA256",
            "x-oss-credential": credential,
            "x-oss-date": request_date,
            "x-oss-signature": signature,
        },
        "expires_at": policy["expiration"],
    }


def head_object(object_key: str):
    if not oss_ready():
        raise RuntimeError("OSS credentials are not configured")
    auth = oss2.AuthV4(settings.oss_access_key_id, settings.oss_access_key_secret)
    bucket = oss2.Bucket(auth, settings.oss_endpoint, settings.oss_bucket, region=settings.oss_region)
    return bucket.head_object(object_key)
