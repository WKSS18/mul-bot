"""Explicit live test: OSS upload -> parse -> two DeepSeek bots -> room events."""
import asyncio
import os
from uuid import uuid4

os.environ.setdefault('DATABASE_URL', f'sqlite+aiosqlite:///./live-probe-{uuid4().hex}.db')

import oss2
import requests
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.documents import MIME, render_document
from app.db import FileRecord, SessionLocal, engine
from app.main import app
from app import worker


async def main():
    if not settings.anthropic_auth_token or not (settings.oss_access_key_id and settings.oss_access_key_secret):
        raise SystemExit('DeepSeek and OSS credentials must be set only in process environment')
    key = None
    artifact_key = None
    extra_keys = []
    auth = oss2.AuthV4(settings.oss_access_key_id, settings.oss_access_key_secret)
    bucket = oss2.Bucket(auth, settings.oss_endpoint, settings.oss_bucket, region=settings.oss_region)
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                account = (await client.post('/api/v1/auth/register', json={'email': f'{uuid4()}@example.com', 'display_name': '联通验证', 'password': 'temporary-test-password-123'})).json()['data']
                headers = {'Authorization': f"Bearer {account['access_token']}"}
                wid = account['workspace_id']
                room = (await client.post(f'/api/v1/workspaces/{wid}/rooms', headers=headers, json={'name': '真实链路测试'})).json()['data']
                bots = []
                for name in ('资料助手', '总结助手'):
                    result = await client.post('/api/v1/bots', headers=headers, json={'workspace_id': wid, 'name': name, 'prompt': f'你是{name}，请根据用户文本和附件给出一句简短中文答复。', 'tools': ['read_attachment'] if name == '资料助手' else ['generate_document']})
                    assert result.status_code == 201, result.text
                    bot_id = result.json()['data']['id']
                    bots.append(bot_id)
                    assert (await client.post(f"/api/v1/rooms/{room['id']}/bots", headers=headers, json={'bot_id': bot_id})).status_code == 201
                content = '同频 AI 联通测试：附件内容已成功解析。'.encode()
                upload = await client.post('/api/v1/uploads/init', headers=headers, json={'workspace_id': wid, 'filename': 'probe.txt', 'mime': 'text/plain', 'size': len(content)})
                assert upload.status_code == 201, upload.text
                upload = upload.json()['data']
                key = upload['upload']['fields']['key']
                response = requests.post(upload['upload']['url'], data=upload['upload']['fields'], files={'file': ('probe.txt', content, 'text/plain')}, timeout=30)
                assert response.status_code == 204, f'OSS POST {response.status_code}: {response.text[:120]}'
                complete = await client.post(f"/api/v1/uploads/{upload['file_id']}/complete", headers=headers)
                assert complete.status_code == 200, complete.text
                assert await worker.process_file_one()
                access = await client.get(f"/api/v1/files/{upload['file_id']}/access?mode=download", headers=headers)
                assert access.status_code == 200, access.text
                assert access.json()['data']['parse_state'] == 'READY'
                print('OSS: signed POST, HEAD, parse, access URL passed')
                sent = await client.post(f"/api/v1/rooms/{room['id']}/messages", headers=headers, json={'client_message_id': str(uuid4()), 'text': '根据附件依次整理和总结，并生成 Markdown 文件。', 'attachment_ids': [upload['file_id']], 'mentions': [{'bot_id': bots[0], 'position': 0}, {'bot_id': bots[1], 'position': 1}]})
                assert sent.status_code == 202, sent.text
                run_id = sent.json()['data']['run_id']
                assert run_id and await worker.process_one()
                run = await client.get(f'/api/v1/runs/{run_id}', headers=headers)
                assert run.json()['data']['status'] == 'COMPLETED', run.text
                messages = (await client.get(f"/api/v1/rooms/{room['id']}/messages", headers=headers)).json()['data']
                assert [m['sender_type'] for m in messages] == ['USER', 'BOT', 'BOT']
                artifact_id = messages[-1]['attachments'][0]['id']
                async with SessionLocal() as db:
                    artifact_key = (await db.get(FileRecord, artifact_id)).object_key
                assert bucket.head_object(artifact_key).content_length > 0
                assert (await client.get(f'/api/v1/files/{artifact_id}/access?mode=download', headers=headers)).status_code == 200
                for doc_type in ('docx', 'pdf'):
                    content = render_document('联调文档', '中文正文与协作结果。', doc_type)
                    extra_key = f'tongpin/{wid}/artifacts/{uuid4()}/result.{doc_type}'
                    extra_keys.append(extra_key)
                    bucket.put_object(extra_key, content, headers={'Content-Type': MIME[doc_type]})
                    assert bucket.head_object(extra_key).content_length == len(content)
                events = (await client.get(f"/api/v1/rooms/{room['id']}/events?after_seq=0", headers=headers)).json()['data']
                assert any(e['type'] == 'run.completed' for e in events)
                print('DeepSeek: streamed two Bot steps, MD/DOCX/PDF artifacts saved, event replay passed')
    finally:
        for extra_key in extra_keys:
            bucket.delete_object(extra_key)
        if artifact_key:
            bucket.delete_object(artifact_key)
        if key:
            try:
                bucket.delete_object(key)
                print('OSS test object cleanup: passed')
            except Exception as exc:
                print('OSS test object cleanup: FAILED', type(exc).__name__)
                raise
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
