import base64
import json
from types import SimpleNamespace

import pytest

from app.config import settings
from app import providers
from app.providers import post_policy


def test_oss_policy_binds_key_and_size(monkeypatch):
    monkeypatch.setattr(settings, "oss_access_key_id", "test-id")
    monkeypatch.setattr(settings, "oss_access_key_secret", "test-secret")
    signed = post_policy("tongpin/workspace/uploads/file/source.pdf", 4096)
    policy = json.loads(base64.b64decode(signed["fields"]["policy"]))
    assert {"key": "tongpin/workspace/uploads/file/source.pdf"} in policy["conditions"]
    assert ["content-length-range", 1, 4096] in policy["conditions"]
    assert signed["url"].startswith("https://wh-syyoss.")
    assert signed["fields"]["x-oss-signature"]


@pytest.mark.asyncio
async def test_deepseek_adapter_uses_server_config(monkeypatch):
    calls = {}

    class FakeMessages:
        async def create(self, **kwargs):
            calls.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="OK")])

    class FakeClient:
        def __init__(self, **kwargs):
            calls["client"] = kwargs
            self.messages = FakeMessages()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    monkeypatch.setattr(settings, "anthropic_auth_token", "test-token")
    monkeypatch.setattr(providers.anthropic, "AsyncAnthropic", FakeClient)
    assert await providers.ask_deepseek("只回复 OK") == "OK"
    assert calls["client"]["base_url"] == "https://api.deepseek.com/anthropic"
    assert calls["model"] == settings.anthropic_model
