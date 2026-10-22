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


TOOL_SCHEMAS = {
    "calculator": {"name": "calculator", "description": "准确计算简单的加减乘除表达式", "input_schema": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}},
    "read_attachment": {"name": "read_attachment", "description": "读取当前任务中授权附件的提取文本", "input_schema": {"type": "object", "properties": {"file_id": {"type": "string"}}, "required": ["file_id"]}},
    "generate_document": {"name": "generate_document", "description": "将内容生成可下载的 Markdown、DOCX 或 PDF 文件", "input_schema": {"type": "object", "properties": {"title": {"type": "string"}, "content": {"type": "string"}, "format": {"type": "string", "enum": ["md", "docx", "pdf"]}}, "required": ["title", "content", "format"]}},
    "handoff_to_bot": {"name": "handoff_to_bot", "description": "将当前任务交接给本任务中下一位已授权 Bot", "input_schema": {"type": "object", "properties": {"bot_id": {"type": "string"}, "summary": {"type": "string"}}, "required": ["bot_id", "summary"]}},
}


async def ask_deepseek(prompt: str, system: str = "你是同频 AI 助手。", model: str | None = None, on_delta=None, tools: list[str] | None = None, on_tool=None) -> str:
    if not model_ready():
        raise RuntimeError("ANTHROPIC_AUTH_TOKEN is not configured")
    async with anthropic.AsyncAnthropic(
        api_key=settings.anthropic_auth_token,
        base_url=settings.anthropic_base_url,
        timeout=settings.api_timeout_ms / 1000,
    ) as client:
        if tools:
            if on_tool is None:
                raise ValueError("Tool callback is required")
            enabled = [TOOL_SCHEMAS[name] for name in tools if name in TOOL_SCHEMAS]
            messages = [{"role": "user", "content": prompt}]
            collected = []
            tool_count = 0
            for _ in range(12):
                async with client.messages.stream(model=model or settings.anthropic_model, max_tokens=1024, system=system, messages=messages, tools=enabled) as stream:
                    async for delta in stream.text_stream:
                        collected.append(delta)
                        if on_delta:
                            await on_delta(delta)
                    response = await stream.get_final_message()
                requests = [block for block in response.content if getattr(block, "type", None) == "tool_use"]
                if not requests:
                    return "".join(collected)
                messages.append({"role": "assistant", "content": [block.model_dump(exclude_none=True) for block in response.content]})
                results = []
                for request in requests:
                    tool_count += 1
                    if tool_count > 8:
                        raise RuntimeError("Tool call budget exceeded")
                    if request.name not in tools or not isinstance(request.input, dict):
                        result, failed = "Tool is not authorized", True
                    else:
                        try:
                            result, failed = str(await on_tool(request.name, request.input, request.id)), False
                        except Exception as exc:
                            result, failed = f"Tool failed: {type(exc).__name__}", True
                    results.append({"type": "tool_result", "tool_use_id": request.id, "content": result[:12000], "is_error": failed})
                messages.append({"role": "user", "content": results})
            raise RuntimeError("Model step budget exceeded")
        if on_delta is None:
            response = await client.messages.create(
                model=model or settings.anthropic_model,
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        chunks = []
        async with client.messages.stream(
            model=model or settings.anthropic_model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            async for delta in stream.text_stream:
                chunks.append(delta)
                await on_delta(delta)
        return "".join(chunks)


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


def signed_file_url(object_key: str, mode: str, filename: str) -> str:
    if not oss_ready():
        raise RuntimeError("OSS credentials are not configured")
    auth = oss2.AuthV4(settings.oss_access_key_id, settings.oss_access_key_secret)
    bucket = oss2.Bucket(auth, settings.oss_endpoint, settings.oss_bucket, region=settings.oss_region)
    # Only a safe ASCII fallback is placed in the response disposition.
    disposition = "inline" if mode == "preview" else "attachment"
    return bucket.sign_url("GET", object_key, 60, params={"response-content-disposition": f"{disposition}; filename=download"})


def read_object(object_key: str) -> bytes:
    if not oss_ready():
        raise RuntimeError("OSS credentials are not configured")
    auth = oss2.AuthV4(settings.oss_access_key_id, settings.oss_access_key_secret)
    bucket = oss2.Bucket(auth, settings.oss_endpoint, settings.oss_bucket, region=settings.oss_region)
    return bucket.get_object(object_key).read()


def read_object_prefix(object_key: str, length: int = 32) -> bytes:
    if not oss_ready():
        raise RuntimeError("OSS credentials are not configured")
    auth = oss2.AuthV4(settings.oss_access_key_id, settings.oss_access_key_secret)
    bucket = oss2.Bucket(auth, settings.oss_endpoint, settings.oss_bucket, region=settings.oss_region)
    return bucket.get_object(object_key, byte_range=(0, length - 1)).read()


def put_object(object_key: str, content: bytes, mime: str) -> None:
    if not oss_ready():
        raise RuntimeError("OSS credentials are not configured")
    auth = oss2.AuthV4(settings.oss_access_key_id, settings.oss_access_key_secret)
    bucket = oss2.Bucket(auth, settings.oss_endpoint, settings.oss_bucket, region=settings.oss_region)
    bucket.put_object(object_key, content, headers={"Content-Type": mime})
