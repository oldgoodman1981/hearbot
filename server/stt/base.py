"""Интерфейс провайдеров распознавания речи."""

from __future__ import annotations

from abc import ABC, abstractmethod


class STTError(RuntimeError):
    """Ошибка провайдера с сообщением, понятным пользователю."""


class STTProvider(ABC):
    """Базовый интерфейс провайдера STT.

    Одна сессия соответствует одной реплике пользователя (utterance):
    start() -> send_audio()* -> finish(). После finish() можно снова
    вызвать start() и начать новую реплику в рамках того же соединения.
    """

    @abstractmethod
    async def start(self, language: str) -> None:
        """Подготовить сессию распознавания для указанного языка."""

    @abstractmethod
    async def send_audio(self, chunk: bytes) -> None:
        """Передать очередной чанк PCM 16-bit, mono, 16 кГц."""

    @abstractmethod
    async def finish(self) -> str:
        """Завершить сессию и вернуть распознанный текст."""

    async def close(self) -> None:
        """Освободить ресурсы. По умолчанию ничего не делает."""
