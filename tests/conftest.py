"""Общая настройка тестов.

Тесты должны быть детерминированными и не зависеть от локального `.env`:
принудительно включаем провайдер `dummy` и отключаем авторизацию до того,
как `server.config` прочитает окружение (load_dotenv не переопределяет уже
заданные переменные).
"""

from __future__ import annotations

import os

os.environ["STT_PROVIDER"] = "dummy"
os.environ["ALLOW_ANONYMOUS"] = "true"
os.environ["HEARBOT_TOKEN"] = ""
os.environ["HEARBOT_ADMIN_TOKEN"] = ""
os.environ["MAX_UTTERANCE_SECONDS"] = "30"
