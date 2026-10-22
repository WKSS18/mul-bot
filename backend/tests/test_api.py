import os
from pathlib import Path
from uuid import uuid4

os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///./test-tongpin.db'
os.environ['JWT_SECRET'] = 'test-only-secret-that-is-long-enough-for-local-tests-123'

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_auth_room_message_permissions():
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            email = f'{uuid4()}@example.com'
            result = await client.post('/api/v1/auth/register', json={'email': email, 'display_name': '小林', 'password': 'testing-password-123'})
            assert result.status_code == 201, result.text
            account = result.json()['data']
            headers = {'Authorization': f"Bearer {account['access_token']}"}
            wid = account['workspace_id']
            room_result = await client.post(f'/api/v1/workspaces/{wid}/rooms', headers=headers, json={'name': '产品发布讨论'})
            assert room_result.status_code == 201, room_result.text
            rid = room_result.json()['data']['id']
            mid = str(uuid4())
            first = await client.post(f'/api/v1/rooms/{rid}/messages', headers=headers, json={'client_message_id': mid, 'text': '开始讨论'})
            assert first.status_code == 202, first.text
            duplicate = await client.post(f'/api/v1/rooms/{rid}/messages', headers=headers, json={'client_message_id': mid, 'text': '开始讨论'})
            assert duplicate.json()['data'] == first.json()['data']
            conflict = await client.post(f'/api/v1/rooms/{rid}/messages', headers=headers, json={'client_message_id': mid, 'text': '其他内容'})
            assert conflict.status_code == 409
            outsider = await client.post('/api/v1/auth/register', json={'email': f'{uuid4()}@example.com', 'display_name': '外部用户', 'password': 'testing-password-123'})
            outsider_headers = {'Authorization': f"Bearer {outsider.json()['data']['access_token']}"}
            denied = await client.get(f'/api/v1/rooms/{rid}/messages', headers=outsider_headers)
            assert denied.status_code == 404
            messages = await client.get(f'/api/v1/rooms/{rid}/messages', headers=headers)
            assert len(messages.json()['data']) == 1
