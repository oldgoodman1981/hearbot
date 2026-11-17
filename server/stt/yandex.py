"""Yandex SpeechKit, синхронный REST API v1.

Аудио реплики накапливается и отправляется одним запросом при finish().
Провайдер возвращает только финальный результат. Для потоковых
промежуточных результатов нужен gRPC API v3 — см. docs/ARCHITECTURE.md.

Ключ и каталог берутся из YANDEX_API_KEY / YANDEX_FOLDER_ID. API-ключ,
созданный в AI Studio, должен иметь область действия
`yc.ai.speechkitStt.execute`, а его сервисный аккаунт — роль
`ai.speechkit-stt.user` или `ai.editor` на каталоге.
"""

from __future__ import annotations

import httpx

from ..config import settings
from .base import STTError, STTProvider

AUTH_HINT = (
    "Проверьте YANDEX_API_KEY и YANDEX_FOLDER_ID: у сервисного аккаунта ключа "
    "должна быть роль ai.speechkit-stt.user (или ai.editor), а у самого ключа — "
    "область действия yc.ai.speechkitStt.execute."
)


def describe_error(response: httpx.Response) -> str:
    """Достаёт текст ошибки Yandex из ответа и добавляет подсказку."""
    try:
        payload = response.json()
    except ValueError:
        payload = None

    detail = ""
    if isinstance(payload, dict):
        detail = str(
            payload.get("error_message")
            or payload.get("message")
            or (payload.get("error") or {}).get("message")
            or ""
        )
    if not detail:
        detail = response.text.strip()[:300]
    detail = " ".join(detail.split())

    message = f"Yandex SpeechKit: HTTP {response.status_code}"
    if detail:
        message = f"{message}: {detail}"
    if response.status_code in (401, 403):
        message = f"{message}. {AUTH_HINT}"
    return message


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
        if response.status_code >= 400:
            raise STTError(describe_error(response))

        result = response.json().get("result", "")
        self._audio.clear()
        return result

    async def close(self) -> None:
        await self._client.aclose()
