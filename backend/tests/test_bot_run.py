from uuid import uuid4
from datetime import timedelta
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.db import FileRecord, MessageAttachment, Run, SessionLocal, utcnow
from app import worker


@pytest.mark.asyncio
async def test_follow_up_bot_message_includes_room_conversation_history(monkeypatch):
    prompts = []

    async def fake_model(prompt, system, model, on_delta=None, **kwargs):
        prompts.append(prompt)
        return "合同约定付款期限为30天。" if len(prompts) == 1 else "继续分析后，付款期限仍为30天。"

    monkeypatch.setattr(worker, "ask_deepseek", fake_model)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            account = (await client.post("/api/v1/auth/register", json={"email": f"{uuid4()}@example.com", "display_name": "连续追问用户", "password": "testing-password-123"})).json()["data"]
            headers = {"Authorization": f"Bearer {account['access_token']}"}
            room = (await client.post(f"/api/v1/workspaces/{account['workspace_id']}/rooms", headers=headers, json={"name": "连续追问"})).json()["data"]
            bot = (await client.post("/api/v1/bots", headers=headers, json={"workspace_id": account["workspace_id"], "name": "合同助手", "prompt": "你是合同分析助手，请结合对话回答。"})).json()["data"]
            await client.post(f"/api/v1/rooms/{room['id']}/bots", headers=headers, json={"bot_id": bot["id"]})
            mention = [{"bot_id": bot["id"], "position": 0}]
            first = await client.post(f"/api/v1/rooms/{room['id']}/messages", headers=headers, json={"client_message_id": str(uuid4()), "text": "合同的付款期限是多少？", "mentions": mention})
            assert first.status_code == 202
            assert await worker.process_one()
            second = await client.post(f"/api/v1/rooms/{room['id']}/messages", headers=headers, json={"client_message_id": str(uuid4()), "text": "继续分析风险", "mentions": []})
            assert second.status_code == 202
            assert await worker.process_one()
            assert "合同的付款期限是多少？" in prompts[1]
            assert "合同约定付款期限为30天。" in prompts[1]
            assert "用户当前消息：\n继续分析风险" in prompts[1]


@pytest.mark.asyncio
async def test_unmentioned_attachment_follow_up_routes_to_room_bot_with_read_permission(monkeypatch):
    prompts = []

    async def fake_model(prompt, system, model, on_delta=None, **kwargs):
        prompts.append(prompt)
        return "已读取并分析。"

    monkeypatch.setattr(worker, "ask_deepseek", fake_model)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            account = (await client.post("/api/v1/auth/register", json={"email": f"{uuid4()}@example.com", "display_name": "附件追问用户", "password": "testing-password-123"})).json()["data"]
            headers = {"Authorization": f"Bearer {account['access_token']}"}
            wid = account["workspace_id"]
            room = (await client.post(f"/api/v1/workspaces/{wid}/rooms", headers=headers, json={"name": "附件连续追问"})).json()["data"]
            general = (await client.post("/api/v1/bots", headers=headers, json={"workspace_id": wid, "name": "通用助手", "prompt": "你是通用助手，负责回答聊天室成员的问题。"})).json()["data"]
            reader = (await client.post("/api/v1/bots", headers=headers, json={"workspace_id": wid, "name": "附件助手", "prompt": "你是附件分析助手，负责阅读文件并回答问题。", "tools": ["read_attachment"]})).json()["data"]
            for bot in (general, reader):
                assert (await client.post(f"/api/v1/rooms/{room['id']}/bots", headers=headers, json={"bot_id": bot["id"]})).status_code == 201
            first = await client.post(f"/api/v1/rooms/{room['id']}/messages", headers=headers, json={"client_message_id": str(uuid4()), "text": "先记住我正在看一份合同。", "mentions": [{"bot_id": general["id"], "position": 0}]})
            assert first.status_code == 202
            assert await worker.process_one()
            async with SessionLocal() as db:
                file = FileRecord(workspace_id=wid, uploader_id=account["user"]["id"], object_key=f"test/{uuid4()}.pdf", filename="合同.pdf", mime="application/pdf", size=12, upload_state="READY", parse_state="READY", extracted_text="付款期限为30天。")
                db.add(file)
                await db.commit()
                file_id = file.id
            second = await client.post(f"/api/v1/rooms/{room['id']}/messages", headers=headers, json={"client_message_id": str(uuid4()), "text": "看下这个", "mentions": [], "attachment_ids": [file_id]})
            assert second.status_code == 202, second.text
            async with SessionLocal() as db:
                run = await db.get(Run, second.json()["data"]["run_id"])
                assert json.loads(run.snapshot)["bots"][0]["bot_id"] == reader["id"]
            assert await worker.process_one()
            assert "付款期限为30天。" in prompts[-1]


@pytest.mark.asyncio
async def test_two_bots_run_in_mention_order(monkeypatch):
    received = []

    async def fake_model(prompt, system, model, on_delta=None, **kwargs):
        received.append((prompt, system, model))
        return f"第{len(received)}个结果"

    monkeypatch.setattr(worker, "ask_deepseek", fake_model)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            account = (await client.post('/api/v1/auth/register', json={'email': f'{uuid4()}@example.com', 'display_name': '协作用户', 'password': 'testing-password-123'})).json()['data']
            headers = {'Authorization': f"Bearer {account['access_token']}"}
            wid = account['workspace_id']
            room = (await client.post(f'/api/v1/workspaces/{wid}/rooms', headers=headers, json={'name': '两Bot协作'})).json()['data']
            bots = []
            for name in ('策划助手', '文案助手'):
                created = await client.post('/api/v1/bots', headers=headers, json={'workspace_id': wid, 'name': name, 'prompt': f'你是{name}，只处理用户的任务。', 'tools': []})
                assert created.status_code == 201, created.text
                bot_id = created.json()['data']['id']
                bots.append(bot_id)
                assert (await client.post(f"/api/v1/rooms/{room['id']}/bots", headers=headers, json={'bot_id': bot_id})).status_code == 201
            sent = await client.post(f"/api/v1/rooms/{room['id']}/messages", headers=headers, json={'client_message_id': str(uuid4()), 'text': '请制定计划并写文案', 'mentions': [{'bot_id': bots[0], 'position': 0}, {'bot_id': bots[1], 'position': 1}]})
            assert sent.status_code == 202, sent.text
            run_id = sent.json()['data']['run_id']
            assert run_id
            assert await worker.process_one()
            run = (await client.get(f'/api/v1/runs/{run_id}', headers=headers)).json()['data']
            assert run['status'] == 'COMPLETED'
            assert len(run['steps']) == 2
            assert '第1个结果' in received[1][0]
            rows = (await client.get(f"/api/v1/rooms/{room['id']}/messages", headers=headers)).json()['data']
            assert [row['sender_type'] for row in rows] == ['USER', 'BOT', 'BOT']
            assert rows[1]['bot_id'] == bots[0]
            assert rows[2]['bot_id'] == bots[1]


@pytest.mark.asyncio
async def test_queued_run_keeps_saved_bot_version(monkeypatch):
    prompts = []

    async def fake_model(prompt, system, model, on_delta=None, **kwargs):
        prompts.append(system)
        return "已完成"

    monkeypatch.setattr(worker, "ask_deepseek", fake_model)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            account = (await client.post('/api/v1/auth/register', json={'email': f'{uuid4()}@example.com', 'display_name': '版本用户', 'password': 'testing-password-123'})).json()['data']
            headers = {'Authorization': f"Bearer {account['access_token']}"}
            room = (await client.post(f"/api/v1/workspaces/{account['workspace_id']}/rooms", headers=headers, json={'name': '版本房间'})).json()['data']
            old_prompt = '你是旧版本助手，使用旧风格回答。'
            bot = (await client.post('/api/v1/bots', headers=headers, json={'workspace_id': account['workspace_id'], 'name': '版本助手', 'prompt': old_prompt})).json()['data']
            assert (await client.post(f"/api/v1/rooms/{room['id']}/bots", headers=headers, json={'bot_id': bot['id']})).status_code == 201
            payload = {'client_message_id': str(uuid4()), 'text': '请回答', 'mentions': [{'bot_id': bot['id'], 'position': 0}]}
            first = await client.post(f"/api/v1/rooms/{room['id']}/messages", headers=headers, json=payload)
            assert first.status_code == 202
            assert (await client.post(f"/api/v1/rooms/{room['id']}/messages", headers=headers, json={**payload, 'mentions': []})).status_code == 409
            new_prompt = '你是新版本助手，使用新风格回答。'
            assert (await client.put(f"/api/v1/bots/{bot['id']}", headers=headers, json={'name': '版本助手', 'prompt': new_prompt, 'revision': 1})).status_code == 200
            assert await worker.process_one()
            second = await client.post(f"/api/v1/rooms/{room['id']}/messages", headers=headers, json={**payload, 'client_message_id': str(uuid4())})
            assert second.status_code == 202
            assert await worker.process_one()
            assert prompts == [old_prompt, new_prompt]


@pytest.mark.asyncio
async def test_model_stream_is_persisted_for_replay(monkeypatch):
    async def fake_model(prompt, system, model, on_delta=None, **kwargs):
        if on_delta:
            await on_delta("第一段")
            await on_delta("第二段")
        return "第一段第二段"

    monkeypatch.setattr(worker, "ask_deepseek", fake_model)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            account = (await client.post('/api/v1/auth/register', json={'email': f'{uuid4()}@example.com', 'display_name': '流式用户', 'password': 'testing-password-123'})).json()['data']
            headers = {'Authorization': f"Bearer {account['access_token']}"}
            room = (await client.post(f"/api/v1/workspaces/{account['workspace_id']}/rooms", headers=headers, json={'name': '流式房间'})).json()['data']
            bot = (await client.post('/api/v1/bots', headers=headers, json={'workspace_id': account['workspace_id'], 'name': '流式助手', 'prompt': '你是一个流式文本助手。'})).json()['data']
            await client.post(f"/api/v1/rooms/{room['id']}/bots", headers=headers, json={'bot_id': bot['id']})
            await client.post(f"/api/v1/rooms/{room['id']}/messages", headers=headers, json={'client_message_id': str(uuid4()), 'text': '请回答', 'mentions': [{'bot_id': bot['id'], 'position': 0}]})
            assert await worker.process_one()
            events = (await client.get(f"/api/v1/rooms/{room['id']}/events", headers=headers)).json()['data']
            assert any(event['type'] == 'message.created' and event['payload']['body'] == '' for event in events)
            assert any(event['type'] == 'message.completed' and event['payload']['body'] == '第一段第二段' for event in events)
            rows = (await client.get(f"/api/v1/rooms/{room['id']}/messages", headers=headers)).json()['data']
            assert rows[-1]['body'] == '第一段第二段'


@pytest.mark.asyncio
async def test_old_pending_run_still_gets_full_processing_window(monkeypatch):
    async def fake_model(prompt, system, model, on_delta=None, **kwargs):
        return '我已收到问题并完成回答。'

    monkeypatch.setattr(worker, 'ask_deepseek', fake_model)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            account = (await client.post('/api/v1/auth/register', json={'email': f'{uuid4()}@example.com', 'display_name': '超时恢复用户', 'password': 'testing-password-123'})).json()['data']
            headers = {'Authorization': f"Bearer {account['access_token']}"}
            room = (await client.post(f"/api/v1/workspaces/{account['workspace_id']}/rooms", headers=headers, json={'name': '超时恢复测试'})).json()['data']
            bot = (await client.post('/api/v1/bots', headers=headers, json={'workspace_id': account['workspace_id'], 'name': '恢复助手', 'prompt': '你是一个会认真回答问题的助手。'})).json()['data']
            await client.post(f"/api/v1/rooms/{room['id']}/bots", headers=headers, json={'bot_id': bot['id']})
            response = await client.post(f"/api/v1/rooms/{room['id']}/messages", headers=headers, json={'client_message_id': str(uuid4()), 'text': '请介绍一下自己', 'mentions': [{'bot_id': bot['id'], 'position': 0}]})
            run_id = response.json()['data']['run_id']
            async with SessionLocal() as db:
                run = await db.get(Run, run_id)
                run.created_at = utcnow() - timedelta(minutes=15)
                await db.commit()

            assert await worker.process_one()
            run = (await client.get(f'/api/v1/runs/{run_id}', headers=headers)).json()['data']
            assert run['status'] == 'COMPLETED'
            messages = (await client.get(f"/api/v1/rooms/{room['id']}/messages", headers=headers)).json()['data']
            assert messages[-1]['body'] == '我已收到问题并完成回答。'
