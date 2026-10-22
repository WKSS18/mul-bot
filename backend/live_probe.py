"""One-shot external connectivity probe. Reads secrets only from process environment."""
import asyncio
import sys
from uuid import uuid4

import oss2
import requests

from app.config import settings
from app.providers import ask_deepseek, model_ready, oss_ready, post_policy


async def main():
    if not model_ready() or not oss_ready():
        raise SystemExit("Both DeepSeek and OSS credentials must be set in environment")

    failed = False
    try:
        answer = await ask_deepseek("只回复 OK，不要解释。")
        if not answer.strip():
            raise RuntimeError("model returned no text")
        print("DeepSeek: success; model:", settings.anthropic_model, "; characters:", len(answer))
    except Exception as exc:
        failed = True
        print("DeepSeek: FAILED", type(exc).__name__, str(exc)[:250])

    object_key = f"tongpin/connectivity-probe/{uuid4()}/probe.txt"
    content = b"tongpin-oss-connectivity-probe\n"
    auth = oss2.AuthV4(settings.oss_access_key_id, settings.oss_access_key_secret)
    bucket = oss2.Bucket(auth, settings.oss_endpoint, settings.oss_bucket, region=settings.oss_region)
    uploaded = False
    try:
        signed = post_policy(object_key, 1024)
        response = requests.post(signed["url"], data=signed["fields"], files={"file": ("probe.txt", content, "text/plain")}, timeout=30)
        if response.status_code // 100 != 2:
            failed = True
            print("OSS POST: FAILED", response.status_code, response.text[:250])
        else:
            uploaded = True
            print("OSS POST: success", response.status_code)
            metadata = bucket.head_object(object_key)
            actual = int(metadata.headers.get("Content-Length", -1))
            print("OSS HEAD:", "success" if actual == len(content) else "FAILED", "; bytes:", actual)
            failed |= actual != len(content)
    except Exception as exc:
        failed = True
        print("OSS: FAILED", type(exc).__name__, str(exc)[:250])
    finally:
        if uploaded:
            try:
                bucket.delete_object(object_key)
                print("OSS cleanup: success")
            except Exception as exc:
                failed = True
                print("OSS cleanup: FAILED", type(exc).__name__, str(exc)[:250])
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
