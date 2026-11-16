"""Hearbot: WebSocket-сервер потокового распознавания речи.

Протокол кратко (подробнее — docs/PROTOCOL.md):
  * клиент подключается к /ws/stt?lang=ru-RU
  * шлёт бинарные чанки PCM 16-bit mono 16 kHz
  * для завершения реплики шлёт текстовое сообщение {"type": "stop"}
  * сервер отвечает JSON-сообщениями ready / partial / final / error
"""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect

from .config import settings
from .stt.factory import create_provider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("hearbot")

app = FastAPI(title="Hearbot STT server", version="0.1.0")

BYTES_PER_SECOND = 16000 * 2  # PCM 16-bit mono 16 kHz
MAX_UTTERANCE_BYTES = int(settings.max_utterance_seconds * BYTES_PER_SECOND)


def _authorized(token: str | None) -> bool:
    if settings.allow_anonymous or not settings.access_token:
        return True
    return token == settings.access_token


@app.get("/health")
async def health() -> dict:
    """Проверка живости сервера и текущей конфигурации."""
    return {
        "status": "ok",
        "provider": settings.stt_provider,
        "language": settings.default_language,
        "max_utterance_seconds": settings.max_utterance_seconds,
    }


@app.websocket("/ws/stt")
async def stt_socket(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    lang: str | None = Query(default=None),
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
        await websocket.send_json({"type": "error", "message": str(exc), "is_final": True})
        await websocket.close(code=1011)
        return

    audio_bytes = 0

    try:
        await provider.start(language)
        await websocket.send_json(
            {
                "type": "ready",
                "provider": settings.stt_provider,
                "language": language,
                "sample_rate": 16000,
                "format": "pcm_s16le_mono",
                "max_utterance_seconds": settings.max_utterance_seconds,
            }
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
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "utterance too long",
                            "is_final": True,
                        }
                    )
                    continue
                audio_bytes += len(chunk)
                await provider.send_audio(chunk)
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
                await websocket.send_json({"type": "pong"})
                continue

            if command_type != "stop":
                continue

            seconds = round(audio_bytes / BYTES_PER_SECOND, 2)
            audio_bytes = 0

            if seconds == 0:
                await websocket.send_json(
                    {"type": "final", "text": "", "is_final": True, "audio_seconds": 0}
                )
                continue

            try:
                recognized = await provider.finish()
            except Exception:
                logger.exception("Ошибка распознавания")
                await provider.start(language)
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": "recognition failed",
                        "is_final": True,
                    }
                )
                continue

            await websocket.send_json(
                {
                    "type": "final",
                    "text": recognized,
                    "is_final": True,
                    "audio_seconds": seconds,
                }
            )
            await provider.start(language)

    except WebSocketDisconnect:
        logger.info("Клиент отключился")
    except Exception:
        logger.exception("Неожиданная ошибка WebSocket-сессии")
    finally:
        try:
            await provider.close()
        except Exception:
            logger.exception("Ошибка при закрытии провайдера")


def main() -> None:
    import uvicorn

    uvicorn.run("server.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
