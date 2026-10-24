"""Database-backed Bot worker. Run as a separate process: python -m app.worker."""
import asyncio
import json
import re
import time
from datetime import timedelta
from uuid import uuid4

from fastapi.concurrency import run_in_threadpool

from sqlalchemy import or_, select
from sqlalchemy.orm import aliased

from .db import BotTestRun, BotVersion, FileRecord, Message, MessageAttachment, Room, RoomEvent, RoomMember, Run, RunStep, SessionLocal, utcnow
from .documents import MIME, render_document
from .providers import ask_deepseek, put_object, read_object
from .tools import calculate, extract_expression


def requested_document_type(question: str) -> str | None:
    if not any(word in question.lower() for word in ("生成", "导出", "保存", "create", "export")):
        return None
    lowered = question.lower()
    if "pdf" in lowered:
        return "pdf"
    if "docx" in lowered or "word" in lowered:
        return "docx"
    if "markdown" in lowered or ".md" in lowered:
        return "md"
    return None


async def emit(db, room: Room, event_type: str, payload: dict):
    db.add(RoomEvent(room_id=room.id, seq=room.next_event_seq, type=event_type, payload=json.dumps(payload, ensure_ascii=False)))
    room.next_event_seq += 1


async def heartbeat_lease(run_id: str, fence: int):
    while True:
        await asyncio.sleep(10)
        async with SessionLocal() as db:
            run = await db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if run is None or run.fence_token != fence or run.status != "RUNNING":
                return
            run.lease_until = utcnow() + timedelta(seconds=45)
            await db.commit()


async def process_one() -> bool:
    async with SessionLocal() as db:
        other = aliased(Run)
        active_other = select(other.id).where(other.room_id == Run.room_id, other.id != Run.id, other.status == "RUNNING", other.lease_until > utcnow()).exists()
        run = await db.scalar(select(Run).where(or_(Run.status == "PENDING", (Run.status == "RUNNING") & (Run.lease_until < utcnow())), ~active_other).order_by(Run.created_at).with_for_update(skip_locked=True).limit(1))
        if run is None:
            return False
        await db.scalar(select(Room).where(Room.id == run.room_id).with_for_update())
        other_running = await db.scalar(select(Run.id).where(Run.room_id == run.room_id, Run.id != run.id, Run.status == "RUNNING", Run.lease_until > utcnow()).limit(1))
        if other_running:
            return False
        run.status = "RUNNING"
        run.lease_until = utcnow() + timedelta(seconds=45)
        run.fence_token += 1
        fence = run.fence_token
        run_id = run.id
        deadline = utcnow().replace(tzinfo=None) + timedelta(seconds=600)
        snapshot = json.loads(run.snapshot)
        await db.commit()

    prior = ""
    for index, bot in enumerate(snapshot["bots"], 1):
        async with SessionLocal() as db:
            run = await db.get(Run, run_id)
            if run.fence_token != fence:
                return True
            if run.cancel_requested:
                run.status = "CANCELLED"
                await db.commit()
                return True
            if utcnow().replace(tzinfo=None) >= deadline:
                run.status = "FAILED"
                run.error = "RUN_DEADLINE_EXCEEDED"
                room = await db.scalar(select(Room).where(Room.id == run.room_id).with_for_update())
                await emit(db, room, "run.failed", {"run_id": run_id, "code": "RUN_DEADLINE_EXCEEDED"})
                await db.commit()
                return True
            if not await db.scalar(select(RoomMember.id).where(RoomMember.room_id == run.room_id, RoomMember.user_id == run.actor_id)):
                run.status = "CANCELLED"
                run.error = "ROOM_ACCESS_REVOKED"
                await db.commit()
                return True
            step = await db.scalar(select(RunStep).where(RunStep.run_id == run_id, RunStep.step_no == index))
            if step and step.status == "COMPLETED":
                prior = step.output or ""
                continue
            run.lease_until = utcnow() + timedelta(seconds=45)
            if step:
                step.status = "RUNNING"
            else:
                step = RunStep(run_id=run_id, step_no=index, bot_version_id=bot["version_id"], status="RUNNING")
                db.add(step)
            room = await db.scalar(select(Room).where(Room.id == run.room_id).with_for_update())
            message = await db.scalar(select(Message).where(Message.room_id == room.id, Message.client_message_id == step.id))
            if message is None:
                message = Message(room_id=room.id, sender_id=None, sender_type="BOT", bot_id=bot["bot_id"], client_message_id=step.id, message_seq=room.next_message_seq, body="")
                room.next_message_seq += 1
                db.add(message)
                await db.flush()
                await emit(db, room, "message.created", {"message_id": message.id, "message_seq": message.message_seq, "sender_type": "BOT", "bot_id": bot["bot_id"], "body": ""})
            message_id = message.id
            await emit(db, room, "run.step.started", {"run_id": run_id, "step_no": index, "bot_id": bot["bot_id"]})
            await db.commit()
        question = snapshot["question"]
        if snapshot.get("history"):
            transcript = []
            remaining = 24000
            for entry in snapshot["history"]:
                text = entry.get("text", "")[:3000]
                if not text or remaining <= 0:
                    continue
                block = f"{entry.get('speaker', '团队成员')}：{text}"
                for attachment in entry.get("attachments", []):
                    if "read_attachment" in bot["tools"] and remaining > 0:
                        excerpt = attachment.get("text", "")[:min(8000, remaining)]
                        if excerpt:
                            block += f"\n[此前附件 {attachment.get('filename', '文件')} 内容]\n{excerpt}"
                block = block[:remaining]
                transcript.append(block)
                remaining -= len(block)
            if transcript:
                question = "以下是当前聊天室最近的对话记录，请结合上下文理解用户的追问；不要声称看不到本聊天室已显示的历史内容。\n" + "\n".join(transcript) + "\n\n用户当前消息：\n" + question
        for attachment in snapshot.get("attachments", []):
            if "read_attachment" not in bot["tools"]:
                continue
            question += f"\n\n附件 {attachment['filename']}：\n{attachment['text']}"
        if snapshot.get("attachments") and "read_attachment" in bot["tools"]:
            async with SessionLocal() as tool_db:
                tool_room = await tool_db.scalar(select(Room).where(Room.id == run.room_id).with_for_update())
                await emit(tool_db, tool_room, "tool.completed", {"run_id": run_id, "step_no": index, "tool": "read_attachment", "count": len(snapshot["attachments"])})
                await tool_db.commit()
        expression = extract_expression(question) if "calculator" in bot["tools"] else None
        if expression:
            try:
                value = calculate(expression)
                question += f"\n\n计算器结果：{expression} = {value:g}"
                async with SessionLocal() as tool_db:
                    tool_room = await tool_db.scalar(select(Room).where(Room.id == run.room_id).with_for_update())
                    await emit(tool_db, tool_room, "tool.completed", {"run_id": run_id, "step_no": index, "tool": "calculator", "expression": expression, "result": value})
                    await tool_db.commit()
            except (ValueError, ZeroDivisionError):
                question += "\n\n计算器无法处理该表达式。"
        if prior:
            question += "\n\n前一位 Bot 的结果：\n" + prior[:12000]
        partial = ""
        last_flush = time.monotonic()
        requested_docs = []

        async def on_delta(delta: str):
            nonlocal partial, last_flush
            partial += delta
            if time.monotonic() - last_flush < 0.15:
                return
            async with SessionLocal() as stream_db:
                current_run = await stream_db.get(Run, run_id)
                if current_run.fence_token != fence or current_run.cancel_requested:
                    raise RuntimeError("Run no longer owns its lease")
                current_message = await stream_db.get(Message, message_id)
                current_message.body = partial
                room = await stream_db.scalar(select(Room).where(Room.id == current_run.room_id).with_for_update())
                await emit(stream_db, room, "message.delta", {"message_id": message_id, "body": partial})
                await stream_db.commit()
            last_flush = time.monotonic()

        async def on_tool(name: str, arguments: dict, tool_use_id: str):
            async with SessionLocal() as tool_db:
                current_run = await tool_db.get(Run, run_id)
                if current_run.fence_token != fence or current_run.cancel_requested or not await tool_db.scalar(select(RoomMember.id).where(RoomMember.room_id == current_run.room_id, RoomMember.user_id == current_run.actor_id)):
                    raise PermissionError("Run access revoked")
            if name == "calculator" and name in bot["tools"]:
                expression = arguments.get("expression")
                if not isinstance(expression, str):
                    raise ValueError("Invalid expression")
                value = calculate(expression)
                async with SessionLocal() as tool_db:
                    tool_room = await tool_db.scalar(select(Room).where(Room.id == run.room_id).with_for_update())
                    await emit(tool_db, tool_room, "tool.completed", {"run_id": run_id, "step_no": index, "tool": name, "expression": expression, "result": value})
                    await tool_db.commit()
                return f"{value:g}"
            if name == "read_attachment" and name in bot["tools"]:
                match = next((item for item in snapshot.get("attachments", []) if item["file_id"] == arguments.get("file_id")), None)
                if match is None:
                    raise ValueError("Attachment not in this run")
                return match["text"][:12000]
            if name == "generate_document" and name in bot["tools"]:
                title, content, doc_type = arguments.get("title"), arguments.get("content"), arguments.get("format")
                if not isinstance(title, str) or not isinstance(content, str) or doc_type not in MIME or not title.strip() or not content.strip() or len(content) > 100000 or len(requested_docs) >= 3:
                    raise ValueError("Invalid document request")
                requested_docs.append((title[:120], content, doc_type, tool_use_id))
                return "Document queued; it becomes downloadable after storage succeeds."
            if name == "handoff_to_bot" and name in bot["tools"]:
                next_bot = snapshot["bots"][index] if index < len(snapshot["bots"]) else None
                if next_bot is None or index > 2 or arguments.get("bot_id") != next_bot["bot_id"]:
                    raise ValueError("Handoff target is not the next authorized Bot")
                summary = arguments.get("summary")
                if not isinstance(summary, str) or len(summary) > 2000:
                    raise ValueError("Invalid handoff summary")
                async with SessionLocal() as tool_db:
                    tool_room = await tool_db.scalar(select(Room).where(Room.id == run.room_id).with_for_update())
                    await emit(tool_db, tool_room, "tool.completed", {"run_id": run_id, "step_no": index, "tool": name, "target_bot_id": next_bot["bot_id"], "summary": summary[:200]})
                    await tool_db.commit()
                return "Handoff recorded. The next authorized Bot will receive your final output."
            raise PermissionError("Tool not authorized")

        lease_task = asyncio.create_task(heartbeat_lease(run_id, fence))
        try:
            remaining = (deadline - utcnow().replace(tzinfo=None)).total_seconds()
            async with asyncio.timeout(max(0.1, remaining)):
                # A bare mention is an intentional address, not a complete task.
                # Respond locally so the Bot does not spend a model call inventing a generic menu.
                mention_only = not re.sub(r"@[^\s@]+", "", question).strip()
                if mention_only:
                    answer = f"你好，我是 @{bot['bot_name']}。请告诉我你希望我处理的具体问题，或直接附上数据、文件和目标。"
                    await on_delta(answer)
                else:
                    answer = await ask_deepseek(question, system=bot["prompt"], model=bot["model"], on_delta=on_delta, tools=bot["tools"], on_tool=on_tool)
            if not answer.strip():
                raise ValueError("model returned empty text")
            artifacts = []
            if "generate_document" in bot["tools"]:
                doc_type = requested_document_type(snapshot["question"])
                documents = requested_docs or ([(f"{bot['bot_name']}生成结果", answer, doc_type, None)] if doc_type and index == len(snapshot["bots"]) else [])
                for title, content_text, doc_type, _tool_id in documents:
                    async with SessionLocal() as check_db:
                        current_run = await check_db.get(Run, run_id)
                        if current_run.fence_token != fence or current_run.cancel_requested or not await check_db.scalar(select(RoomMember.id).where(RoomMember.room_id == current_run.room_id, RoomMember.user_id == current_run.actor_id)):
                            return True
                    artifact_id = str(uuid4())
                    content = await run_in_threadpool(render_document, title, content_text, doc_type)
                    async with SessionLocal() as artifact_db:
                        artifact_run = await artifact_db.get(Run, run_id)
                        artifact_room = await artifact_db.get(Room, artifact_run.room_id)
                        object_key = f"tongpin/{artifact_room.workspace_id}/artifacts/{artifact_id}/result.{doc_type}"
                    await run_in_threadpool(put_object, object_key, content, MIME[doc_type])
                    artifacts.append((artifact_id, object_key, title, doc_type, content, content_text))
        except Exception as exc:
            async with SessionLocal() as db:
                run = await db.get(Run, run_id)
                if run.fence_token != fence:
                    return True
                run.status = "FAILED"
                run.error = type(exc).__name__
                step = await db.scalar(select(RunStep).where(RunStep.run_id == run_id, RunStep.step_no == index))
                step.status = "FAILED"
                room = await db.scalar(select(Room).where(Room.id == run.room_id).with_for_update())
                error_type = type(exc).__name__
                if error_type in {"AuthenticationError", "PermissionDeniedError"}:
                    reason = "模型服务认证失败，请检查服务端模型密钥和权限。"
                elif error_type == "RateLimitError":
                    reason = "模型服务当前请求过多或额度受限，请稍后重试。"
                elif error_type in {"APIConnectionError", "APITimeoutError", "TimeoutError"}:
                    reason = "连接模型服务超时或失败，请检查服务地址和网络后重试。"
                elif error_type in {"NotFoundError", "BadRequestError"}:
                    reason = "模型服务拒绝了请求，请检查模型名称和服务配置。"
                elif error_type == "ValueError":
                    reason = "模型返回了空回复，请重新发送；如果再次发生，请检查模型服务的响应格式。"
                elif error_type == "ModelResponseIncomplete":
                    reason = "本次回复未完整生成（输出额度耗尽或响应中断），请重试或缩小问题范围。"
                else:
                    reason = f"模型调用失败（{error_type}），请检查模型配置或稍后重试。"
                message = await db.get(Message, message_id)
                message.body = (message.body.rstrip() + "\n\n" if message.body.strip() else "") + reason
                await emit(db, room, "message.completed", {"message_id": message.id, "message_seq": message.message_seq, "body": message.body})
                await emit(db, room, "run.failed", {"run_id": run_id, "step_no": index, "code": "MODEL_FAILED", "reason": reason})
                await db.commit()
            return True
        finally:
            lease_task.cancel()
            await asyncio.gather(lease_task, return_exceptions=True)
        async with SessionLocal() as db:
            run = await db.get(Run, run_id)
            if run.fence_token != fence:
                return True
            if run.cancel_requested:
                run.status = "CANCELLED"
                await db.commit()
                return True
            room = await db.scalar(select(Room).where(Room.id == run.room_id).with_for_update())
            step = await db.scalar(select(RunStep).where(RunStep.run_id == run_id, RunStep.step_no == index))
            step.status = "COMPLETED"
            step.output = answer
            message = await db.get(Message, message_id)
            message.body = answer
            files = []
            for artifact_id, object_key, title, doc_type, content, content_text in artifacts:
                db.add(FileRecord(id=artifact_id, workspace_id=room.workspace_id, uploader_id=run.actor_id, object_key=object_key, filename=f"{title}.{doc_type}", mime=MIME[doc_type], size=len(content), upload_state="READY", parse_state="READY", extracted_text=content_text[:100000]))
                db.add(MessageAttachment(message_id=message.id, file_id=artifact_id))
                files.append({"id": artifact_id, "filename": f"{title}.{doc_type}", "mime": MIME[doc_type], "parse_state": "READY"})
                await emit(db, room, "tool.completed", {"run_id": run_id, "step_no": index, "tool": "generate_document", "artifact_id": artifact_id, "format": doc_type})
            await emit(db, room, "message.completed", {"message_id": message.id, "message_seq": message.message_seq, "body": answer, "attachments": files})
            if index == len(snapshot["bots"]):
                run.status = "COMPLETED"
                await emit(db, room, "run.completed", {"run_id": run_id})
            await db.commit()
        prior = answer

    # A recovered worker may find every step committed while the final run
    # transition was interrupted. Repair that state without another model call.
    async with SessionLocal() as db:
        run = await db.get(Run, run_id)
        if run.fence_token == fence and run.status == "RUNNING":
            run.status = "COMPLETED"
            room = await db.scalar(select(Room).where(Room.id == run.room_id).with_for_update())
            await emit(db, room, "run.completed", {"run_id": run_id})
            await db.commit()
    return True


def extract_text(filename: str, content: bytes) -> str:
    from io import BytesIO
    from pathlib import Path
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md"}:
        result = content.decode("utf-8-sig", errors="replace")
    elif suffix == ".pdf":
        from pypdf import PdfReader
        result = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)
    elif suffix == ".docx":
        from docx import Document
        result = "\n".join(paragraph.text for paragraph in Document(BytesIO(content)).paragraphs)
    else:
        raise ValueError("此文件类型尚不支持文本解析")
    if not result.strip():
        raise ValueError("文件中没有可提取文字")
    return result[:100000]


async def process_file_one() -> bool:
    from fastapi.concurrency import run_in_threadpool
    async with SessionLocal() as db:
        file = await db.scalar(select(FileRecord).where(FileRecord.upload_state == "READY", FileRecord.parse_state == "PENDING").order_by(FileRecord.created_at).with_for_update(skip_locked=True).limit(1))
        if file is None:
            return False
        file.parse_state = "PROCESSING"
        file_id, key, name = file.id, file.object_key, file.filename
        await db.commit()
    try:
        content = await run_in_threadpool(read_object, key)
        text = await run_in_threadpool(extract_text, name, content)
        state = "READY"
    except Exception:
        text = None
        state = "FAILED"
    async with SessionLocal() as db:
        file = await db.get(FileRecord, file_id)
        file.extracted_text = text
        file.parse_state = state
        await db.commit()
    return True


async def process_bot_test_one() -> bool:
    async with SessionLocal() as db:
        test_run = await db.scalar(select(BotTestRun).where(BotTestRun.status == "PENDING").order_by(BotTestRun.created_at).with_for_update(skip_locked=True).limit(1))
        if test_run is None:
            return False
        test_run.status = "RUNNING"
        test_id, version_id, prompt = test_run.id, test_run.bot_version_id, test_run.prompt
        await db.commit()
    async with SessionLocal() as db:
        version = await db.get(BotVersion, version_id)
        system, model = version.prompt, version.model_name
    try:
        async with asyncio.timeout(60):
            output = await ask_deepseek(prompt, system=system, model=model)
        status, error = "COMPLETED", None
    except Exception as exc:
        output, status, error = None, "FAILED", type(exc).__name__
    async with SessionLocal() as db:
        test_run = await db.get(BotTestRun, test_id)
        test_run.status, test_run.output, test_run.error = status, output, error
        await db.commit()
    return True


async def main():
    while True:
        if not await process_file_one() and not await process_one() and not await process_bot_test_one():
            await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(main())
