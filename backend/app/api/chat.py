"""Internal chat: 1:1 conversations, groups, attachments and read receipts."""
import csv
import io
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import ADMIN, DEVELOPERS, get_current_user
from app.models import (Base, ChatAttachment, ChatGroupMeta, ChatMessage, ChatParticipant,
                        ChatReadReceipt, ChatRoom, TeamMember, User)
from app.services import storage
from app.services.notify import notify
from app.ws.manager import manager

router = APIRouter(prefix="/chat", tags=["Chat"])

MAX_ATTACHMENT = 20 * 1024 * 1024
RETENTION_DAYS = 365
ALLOWED_DOC_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".csv", ".zip", ".md", ".json"}


def _ensure_tables():
    try:
        from app.core.db import engine
        Base.metadata.create_all(bind=engine)
    except Exception:
        pass


_ensure_tables()


class RoomIn(BaseModel):
    type: str = "direct"
    name: str = ""
    description: str = ""
    participant_ids: list[str] = []
    team_id: str | None = None


class MessageIn(BaseModel):
    body: str = ""
    reply_to_id: str | None = None


class MembersIn(BaseModel):
    user_ids: list[str]


class GrantIn(BaseModel):
    user_id: str
    note: str = ""


# ------------------------------------------------------------------ helpers
def _is_member(db: Session, room_id: str, user_id: str) -> bool:
    return db.query(ChatParticipant).filter(ChatParticipant.room_id == room_id,
                                            ChatParticipant.user_id == user_id).one_or_none() is not None


def _require_member(db: Session, room_id: str, user: User) -> ChatRoom:
    room = db.get(ChatRoom, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not _is_member(db, room_id, user.id):
        raise HTTPException(status_code=403, detail="You are not a participant in this conversation")
    return room


def _room_access(db: Session, room_id: str, user: User, request: Request | None = None,
                 action: str = "view_conversation") -> ChatRoom:
    room = db.get(ChatRoom, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not _is_member(db, room_id, user.id):
        raise HTTPException(status_code=403, detail="You do not have access to this conversation")
    return room


def _brief(u: User | None) -> dict:
    if not u:
        return {"id": "", "full_name": "Unknown", "role": "", "avatar_color": "#64748b", "email": ""}
    return {"id": u.id, "full_name": u.full_name, "email": u.email, "role": u.role,
            "avatar_color": u.avatar_color, "employee_code": getattr(u, "employee_code", None)}


def _online() -> set[str]:
    try:
        return set(manager.online_users())
    except Exception:
        return set()


def _attachments(db: Session, message_ids: list[str]) -> dict[str, list[dict]]:
    if not message_ids:
        return {}
    out: dict[str, list[dict]] = {}
    for a in db.query(ChatAttachment).filter(ChatAttachment.message_id.in_(message_ids)).all():
        out.setdefault(a.message_id, []).append({
            "id": a.id, "file_name": a.file_name, "mime": a.mime, "size_bytes": a.size_bytes,
            "url": f"/api/chat/attachments/{a.id}"})
    return out


def _receipts(db: Session, message_ids: list[str]) -> dict[str, list[str]]:
    if not message_ids:
        return {}
    out: dict[str, list[str]] = {}
    for r in db.query(ChatReadReceipt).filter(ChatReadReceipt.message_id.in_(message_ids)).all():
        out.setdefault(r.message_id, []).append(r.user_id)
    return out


def _message_out(m: ChatMessage, sender: User | None, atts: list[dict], read_by: list[str],
                 participants: int, include_deleted: bool = False) -> dict:
    return {
        "id": m.id, "room_id": m.room_id, "body": m.body or "",
        "sender": _brief(sender), "sender_id": m.sender_id,
        "created_at": m.created_at, "edited_at": m.edited_at, "deleted_at": m.deleted_at,
        "reply_to_id": m.reply_to_id, "attachments": atts,
        "read_by": read_by, "delivered_to": max(participants - 1, 0),
        "is_deleted": m.deleted_at is not None,
    }


def _messages_payload(db: Session, rows: list[ChatMessage], participants: int,
                      include_deleted: bool = False) -> list[dict]:
    ids = [m.id for m in rows]
    atts = _attachments(db, ids)
    rec = _receipts(db, ids)
    senders: dict[str, User] = {}
    out = []
    for m in rows:
        if m.sender_id not in senders:
            senders[m.sender_id] = db.get(User, m.sender_id)
        out.append(_message_out(m, senders[m.sender_id], atts.get(m.id, []), rec.get(m.id, []),
                                participants, include_deleted))
    return out


def _room_out(db: Session, room: ChatRoom, viewer: User) -> dict:
    parts = db.query(ChatParticipant).filter(ChatParticipant.room_id == room.id).all()
    users = [db.get(User, p.user_id) for p in parts]
    users = [u for u in users if u]
    mine = next((p for p in parts if p.user_id == viewer.id), None)
    last = (db.query(ChatMessage)
            .filter(ChatMessage.room_id == room.id, ChatMessage.deleted_at.is_(None))
            .order_by(ChatMessage.created_at.desc()).first())
    unread = 0
    if mine:
        unread = (db.query(func.count(ChatMessage.id))
                  .filter(ChatMessage.room_id == room.id,
                          ChatMessage.deleted_at.is_(None),
                          ChatMessage.sender_id != viewer.id,
                          ChatMessage.created_at > (mine.last_read_at or datetime(1970, 1, 1, tzinfo=timezone.utc)))
                  .scalar() or 0)
    others = [u for u in users if u.id != viewer.id]
    title = room.name or ", ".join(u.full_name for u in others[:3]) or "Conversation"
    meta = db.get(ChatGroupMeta, room.id)
    online = _online()
    return {
        "id": room.id, "type": room.type, "name": title, "description": (meta.description if meta else ""),
        "team_id": room.team_id, "created_by": room.created_by, "created_at": room.created_at,
        "is_member": bool(mine), "participant_count": len(users),
        "participants": [{**_brief(u), "online": u.id in online} for u in users],
        "participant_ids": [u.id for u in users],
        "unread": unread,
        "last_message": (last.body if last else ""),
        "last_message_at": (last.created_at if last else room.created_at),
        "last_sender": (_brief(db.get(User, last.sender_id))["full_name"] if last else ""),
    }


def _touch(db: Session, room_id: str, user: User) -> None:
    p = db.query(ChatParticipant).filter(ChatParticipant.room_id == room_id,
                                         ChatParticipant.user_id == user.id).one_or_none()
    if p:
        p.last_read_at = datetime.now(timezone.utc)
        db.flush()


def _broadcast(room_id: str, payload: dict, user_ids: list[str]) -> None:
    manager.publish_room(room_id, payload)
    for uid in user_ids:
        manager.publish(uid, payload)


# ------------------------------------------------------------------ basics
def policy(current: User = Depends(get_current_user)):
    return {
        "notice": "WORKCODE conversations may be monitored and reviewed by authorized administrators "
                  "and designated On-Roll monitors. Chats are retained for company compliance and "
                  "every monitoring access is written to an audit log.",
        "retention_days": RETENTION_DAYS,
        "monitoring": "Admin and designated On-Roll monitors can review all conversations, including "
                      "1:1 and group chats they do not participate in.",
        "attachments": "Attachments are private, authorization-checked and capped at 20 MB.",
    }


@router.get("/presence")
def presence(current: User = Depends(get_current_user)):
    return {"online": sorted(_online())}


@router.get("/contacts")
def contacts(q: str = "", db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(User).filter(User.is_active.is_(True), User.id != user.id)
    if q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(or_(User.full_name.ilike(term), User.email.ilike(term),
                                 User.username.ilike(term) if hasattr(User, "username") else User.email.ilike(term)))
    rows = query.order_by(User.full_name).limit(300).all()
    online = _online()
    out = []
    for u in rows:
        d = _brief(u)
        d["online"] = u.id in online
        d["employee_id"] = getattr(u, "employee_code", None) or u.id[:8]
        out.append(d)
    if q.strip():
        term = q.strip().lower()
        out = [u for u in out if term in (u["full_name"] or "").lower()
               or term in (u["email"] or "").lower() or term in (u["employee_id"] or "").lower()]
    return out


@router.get("/rooms")
def my_rooms(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    parts = db.query(ChatParticipant).filter(ChatParticipant.user_id == user.id).all()
    rooms = [db.get(ChatRoom, p.room_id) for p in parts]
    out = [_room_out(db, r, user) for r in rooms if r]
    return sorted(out, key=lambda r: r["last_message_at"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


@router.post("/rooms", status_code=201)
def create_room(payload: RoomIn, request: Request, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    if payload.type not in ("direct", "group", "team"):
        raise HTTPException(status_code=422, detail="Invalid conversation type")
    ids = {i for i in payload.participant_ids if i} | {user.id}
    ids = {i for i in ids if db.get(User, i)}
    if payload.type == "direct":
        if len(ids) != 2:
            raise HTTPException(status_code=422, detail="A 1:1 chat needs exactly one other participant")
        for room in db.query(ChatRoom).filter(ChatRoom.type == "direct").all():
            members = {p.user_id for p in db.query(ChatParticipant).filter(ChatParticipant.room_id == room.id)}
            if members == ids:
                return _room_out(db, room, user)
    if payload.type == "group" and len(ids) < 2:
        raise HTTPException(status_code=422, detail="A group needs at least two members")
    if payload.type == "team":
        if not payload.team_id:
            raise HTTPException(status_code=422, detail="team_id is required for team chats")
        ids |= {m.user_id for m in db.query(TeamMember).filter(TeamMember.team_id == payload.team_id)}
    room = ChatRoom(type=payload.type, name=(payload.name or "").strip()[:160] or "",
                    team_id=payload.team_id, created_by=user.id)
    db.add(room)
    db.flush()
    for pid in ids:
        db.add(ChatParticipant(room_id=room.id, user_id=pid))
    if payload.description:
        db.add(ChatGroupMeta(room_id=room.id, description=payload.description[:400]))
    db.flush()
    _broadcast(room.id, {"channel": "room", "data": {"room_id": room.id, "action": "created"}}, list(ids))
    return _room_out(db, room, user)


@router.get("/rooms/{room_id}")
def room_detail(room_id: str, request: Request, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    room = _room_access(db, room_id, user, request)
    return _room_out(db, room, user)


@router.patch("/rooms/{room_id}")
def update_room(room_id: str, payload: RoomIn, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    room = db.get(ChatRoom, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if user.role != ADMIN and room.created_by != user.id:
        raise HTTPException(status_code=403, detail="Only the group owner or an admin can edit this group")
    if payload.name:
        room.name = payload.name.strip()[:160]
    meta = db.get(ChatGroupMeta, room_id)
    if payload.description is not None:
        if meta:
            meta.description = payload.description[:400]
        else:
            db.add(ChatGroupMeta(room_id=room_id, description=payload.description[:400]))
    db.flush()
    return _room_out(db, room, user)


@router.post("/rooms/{room_id}/members")
def add_members(room_id: str, payload: MembersIn, request: Request, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    room = db.get(ChatRoom, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if user.role != ADMIN and room.created_by != user.id:
        raise HTTPException(status_code=403, detail="Only the group owner or an admin can add members")
    if room.type == "direct":
        raise HTTPException(status_code=422, detail="Use a 1:1 chat to add a direct participant")
    added = []
    for uid in payload.user_ids:
        u = db.get(User, uid)
        if u and not _is_member(db, room_id, uid):
            db.add(ChatParticipant(room_id=room_id, user_id=uid))
            added.append(uid)
    db.flush()
    ids = [p.user_id for p in db.query(ChatParticipant).filter(ChatParticipant.room_id == room_id)]
    _broadcast(room_id, {"channel": "room", "data": {"room_id": room_id, "action": "members"}}, ids)
    return _room_out(db, room, user)


@router.delete("/rooms/{room_id}/members/{member_id}")
def remove_member(room_id: str, member_id: str, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    room = db.get(ChatRoom, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if user.role != ADMIN and room.created_by != user.id and member_id != user.id:
        raise HTTPException(status_code=403, detail="Only the group owner or an admin can remove members")
    part = db.query(ChatParticipant).filter(ChatParticipant.room_id == room_id,
                                            ChatParticipant.user_id == member_id).one_or_none()
    if not part:
        raise HTTPException(status_code=404, detail="Member not found")
    db.delete(part)
    db.flush()
    return {"detail": "removed"}


@router.get("/rooms/{room_id}/messages")
def get_messages(room_id: str, request: Request, q: str = "", before: str | None = Query(None),
                 limit: int = Query(60, ge=1, le=200), include_deleted: bool = Query(False),
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    room = _room_access(db, room_id, user, request)
    member = _is_member(db, room_id, user.id)
    query = db.query(ChatMessage).filter(ChatMessage.room_id == room_id)
    if not include_deleted:
        query = query.filter(ChatMessage.deleted_at.is_(None))
    if q.strip():
        query = query.filter(ChatMessage.body.ilike(f"%{q.strip()}%"))
    if before:
        ref = db.get(ChatMessage, before)
        if ref:
            query = query.filter(ChatMessage.created_at < ref.created_at)
    rows = query.order_by(ChatMessage.created_at.desc()).limit(limit).all()
    rows = list(reversed(rows))
    count = db.query(ChatParticipant).filter(ChatParticipant.room_id == room_id).count()
    payload = _messages_payload(db, rows, count, include_deleted)
    if member and not q.strip():
        _touch(db, room_id, user)
    return payload


@router.post("/rooms/{room_id}/messages", status_code=201)
def send_message(room_id: str, payload: MessageIn, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    _require_member(db, room_id, user)
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=422, detail="Message cannot be empty")
    msg = ChatMessage(room_id=room_id, sender_id=user.id, body=body[:4000], reply_to_id=payload.reply_to_id)
    db.add(msg)
    db.flush()
    parts = db.query(ChatParticipant).filter(ChatParticipant.room_id == room_id).all()
    ids = [p.user_id for p in parts]
    _touch(db, room_id, user)
    for uid in ids:
        if uid != user.id:
            notify(db, uid, "chat_message", f"New message from {user.full_name}", body[:120], f"/chat/{room_id}")
    out = _message_out(msg, user, [], [], len(ids))
    _broadcast(room_id, {"channel": "message", "data": out}, ids)
    return out


@router.delete("/messages/{message_id}")
def delete_message(message_id: str, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    msg = db.get(ChatMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    _require_member(db, msg.room_id, user)
    if msg.sender_id != user.id and user.role != ADMIN:
        raise HTTPException(status_code=403, detail="You can only delete your own messages")
    msg.deleted_at = datetime.now(timezone.utc)
    msg.body = "[deleted]"
    db.flush()
    ids = [p.user_id for p in db.query(ChatParticipant).filter(ChatParticipant.room_id == msg.room_id)]
    _broadcast(msg.room_id, {"channel": "message_deleted", "data": {"id": message_id, "room_id": msg.room_id}}, ids)
    return {"detail": "deleted", "id": message_id}


@router.post("/rooms/{room_id}/read")
def mark_read(room_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _require_member(db, room_id, user)
    part = db.query(ChatParticipant).filter(ChatParticipant.room_id == room_id,
                                            ChatParticipant.user_id == user.id).one_or_none()
    if part:
        part.last_read_at = datetime.now(timezone.utc)
        unread = (db.query(ChatMessage)
                  .filter(ChatMessage.room_id == room_id, ChatMessage.sender_id != user.id,
                          ChatMessage.deleted_at.is_(None)).all())
        have = {r.message_id for r in db.query(ChatReadReceipt).filter(ChatReadReceipt.user_id == user.id,
                                                                      ChatReadReceipt.room_id.is_(None) if False else
                                                                      ChatReadReceipt.message_id.in_([m.id for m in unread] or [""])).all()}
        for m in unread:
            if m.id not in have:
                db.add(ChatReadReceipt(message_id=m.id, user_id=user.id))
        db.flush()
        _broadcast(room_id, {"channel": "read", "data": {"room_id": room_id, "user_id": user.id,
                                                         "at": part.last_read_at.isoformat()}},
                   [p.user_id for p in db.query(ChatParticipant).filter(ChatParticipant.room_id == room_id)])
    return {"detail": "ok"}


@router.post("/rooms/{room_id}/attachments", status_code=201)
async def upload_attachment(room_id: str, file: UploadFile = File(...), db: Session = Depends(get_db),
                            user: User = Depends(get_current_user)):
    _require_member(db, room_id, user)
    name = (file.filename or "file").replace("\\", "/").split("/")[-1]
    safe_name = "".join(c for c in name if c.isalnum() or c in "-_. ")[:90] or "file"
    ext = ("." + safe_name.rsplit(".", 1)[-1].lower()) if "." in safe_name else ""
    mime = file.content_type or "application/octet-stream"
    if not (mime.startswith("image/") or ext in ALLOWED_DOC_EXT):
        raise HTTPException(status_code=415, detail="Unsupported attachment type")
    data = await file.read()
    if len(data) > MAX_ATTACHMENT:
        raise HTTPException(status_code=413, detail="Attachment too large (20 MB max)")
    key = f"{room_id}/{int(datetime.now(timezone.utc).timestamp())}-{safe_name}"
    target = storage.chat_dir() / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    msg = ChatMessage(room_id=room_id, sender_id=user.id, body=f"📎 {safe_name}",
                      attachment_key=key, attachment_name=safe_name)
    db.add(msg)
    db.flush()
    att = ChatAttachment(message_id=msg.id, room_id=room_id, uploader_id=user.id, file_name=safe_name,
                         mime=mime, size_bytes=len(data), storage_key=key)
    db.add(att)
    db.flush()
    _touch(db, room_id, user)
    ids = [p.user_id for p in db.query(ChatParticipant).filter(ChatParticipant.room_id == room_id)]
    out = _message_out(msg, user, [{"id": att.id, "file_name": safe_name, "mime": mime,
                                    "size_bytes": len(data), "url": f"/api/chat/attachments/{att.id}"}],
                       [], len(ids))
    _broadcast(room_id, {"channel": "message", "data": out}, ids)
    return out


@router.get("/attachments/{attachment_id}")
def download_attachment(attachment_id: str, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    att = db.get(ChatAttachment, attachment_id)
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    _room_access(db, att.room_id, user, request, action="attachment_download")
    try:
        path = storage.safe_join(storage.chat_dir(), att.storage_key)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid attachment key")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Attachment missing from storage")
    return FileResponse(str(path), filename=att.file_name, media_type=att.mime)


@router.get("/unread")
def unread_total(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    total = 0
    for p in db.query(ChatParticipant).filter(ChatParticipant.user_id == user.id).all():
        total += (db.query(func.count(ChatMessage.id))
                  .filter(ChatMessage.room_id == p.room_id, ChatMessage.deleted_at.is_(None),
                          ChatMessage.sender_id != user.id,
                          ChatMessage.created_at > (p.last_read_at or datetime(1970, 1, 1, tzinfo=timezone.utc)))
                  .scalar() or 0)
    return {"unread": total}