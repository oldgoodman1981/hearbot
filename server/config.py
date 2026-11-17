"""Конфигурация сервера Hearbot.

Все параметры читаются из переменных окружения. Для удобства локальной
разработки поддерживается файл .env в корне проекта (см. .env.example).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # python-dotenv не является жёсткой зависимостью
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - зависит от окружения
    pass


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    stt_provider: str = os.getenv("STT_PROVIDER", "dummy").strip().lower()
    yandex_api_key: str = os.getenv("YANDEX_API_KEY", "").strip()
    yandex_folder_id: str = os.getenv("YANDEX_FOLDER_ID", "").strip()
    yandex_endpoint: str = os.getenv(
        "YANDEX_STT_ENDPOINT",
        "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize",
    ).strip()
    vosk_model_path: str = os.getenv(
        "VOSK_MODEL_PATH", "models/vosk-model-small-ru-0.22"
    ).strip()
    default_language: str = os.getenv("STT_LANGUAGE", "ru-RU").strip()
    max_utterance_seconds: float = float(os.getenv("MAX_UTTERANCE_SECONDS", "30"))
    allow_anonymous: bool = _get_bool("ALLOW_ANONYMOUS", True)
    access_token: str = os.getenv("HEARBOT_TOKEN", "").strip()
    admin_token: str = os.getenv("HEARBOT_ADMIN_TOKEN", "").strip()


settings = Settings()
