"""Учёт активных клиентов и статистики для панели управления."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque

from .config import settings


@dataclass
class ClientSession:
    """Активное WebSocket-подключение клиента."""

    id: str
    platform: str
    language: str
    remote: str
    connected_at: float
    websocket: Any
    recording: bool = True
    audio_seconds: float = 0.0
    last_text: str = ""
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "platform": self.platform,
            "language": self.language,
            "remote": self.remote,
            "connected_at": self.connected_at,
            "recording": self.recording,
            "audio_seconds": round(self.audio_seconds, 2),
            "last_text": self.last_text,
        }


class ConnectionManager:
    """Реестр клиентов, события и агрегированная статистика."""

    def __init__(self, history_size: int = 100) -> None:
        self._clients: dict[str, ClientSession] = {}
        self._events: Deque[dict[str, Any]] = deque(maxlen=history_size)
        self.started_at = time.time()
        self.total_utterances = 0
        self.total_audio_seconds = 0.0
        self.total_errors = 0

    # --- Клиенты -----------------------------------------------------

    async def add(self, websocket: Any, platform: str, language: str) -> ClientSession:
        session = ClientSession(
            id=uuid.uuid4().hex[:8],
            platform=platform or "unknown",
            language=language,
            remote=self._remote(websocket),
            connected_at=time.time(),
            websocket=websocket,
        )
        self._clients[session.id] = session
        self._event("connect", session, text=f"{session.platform} подключён")
        return session

    def remove(self, session: ClientSession) -> None:
        if self._clients.pop(session.id, None) is not None:
            self._event("disconnect", session, text="клиент отключён")

    def get(self, client_id: str) -> ClientSession | None:
        return self._clients.get(client_id)

    def clients(self) -> list[dict[str, Any]]:
        return [session.snapshot() for session in self._clients.values()]

    # --- Метрики -----------------------------------------------------

    def add_audio(self, session: ClientSession, seconds: float) -> None:
        session.recording = True
        session.audio_seconds += seconds
        self.total_audio_seconds += seconds

    def record_final(self, session: ClientSession, text: str, seconds: float) -> None:
        session.recording = False
        session.last_text = text or ""
        self.total_utterances += 1
        if text:
            self._event("final", session, text=text, seconds=seconds)

    def record_error(self, session: ClientSession | None, message: str) -> None:
        self.total_errors += 1
        if session is not None:
            session.recording = False
            self._event("error", session, text=message)
            return
        self._events.append(
            {
                "type": "error",
                "at": time.time(),
                "client_id": None,
                "platform": None,
                "text": message,
                "seconds": None,
            }
        )

    # --- Управление клиентом -----------------------------------------

    async def send_json(self, session: ClientSession, payload: dict[str, Any]) -> None:
        """Единственная точка отправки — защищает сокет от параллельных записей."""
        async with session.send_lock:
            await session.websocket.send_json(payload)

    async def send_control(self, session: ClientSession, action: str) -> bool:
        if action not in {"start_recording", "stop_recording"}:
            raise ValueError(f"Неизвестное действие: {action}")

        try:
            await self.send_json(session, {"type": "control", "action": action})
        except Exception:
            return False

        session.recording = action == "start_recording"
        return True

    async def disconnect(self, session: ClientSession) -> None:
        try:
            async with session.send_lock:
                await session.websocket.close(code=1000, reason="disconnected by admin")
        except Exception:
            pass

    # --- Снимок состояния --------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        online = len(self._clients)
        return {
            "server": {
                "provider": settings.stt_provider,
                "language": settings.default_language,
                "max_utterance_seconds": settings.max_utterance_seconds,
                "admin_auth_required": bool(settings.admin_token),
            },
            "stats": {
                "uptime_seconds": round(time.time() - self.started_at, 1),
                "clients_online": online,
                "total_utterances": self.total_utterances,
                "total_audio_seconds": round(self.total_audio_seconds, 2),
                "errors": self.total_errors,
            },
            "clients": self.clients(),
            "events": list(reversed(self._events))[:60],
        }

    # --- Внутреннее ---------------------------------------------------

    @staticmethod
    def _remote(websocket: Any) -> str:
        client = getattr(websocket, "client", None)
        if client is None:
            return "unknown"
        host = getattr(client, "host", None) or "unknown"
        port = getattr(client, "port", None)
        return f"{host}:{port}" if port else str(host)

    def _event(
        self,
        kind: str,
        session: ClientSession,
        text: str,
        seconds: float | None = None,
    ) -> None:
        self._events.append(
            {
                "type": kind,
                "at": time.time(),
                "client_id": session.id,
                "platform": session.platform,
                "text": text,
                "seconds": seconds,
            }
        )
