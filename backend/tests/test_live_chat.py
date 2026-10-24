"""Opt-in real provider regression; uses an isolated database, never user rooms."""
import os
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.db import FileRecord, SessionLocal
from app import worker


@pytest.mark.skipif(os.getenv("RUN_LIVE_MODEL_TESTS") != "1", reason="Requires real model credentials")
@pytest.mark.asyncio
async def test_real_model_attachment_followups_and_multiple_bots():
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            account = (await client.post("/api/v1/auth/register", json={"email": f"{uuid4()}@example.com", "display_name": "回归测试", "password": "testing-password-123"})).json()["data"]
            headers = {"Authorization": f"Bearer {account['access_token']}"}
            wid = account["workspace_id"]
            room = (await client.post(f"/api/v1/workspaces/{wid}/rooms", headers=headers, json={"name": "真实模型回归"})).json()["data"]
            bots = []
            for name in ("学习", "产品经理"):
                bot = (await client.post("/api/v1/bots", headers=headers, json={"workspace_id": wid, "name": name, "prompt": "你是文件分析助手，结合聊天记录简明回答，不超过150字。", "tools": ["read_attachment"]})).json()["data"]
                assert (await client.post(f"/api/v1/rooms/{room['id']}/bots", headers=headers, json={"bot_id": bot["id"]})).status_code == 201
                bots.append(bot["id"])
            async with SessionLocal() as db:
                file = FileRecord(workspace_id=wid, uploader_id=account["user"]["id"], object_key=f"test/{uuid4()}.txt", filename="项目说明.txt", mime="text/plain", size=100, upload_state="READY", parse_state="READY", extracted_text="青鸟项目：交付日期为2026年12月15日，预算30万元。需要完成设计、开发、验收三个阶段。")
                db.add(file)
                await db.commit()
                file_id = file.id
            cases = [
                ("你好，请回复准备好了", [bots[0]], [], None),
                ("看下这个，概括项目名称和交付日期", [], [file_id], "青鸟"),
                ("继续，前面的项目预算是多少？", [], [], "30"),
                ("请分别简要说明前面项目的交付日期", bots, [], "15"),
            ]
            for text, targets, attachments, expected in cases:
                sent = await client.post(f"/api/v1/rooms/{room['id']}/messages", headers=headers, json={"client_message_id": str(uuid4()), "text": text, "mentions": [{"bot_id": b, "position": i} for i, b in enumerate(targets)], "attachment_ids": attachments})
                assert sent.status_code == 202, sent.text
                run_id = sent.json()["data"]["run_id"]
                assert run_id
                assert await worker.process_one()
                run = (await client.get(f"/api/v1/runs/{run_id}", headers=headers)).json()["data"]
                assert run["status"] == "COMPLETED", run
                assert len(run["steps"]) == max(1, len(targets))
                for step in run["steps"]:
                    assert step["output"].strip()
                    if expected:
                        assert expected in step["output"], step["output"]
                print(f"live case passed: {text}; reply lengths={[len(s['output']) for s in run['steps']]}", flush=True)
