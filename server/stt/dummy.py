"""Провайдер-заглушка для локальной разработки без облачных ключей."""

from __future__ import annotations

from .base import STTProvider


class DummyProvider(STTProvider):
    """Ничего не распознаёт, но сообщает длительность полученного аудио.

    Позволяет проверить работу WebSocket, клиентов и протокола, не
    расходуя бесплатные минуты облачного сервиса.
    """

    def __init__(self, sample_rate: int = 16000, sample_width: int = 2) -> None:
        self._sample_rate = sample_rate
        self._sample_width = sample_width
        self._bytes = 0

    async def start(self, language: str) -> None:
        self._bytes = 0

    async def send_audio(self, chunk: bytes) -> None:
        self._bytes += len(chunk)

    async def finish(self) -> str:
        seconds = self._bytes / (self._sample_rate * self._sample_width)
        return f"[dummy] получено {seconds:.1f} с аудио (провайдер не настроен)"
