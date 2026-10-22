from uuid import uuid4

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app


def test_realtime_ticket_room_event_and_replay():
    with TestClient(app) as client:
        account = client.post('/api/v1/auth/register', json={'email': f'{uuid4()}@example.com', 'display_name': '实时用户', 'password': 'testing-password-123'}).json()['data']
        headers = {'Authorization': f"Bearer {account['access_token']}"}
        room = client.post(f"/api/v1/workspaces/{account['workspace_id']}/rooms", headers=headers, json={'name': '协作测试'}).json()['data']
        ticket = client.post('/api/v1/realtime/tickets', headers=headers).json()['data']['ticket']
        with client.websocket_connect('/api/v1/realtime') as ws:
            ws.send_json({'type': 'auth', 'ticket': ticket})
            assert ws.receive_json()['type'] == 'authenticated'
            ws.send_json({'type': 'subscribe', 'room_id': room['id'], 'after_seq': 0})
            assert ws.receive_json()['type'] == 'subscribed'
            client.post(f"/api/v1/rooms/{room['id']}/messages", headers=headers, json={'client_message_id': str(uuid4()), 'text': '实时同步内容'})
            event = ws.receive_json()
            assert event['type'] == 'message.created'
            assert event['payload']['body'] == '实时同步内容'
            assert client.get(f"/api/v1/rooms/{room['id']}/events?after_seq=0", headers=headers).json()['data'][0]['seq'] == event['seq']
        with client.websocket_connect('/api/v1/realtime') as ws:
            ws.send_json({'type': 'auth', 'ticket': ticket})
            from starlette.websockets import WebSocketDisconnect
            try:
                ws.receive_json()
                assert False, 'ticket replay should close connection'
            except WebSocketDisconnect as exc:
                assert exc.code == 4401


def test_removed_member_loses_live_subscription():
    with TestClient(app) as client:
        owner = client.post('/api/v1/auth/register', json={'email': f'{uuid4()}@example.com', 'display_name': '房主', 'password': 'testing-password-123'}).json()['data']
        member_email = f'{uuid4()}@example.com'
        member = client.post('/api/v1/auth/register', json={'email': member_email, 'display_name': '成员', 'password': 'testing-password-123'}).json()['data']
        owner_h = {'Authorization': f"Bearer {owner['access_token']}"}
        member_h = {'Authorization': f"Bearer {member['access_token']}"}
        room = client.post(f"/api/v1/workspaces/{owner['workspace_id']}/rooms", headers=owner_h, json={'name': '撤权房间'}).json()['data']
        invite = client.post(f"/api/v1/rooms/{room['id']}/invitations", headers=owner_h, json={'email': member_email}).json()['data']['invitation_token']
        assert client.post('/api/v1/invitations/accept', headers=member_h, json={'token': invite}).status_code == 200
        ticket = client.post('/api/v1/realtime/tickets', headers=member_h).json()['data']['ticket']
        with client.websocket_connect('/api/v1/realtime') as ws:
            ws.send_json({'type': 'auth', 'ticket': ticket})
            assert ws.receive_json()['type'] == 'authenticated'
            ws.send_json({'type': 'subscribe', 'room_id': room['id'], 'after_seq': 0})
            assert ws.receive_json()['type'] == 'subscribed'
            assert client.delete(f"/api/v1/rooms/{room['id']}/members/{member['user']['id']}", headers=owner_h).status_code == 200
            try:
                ws.receive_json()
                assert False, 'revoked member must be disconnected'
            except WebSocketDisconnect as exc:
                assert exc.code == 4403
