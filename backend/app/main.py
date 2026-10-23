from contextlib import asynccontextmanager
from contextvars import ContextVar
from collections import defaultdict, deque
from datetime import datetime, timedelta
import asyncio
import hashlib
import json
from pathlib import Path
import secrets
import time
from uuid import UUID
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings, validate_settings
from .db import Base, Bot, BotVersion, FileRecord, Invitation, LoginSession, Message, MessageAttachment, RealtimeTicket, Room, RoomBot, RoomEvent, RoomMember, RoomReadPosition, Run, SessionLocal, User, Workspace, WorkspaceMember, engine, get_db, utcnow
from .bots import router as bots_router
from .providers import head_object, model_ready, oss_ready, post_policy, read_object_prefix
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
app.include_router(bots_router)
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")
_rate_windows = defaultdict(deque)
_rate_lock = asyncio.Lock()


async def rate_limit(scope: str, key: str, count: int, seconds: int):
    now = time.monotonic()
    async with _rate_lock:
        if len(_rate_windows) > 10000:
            for old_key, old_window in list(_rate_windows.items()):
                if not old_window or old_window[-1] <= now - 3600:
                    del _rate_windows[old_key]
        if len(_rate_windows) >= 10000 and (scope, key) not in _rate_windows:
            raise HTTPException(429, "请求过多，请稍后重试")
        window = _rate_windows[(scope, key)]
        while window and window[0] <= now - seconds:
            window.popleft()
        if len(window) >= count:
            raise HTTPException(429, "操作过于频繁，请稍后重试")
        window.append(now)


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


class RoomUpdate(RoomIn):
    revision: int = Field(ge=1)


class TransferOwnerIn(BaseModel):
    user_id: str


class MentionIn(BaseModel):
    bot_id: str
    position: int = Field(ge=0, le=2)


class MessageIn(BaseModel):
    client_message_id: UUID
    text: str = Field(default="", max_length=20000)
    mentions: list[MentionIn] = Field(default_factory=list, max_length=3)
    attachment_ids: list[str] = Field(default_factory=list)


class ReadPositionIn(BaseModel):
    message_seq: int = Field(ge=0)


class UploadIn(BaseModel):
    workspace_id: str
    filename: str = Field(min_length=1, max_length=255)
    mime: str = Field(min_length=1, max_length=120)
    size: int = Field(gt=0)


class InviteIn(BaseModel):
    email: EmailStr


class AcceptInviteIn(BaseModel):
    token: str = Field(min_length=20)


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


def set_session_cookies(response: Response, refresh_token: str, csrf_token: str):
    response.set_cookie("tp_refresh", refresh_token, max_age=30*86400, httponly=True, secure=settings.cookie_secure, samesite="lax", path="/api/v1/auth")
    response.set_cookie("tp_csrf", csrf_token, max_age=30*86400, httponly=False, secure=settings.cookie_secure, samesite="lax", path="/")


async def new_session(db: AsyncSession, user_id: str, response: Response):
    refresh_token = secrets.token_urlsafe(40)
    csrf_token = secrets.token_urlsafe(24)
    db.add(LoginSession(user_id=user_id, refresh_hash=hashlib.sha256(refresh_token.encode()).hexdigest(), expires_at=utcnow()+timedelta(days=30)))
    set_session_cookies(response, refresh_token, csrf_token)


def check_csrf(request: Request):
    cookie = request.cookies.get("tp_csrf")
    header = request.headers.get("X-CSRF-Token")
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        raise HTTPException(403, "CSRF 校验失败")


@app.post("/api/v1/auth/register", status_code=201)
async def register(payload: RegisterIn, response: Response, request: Request, db: AsyncSession = Depends(get_db)):
    await rate_limit("register", request.client.host if request.client else "unknown", 100, 3600)
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
    await new_session(db, user.id, response)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "邮箱已注册") from None
    return wrap({"user": {"id": user.id, "email": user.email, "display_name": user.display_name}, "workspace_id": workspace.id, "access_token": issue_access_token(user.id)})


@app.post("/api/v1/auth/login")
async def login(payload: LoginIn, response: Response, request: Request, db: AsyncSession = Depends(get_db)):
    await rate_limit("login", f"{request.client.host if request.client else 'unknown'}:{str(payload.email).lower()}", 10, 300)
    user = await db.scalar(select(User).where(User.email == str(payload.email).lower()))
    if user is None or not password_hash.verify(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "邮箱或密码错误")
    await new_session(db, user.id, response)
    await db.commit()
    return wrap({"access_token": issue_access_token(user.id), "user": {"id": user.id, "email": user.email, "display_name": user.display_name}})


@app.post("/api/v1/auth/refresh")
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    check_csrf(request)
    token = request.cookies.get("tp_refresh")
    if not token:
        raise HTTPException(401, "会话已失效")
    session = await db.scalar(select(LoginSession).where(LoginSession.refresh_hash == hashlib.sha256(token.encode()).hexdigest()).with_for_update())
    if session is None or session.revoked_at or session.expires_at.replace(tzinfo=None) < utcnow().replace(tzinfo=None):
        raise HTTPException(401, "会话已失效")
    user = await db.get(User, session.user_id)
    if user is None:
        raise HTTPException(401, "会话已失效")
    session.revoked_at = utcnow()
    await new_session(db, user.id, response)
    await db.commit()
    return wrap({"access_token": issue_access_token(user.id)})


@app.post("/api/v1/auth/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    check_csrf(request)
    token = request.cookies.get("tp_refresh")
    if token:
        session = await db.scalar(select(LoginSession).where(LoginSession.refresh_hash == hashlib.sha256(token.encode()).hexdigest()))
        if session:
            session.revoked_at = utcnow()
            await db.commit()
    response.delete_cookie("tp_refresh", path="/api/v1/auth")
    response.delete_cookie("tp_csrf", path="/")
    return wrap({"status": "logged_out"})


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
    return wrap([{"id": r.id, "name": r.name, "revision": r.revision, "archived": r.archived_at is not None} for r in rooms])


@app.post("/api/v1/workspaces/{wid}/rooms", status_code=201)
async def create_room(wid: str, payload: RoomIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await workspace_access(db, wid, user.id)
    room = Room(workspace_id=wid, name=payload.name.strip(), created_by=user.id)
    db.add(room)
    await db.flush()
    db.add(RoomMember(room_id=room.id, user_id=user.id, role="OWNER"))
    await db.commit()
    return wrap({"id": room.id, "name": room.name, "revision": room.revision, "archived": False})


@app.patch("/api/v1/rooms/{rid}")
async def update_room(rid: str, payload: RoomUpdate, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    room = await room_access(db, rid, user.id)
    member = await db.scalar(select(RoomMember).where(RoomMember.room_id == rid, RoomMember.user_id == user.id))
    if member.role != "OWNER":
        raise HTTPException(403, "仅房主可以修改聊天室")
    if room.revision != payload.revision:
        raise HTTPException(409, "聊天室已经更新，请刷新后重试")
    room.name = payload.name.strip()
    room.revision += 1
    await db.commit()
    return wrap({"id": room.id, "name": room.name, "revision": room.revision})


@app.get("/api/v1/rooms/{rid}")
async def get_room(rid: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    room = await room_access(db, rid, user.id)
    member = await db.scalar(select(RoomMember).where(RoomMember.room_id == rid, RoomMember.user_id == user.id))
    return wrap({"id": room.id, "name": room.name, "workspace_id": room.workspace_id, "revision": room.revision, "archived": room.archived_at is not None, "role": member.role})


@app.post("/api/v1/rooms/{rid}/archive")
async def archive_room(rid: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    room = await room_access(db, rid, user.id)
    member = await db.scalar(select(RoomMember).where(RoomMember.room_id == rid, RoomMember.user_id == user.id))
    if member.role != "OWNER":
        raise HTTPException(403, "仅房主可以归档聊天室")
    if room.archived_at is None:
        room.archived_at = utcnow()
        room.revision += 1
        pending = (await db.scalars(select(Run).where(Run.room_id == rid, Run.status.in_(["PENDING", "RUNNING"])))).all()
        for run in pending:
            run.cancel_requested = True
        await db.commit()
    return wrap({"id": room.id, "archived": True, "revision": room.revision})


@app.post("/api/v1/rooms/{rid}/transfer-owner")
async def transfer_room_owner(rid: str, payload: TransferOwnerIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await room_access(db, rid, user.id)
    actor = await db.scalar(select(RoomMember).where(RoomMember.room_id == rid, RoomMember.user_id == user.id))
    target = await db.scalar(select(RoomMember).where(RoomMember.room_id == rid, RoomMember.user_id == payload.user_id))
    if actor.role != "OWNER":
        raise HTTPException(403, "仅房主可以转让聊天室")
    if target is None or target.user_id == user.id:
        raise HTTPException(422, "请选择其他房间成员")
    actor.role = "MEMBER"
    target.role = "OWNER"
    await db.commit()
    return wrap({"owner_id": target.user_id})


@app.delete("/api/v1/rooms/{rid}/members/{uid}")
async def remove_member(rid: str, uid: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await room_access(db, rid, user.id)
    actor = await db.scalar(select(RoomMember).where(RoomMember.room_id == rid, RoomMember.user_id == user.id))
    target = await db.scalar(select(RoomMember).where(RoomMember.room_id == rid, RoomMember.user_id == uid))
    if target is None:
        raise HTTPException(404, "成员不存在")
    if uid != user.id and actor.role != "OWNER":
        raise HTTPException(403, "没有移除成员的权限")
    if target.role == "OWNER":
        raise HTTPException(409, "房主须先转让房间，不能直接离开")
    await db.delete(target)
    # Pending Bot tasks must not outlive the actor's membership.
    runs = (await db.scalars(select(Run).where(Run.room_id == rid, Run.actor_id == uid, Run.status.in_(["PENDING", "RUNNING"])))).all()
    for run in runs:
        run.cancel_requested = True
    await db.commit()
    return wrap({"removed": True})


@app.post("/api/v1/rooms/{rid}/leave")
async def leave_room(rid: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return await remove_member(rid, user.id, user, db)


@app.post("/api/v1/rooms/{rid}/invitations", status_code=201)
async def invite_member(rid: str, payload: InviteIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    room = await room_access(db, rid, user.id)
    membership = await db.scalar(select(RoomMember).where(RoomMember.room_id == rid, RoomMember.user_id == user.id))
    if membership.role != "OWNER":
        raise HTTPException(403, "没有邀请成员的权限")
    await rate_limit("invite", f"{user.id}:{rid}", 30, 3600)
    token = secrets.token_urlsafe(32)
    invitation = Invitation(room_id=room.id, email=str(payload.email).lower(), token_hash=hashlib.sha256(token.encode()).hexdigest(), token_plain=token, created_by=user.id, expires_at=utcnow() + timedelta(days=7))
    db.add(invitation)
    await db.commit()
    return wrap({"id": invitation.id, "invitation_token": token, "expires_in": 604800})


@app.get("/api/v1/invitations/pending")
async def pending_invitations(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(Invitation).where(Invitation.email == user.email.lower(), Invitation.used_at.is_(None), Invitation.revoked_at.is_(None), Invitation.expires_at > utcnow()).order_by(Invitation.created_at.desc() if hasattr(Invitation, 'created_at') else Invitation.expires_at.desc()))).all()
    return wrap([{"id": item.id, "token": item.token_plain, "room_id": item.room_id, "expires_at": item.expires_at.isoformat()} for item in rows if item.token_plain])


@app.get("/api/v1/rooms/{rid}/invitations")
async def list_invitations(rid: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await room_access(db, rid, user.id)
    member = await db.scalar(select(RoomMember).where(RoomMember.room_id == rid, RoomMember.user_id == user.id))
    if member.role != "OWNER":
        raise HTTPException(403, "没有查看邀请的权限")
    rows = (await db.scalars(select(Invitation).where(Invitation.room_id == rid).order_by(Invitation.expires_at.desc()))).all()
    return wrap([{"id": item.id, "email": item.email, "used": item.used_at is not None, "revoked": item.revoked_at is not None, "expires_at": item.expires_at.isoformat()} for item in rows])


@app.delete("/api/v1/rooms/{rid}/invitations/{iid}")
async def revoke_invitation(rid: str, iid: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await room_access(db, rid, user.id)
    member = await db.scalar(select(RoomMember).where(RoomMember.room_id == rid, RoomMember.user_id == user.id))
    if member.role != "OWNER":
        raise HTTPException(403, "没有撤销邀请的权限")
    invitation = await db.get(Invitation, iid)
    if invitation is None or invitation.room_id != rid:
        raise HTTPException(404, "邀请不存在")
    if invitation.revoked_at is None:
        invitation.revoked_at = utcnow()
        await db.commit()
    return wrap({"id": iid, "revoked": True})


@app.post("/api/v1/invitations/accept")
async def accept_invite(payload: AcceptInviteIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    invitation = await db.scalar(select(Invitation).where(Invitation.token_hash == hashlib.sha256(payload.token.encode()).hexdigest()).with_for_update())
    if invitation is None or invitation.used_at or invitation.revoked_at or invitation.expires_at.replace(tzinfo=None) < utcnow().replace(tzinfo=None):
        raise HTTPException(404, "邀请不存在或已失效")
    if invitation.email != user.email:
        raise HTTPException(403, "该邀请不属于当前账户")
    room = await db.get(Room, invitation.room_id)
    member = await db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == room.workspace_id, WorkspaceMember.user_id == user.id))
    if member is None:
        db.add(WorkspaceMember(workspace_id=room.workspace_id, user_id=user.id, role="MEMBER"))
    existing = await db.scalar(select(RoomMember).where(RoomMember.room_id == room.id, RoomMember.user_id == user.id))
    if existing is None:
        db.add(RoomMember(room_id=room.id, user_id=user.id, role="MEMBER"))
    invitation.used_at = utcnow()
    await db.commit()
    return wrap({"room_id": room.id, "workspace_id": room.workspace_id})


@app.get("/api/v1/rooms/{rid}/members")
async def list_members(rid: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await room_access(db, rid, user.id)
    rows = (await db.execute(select(User.id, User.display_name, User.email, RoomMember.role).join(RoomMember, RoomMember.user_id == User.id).where(RoomMember.room_id == rid))).all()
    return wrap([{"id": row.id, "display_name": row.display_name, "email": row.email, "role": row.role} for row in rows])


@app.get("/api/v1/rooms/{rid}/messages")
async def messages(rid: str, before_seq: int | None = None, limit: int = 50, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await room_access(db, rid, user.id)
    q = select(Message).where(Message.room_id == rid)
    if before_seq is not None:
        q = q.where(Message.message_seq < before_seq)
    rows = (await db.scalars(q.order_by(Message.message_seq.desc()).limit(min(max(limit, 1), 100)))).all()
    result = []
    for m in reversed(rows):
        files = (await db.execute(select(FileRecord.id, FileRecord.filename, FileRecord.mime, FileRecord.parse_state).join(MessageAttachment, MessageAttachment.file_id == FileRecord.id).where(MessageAttachment.message_id == m.id))).all()
        result.append({"id": m.id, "message_seq": m.message_seq, "sender_id": m.sender_id, "sender_type": m.sender_type, "bot_id": m.bot_id, "body": m.body, "created_at": m.created_at.isoformat(), "attachments": [{"id": f.id, "filename": f.filename, "mime": f.mime, "parse_state": f.parse_state} for f in files]})
    return wrap(result)


@app.get("/api/v1/rooms/{rid}/bootstrap")
async def room_bootstrap(rid: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    room = await room_access(db, rid, user.id)
    room = await db.scalar(select(Room).where(Room.id == rid).with_for_update())
    member = await db.scalar(select(RoomMember).where(RoomMember.room_id == rid, RoomMember.user_id == user.id))
    history = (await messages(rid, None, 50, user, db))["data"]
    runs = (await db.scalars(select(Run).where(Run.room_id == rid, Run.status.in_(["PENDING", "RUNNING"])).order_by(Run.created_at))).all()
    read = await db.scalar(select(RoomReadPosition.message_seq).where(RoomReadPosition.room_id == rid, RoomReadPosition.user_id == user.id))
    return wrap({"room": {"id": room.id, "name": room.name, "revision": room.revision, "archived": room.archived_at is not None, "role": member.role}, "messages": history, "active_runs": [{"id": run.id, "status": run.status} for run in runs], "read_message_seq": read or 0, "snapshot_seq": room.next_event_seq - 1})


@app.post("/api/v1/rooms/{rid}/read-position")
async def update_read_position(rid: str, payload: ReadPositionIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    room = await room_access(db, rid, user.id)
    if payload.message_seq >= room.next_message_seq:
        raise HTTPException(422, "已读位置超出房间消息")
    position = await db.scalar(select(RoomReadPosition).where(RoomReadPosition.room_id == rid, RoomReadPosition.user_id == user.id).with_for_update())
    if position is None:
        position = RoomReadPosition(room_id=rid, user_id=user.id, message_seq=payload.message_seq)
        db.add(position)
    else:
        position.message_seq = max(position.message_seq, payload.message_seq)
    await db.commit()
    return wrap({"message_seq": position.message_seq})


@app.post("/api/v1/rooms/{rid}/messages", status_code=202)
async def send_message(rid: str, payload: MessageIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    room = await room_access(db, rid, user.id)
    if room.archived_at is not None:
        raise HTTPException(409, "聊天室已归档")
    if not payload.text.strip() and not payload.attachment_ids:
        raise HTTPException(422, "消息或附件不能为空")
    member = await db.scalar(select(RoomMember).where(RoomMember.room_id == rid, RoomMember.user_id == user.id))
    if member.role == "VIEWER":
        raise HTTPException(403, "只读成员不能发送消息")
    client_id = str(payload.client_message_id)
    existing = await db.scalar(select(Message).where(Message.room_id == rid, Message.sender_id == user.id, Message.client_message_id == client_id))
    if existing is not None:
        if existing.body != payload.text:
            raise HTTPException(409, "消息幂等键已用于其他内容")
        old_run = await db.scalar(select(Run).where(Run.source_message_id == existing.id))
        prior_files = (await db.scalars(select(MessageAttachment.file_id).where(MessageAttachment.message_id == existing.id))).all()
        prior_bots = [entry["bot_id"] for entry in json.loads(old_run.snapshot)["bots"]] if old_run else []
        requested_bots = [entry.bot_id for entry in sorted(payload.mentions, key=lambda entry: entry.position)]
        if sorted(prior_files) != sorted(payload.attachment_ids) or prior_bots != requested_bots:
            raise HTTPException(409, "消息幂等键已用于其他内容")
        return wrap({"message_id": existing.id, "message_seq": existing.message_seq, "run_id": old_run.id if old_run else None, "run_status": old_run.status if old_run else None})
    await rate_limit("send", f"{user.id}:{rid}", 120, 60)
    if payload.mentions:
        await rate_limit("run", f"{user.id}:{rid}", 20, 60)
    bot_snapshots = []
    attachments = []
    if len(payload.attachment_ids) > 5 or len(set(payload.attachment_ids)) != len(payload.attachment_ids):
        raise HTTPException(422, "附件数量或列表不合法")
    for fid in payload.attachment_ids:
        file = await db.get(FileRecord, fid)
        if file is None or file.workspace_id != room.workspace_id or file.uploader_id != user.id or file.upload_state != "READY":
            raise HTTPException(422, "附件不存在或尚未上传完成")
        if payload.mentions and file.parse_state != "READY":
            raise HTTPException(409, "图片或扫描件暂不支持 AI 文本读取" if file.parse_state == "UNSUPPORTED" else "附件内容尚在解析或解析失败")
        attachments.append(file)
    if payload.mentions:
        if len({m.bot_id for m in payload.mentions}) != len(payload.mentions) or len({m.position for m in payload.mentions}) != len(payload.mentions):
            raise HTTPException(422, "Bot 提及重复或顺序冲突")
        for mention in sorted(payload.mentions, key=lambda m: m.position):
            bot = await db.get(Bot, mention.bot_id)
            allowed = await db.scalar(select(RoomBot.id).where(RoomBot.room_id == rid, RoomBot.bot_id == mention.bot_id))
            if bot is None or bot.workspace_id != room.workspace_id or not bot.enabled or not allowed:
                raise HTTPException(422, "提及的 Bot 不在此聊天室")
            version = await db.get(BotVersion, bot.current_version_id)
            bot_snapshots.append({"bot_id": bot.id, "bot_name": bot.name, "version_id": version.id, "prompt": version.prompt, "model": version.model_name, "tools": json.loads(version.tool_allowlist)})
    room = await db.scalar(select(Room).where(Room.id == rid).with_for_update())
    if bot_snapshots:
        queued = await db.scalar(select(func.count(Run.id)).where(Run.room_id == rid, Run.status.in_(["PENDING", "RUNNING"])))
        if queued >= 10:
            raise HTTPException(429, "聊天室 Bot 任务队列已满")
    message = Message(room_id=rid, sender_id=user.id, client_message_id=client_id, message_seq=room.next_message_seq, body=payload.text)
    room.next_message_seq += 1
    db.add(message)
    await db.flush()
    for file in attachments:
        db.add(MessageAttachment(message_id=message.id, file_id=file.id))
    event = RoomEvent(room_id=rid, seq=room.next_event_seq, type="message.created", payload=json.dumps({"message_id": message.id, "message_seq": message.message_seq, "sender_id": user.id, "body": message.body, "attachments": [{"id": f.id, "filename": f.filename, "mime": f.mime, "parse_state": f.parse_state} for f in attachments]}, ensure_ascii=False))
    room.next_event_seq += 1
    db.add(event)
    run_id = None
    if bot_snapshots:
        run = Run(room_id=rid, actor_id=user.id, source_message_id=message.id, snapshot=json.dumps({"question": payload.text, "bots": bot_snapshots, "attachments": [{"file_id": file.id, "filename": file.filename, "text": (file.extracted_text or "")[:20000]} for file in attachments]}, ensure_ascii=False))
        db.add(run)
        await db.flush()
        run_id = run.id
    await db.commit()
    return wrap({"message_id": message.id, "message_seq": message.message_seq, "run_id": run_id, "run_status": "PENDING" if run_id else None})


@app.get("/api/v1/rooms/{rid}/events")
async def events(rid: str, after_seq: int = 0, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await room_access(db, rid, user.id)
    rows = (await db.scalars(select(RoomEvent).where(RoomEvent.room_id == rid, RoomEvent.seq > max(after_seq, 0)).order_by(RoomEvent.seq).limit(100))).all()
    return wrap([{"room_id": rid, "seq": e.seq, "type": e.type, "payload": json.loads(e.payload)} for e in rows])


@app.get("/api/v1/records")
async def search_records(room_id: str | None = None, q: str = "", before: datetime | None = None, limit: int = 50, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if len(q) > 100:
        raise HTTPException(422, "搜索词过长")
    if room_id:
        await room_access(db, room_id, user.id)
    query = select(Message, Room.name).join(Room, Room.id == Message.room_id).join(RoomMember, RoomMember.room_id == Room.id).where(RoomMember.user_id == user.id)
    if room_id:
        query = query.where(Message.room_id == room_id)
    if q:
        query = query.where(Message.body.contains(q, autoescape=True))
    if before:
        query = query.where(Message.created_at < before)
    rows = (await db.execute(query.order_by(Message.created_at.desc(), Message.id.desc()).limit(min(max(limit, 1), 100)))).all()
    return wrap([{"id": message.id, "room_id": message.room_id, "room_name": room_name, "message_seq": message.message_seq, "sender_type": message.sender_type, "body": message.body, "created_at": message.created_at.isoformat()} for message, room_name in rows])


@app.post("/api/v1/realtime/tickets")
async def realtime_ticket(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    token = secrets.token_urlsafe(32)
    db.add(RealtimeTicket(user_id=user.id, token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=utcnow() + timedelta(seconds=30)))
    await db.commit()
    return wrap({"ticket": token, "expires_in": 30})


@app.websocket("/api/v1/realtime")
async def realtime(websocket: WebSocket):
    origin = websocket.headers.get("origin")
    allowed_origins = {settings.app_origin, "app://ui"}
    if settings.app_env == "development":
        allowed_origins.update({"http://localhost:5174", "http://127.0.0.1:5174"})
    if origin and origin not in allowed_origins:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    try:
        hello = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        token = hello.get("ticket") if isinstance(hello, dict) else None
        if not isinstance(hello, dict) or hello.get("type") != "auth" or not isinstance(token, str):
            await websocket.close(code=4401)
            return
        async with SessionLocal() as db:
            ticket = await db.scalar(select(RealtimeTicket).where(RealtimeTicket.token_hash == hashlib.sha256(token.encode()).hexdigest()).with_for_update())
            if ticket is None or ticket.used_at is not None or ticket.expires_at.replace(tzinfo=None) < utcnow().replace(tzinfo=None):
                await websocket.close(code=4401)
                return
            ticket.used_at = utcnow()
            user_id = ticket.user_id
            await db.commit()
        await websocket.send_json({"type": "authenticated"})
        room_id = None
        after_seq = 0
        while True:
            try:
                command = await asyncio.wait_for(websocket.receive_json(), timeout=0.25)
                if command.get("type") == "subscribe":
                    requested = command.get("room_id")
                    if not isinstance(requested, str):
                        await websocket.send_json({"type": "error", "code": "INVALID_ROOM"})
                        continue
                    async with SessionLocal() as db:
                        try:
                            await room_access(db, requested, user_id)
                        except HTTPException:
                            await websocket.send_json({"type": "error", "code": "ROOM_NOT_FOUND"})
                            continue
                    room_id = requested
                    after_seq = max(int(command.get("after_seq", 0)), 0)
                    await websocket.send_json({"type": "subscribed", "room_id": room_id})
                elif command.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                pass
            if room_id:
                async with SessionLocal() as db:
                    if not await db.scalar(select(RoomMember.id).where(RoomMember.room_id == room_id, RoomMember.user_id == user_id)):
                        await websocket.close(code=4403)
                        return
                    rows = (await db.scalars(select(RoomEvent).where(RoomEvent.room_id == room_id, RoomEvent.seq > after_seq).order_by(RoomEvent.seq).limit(100))).all()
                    for event in rows:
                        await websocket.send_json({"type": event.type, "room_id": room_id, "seq": event.seq, "payload": json.loads(event.payload)})
                        after_seq = event.seq
    except (WebSocketDisconnect, asyncio.TimeoutError):
        return


@app.post("/api/v1/uploads/init", status_code=201)
async def init_upload(payload: UploadIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await workspace_access(db, payload.workspace_id, user.id)
    if payload.size > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "文件超过上传限制")
    if not oss_ready():
        raise HTTPException(503, "对象存储尚未配置")
    file_id = str(uuid4())
    suffix = Path(payload.filename).suffix.lower()
    allowed_mimes = {".pdf": {"application/pdf"}, ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}, ".txt": {"text/plain"}, ".md": {"text/markdown", "text/plain"}, ".png": {"image/png"}, ".jpg": {"image/jpeg"}, ".jpeg": {"image/jpeg"}}
    if suffix not in allowed_mimes:
        raise HTTPException(422, "不支持此文件类型")
    if payload.mime not in allowed_mimes[suffix]:
        raise HTTPException(422, "文件类型与扩展名不匹配")
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
    try:
        prefix = await run_in_threadpool(read_object_prefix, record.object_key)
    except Exception:
        raise HTTPException(502, "OSS 文件内容验证失败") from None
    suffix = Path(record.filename).suffix.lower()
    if (suffix == ".pdf" and not prefix.startswith(b"%PDF-")) or (suffix == ".docx" and not prefix.startswith(b"PK\x03\x04")) or (suffix == ".png" and not prefix.startswith(b"\x89PNG\r\n\x1a\n")) or (suffix in {".jpg", ".jpeg"} and not prefix.startswith(b"\xff\xd8\xff")):
        raise HTTPException(422, "文件内容与类型不匹配")
    record.upload_state = "READY"
    record.parse_state = "UNSUPPORTED" if Path(record.filename).suffix.lower() in {".png", ".jpg", ".jpeg"} else "PENDING"
    record.etag = result.headers.get("ETag")
    await db.commit()
    return wrap({"file_id": fid, "status": "READY"})


@app.post("/api/v1/uploads/{fid}/resign")
async def resign_upload(fid: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    record = await db.get(FileRecord, fid)
    if record is None or record.uploader_id != user.id:
        raise HTTPException(404, "文件不存在")
    await workspace_access(db, record.workspace_id, user.id)
    if record.upload_state != "PENDING":
        raise HTTPException(409, "上传会话已完成")
    return wrap({"file_id": fid, "upload": post_policy(record.object_key, record.size)})


@app.get("/api/v1/files/{fid}/access")
async def file_access(fid: str, mode: str = "preview", user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if mode not in {"preview", "download"}:
        raise HTTPException(422, "访问方式不支持")
    record = await db.get(FileRecord, fid)
    if record is None or record.upload_state != "READY":
        raise HTTPException(404, "文件不存在")
    # 发送前上传者仍须属于原工作空间；发送后只允许关联房间当前成员访问。
    attached = bool(await db.scalar(select(MessageAttachment.id).where(MessageAttachment.file_id == fid)))
    allowed = bool(await db.scalar(select(MessageAttachment.id).join(Message, Message.id == MessageAttachment.message_id).join(RoomMember, RoomMember.room_id == Message.room_id).where(MessageAttachment.file_id == fid, RoomMember.user_id == user.id)))
    if not attached and record.uploader_id == user.id:
        allowed = bool(await db.scalar(select(WorkspaceMember.id).where(WorkspaceMember.workspace_id == record.workspace_id, WorkspaceMember.user_id == user.id)))
    if not allowed:
        raise HTTPException(404, "文件不存在")
    from .providers import signed_file_url
    url = await run_in_threadpool(signed_file_url, record.object_key, mode, record.filename)
    return wrap({"url": url, "expires_in": 60, "parse_state": record.parse_state})
