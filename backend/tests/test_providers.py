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
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="OK")])

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
    assert calls["max_tokens"] == settings.model_max_tokens


@pytest.mark.parametrize("reason", ["max_tokens", None, "pause_turn"])
def test_incomplete_response_is_not_success(reason):
    with pytest.raises(providers.ModelResponseIncomplete):
        providers.validate_response(SimpleNamespace(stop_reason=reason))


@pytest.mark.asyncio
@pytest.mark.parametrize("with_tools", [False, True])
@pytest.mark.parametrize("stop_reason", ["end_turn", "max_tokens", None])
async def test_stream_checks_final_state(monkeypatch, with_tools, stop_reason):
    calls, deltas = [], []

    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        @property
        def text_stream(self):
            async def chunks():
                yield "partial reply"
            return chunks()

        async def get_final_message(self):
            return SimpleNamespace(stop_reason=stop_reason, content=[])

    class Client:
        def __init__(self, **kwargs):
            self.messages = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def stream(self, **kwargs):
            calls.append(kwargs)
            return Stream()

    async def delta(text):
        deltas.append(text)

    async def tool(*args):
        pytest.fail("No tool may run for incomplete output")

    monkeypatch.setattr(settings, "anthropic_auth_token", "test-token")
    monkeypatch.setattr(providers.anthropic, "AsyncAnthropic", Client)
    request = providers.ask_deepseek("question", on_delta=delta, tools=["read_attachment"] if with_tools else None, on_tool=tool)
    if stop_reason == "end_turn":
        assert await request == "partial reply"
    else:
        with pytest.raises(providers.ModelResponseIncomplete):
            await request
    assert calls[0]["max_tokens"] == settings.model_max_tokens
    assert deltas == ["partial reply"]
