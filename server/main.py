"""Hearbot: WebSocket-сервер потокового распознавания речи.

Протокол кратко (подробнее — docs/PROTOCOL.md):
  * клиент подключается к /ws/stt?lang=ru-RU&platform=windows
  * шлёт бинарные чанки PCM 16-bit mono 16 kHz
  * для завершения реплики шлёт текстовое сообщение {"type": "stop"}
  * сервер отвечает JSON-сообщениями ready / partial / final / error / control

Панель управления: http://127.0.0.1:8000/admin
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse

from .admin_api import create_router
from .config import settings
from .manager import ConnectionManager
from .stt.factory import create_provider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("hearbot")

app = FastAPI(title="Hearbot STT server", version="0.2.0")

manager = ConnectionManager()
app.include_router(create_router(manager))

DASHBOARD_PATH = Path(__file__).parent / "static" / "dashboard.html"

BYTES_PER_SECOND = 16000 * 2  # PCM 16-bit mono 16 kHz
MAX_UTTERANCE_BYTES = int(settings.max_utterance_seconds * BYTES_PER_SECOND)


def _authorized(token: str | None) -> bool:
    if settings.allow_anonymous or not settings.access_token:
        return True
    return token == settings.access_token


# --- Панель управления ------------------------------------------------


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/admin")


@app.get("/admin", include_in_schema=False)
async def admin_page() -> HTMLResponse:
    if not DASHBOARD_PATH.exists():
        return HTMLResponse("<h1>dashboard.html не найден</h1>", status_code=500)
    return HTMLResponse(DASHBOARD_PATH.read_text(encoding="utf-8"))


@app.get("/health")
async def health() -> dict:
    """Проверка живости сервера и текущей конфигурации."""
    return {
        "status": "ok",
        "provider": settings.stt_provider,
        "language": settings.default_language,
        "clients_online": len(manager.clients()),
        "max_utterance_seconds": settings.max_utterance_seconds,
    }


# --- WebSocket распознавания ------------------------------------------


@app.websocket("/ws/stt")
async def stt_socket(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    lang: str | None = Query(default=None),
    platform: str | None = Query(default=None),
) -> None:
    if not _authorized(token):
        await websocket.close(code=4401, reason="unauthorized")
        return

    await websocket.accept()
    language = lang or settings.default_language

    try:
        provider = create_provider()
    except Exception as exc:  # например, не заданы ключи Yandex
        logger.exception("Не удалось создать провайдер STT")
        manager.record_error(None, str(exc))
        await websocket.send_json({"type": "error", "message": str(exc), "is_final": True})
        await websocket.close(code=1011)
        return

    session = await manager.add(websocket, platform or "unknown", language)
    audio_bytes = 0

    try:
        await provider.start(language)
        await manager.send_json(
            session,
            {
                "type": "ready",
                "client_id": session.id,
                "provider": settings.stt_provider,
                "language": language,
                "sample_rate": 16000,
                "format": "pcm_s16le_mono",
                "max_utterance_seconds": settings.max_utterance_seconds,
            },
        )

        while True:
            message = await websocket.receive()

            if message["type"] == "websocket.disconnect":
                break

            chunk = message.get("bytes")
            if chunk is not None:
                if audio_bytes + len(chunk) > MAX_UTTERANCE_BYTES:
                    logger.warning(
                        "Реплика превысила лимит (%s с), сбрасываю",
                        settings.max_utterance_seconds,
                    )
                    audio_bytes = 0
                    await provider.start(language)
                    manager.record_error(session, "utterance too long")
                    await manager.send_json(
                        session,
                        {
                            "type": "error",
                            "message": "utterance too long",
                            "is_final": True,
                        },
                    )
                    continue
                audio_bytes += len(chunk)
                await provider.send_audio(chunk)
                manager.add_audio(session, len(chunk) / BYTES_PER_SECOND)
                continue

            text = message.get("text")
            if not text:
                continue

            try:
                command = json.loads(text)
            except json.JSONDecodeError:
                command = {"type": text.strip()}

            command_type = command.get("type")

            if command_type == "ping":
                await manager.send_json(session, {"type": "pong"})
                continue

            if command_type != "stop":
                continue

            seconds = round(audio_bytes / BYTES_PER_SECOND, 2)
            audio_bytes = 0

            if seconds == 0:
                session.recording = False
                await manager.send_json(
                    session,
                    {"type": "final", "text": "", "is_final": True, "audio_seconds": 0},
                )
                continue

            try:
                recognized = await provider.finish()
            except Exception:
                logger.exception("Ошибка распознавания")
                await provider.start(language)
                manager.record_error(session, "recognition failed")
                await manager.send_json(
                    session,
                    {
                        "type": "error",
                        "message": "recognition failed",
                        "is_final": True,
                    },
                )
                continue

            manager.record_final(session, recognized, seconds)
            await manager.send_json(
                session,
                {
                    "type": "final",
                    "text": recognized,
                    "is_final": True,
                    "audio_seconds": seconds,
                },
            )
            await provider.start(language)

    except WebSocketDisconnect:
        logger.info("Клиент %s отключился", session.id)
    except Exception:
        logger.exception("Неожиданная ошибка WebSocket-сессии")
    finally:
        manager.remove(session)
        try:
            await provider.close()
        except Exception:
            logger.exception("Ошибка при закрытии провайдера")


def main() -> None:
    import uvicorn

    uvicorn.run("server.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
