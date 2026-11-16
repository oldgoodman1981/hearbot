"""Yandex SpeechKit, синхронный REST API v1.

Аудио реплики накапливается и отправляется одним запросом при finish().
Провайдер возвращает только финальный результат. Для потоковых
промежуточных результатов нужен gRPC API v3 — см. docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import httpx

from ..config import settings
from .base import STTProvider


class YandexProvider(STTProvider):
    def __init__(self) -> None:
        if not settings.yandex_api_key or not settings.yandex_folder_id:
            raise RuntimeError(
                "Для STT_PROVIDER=yandex задайте YANDEX_API_KEY и YANDEX_FOLDER_ID"
            )
        self._audio = bytearray()
        self._language = settings.default_language
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))

    async def start(self, language: str) -> None:
        self._audio.clear()
        self._language = language or settings.default_language

    async def send_audio(self, chunk: bytes) -> None:
        self._audio.extend(chunk)

    async def finish(self) -> str:
        if not self._audio:
            return ""

        params = {
            "folderId": settings.yandex_folder_id,
            "lang": self._language,
            "format": "lpcm",
            "sampleRateHertz": "16000",
        }
        headers = {"Authorization": f"Api-Key {settings.yandex_api_key}"}

        response = await self._client.post(
            settings.yandex_endpoint,
            params=params,
            headers=headers,
            content=bytes(self._audio),
        )
        response.raise_for_status()
        result = response.json().get("result", "")
        self._audio.clear()
        return result

    async def close(self) -> None:
        await self._client.aclose()
