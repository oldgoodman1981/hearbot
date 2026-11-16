"""Hearbot: клиент для Android на Kivy.

Записывает микрофон через android.media.AudioRecord (pyjnius), отправляет
PCM 16-bit mono 16 kHz на сервер по WebSocket и показывает распознанный
текст. На десктопе для отладки используется sounddevice, если установлен.

Сборка APK:
    cd clients/android
    buildozer -v android debug
"""

from __future__ import annotations

import json
import queue
import threading
from array import array
from urllib.parse import urlencode

import websocket
from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput

SAMPLE_RATE = 16000
# 10.0.2.2 — loopback хоста для эмулятора Android.
DEFAULT_URL = "ws://10.0.2.2:8000/ws/stt"

try:
    from jnius import autoclass  # доступно в python-for-android

    ANDROID = True
except Exception:  # pragma: no cover - выполняется только в APK
    autoclass = None
    ANDROID = False

try:
    import sounddevice as sd  # опционально, только для десктопа
except Exception:
    sd = None


class AndroidRecorder:
    """Запись с микрофона через AudioRecord."""

    def __init__(self, on_chunk) -> None:
        self._on_chunk = on_chunk
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._record = None
        self._read_blocking = 0
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        if not ANDROID:
            raise RuntimeError("AndroidRecorder доступен только внутри APK")

        audio_record = autoclass("android.media.AudioRecord")
        audio_format = autoclass("android.media.AudioFormat")
        audio_source = autoclass("android.media.MediaRecorder$AudioSource")

        channel = audio_format.CHANNEL_IN_MONO
        encoding = audio_format.ENCODING_PCM_16BIT
        min_buffer = audio_record.getMinBufferSize(SAMPLE_RATE, channel, encoding)
        buffer_size = max(min_buffer, SAMPLE_RATE // 2)

        self._record = audio_record(
            audio_source.MIC,
            SAMPLE_RATE,
            channel,
            encoding,
            buffer_size * 2,
        )
        self._read_blocking = audio_record.READ_BLOCKING
        self._record.startRecording()

        self._stop.clear()
        self._started = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        block = array("h", [0]) * 800  # 50 мс при 16 кГц
        while not self._stop.is_set():
            count = self._record.read(block, 0, len(block), self._read_blocking)
            if count and count > 0:
                self._on_chunk(block[:count].tobytes())

    def stop(self) -> None:
        if not self._started:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        try:
            self._record.stop()
            self._record.release()
        except Exception:
            pass
        self._record = None
        self._started = False


class DesktopRecorder:
    """Тот же интерфейс, но через sounddevice — удобно для отладки UI."""

    def __init__(self, on_chunk) -> None:
        self._on_chunk = on_chunk
        self._stream = None

    def start(self) -> None:
        if sd is None:
            raise RuntimeError(
                "Для записи на десктопе установите sounddevice: pip install sounddevice"
            )
        if self._stream is not None:
            return

        def callback(indata, frames, time_info, status) -> None:
            self._on_chunk(indata.tobytes())

        self._stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=800,
            callback=callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class Connection:
    """WebSocket-клиент с очередью исходящих сообщений."""

    def __init__(self, url, on_message, on_open, on_error) -> None:
        self._on_message = on_message
        self._on_open = on_open
        self._on_error = on_error
        self._outbox: queue.Queue[object] = queue.Queue()
        self._closed = threading.Event()

        self._app = websocket.WebSocketApp(
            url,
            on_open=self._handle_open,
            on_message=self._handle_message,
            on_error=self._handle_error,
            on_close=self._handle_close,
        )
        self._thread = threading.Thread(target=self._app.run_forever, daemon=True)
        self._sender = threading.Thread(target=self._sender_loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _handle_open(self, ws=None) -> None:
        self._on_open()
        if not self._sender.is_alive():
            self._sender.start()

    def _handle_message(self, ws, message: str) -> None:
        try:
            payload = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            return
        self._on_message(payload)

    def _handle_error(self, ws, error) -> None:
        self._on_error(error)

    def _handle_close(self, ws, code, reason) -> None:
        self._closed.set()
        self._outbox.put(None)
        if code not in (None, 1000):
            self._on_error(f"Соединение закрыто: {code} {reason or ''}".strip())

    def _sender_loop(self) -> None:
        while not self._closed.is_set():
            try:
                item = self._outbox.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                return
            try:
                if isinstance(item, bytes):
                    self._app.send(item, opcode=websocket.ABNF.OPCODE_BINARY)
                else:
                    self._app.send(item)
            except Exception as exc:
                self._on_error(exc)
                return

    def send_audio(self, data: bytes) -> None:
        if not self._closed.is_set():
            self._outbox.put(data)

    def send_control(self, payload: dict) -> None:
        if not self._closed.is_set():
            self._outbox.put(json.dumps(payload))

    def close(self) -> None:
        self._closed.set()
        self._outbox.put(None)
        try:
            self._app.close()
        except Exception:
            pass


class HearbotApp(App):
    title = "Hearbot"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._connection: Connection | None = None
        self._recorder = None
        self._recording = False
        self._connected = False

    # --- UI ---------------------------------------------------------

    def build(self):
        root = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))

        root.add_widget(Label(text="Адрес сервера", size_hint_y=None, height=dp(24)))
        self.url_input = TextInput(
            text=DEFAULT_URL,
            multiline=False,
            size_hint_y=None,
            height=dp(40),
            write_tab=False,
        )
        root.add_widget(self.url_input)

        self.status_label = Label(text="Готово", size_hint_y=None, height=dp(28))
        root.add_widget(self.status_label)

        self.partial_label = Label(
            text="",
            size_hint_y=None,
            height=dp(44),
            color=(0.15, 0.39, 0.92, 1),
            halign="left",
            valign="middle",
        )
        self.partial_label.bind(size=lambda widget, *_: setattr(widget, "text_size", widget.size))
        root.add_widget(self.partial_label)

        self.toggle_button = Button(text="Начать", size_hint_y=None, height=dp(52))
        self.toggle_button.bind(on_release=self._toggle)
        root.add_widget(self.toggle_button)

        clear_button = Button(text="Очистить текст", size_hint_y=None, height=dp(40))
        clear_button.bind(on_release=lambda *_: self._clear())
        root.add_widget(clear_button)

        self.transcript = TextInput(readonly=True, multiline=True)
        root.add_widget(self.transcript)
        return root

    # --- Управление -------------------------------------------------

    def _post(self, func, *args) -> None:
        Clock.schedule_once(lambda _dt: func(*args))

    def _set_status(self, text: str) -> None:
        self._post(self.status_label.setter("text"), text)

    def _append(self, text: str) -> None:
        current = self.transcript.text
        self.transcript.text = f"{current}\n{text}" if current else text

    def _clear(self) -> None:
        self.partial_label.text = ""
        self.transcript.text = ""

    def _toggle(self, *_args) -> None:
        if self._recording:
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        url = self.url_input.text.strip()
        if not url:
            self._set_status("Укажите адрес сервера")
            return

        if self._connection is None:
            separator = "&" if "?" in url else "?"
            full_url = f"{url}{separator}{urlencode({'lang': 'ru-RU', 'platform': 'android'})}"
            self._set_status("Подключение…")
            self._connection = Connection(
                full_url,
                on_message=lambda message: self._post(self._handle_message, message),
                on_open=lambda: self._post(self._on_open),
                on_error=lambda error: self._post(self._on_error, error),
            )
            self._connection.start()

        try:
            self._recorder = AndroidRecorder(self._on_chunk) if ANDROID else DesktopRecorder(self._on_chunk)
            self._recorder.start()
        except Exception as exc:
            self._set_status(f"Микрофон: {exc}")
            return

        self._recording = True
        self.toggle_button.text = "Остановить"
        self._set_status("Запись…")

    def _stop(self) -> None:
        self._recording = False
        self.toggle_button.text = "Начать"
        if self._recorder is not None:
            try:
                self._recorder.stop()
            except Exception:
                pass
            self._recorder = None
        if self._connection is not None:
            self._connection.send_control({"type": "stop"})
            self._set_status("Распознавание…")
        else:
            self._set_status("Готово")

    def _on_chunk(self, data: bytes) -> None:
        if self._recording and self._connection is not None:
            self._connection.send_audio(data)

    # --- События соединения ------------------------------------------

    def _on_open(self) -> None:
        self._connected = True

    def _on_error(self, error) -> None:
        self._connected = False
        self._set_status(f"Соединение: {error}")
        self._append(f"[ошибка] {error}")

    def _handle_message(self, message: dict) -> None:
        kind = message.get("type")

        if kind == "ready":
            self._set_status(f"Подключено ({message.get('provider', '?')})")
            return

        if kind == "control":
            action = message.get("action")
            if action == "stop_recording" and self._recording:
                self._stop()
            elif action == "start_recording" and not self._recording:
                self._start()
            return

        if kind == "partial":
            self.partial_label.text = (message.get("text") or "").strip()
            return

        if kind == "final":
            text = (message.get("text") or "").strip()
            self.partial_label.text = ""
            if text:
                self._append(text)
            self._set_status("Запись…" if self._recording else "Готово")
            return

        if kind == "error":
            self.partial_label.text = ""
            self._set_status("Ошибка")
            self._append(f"[ошибка] {message.get('message', '')}")

    # --- Жизненный цикл ---------------------------------------------

    def on_start(self) -> None:
        if ANDROID:
            try:
                from android.permissions import Permission, request_permissions

                request_permissions([Permission.RECORD_AUDIO, Permission.INTERNET])
            except Exception:
                pass

    def on_stop(self) -> None:
        if self._recording:
            self._stop()
        if self._connection is not None:
            self._connection.close()
            self._connection = None


if __name__ == "__main__":
    HearbotApp().run()
