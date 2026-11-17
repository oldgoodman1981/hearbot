"""Vosk — офлайн-распознавание русской речи без ключей и интернета.

Модель загружается один раз на процесс и переиспользуется всеми сессиями:
`vosk.Model` потокобезопасен, а `KaldiRecognizer` создаётся на каждую реплику.
Аудио приходит чанками PCM 16-bit, mono, 16 кГц — ровно то, что ждёт модель.

Модель по умолчанию — `vosk-model-small-ru-0.22` (~45 МБ). Скачать её можно
скриптом `python scripts/download_vosk_model.py` или вручную с
https://alphacephei.com/vosk/models и указать путь в `VOSK_MODEL_PATH`.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

from ..config import PROJECT_ROOT, settings
from .base import STTError, STTProvider

_model: object | None = None
_model_path: str | None = None
_model_lock = threading.Lock()


def _resolve_model_path() -> Path:
    raw = Path(settings.vosk_model_path)
    if not raw.is_absolute():
        raw = PROJECT_ROOT / raw
    return raw


def _load_model(path: Path) -> object:
    """Загружает модель Vosk (тяжёлая операция, вызывается в отдельном потоке)."""
    global _model, _model_path

    with _model_lock:
        if _model is not None and _model_path == str(path):
            return _model

        vosk = _import_vosk()

        if not path.is_dir():
            raise STTError(
                f"Модель Vosk не найдена: {path}. Скачайте её командой "
                "python scripts/download_vosk_model.py или укажите путь "
                "в VOSK_MODEL_PATH."
            )

        try:
            _model = vosk.Model(str(path))
        except Exception as exc:  # noqa: BLE001 - сообщение важнее типа
            raise STTError(f"Не удалось загрузить модель Vosk из {path}: {exc}") from exc
        _model_path = str(path)
        return _model


def _import_vosk():
    try:
        import vosk
    except ImportError as exc:  # pragma: no cover - зависит от окружения
        raise STTError(
            "Для STT_PROVIDER=vosk установите пакет vosk: "
            "pip install -r requirements.txt"
        ) from exc

    # По умолчанию Vosk печатает отладочные LOG-строки в stderr.
    vosk.SetLogLevel(-1)
    return vosk


class VoskProvider(STTProvider):
    def __init__(self) -> None:
        self._recognizer = None
        self._parts: list[str] = []

    async def start(self, language: str) -> None:
        vosk = _import_vosk()

        model = await asyncio.to_thread(_load_model, _resolve_model_path())
        self._recognizer = vosk.KaldiRecognizer(model, 16000)
        self._recognizer.SetWords(False)
        self._parts = []

    async def send_audio(self, chunk: bytes) -> None:
        if self._recognizer is None:
            return

        if await asyncio.to_thread(self._recognizer.AcceptWaveform, chunk):
            text = self._extract_text(await asyncio.to_thread(self._recognizer.Result))
            if text:
                self._parts.append(text)

    async def finish(self) -> str:
        if self._recognizer is None:
            return ""

        try:
            text = self._extract_text(
                await asyncio.to_thread(self._recognizer.FinalResult)
            )
        finally:
            self._recognizer = None

        if text:
            self._parts.append(text)
        result = " ".join(self._parts).strip()
        self._parts = []
        return result

    @staticmethod
    def _extract_text(payload: str) -> str:
        try:
            return str(json.loads(payload).get("text", "")).strip()
        except (TypeError, ValueError):
            return ""

    async def close(self) -> None:
        self._recognizer = None
        self._parts = []
