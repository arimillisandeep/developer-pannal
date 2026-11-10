"""WebSocket endpoints: live notifications and chat."""
import json

import jwt
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.db import SessionLocal
from app.core.security import decode_token
from app.models import ChatParticipant, ChatRoom, User
from app.ws.manager import manager

router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = Query(...)):
    """Single multiplexed socket: notifications by default, `subscribe` for rooms."""
    try:
        claims = decode_token(token, expected_type="access")
    except jwt.PyJWTError:
        await ws.close(code=4401)
        return

    db = SessionLocal()
    try:
        user = db.get(User, claims.get("sub"))
        if not user or not user.is_active:
            await ws.close(code=4403)
            return
        user_id = user.id
    finally:
        db.close()

    await manager.connect_user(user_id, ws)
    await ws.send_text(json.dumps({"channel": "ready", "data": {"user_id": user_id}}))

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            action = msg.get("action")
            if action == "subscribe":
                room_id = msg.get("room_id")
                db = SessionLocal()
                try:
                    member = db.query(ChatParticipant).filter(
                        ChatParticipant.room_id == room_id,
                        ChatParticipant.user_id == user_id).one_or_none()
                    room = db.get(ChatRoom, room_id)
                finally:
                    db.close()
                if member or (room and room.type == "task"):
                    await manager.connect_room(room_id, ws)
                    await ws.send_text(json.dumps({"channel": "subscribed", "data": {"room_id": room_id}}))

            elif action == "typing":
                await manager.send_to_room(msg.get("room_id", ""), {
                    "channel": "typing",
                    "data": {"room_id": msg.get("room_id"), "user_id": user_id},
                })

            elif action == "ping":
                await ws.send_text(json.dumps({"channel": "pong", "data": {}}))

    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect_user(user_id, ws)
        for rid in list(manager.room_sockets.keys()):
            manager.disconnect_room(rid, ws)
