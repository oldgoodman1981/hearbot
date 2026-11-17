"""Выбор провайдера STT по настройкам."""

from __future__ import annotations

from ..config import settings
from .base import STTProvider
from .dummy import DummyProvider
from .yandex import YandexProvider


def create_provider() -> STTProvider:
    if settings.stt_provider == "yandex":
        return YandexProvider()
    if settings.stt_provider == "vosk":
        # Импорт внутри ветки: vosk не нужен при работе с dummy/yandex.
        from .vosk import VoskProvider

        return VoskProvider()
    if settings.stt_provider == "dummy":
        return DummyProvider()

    raise ValueError(
        f"Неизвестный STT_PROVIDER={settings.stt_provider!r}. "
        "Допустимые значения: dummy, vosk, yandex."
    )
