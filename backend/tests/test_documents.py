from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.documents import render_document
from app.main import app
from app import worker


def test_document_formats():
    assert render_document("测试标题", "第一段\n第二段", "md").startswith(b"# ")
    assert render_document("测试标题", "正文", "docx").startswith(b"PK")
    assert render_document("测试标题", "中文正文", "pdf").startswith(b"%PDF")


@pytest.mark.asyncio
async def test_generated_file_card_only_after_storage_succeeds(monkeypatch):
    objects = []

    async def fake_model(prompt, system, model, on_delta=None, **kwargs):
        return "这是一份公告正文。"

    def fake_put(key, content, mime):
        objects.append((key, content, mime))

    monkeypatch.setattr(worker, "ask_deepseek", fake_model)
    monkeypatch.setattr(worker, "put_object", fake_put)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            account = (await client.post('/api/v1/auth/register', json={'email': f'{uuid4()}@example.com', 'display_name': '文档用户', 'password': 'testing-password-123'})).json()['data']
            headers = {'Authorization': f"Bearer {account['access_token']}"}
            room = (await client.post(f"/api/v1/workspaces/{account['workspace_id']}/rooms", headers=headers, json={'name': '文档房间'})).json()['data']
            bot = (await client.post('/api/v1/bots', headers=headers, json={'workspace_id': account['workspace_id'], 'name': '文档助手', 'prompt': '你是文档助手，认真完成任务。', 'tools': ['generate_document']})).json()['data']
            assert (await client.post(f"/api/v1/rooms/{room['id']}/bots", headers=headers, json={'bot_id': bot['id']})).status_code == 201
            sent = await client.post(f"/api/v1/rooms/{room['id']}/messages", headers=headers, json={'client_message_id': str(uuid4()), 'text': '生成一份 Markdown 公告文件', 'mentions': [{'bot_id': bot['id'], 'position': 0}]})
            assert sent.status_code == 202
            assert await worker.process_one()
            rows = (await client.get(f"/api/v1/rooms/{room['id']}/messages", headers=headers)).json()['data']
            assert len(objects) == 1
            assert len(rows[-1]['attachments']) == 1
            assert rows[-1]['attachments'][0]['filename'].endswith('.md')


@pytest.mark.asyncio
async def test_storage_failure_does_not_publish_file(monkeypatch):
    async def fake_model(prompt, system, model, on_delta=None, **kwargs):
        return "正文"

    def failed_put(key, content, mime):
        raise OSError("storage unavailable")

    monkeypatch.setattr(worker, "ask_deepseek", fake_model)
    monkeypatch.setattr(worker, "put_object", failed_put)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            account = (await client.post('/api/v1/auth/register', json={'email': f'{uuid4()}@example.com', 'display_name': '失败用户', 'password': 'testing-password-123'})).json()['data']
            headers = {'Authorization': f"Bearer {account['access_token']}"}
            room = (await client.post(f"/api/v1/workspaces/{account['workspace_id']}/rooms", headers=headers, json={'name': '失败房间'})).json()['data']
            bot = (await client.post('/api/v1/bots', headers=headers, json={'workspace_id': account['workspace_id'], 'name': '文档助手', 'prompt': '你是文档助手，认真完成任务。', 'tools': ['generate_document']})).json()['data']
            await client.post(f"/api/v1/rooms/{room['id']}/bots", headers=headers, json={'bot_id': bot['id']})
            sent = await client.post(f"/api/v1/rooms/{room['id']}/messages", headers=headers, json={'client_message_id': str(uuid4()), 'text': '生成一份 PDF 文件', 'mentions': [{'bot_id': bot['id'], 'position': 0}]})
            assert await worker.process_one()
            run = (await client.get(f"/api/v1/runs/{sent.json()['data']['run_id']}", headers=headers)).json()['data']
            assert run['status'] == 'FAILED'
            rows = (await client.get(f"/api/v1/rooms/{room['id']}/messages", headers=headers)).json()['data']
            assert len(rows) == 2 and all(row['attachments'] == [] for row in rows)
