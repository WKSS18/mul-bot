import os
import atexit
from types import SimpleNamespace
from uuid import uuid4

_test_db = f'test-tongpin-{uuid4().hex}.db'
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///./{_test_db}'
os.environ['JWT_SECRET'] = 'test-only-secret-that-is-long-enough-for-local-tests-123'
atexit.register(lambda: os.path.exists(_test_db) and os.unlink(_test_db))

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.config import settings
from app import worker


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
            bootstrap = (await client.get(f'/api/v1/rooms/{rid}/bootstrap', headers=headers)).json()['data']
            assert bootstrap['messages'][0]['id'] == first.json()['data']['message_id']
            assert bootstrap['snapshot_seq'] >= 1
            assert (await client.post(f'/api/v1/rooms/{rid}/read-position', headers=headers, json={'message_seq': 1})).json()['data']['message_seq'] == 1
            assert (await client.post(f'/api/v1/rooms/{rid}/read-position', headers=headers, json={'message_seq': 0})).json()['data']['message_seq'] == 1
            assert (await client.post(f'/api/v1/rooms/{rid}/read-position', headers=headers, json={'message_seq': 2})).status_code == 422
            assert len((await client.get('/api/v1/records?q=开始', headers=headers)).json()['data']) == 1
            assert (await client.get('/api/v1/records', headers=outsider_headers)).json()['data'] == []
            assert messages.json()['request_id'] == messages.headers['X-Request-ID']
            login = await client.post('/api/v1/auth/login', json={'email': email, 'password': 'testing-password-123'})
            assert login.status_code == 200
            assert (await client.get('/api/v1/me', headers=headers)).json()['data']['workspaces'][0]['id'] == wid
            assert len((await client.get(f'/api/v1/workspaces/{wid}/rooms', headers=headers)).json()['data']) == 1
            assert (await client.get(f'/api/v1/workspaces/{wid}/rooms', headers=outsider_headers)).status_code == 404


@pytest.mark.asyncio
async def test_upload_policy_and_completion(monkeypatch):
    monkeypatch.setattr(settings, 'oss_access_key_id', 'test-id')
    monkeypatch.setattr(settings, 'oss_access_key_secret', 'test-secret')
    monkeypatch.setattr('app.main.head_object', lambda _key: SimpleNamespace(headers={'Content-Length': '5', 'ETag': 'etag'}))
    monkeypatch.setattr('app.main.read_object_prefix', lambda _key: b'hello')
    monkeypatch.setattr(worker, 'read_object', lambda _key: b'hello')
    monkeypatch.setattr('app.providers.signed_file_url', lambda _key, _mode, _name: 'https://example.invalid/short-lived')
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            account = (await client.post('/api/v1/auth/register', json={'email': f'{uuid4()}@example.com', 'display_name': '上传者', 'password': 'testing-password-123'})).json()['data']
            headers = {'Authorization': f"Bearer {account['access_token']}"}
            wid = account['workspace_id']
            invalid = await client.post('/api/v1/uploads/init', headers=headers, json={'workspace_id': wid, 'filename': 'bad.exe', 'mime': 'application/octet-stream', 'size': 5})
            assert invalid.status_code == 422
            wrong_mime = await client.post('/api/v1/uploads/init', headers=headers, json={'workspace_id': wid, 'filename': 'bad.pdf', 'mime': 'text/html', 'size': 5})
            assert wrong_mime.status_code == 422
            disguised = await client.post('/api/v1/uploads/init', headers=headers, json={'workspace_id': wid, 'filename': 'bad.pdf', 'mime': 'application/pdf', 'size': 5})
            assert (await client.post(f"/api/v1/uploads/{disguised.json()['data']['file_id']}/complete", headers=headers)).status_code == 422
            result = await client.post('/api/v1/uploads/init', headers=headers, json={'workspace_id': wid, 'filename': 'note.txt', 'mime': 'text/plain', 'size': 5})
            assert result.status_code == 201, result.text
            payload = result.json()['data']
            assert payload['upload']['fields']['key'].startswith(f'tongpin/{wid}/uploads/')
            resign = await client.post(f"/api/v1/uploads/{payload['file_id']}/resign", headers=headers)
            assert resign.status_code == 200
            assert resign.json()['data']['upload']['fields']['key'] == payload['upload']['fields']['key']
            complete = await client.post(f"/api/v1/uploads/{payload['file_id']}/complete", headers=headers)
            assert complete.json()['data']['status'] == 'READY'
            assert (await client.post(f"/api/v1/uploads/{payload['file_id']}/resign", headers=headers)).status_code == 409
            assert await worker.process_file_one()
            access = await client.get(f"/api/v1/files/{payload['file_id']}/access?mode=preview", headers=headers)
            assert access.json()['data']['parse_state'] == 'READY'
            outsider = (await client.post('/api/v1/auth/register', json={'email': f'{uuid4()}@example.com', 'display_name': '其他用户', 'password': 'testing-password-123'})).json()['data']
            denied = await client.post(f"/api/v1/uploads/{payload['file_id']}/complete", headers={'Authorization': f"Bearer {outsider['access_token']}"})
            assert denied.status_code == 404
            assert (await client.get(f"/api/v1/files/{payload['file_id']}/access", headers={'Authorization': f"Bearer {outsider['access_token']}"})).status_code == 404


@pytest.mark.asyncio
async def test_invitation_two_users_share_room():
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            owner = (await client.post('/api/v1/auth/register', json={'email': f'{uuid4()}@example.com', 'display_name': '房主', 'password': 'testing-password-123'})).json()['data']
            guest_email = f'{uuid4()}@example.com'
            guest = (await client.post('/api/v1/auth/register', json={'email': guest_email, 'display_name': '受邀人', 'password': 'testing-password-123'})).json()['data']
            owner_headers = {'Authorization': f"Bearer {owner['access_token']}"}
            guest_headers = {'Authorization': f"Bearer {guest['access_token']}"}
            room = (await client.post(f"/api/v1/workspaces/{owner['workspace_id']}/rooms", headers=owner_headers, json={'name': '双人协作'})).json()['data']
            assert (await client.get(f"/api/v1/rooms/{room['id']}/messages", headers=guest_headers)).status_code == 404
            invited = await client.post(f"/api/v1/rooms/{room['id']}/invitations", headers=owner_headers, json={'email': guest_email})
            token = invited.json()['data']['invitation_token']
            assert (await client.post('/api/v1/invitations/accept', headers=owner_headers, json={'token': token})).status_code == 403
            assert (await client.post('/api/v1/invitations/accept', headers=guest_headers, json={'token': token})).status_code == 200
            assert (await client.post('/api/v1/invitations/accept', headers=guest_headers, json={'token': token})).status_code == 404
            assert len((await client.get(f"/api/v1/rooms/{room['id']}/members", headers=guest_headers)).json()['data']) == 2
            assert (await client.get(f"/api/v1/rooms/{room['id']}/messages", headers=guest_headers)).status_code == 200


@pytest.mark.asyncio
async def test_refresh_csrf_rotation_and_logout():
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            created = await client.post('/api/v1/auth/register', json={'email': f'{uuid4()}@example.com', 'display_name': '会话用户', 'password': 'testing-password-123'})
            assert created.status_code == 201
            assert client.cookies.get('tp_refresh')
            assert (await client.post('/api/v1/auth/refresh')).status_code == 403
            csrf = client.cookies.get('tp_csrf')
            old_refresh = client.cookies.get('tp_refresh')
            refreshed = await client.post('/api/v1/auth/refresh', headers={'X-CSRF-Token': csrf})
            assert refreshed.status_code == 200, refreshed.text
            assert client.cookies.get('tp_refresh') != old_refresh
            assert (await client.post('/api/v1/auth/logout', headers={'X-CSRF-Token': client.cookies.get('tp_csrf')})).status_code == 200
            assert not client.cookies.get('tp_refresh')
