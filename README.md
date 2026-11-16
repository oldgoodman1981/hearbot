# Hearbot

Онлайн-распознавание речи для Windows и Android: лёгкий сервер на Python
(FastAPI + WebSocket) и два клиента, которые отправляют аудио с микрофона и
показывают распознанный текст в реальном времени.

## Возможности

- **Потоковая передача аудио** по WebSocket: PCM 16-bit, mono, 16 кГц.
- **Сменные провайдеры STT**: `dummy` (для разработки без ключей) и
  `yandex` (Yandex SpeechKit, есть бесплатный тариф).
- **Клиент Windows**: tkinter + PyAudio, запускается как обычное приложение.
- **Клиент Android**: Kivy + `android.media.AudioRecord`, собирается в APK.
- Конфигурация через переменные окружения / `.env`.

## Структура репозитория

```
hearbot/
├── server/                  # FastAPI-сервер
│   ├── main.py              # WebSocket endpoint /ws/stt, /health
│   ├── config.py            # настройки из окружения
│   └── stt/                 # провайдеры распознавания
│       ├── base.py          # интерфейс STTProvider
│       ├── dummy.py         # заглушка
│       ├── yandex.py        # Yandex SpeechKit
│       └── factory.py       # выбор провайдера
├── clients/
│   ├── windows/main.py      # Windows-клиент (tkinter + PyAudio)
│   └── android/             # Android-клиент (Kivy)
│       ├── main.py
│       └── buildozer.spec
├── docs/
│   ├── ARCHITECTURE.md
│   └── PROTOCOL.md
├── tests/test_server.py
├── requirements.txt
└── .env.example
```

## Быстрый старт

### 1. Сервер

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m server.main
```

Сервер поднимется на `http://0.0.0.0:8000`. Проверка:

```powershell
curl http://127.0.0.1:8000/health
```

По умолчанию используется провайдер `dummy` — он не распознаёт речь, но
показывает длительность полученного аудио. Этого достаточно, чтобы проверить
клиентов и протокол.

### 2. Клиент Windows

```powershell
pip install -r clients/windows/requirements.txt
python clients/windows/main.py
```

В окне укажите адрес сервера (`ws://127.0.0.1:8000/ws/stt`), выберите язык и
нажмите «Начать запись». После остановки сервер вернёт распознанный текст.

### 3. Клиент Android

Сборка APK выполняется через [Buildozer](https://buildozer.readthedocs.io/).
Удобнее всего на Linux или в WSL/Docker (на Windows нативно сборка не
поддерживается):

```bash
cd clients/android
pip install buildozer cython
buildozer -v android debug
```

Полученный APK — в `clients/android/bin/`.

На реальном устройстве замените адрес сервера на IP компьютера в локальной
сети, например `ws://192.168.1.10:8000/ws/stt`, и разрешите входящие
подключения к порту 8000 в брандмауэре.

## Подключение Yandex SpeechKit

1. Создайте каталог в [Yandex Cloud](https://console.cloud.yandex.ru/).
2. Создайте сервисный аккаунт с ролью `ai.speechkit-stt.user`.
3. Выпустите API-ключ для сервисного аккаунта.
4. Заполните `.env`:

```ini
STT_PROVIDER=yandex
YANDEX_API_KEY=ваш_api_ключ
YANDEX_FOLDER_ID=ваш_folder_id
STT_LANGUAGE=ru-RU
```

5. Перезапустите сервер.

> Бесплатный тариф Yandex SpeechKit ограничен. Провайдер отправляет в облако
> **каждую реплику целиком**, поэтому тишину лучше не записывать: нажимайте
> «Остановить» сразу после фразы. Поддержка VAD и потоковых частичных
> результатов — в планах (см. `docs/ARCHITECTURE.md`).

## Протокол

Подробное описание — в [docs/PROTOCOL.md](docs/PROTOCOL.md). Кратко:

| Направление | Сообщение | Описание |
|-------------|-----------|----------|
| клиент → сервер | бинарный чанк | PCM s16le, mono, 16 кГц |
| клиент → сервер | `{"type": "stop"}` | завершить реплику |
| сервер → клиент | `{"type": "ready", ...}` | сессия готова |
| сервер → клиент | `{"type": "final", "text": "..."}` | результат распознавания |
| сервер → клиент | `{"type": "error", "message": "..."}` | ошибка |

## Тесты

```powershell
pip install -r requirements-dev.txt
pytest
```

Тесты используют провайдер `dummy` и не требуют облачных ключей.

## Ограничения текущей версии

- Yandex-провайдер возвращает только финальный текст (REST API v1).
- Нет автоматического определения речи (VAD) — аудио отправляется всё время записи.
- Аутентификация простая: общий токен в query-параметре (для продакшена нужен JWT/OAuth).
- Аудио реплики хранится в памяти сервера; лимит — `MAX_UTTERANCE_SECONDS`.

## Лицензия

MIT, см. [LICENSE](LICENSE).
