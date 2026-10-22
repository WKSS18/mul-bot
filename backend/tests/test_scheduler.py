from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.db import Run, SessionLocal, utcnow
from app import worker


@pytest.mark.asyncio
async def test_busy_room_does_not_block_other_room(monkeypatch):
    async def fake_model(prompt, system, model, on_delta=None, **kwargs):
        return "完成"

    monkeypatch.setattr(worker, "ask_deepseek", fake_model)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            account = (await client.post('/api/v1/auth/register', json={'email': f'{uuid4()}@example.com', 'display_name': '排队用户', 'password': 'testing-password-123'})).json()['data']
            headers = {'Authorization': f"Bearer {account['access_token']}"}
            bot = (await client.post('/api/v1/bots', headers=headers, json={'workspace_id': account['workspace_id'], 'name': '排队助手', 'prompt': '你是严格按顺序处理任务的测试助手。'})).json()['data']
            rooms = []
            for name in ('忙碌房间', '空闲房间'):
                room = (await client.post(f"/api/v1/workspaces/{account['workspace_id']}/rooms", headers=headers, json={'name': name})).json()['data']
                await client.post(f"/api/v1/rooms/{room['id']}/bots", headers=headers, json={'bot_id': bot['id']})
                rooms.append(room)

            async def send(room):
                response = await client.post(f"/api/v1/rooms/{room['id']}/messages", headers=headers, json={'client_message_id': str(uuid4()), 'text': '请回答', 'mentions': [{'bot_id': bot['id'], 'position': 0}]})
                return response.json()['data']['run_id']

            first, second, third = await send(rooms[0]), await send(rooms[0]), await send(rooms[1])
            async with SessionLocal() as db:
                run = await db.get(Run, first)
                run.status = 'RUNNING'
                run.lease_until = utcnow() + timedelta(minutes=15)
                await db.commit()
            assert await worker.process_one()
            assert (await client.get(f'/api/v1/runs/{second}', headers=headers)).json()['data']['status'] == 'PENDING'
            assert (await client.get(f'/api/v1/runs/{third}', headers=headers)).json()['data']['status'] == 'COMPLETED'


@pytest.mark.asyncio
async def test_expired_worker_lease_is_recovered(monkeypatch):
    async def fake_model(prompt, system, model, on_delta=None, **kwargs):
        return "恢复完成"

    monkeypatch.setattr(worker, "ask_deepseek", fake_model)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            account = (await client.post('/api/v1/auth/register', json={'email': f'{uuid4()}@example.com', 'display_name': '恢复用户', 'password': 'testing-password-123'})).json()['data']
            headers = {'Authorization': f"Bearer {account['access_token']}"}
            bot = (await client.post('/api/v1/bots', headers=headers, json={'workspace_id': account['workspace_id'], 'name': '恢复助手', 'prompt': '你是能够处理任务恢复的测试助手。'})).json()['data']
            room = (await client.post(f"/api/v1/workspaces/{account['workspace_id']}/rooms", headers=headers, json={'name': '恢复房间'})).json()['data']
            await client.post(f"/api/v1/rooms/{room['id']}/bots", headers=headers, json={'bot_id': bot['id']})
            sent = await client.post(f"/api/v1/rooms/{room['id']}/messages", headers=headers, json={'client_message_id': str(uuid4()), 'text': '请回答', 'mentions': [{'bot_id': bot['id'], 'position': 0}]})
            run_id = sent.json()['data']['run_id']
            async with SessionLocal() as db:
                run = await db.get(Run, run_id)
                run.status = 'RUNNING'
                run.lease_until = utcnow() - timedelta(seconds=1)
                await db.commit()
            assert await worker.process_one()
            assert (await client.get(f'/api/v1/runs/{run_id}', headers=headers)).json()['data']['status'] == 'COMPLETED'
