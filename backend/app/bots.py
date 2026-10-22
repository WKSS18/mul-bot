import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import Bot, BotTestRun, BotVersion, Room, RoomBot, RoomMember, Run, RunStep, User, WorkspaceMember, get_db, new_id
from .security import current_user

router = APIRouter(prefix="/api/v1")


class BotIn(BaseModel):
    workspace_id: str
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=255)
    prompt: str = Field(min_length=10, max_length=20000)
    tools: list[str] = Field(default_factory=list)


class RoomBotIn(BaseModel):
    bot_id: str


class BotUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=255)
    prompt: str = Field(min_length=10, max_length=20000)
    tools: list[str] = Field(default_factory=list)
    revision: int = Field(ge=1)


class BotState(BaseModel):
    enabled: bool
    revision: int = Field(ge=1)


class BotTestIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)


ALLOWED_TOOLS = {"read_attachment", "generate_document", "calculator", "handoff_to_bot"}


async def editable_bot(db: AsyncSession, bot_id: str, user: User) -> Bot:
    bot = await db.get(Bot, bot_id)
    if bot is None:
        raise HTTPException(404, "Bot 不存在")
    member = await db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == bot.workspace_id, WorkspaceMember.user_id == user.id))
    if member is None or (bot.owner_id != user.id and member.role not in {"OWNER", "ADMIN"}):
        raise HTTPException(404, "Bot 不存在")
    return bot


@router.get("/bots")
async def list_bots(workspace_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    allowed = await db.scalar(select(WorkspaceMember.id).where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user.id))
    if not allowed:
        raise HTTPException(404, "工作空间不存在")
    bots = (await db.scalars(select(Bot).where(Bot.workspace_id == workspace_id).order_by(Bot.created_at))).all()
    return {"data": [{"id": b.id, "name": b.name, "description": b.description, "enabled": b.enabled, "revision": b.revision} for b in bots]}


@router.post("/bots", status_code=201)
async def create_bot(payload: BotIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    member = await db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == payload.workspace_id, WorkspaceMember.user_id == user.id))
    if member is None or member.role not in {"OWNER", "ADMIN"}:
        raise HTTPException(403, "没有创建 Bot 的权限")
    if any(tool not in ALLOWED_TOOLS for tool in payload.tools):
        raise HTTPException(422, "工具不在许可列表")
    bot = Bot(id=new_id(), workspace_id=payload.workspace_id, owner_id=user.id, name=payload.name.strip(), description=payload.description, current_version_id=new_id())
    version = BotVersion(id=bot.current_version_id, bot_id=bot.id, version=1, prompt=payload.prompt, tool_allowlist=json.dumps(payload.tools), model_name=settings.anthropic_model)
    db.add_all([bot, version])
    await db.commit()
    return {"data": {"id": bot.id, "name": bot.name, "version": 1}}


@router.get("/bots/{bot_id}")
async def get_bot(bot_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    bot = await db.get(Bot, bot_id)
    if bot is None or not await db.scalar(select(WorkspaceMember.id).where(WorkspaceMember.workspace_id == bot.workspace_id, WorkspaceMember.user_id == user.id)):
        raise HTTPException(404, "Bot 不存在")
    version = await db.get(BotVersion, bot.current_version_id)
    return {"data": {"id": bot.id, "name": bot.name, "description": bot.description, "enabled": bot.enabled, "revision": bot.revision, "owner_id": bot.owner_id, "version": version.version, "prompt": version.prompt, "tools": json.loads(version.tool_allowlist)}}


@router.put("/bots/{bot_id}")
async def update_bot(bot_id: str, payload: BotUpdate, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    bot = await editable_bot(db, bot_id, user)
    if bot.revision != payload.revision:
        raise HTTPException(409, "Bot 已由其他人修改，请刷新后重试")
    if any(tool not in ALLOWED_TOOLS for tool in payload.tools):
        raise HTTPException(422, "工具不在许可列表")
    current = await db.get(BotVersion, bot.current_version_id)
    version = BotVersion(id=new_id(), bot_id=bot.id, version=current.version + 1, prompt=payload.prompt, tool_allowlist=json.dumps(payload.tools), model_name=settings.anthropic_model)
    db.add(version)
    bot.name = payload.name.strip()
    bot.description = payload.description
    bot.current_version_id = version.id
    bot.revision += 1
    await db.commit()
    return {"data": {"id": bot.id, "revision": bot.revision, "version": version.version}}


@router.patch("/bots/{bot_id}/state")
async def set_bot_state(bot_id: str, payload: BotState, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    bot = await editable_bot(db, bot_id, user)
    if bot.revision != payload.revision:
        raise HTTPException(409, "Bot 已由其他人修改，请刷新后重试")
    bot.enabled = payload.enabled
    bot.revision += 1
    await db.commit()
    return {"data": {"id": bot.id, "enabled": bot.enabled, "revision": bot.revision}}


@router.post("/bots/{bot_id}/test-runs", status_code=202)
async def create_bot_test(bot_id: str, payload: BotTestIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    bot = await editable_bot(db, bot_id, user)
    if not bot.enabled:
        raise HTTPException(409, "Bot 已停用")
    test_run = BotTestRun(bot_id=bot.id, bot_version_id=bot.current_version_id, actor_id=user.id, prompt=payload.prompt)
    db.add(test_run)
    await db.commit()
    return {"data": {"id": test_run.id, "status": test_run.status}}


@router.get("/bot-test-runs/{test_id}")
async def get_bot_test(test_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    test_run = await db.get(BotTestRun, test_id)
    if test_run is None or test_run.actor_id != user.id:
        raise HTTPException(404, "测试任务不存在")
    return {"data": {"id": test_run.id, "status": test_run.status, "output": test_run.output, "error": test_run.error}}


@router.post("/rooms/{rid}/bots", status_code=201)
async def add_room_bot(rid: str, payload: RoomBotIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    room = await db.get(Room, rid)
    membership = await db.scalar(select(RoomMember).where(RoomMember.room_id == rid, RoomMember.user_id == user.id))
    bot = await db.get(Bot, payload.bot_id)
    if room is None or membership is None:
        raise HTTPException(404, "聊天室不存在")
    if room.archived_at is not None:
        raise HTTPException(409, "聊天室已归档")
    if membership.role not in {"OWNER", "MEMBER"}:
        raise HTTPException(403, "没有添加 Bot 的权限")
    if bot is None or bot.workspace_id != room.workspace_id or not bot.enabled:
        raise HTTPException(404, "Bot 不存在")
    existing = await db.scalar(select(RoomBot).where(RoomBot.room_id == rid, RoomBot.bot_id == bot.id))
    if existing:
        return {"data": {"room_id": rid, "bot_id": bot.id}}
    db.add(RoomBot(room_id=rid, bot_id=bot.id, added_by=user.id))
    await db.commit()
    return {"data": {"room_id": rid, "bot_id": bot.id}}


@router.get("/rooms/{rid}/bots")
async def list_room_bots(rid: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if not await db.scalar(select(RoomMember.id).where(RoomMember.room_id == rid, RoomMember.user_id == user.id)):
        raise HTTPException(404, "聊天室不存在")
    rows = (await db.execute(select(Bot.id, Bot.name, Bot.enabled).join(RoomBot, RoomBot.bot_id == Bot.id).where(RoomBot.room_id == rid))).all()
    return {"data": [{"id": row.id, "name": row.name, "enabled": row.enabled} for row in rows]}


@router.delete("/rooms/{rid}/bots/{bot_id}")
async def remove_room_bot(rid: str, bot_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    member = await db.scalar(select(RoomMember).where(RoomMember.room_id == rid, RoomMember.user_id == user.id))
    if member is None:
        raise HTTPException(404, "聊天室不存在")
    if member.role != "OWNER":
        raise HTTPException(403, "没有移除 Bot 的权限")
    attached = await db.scalar(select(RoomBot).where(RoomBot.room_id == rid, RoomBot.bot_id == bot_id))
    if attached is None:
        raise HTTPException(404, "Bot 不在此聊天室")
    await db.delete(attached)
    await db.commit()
    return {"data": {"removed": True}}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    run = await db.get(Run, run_id)
    if run is None or not await db.scalar(select(RoomMember.id).where(RoomMember.room_id == run.room_id, RoomMember.user_id == user.id)):
        raise HTTPException(404, "任务不存在")
    steps = (await db.scalars(select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.step_no))).all()
    return {"data": {"id": run.id, "status": run.status, "error": run.error, "steps": [{"step_no": s.step_no, "status": s.status, "output": s.output} for s in steps]}}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    run = await db.get(Run, run_id)
    if run is None or not await db.scalar(select(RoomMember.id).where(RoomMember.room_id == run.room_id, RoomMember.user_id == user.id)):
        raise HTTPException(404, "任务不存在")
    run.cancel_requested = True
    await db.commit()
    return {"data": {"id": run.id, "cancel_requested": True}}
