from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_bot_version_and_room_permissions():
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            owner = (await client.post("/api/v1/auth/register", json={"email": f"{uuid4()}@example.com", "display_name": "房主", "password": "testing-password-123"})).json()["data"]
            invited_email = f"{uuid4()}@example.com"
            other = (await client.post("/api/v1/auth/register", json={"email": invited_email, "display_name": "成员", "password": "testing-password-123"})).json()["data"]
            owner_h = {"Authorization": f"Bearer {owner['access_token']}"}
            other_h = {"Authorization": f"Bearer {other['access_token']}"}
            wid = owner["workspace_id"]
            room = (await client.post(f"/api/v1/workspaces/{wid}/rooms", headers=owner_h, json={"name": "测试房间"})).json()["data"]
            created = await client.post("/api/v1/bots", headers=owner_h, json={"workspace_id": wid, "name": "写作助手", "prompt": "你是一个严谨的写作助手。", "tools": []})
            assert created.status_code == 201
            bid = created.json()["data"]["id"]
            first = (await client.get(f"/api/v1/bots/{bid}", headers=owner_h)).json()["data"]
            updated = await client.put(f"/api/v1/bots/{bid}", headers=owner_h, json={"name": "新版助手", "prompt": "你是一个严谨而简洁的写作助手。", "tools": ["calculator"], "revision": 1})
            assert updated.status_code == 200, updated.text
            assert updated.json()["data"]["version"] == 2
            assert (await client.put(f"/api/v1/bots/{bid}", headers=owner_h, json={"name": "过期", "prompt": first["prompt"], "revision": 1})).status_code == 409
            current = (await client.get(f"/api/v1/bots/{bid}", headers=owner_h)).json()["data"]
            assert current["prompt"] != first["prompt"] and current["revision"] == 2
            invitation = (await client.post(f"/api/v1/rooms/{room['id']}/invitations", headers=owner_h, json={"email": invited_email})).json()["data"]["invitation_token"]
            assert (await client.post("/api/v1/invitations/accept", headers=other_h, json={"token": invitation})).status_code == 200
            assert (await client.patch(f"/api/v1/rooms/{room['id']}", headers=other_h, json={"name": "越权重命名", "revision": 1})).status_code == 403
            renamed = await client.patch(f"/api/v1/rooms/{room['id']}", headers=owner_h, json={"name": "新版房间", "revision": 1})
            assert renamed.status_code == 200
            assert (await client.patch(f"/api/v1/rooms/{room['id']}", headers=owner_h, json={"name": "旧版", "revision": 1})).status_code == 409
            assert (await client.post(f"/api/v1/rooms/{room['id']}/messages", headers=other_h, json={"client_message_id": str(uuid4()), "text": ""})).status_code == 422
            assert (await client.delete(f"/api/v1/rooms/{room['id']}/members/{other['user']['id']}", headers=owner_h)).status_code == 200
            assert (await client.get(f"/api/v1/rooms/{room['id']}/messages", headers=other_h)).status_code == 404


@pytest.mark.asyncio
async def test_room_owner_transfer_and_archive():
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            owner = (await client.post("/api/v1/auth/register", json={"email": f"{uuid4()}@example.com", "display_name": "房主", "password": "testing-password-123"})).json()["data"]
            invited_email = f"{uuid4()}@example.com"
            member = (await client.post("/api/v1/auth/register", json={"email": invited_email, "display_name": "新房主", "password": "testing-password-123"})).json()["data"]
            owner_h = {"Authorization": f"Bearer {owner['access_token']}"}
            member_h = {"Authorization": f"Bearer {member['access_token']}"}
            room = (await client.post(f"/api/v1/workspaces/{owner['workspace_id']}/rooms", headers=owner_h, json={"name": "转让房间"})).json()["data"]
            invitation = (await client.post(f"/api/v1/rooms/{room['id']}/invitations", headers=owner_h, json={"email": invited_email})).json()["data"]["invitation_token"]
            assert (await client.post("/api/v1/invitations/accept", headers=member_h, json={"token": invitation})).status_code == 200
            assert (await client.delete(f"/api/v1/rooms/{room['id']}/members/{owner['user']['id']}", headers=owner_h)).status_code == 409
            assert (await client.post(f"/api/v1/rooms/{room['id']}/transfer-owner", headers=owner_h, json={"user_id": member['user']['id']})).status_code == 200
            assert (await client.delete(f"/api/v1/rooms/{room['id']}/members/{owner['user']['id']}", headers=owner_h)).status_code == 200
            assert (await client.post(f"/api/v1/rooms/{room['id']}/archive", headers=member_h)).status_code == 200
            assert (await client.post(f"/api/v1/rooms/{room['id']}/messages", headers=member_h, json={"client_message_id": str(uuid4()), "text": "应失败"})).status_code == 409


@pytest.mark.asyncio
async def test_invitation_can_be_revoked():
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            owner = (await client.post('/api/v1/auth/register', json={'email': f'{uuid4()}@example.com', 'display_name': '邀请房主', 'password': 'testing-password-123'})).json()['data']
            invited_email = f'{uuid4()}@example.com'
            invitee = (await client.post('/api/v1/auth/register', json={'email': invited_email, 'display_name': '被邀请人', 'password': 'testing-password-123'})).json()['data']
            owner_h = {'Authorization': f"Bearer {owner['access_token']}"}
            invitee_h = {'Authorization': f"Bearer {invitee['access_token']}"}
            room = (await client.post(f"/api/v1/workspaces/{owner['workspace_id']}/rooms", headers=owner_h, json={'name': '撤邀房间'})).json()['data']
            invitation = (await client.post(f"/api/v1/rooms/{room['id']}/invitations", headers=owner_h, json={'email': invited_email})).json()['data']
            assert (await client.get(f"/api/v1/rooms/{room['id']}/invitations", headers=owner_h)).json()['data'][0]['id'] == invitation['id']
            assert (await client.delete(f"/api/v1/rooms/{room['id']}/invitations/{invitation['id']}", headers=owner_h)).status_code == 200
            assert (await client.post('/api/v1/invitations/accept', headers=invitee_h, json={'token': invitation['invitation_token']})).status_code == 404
