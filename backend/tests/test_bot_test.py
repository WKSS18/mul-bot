from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app import worker


@pytest.mark.asyncio
async def test_bot_test_run_private_and_not_in_room(monkeypatch):
    async def fake_model(prompt, system, model, on_delta=None, **kwargs):
        return "测试结果"

    monkeypatch.setattr(worker, "ask_deepseek", fake_model)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            owner = (await client.post('/api/v1/auth/register', json={'email': f'{uuid4()}@example.com', 'display_name': '测试者', 'password': 'testing-password-123'})).json()['data']
            outsider = (await client.post('/api/v1/auth/register', json={'email': f'{uuid4()}@example.com', 'display_name': '其他人', 'password': 'testing-password-123'})).json()['data']
            owner_h = {'Authorization': f"Bearer {owner['access_token']}"}
            outsider_h = {'Authorization': f"Bearer {outsider['access_token']}"}
            bot = (await client.post('/api/v1/bots', headers=owner_h, json={'workspace_id': owner['workspace_id'], 'name': '测试助手', 'prompt': '你是严谨的测试助手。'})).json()['data']
            test = await client.post(f"/api/v1/bots/{bot['id']}/test-runs", headers=owner_h, json={'prompt': '测试一下'})
            assert test.status_code == 202
            test_id = test.json()['data']['id']
            assert (await client.get(f'/api/v1/bot-test-runs/{test_id}', headers=outsider_h)).status_code == 404
            assert await worker.process_bot_test_one()
            result = (await client.get(f'/api/v1/bot-test-runs/{test_id}', headers=owner_h)).json()['data']
            assert result['status'] == 'COMPLETED' and result['output'] == '测试结果'
