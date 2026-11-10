"""WebSocket connection manager.

One hub per process. `publish()` is safe to call from sync FastAPI routes
(which run in a threadpool) because it hands the coroutine to the main loop.
"""
import asyncio
import json
from collections import defaultdict

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.user_sockets: dict[str, set[WebSocket]] = defaultdict(set)
        self.room_sockets: dict[str, set[WebSocket]] = defaultdict(set)

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    async def connect_user(self, user_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self.user_sockets[user_id].add(ws)

    async def connect_room(self, room_id: str, ws: WebSocket) -> None:
        self.room_sockets[room_id].add(ws)

    def disconnect_user(self, user_id: str, ws: WebSocket) -> None:
        self.user_sockets[user_id].discard(ws)

    def disconnect_room(self, room_id: str, ws: WebSocket) -> None:
        self.room_sockets[room_id].discard(ws)

    @staticmethod
    async def _send(sockets: set[WebSocket], payload: dict) -> None:
        dead = []
        for ws in list(sockets):
            try:
                await ws.send_text(json.dumps(payload, default=str))
            except Exception:
                dead.append(ws)
        for ws in dead:
            sockets.discard(ws)

    async def send_to_user(self, user_id: str, payload: dict) -> None:
        await self._send(self.user_sockets.get(user_id, set()), payload)

    async def send_to_room(self, room_id: str, payload: dict) -> None:
        await self._send(self.room_sockets.get(room_id, set()), payload)

    def publish(self, user_id: str, payload: dict) -> None:
        if not self.loop:
            return
        try:
            asyncio.run_coroutine_threadsafe(self.send_to_user(user_id, payload), self.loop)
        except RuntimeError:
            pass

    def publish_room(self, room_id: str, payload: dict) -> None:
        if not self.loop:
            return
        try:
            asyncio.run_coroutine_threadsafe(self.send_to_room(room_id, payload), self.loop)
        except RuntimeError:
            pass

    def online_users(self) -> list[str]:
        return [uid for uid, socks in self.user_sockets.items() if socks]


manager = ConnectionManager()
