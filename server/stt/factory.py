"""Выбор провайдера STT по настройкам."""

from __future__ import annotations

from ..config import settings
from .base import STTProvider
from .dummy import DummyProvider
from .yandex import YandexProvider


def create_provider() -> STTProvider:
    if settings.stt_provider == "yandex":
        return YandexProvider()
    return DummyProvider()
