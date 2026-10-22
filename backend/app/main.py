from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings, validate_settings
from .db import Base, FileRecord, Message, Room, RoomMember, User, Workspace, WorkspaceMember, engine, get_db
from .providers import head_object, model_ready, oss_ready, post_policy
from .security import current_user, issue_access_token, password_hash


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_settings()
    if settings.app_env == "development":
        # 开发数据库可快速启动；生产改用 Alembic 迁移，不自动建表。
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="同频 AI API", version="0.1.0", lifespan=lifespan)
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")


@app.middleware("http")
async def with_request_id(request: Request, call_next):
    request_id = str(uuid4())
    token = request_id_ctx.set(request_id)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        request_id_ctx.reset(token)


class RegisterIn(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RoomIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)


class MessageIn(BaseModel):
    client_message_id: UUID
    text: str = Field(min_length=1, max_length=20000)
    mentions: list[dict] = Field(default_factory=list)
    attachment_ids: list[str] = Field(default_factory=list)


class UploadIn(BaseModel):
    workspace_id: str
    filename: str = Field(min_length=1, max_length=255)
    mime: str = Field(min_length=1, max_length=120)
    size: int = Field(gt=0)


def wrap(data: object) -> dict:
    return {"data": data, "request_id": request_id_ctx.get()}


@app.exception_handler(HTTPException)
async def api_error(_: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": "REQUEST_REJECTED", "message": str(exc.detail)}, "request_id": request_id_ctx.get()})


@app.get("/api/v1/health/live")
async def live():
    return wrap({"status": "ok"})


@app.get("/api/v1/health/ready")
async def ready(db: AsyncSession = Depends(get_db)):
    await db.execute(select(User.id).limit(1))
    return wrap({"status": "ok", "database": "ready", "model_configured": model_ready(), "oss_configured": oss_ready()})


@app.post("/api/v1/auth/register", status_code=201)
async def register(payload: RegisterIn, db: AsyncSession = Depends(get_db)):
    email = str(payload.email).lower()
    if await db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(409, "邮箱已注册")
    user = User(email=email, display_name=payload.display_name.strip(), password_hash=password_hash.hash(payload.password))
    db.add(user)
    await db.flush()
    workspace = Workspace(name=f"{user.display_name}的工作空间", owner_id=user.id)
    db.add(workspace)
    await db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="OWNER"))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "邮箱已注册") from None
    return wrap({"user": {"id": user.id, "email": user.email, "display_name": user.display_name}, "workspace_id": workspace.id, "access_token": issue_access_token(user.id)})


@app.post("/api/v1/auth/login")
async def login(payload: LoginIn, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).where(User.email == str(payload.email).lower()))
    if user is None or not password_hash.verify(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "邮箱或密码错误")
    return wrap({"access_token": issue_access_token(user.id), "user": {"id": user.id, "email": user.email, "display_name": user.display_name}})


@app.get("/api/v1/me")
async def me(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    memberships = (await db.scalars(select(WorkspaceMember).where(WorkspaceMember.user_id == user.id))).all()
    return wrap({"id": user.id, "email": user.email, "display_name": user.display_name, "workspaces": [{"id": m.workspace_id, "role": m.role} for m in memberships]})


async def workspace_access(db: AsyncSession, workspace_id: str, user_id: str) -> WorkspaceMember:
    membership = await db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id))
    if membership is None:
        raise HTTPException(404, "工作空间不存在")
    return membership


async def room_access(db: AsyncSession, room_id: str, user_id: str) -> Room:
    room = await db.get(Room, room_id)
    if room is None or not await db.scalar(select(RoomMember.id).where(RoomMember.room_id == room_id, RoomMember.user_id == user_id)):
        raise HTTPException(404, "聊天室不存在")
    return room


@app.get("/api/v1/workspaces/{wid}/rooms")
async def list_rooms(wid: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await workspace_access(db, wid, user.id)
    rooms = (await db.scalars(select(Room).join(RoomMember, RoomMember.room_id == Room.id).where(Room.workspace_id == wid, RoomMember.user_id == user.id).order_by(Room.created_at.desc()))).all()
    return wrap([{"id": r.id, "name": r.name, "revision": r.revision} for r in rooms])


@app.post("/api/v1/workspaces/{wid}/rooms", status_code=201)
async def create_room(wid: str, payload: RoomIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await workspace_access(db, wid, user.id)
    room = Room(workspace_id=wid, name=payload.name.strip(), created_by=user.id)
    db.add(room)
    await db.flush()
    db.add(RoomMember(room_id=room.id, user_id=user.id, role="OWNER"))
    await db.commit()
    return wrap({"id": room.id, "name": room.name, "revision": room.revision})


@app.get("/api/v1/rooms/{rid}/messages")
async def messages(rid: str, before_seq: int | None = None, limit: int = 50, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await room_access(db, rid, user.id)
    q = select(Message).where(Message.room_id == rid)
    if before_seq is not None:
        q = q.where(Message.message_seq < before_seq)
    rows = (await db.scalars(q.order_by(Message.message_seq.desc()).limit(min(max(limit, 1), 100)))).all()
    return wrap([{"id": m.id, "message_seq": m.message_seq, "sender_id": m.sender_id, "body": m.body, "created_at": m.created_at.isoformat()} for m in reversed(rows)])


@app.post("/api/v1/rooms/{rid}/messages", status_code=202)
async def send_message(rid: str, payload: MessageIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    # 此阶段只提交人类消息。Bot、文件需后续模块校验后才能启用。
    if payload.mentions or payload.attachment_ids:
        raise HTTPException(501, "Bot 与文件接口尚未启用")
    room = await room_access(db, rid, user.id)
    client_id = str(payload.client_message_id)
    existing = await db.scalar(select(Message).where(Message.room_id == rid, Message.sender_id == user.id, Message.client_message_id == client_id))
    if existing is not None:
        if existing.body != payload.text:
            raise HTTPException(409, "消息幂等键已用于其他内容")
        return wrap({"message_id": existing.id, "message_seq": existing.message_seq})
    # 并发序号事务与事件广播在下一阶段补齐；当前先保证单进程持久化闭环。
    message = Message(room_id=rid, sender_id=user.id, client_message_id=client_id, message_seq=room.next_message_seq, body=payload.text)
    room.next_message_seq += 1
    db.add(message)
    await db.commit()
    return wrap({"message_id": message.id, "message_seq": message.message_seq})


@app.post("/api/v1/uploads/init", status_code=201)
async def init_upload(payload: UploadIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await workspace_access(db, payload.workspace_id, user.id)
    if payload.size > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "文件超过上传限制")
    if not oss_ready():
        raise HTTPException(503, "对象存储尚未配置")
    file_id = str(uuid4())
    suffix = Path(payload.filename).suffix.lower()
    if suffix not in {".pdf", ".docx", ".txt", ".md", ".png", ".jpg", ".jpeg"}:
        raise HTTPException(422, "不支持此文件类型")
    object_key = f"tongpin/{payload.workspace_id}/uploads/{file_id}/source{suffix}"
    signed = post_policy(object_key, payload.size)
    db.add(FileRecord(id=file_id, workspace_id=payload.workspace_id, uploader_id=user.id, object_key=object_key, filename=payload.filename, mime=payload.mime, size=payload.size))
    await db.commit()
    return wrap({"file_id": file_id, "upload": signed})


@app.post("/api/v1/uploads/{fid}/complete")
async def complete_upload(fid: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    record = await db.get(FileRecord, fid)
    if record is None or record.uploader_id != user.id:
        raise HTTPException(404, "文件不存在")
    await workspace_access(db, record.workspace_id, user.id)
    if record.upload_state == "READY":
        return wrap({"file_id": fid, "status": "READY"})
    try:
        result = await run_in_threadpool(head_object, record.object_key)
    except Exception:
        raise HTTPException(502, "OSS 文件验证失败") from None
    if int(result.headers.get("Content-Length", -1)) != record.size:
        raise HTTPException(422, "文件大小不匹配")
    record.upload_state = "READY"
    record.etag = result.headers.get("ETag")
    await db.commit()
    return wrap({"file_id": fid, "status": "READY"})
