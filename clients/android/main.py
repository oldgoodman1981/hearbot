"""Hearbot: клиент для Android на Kivy.

Записывает микрофон через android.media.AudioRecord (pyjnius), отправляет
PCM 16-bit mono 16 kHz на сервер по WebSocket и показывает распознанный
текст. Интерфейс — тёмная тема в стиле панели /admin: индикатор связи,
круглая кнопка записи с пульсацией, таймер и уровень микрофона.

На десктопе для отладки используется sounddevice, если установлен.

Сборка APK:
    cd clients/android
    buildozer -v android debug
"""

from __future__ import annotations

import json
import math
import queue
import threading
import time
from array import array
from urllib.parse import urlencode

import websocket
from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.graphics import Color, Ellipse, Rectangle, RoundedRectangle
from kivy.metrics import dp
from kivy.properties import BooleanProperty, ListProperty, NumericProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

SAMPLE_RATE = 16000
# 10.0.2.2 — loopback хоста для эмулятора Android.
DEFAULT_URL = "ws://10.0.2.2:8000/ws/stt"

# Палитра в стиле панели управления
BG = (0.008, 0.024, 0.09, 1)
PANEL = (0.06, 0.09, 0.16, 1)
PANEL_2 = (0.08, 0.12, 0.21, 1)
TEXT = (0.89, 0.91, 0.95, 1)
MUTED = (0.58, 0.64, 0.72, 1)
ACCENT = (0.18, 0.83, 0.75, 1)
DANGER = (0.97, 0.44, 0.44, 1)
AMBER = (0.98, 0.75, 0.14, 1)
ACCENT_TEXT = (0.01, 0.13, 0.11, 1)
DANGER_TEXT = (0.13, 0.02, 0.02, 1)

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


# --- Вспомогательные виджеты -------------------------------------------


class RootLayout(BoxLayout):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        with self.canvas.before:
            Color(*BG)
            self._background = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_args) -> None:
        self._background.pos = self.pos
        self._background.size = self.size


class Card(BoxLayout):
    """Панель с закруглёнными углами."""

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("orientation", "vertical")
        super().__init__(**kwargs)
        self.padding = dp(14)
        self.spacing = dp(8)
        with self.canvas.before:
            Color(*PANEL)
            self._rect = RoundedRectangle(radius=[dp(16)])
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_args) -> None:
        self._rect.pos = self.pos
        self._rect.size = self.size


class StatusDot(Widget):
    dot_color = ListProperty(list(MUTED))

    def __init__(self, **kwargs) -> None:
        super().__init__(size_hint=(None, None), size=(dp(10), dp(10)), **kwargs)
        with self.canvas:
            self._color = Color(*self.dot_color)
            self._ellipse = Ellipse(pos=self.pos, size=self.size)
        self.bind(pos=self._sync, size=self._sync, dot_color=self._update_color)

    def _sync(self, *_args) -> None:
        self._ellipse.pos = self.pos
        self._ellipse.size = self.size

    def _update_color(self, *_args) -> None:
        self._color.rgba = self.dot_color


class LevelMeter(Widget):
    level = NumericProperty(0.0)

    def __init__(self, **kwargs) -> None:
        super().__init__(size_hint_y=None, height=dp(12), **kwargs)
        with self.canvas:
            Color(*PANEL_2)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(6)])
            self._fg_color = Color(*ACCENT)
            self._fg = RoundedRectangle(pos=self.pos, size=(0, self.height), radius=[dp(6)])
        self.bind(pos=self._sync, size=self._sync, level=self._update)

    def _sync(self, *_args) -> None:
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._update()

    def _update(self, *_args) -> None:
        width = max(0.0, min(1.0, self.level)) * self.width
        self._fg.pos = self.pos
        self._fg.size = (width, self.height)


class RoundRecordButton(ButtonBehavior, Label):
    """Круглая кнопка записи с пульсирующим кольцом."""

    accent = ListProperty(list(ACCENT))
    pulsing = BooleanProperty(False)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.size_hint = (None, None)
        self.size = (dp(112), dp(112))
        self.font_size = dp(40)
        self.bold = True
        self.color = list(ACCENT_TEXT)
        self.text = "●"

        with self.canvas.before:
            self._ring_color = Color(ACCENT[0], ACCENT[1], ACCENT[2], 0.0)
            self._ring = Ellipse(pos=self.pos, size=self.size)
            self._fill_color = Color(*self.accent)
            self._fill = Ellipse(pos=self.pos, size=self.size)

        self.bind(pos=self._sync, size=self._sync, accent=self._sync_accent)
        self._phase = 0.0
        Clock.schedule_interval(self._animate, 1 / 30)

    def _sync(self, *_args) -> None:
        self._fill.pos = self.pos
        self._fill.size = self.size
        self._sync_ring()

    def _sync_accent(self, *_args) -> None:
        self._fill_color.rgba = self.accent

    def _sync_ring(self) -> None:
        if self.pulsing:
            grow = dp(6) + dp(6) * (0.5 + 0.5 * math.sin(self._phase))
        else:
            grow = 0.0
        self._ring.pos = (self.x - grow, self.y - grow)
        self._ring.size = (self.width + 2 * grow, self.height + 2 * grow)

    def _animate(self, dt) -> None:
        if not self.pulsing:
            self._ring_color.a = 0.0
            self._sync_ring()
            return
        self._phase += dt * 4.0
        self._ring_color.a = 0.18 + 0.12 * (0.5 + 0.5 * math.sin(self._phase))
        self._sync_ring()


def make_label(text: str, size: int = 13, color=TEXT, bold: bool = False, **kwargs) -> Label:
    return Label(text=text, font_size=dp(size), color=color, bold=bold, **kwargs)


def flat_button(text: str, **kwargs) -> Button:
    return Button(
        text=text,
        background_normal="",
        background_down="",
        background_color=PANEL_2,
        color=TEXT,
        font_size=dp(13),
        **kwargs,
    )


def styled_input(**kwargs) -> TextInput:
    return TextInput(
        background_color=(0.03, 0.06, 0.11, 1),
        foreground_color=TEXT,
        cursor_color=ACCENT,
        font_size=dp(15),
        padding=[dp(10), dp(10), dp(10), dp(10)],
        **kwargs,
    )


# --- Аудио и сеть -------------------------------------------------------


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
        self._closing = False
        self._failed = False

        self._app = websocket.WebSocketApp(
            url,
            on_open=self._handle_open,
            on_message=self._handle_message,
            on_error=self._handle_error,
            on_close=self._handle_close,
        )
        self._thread = threading.Thread(target=self._app.run_forever, daemon=True)
        self._sender = threading.Thread(target=self._sender_loop, daemon=True)

    @property
    def is_closed(self) -> bool:
        return self._closed.is_set()

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
        if self._failed or self._closing:
            return
        self._failed = True
        self._on_error(error)

    def _handle_close(self, ws, code, reason) -> None:
        self._closed.set()
        self._outbox.put(None)
        if self._closing or self._failed:
            return
        if code not in (None, 1000):
            self._failed = True
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
                if not self._failed and not self._closing:
                    self._failed = True
                    self._on_error(exc)
                return

    def send_audio(self, data: bytes) -> None:
        if not self._closed.is_set():
            self._outbox.put(data)

    def send_control(self, payload: dict) -> None:
        if not self._closed.is_set():
            self._outbox.put(json.dumps(payload))

    def close(self) -> None:
        self._closing = True
        self._closed.set()
        self._outbox.put(None)
        try:
            self._app.close()
        except Exception:
            pass


# --- Приложение ---------------------------------------------------------


class HearbotApp(App):
    title = "Hearbot"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._connection: Connection | None = None
        self._current_url = ""
        self._recorder = None
        self._recording = False
        self._connected = False
        self._provider = "—"
        self._level_raw = 0.0
        self._level_display = 0.0
        self._record_started = 0.0
        self._language = "ru-RU"

    # --- UI ---------------------------------------------------------

    def build(self):
        Window.clearcolor = BG
        if not ANDROID:
            Window.size = (420, 780)

        root = RootLayout(orientation="vertical", padding=dp(14), spacing=dp(10))

        # Шапка
        header = BoxLayout(
            orientation="horizontal", size_hint_y=None, height=dp(54), spacing=dp(8)
        )
        titles = BoxLayout(orientation="vertical", spacing=0)
        titles.add_widget(make_label("Hearbot", size=25, bold=True))
        titles.add_widget(make_label("Распознавание речи", size=12, color=MUTED))
        header.add_widget(titles)

        status_box = BoxLayout(
            orientation="horizontal", size_hint=(None, 1), width=dp(150), spacing=dp(6)
        )
        self.status_dot = StatusDot()
        self.status_text = make_label(
            "Не подключён", size=12, color=MUTED, halign="right", valign="middle"
        )
        self.status_text.bind(
            size=lambda widget, *_: setattr(widget, "text_size", widget.size)
        )
        status_box.add_widget(self.status_dot)
        status_box.add_widget(self.status_text)
        header.add_widget(status_box)
        root.add_widget(header)

        # Подключение
        settings = Card(size_hint_y=None, height=dp(144))
        settings.add_widget(
            make_label("Подключение", size=11, color=MUTED, size_hint_y=None, height=dp(16))
        )
        self.url_input = styled_input(
            text=DEFAULT_URL,
            multiline=False,
            size_hint_y=None,
            height=dp(44),
            write_tab=False,
        )
        settings.add_widget(self.url_input)

        row = BoxLayout(
            orientation="horizontal", size_hint_y=None, height=dp(38), spacing=dp(8)
        )
        self.token_input = styled_input(
            hint_text="Токен (если требуется)",
            password=True,
            multiline=False,
            write_tab=False,
        )
        row.add_widget(self.token_input)
        self.lang_button = flat_button(
            f"Язык: {self._language}", size_hint=(None, 1), width=dp(128)
        )
        self.lang_button.bind(on_release=self._cycle_language)
        row.add_widget(self.lang_button)
        settings.add_widget(row)
        root.add_widget(settings)

        # Запись
        record = Card(size_hint_y=None, height=dp(240))
        self.record_button = RoundRecordButton()
        self.record_button.pos_hint = {"center_x": 0.5}
        self.record_button.bind(on_release=self._toggle)
        record.add_widget(self.record_button)

        self.timer_label = make_label("00:00.0", size=28, bold=True)
        self.timer_label.size_hint_y = None
        self.timer_label.height = dp(36)
        self.timer_label.halign = "center"
        self.timer_label.bind(
            size=lambda widget, *_: setattr(widget, "text_size", widget.size)
        )
        record.add_widget(self.timer_label)

        self.hint_label = make_label("Нажмите, чтобы начать", size=12, color=MUTED)
        self.hint_label.size_hint_y = None
        self.hint_label.height = dp(20)
        self.hint_label.halign = "center"
        self.hint_label.bind(
            size=lambda widget, *_: setattr(widget, "text_size", widget.size)
        )
        record.add_widget(self.hint_label)

        self.level_meter = LevelMeter()
        record.add_widget(self.level_meter)
        root.add_widget(record)

        # Текст
        transcript_card = Card(size_hint_y=1)
        header_row = BoxLayout(
            orientation="horizontal", size_hint_y=None, height=dp(30), spacing=dp(6)
        )
        header_row.add_widget(make_label("Распознанный текст", size=14, bold=True))
        copy_button = flat_button("Копировать", size_hint=(None, 1), width=dp(104))
        copy_button.bind(on_release=lambda *_: self._copy())
        header_row.add_widget(copy_button)
        clear_button = flat_button("Очистить", size_hint=(None, 1), width=dp(86))
        clear_button.bind(on_release=lambda *_: self._clear())
        header_row.add_widget(clear_button)
        transcript_card.add_widget(header_row)

        self.partial_label = make_label("", size=14, color=ACCENT, halign="left")
        self.partial_label.size_hint_y = None
        self.partial_label.height = dp(24)
        self.partial_label.bind(
            size=lambda widget, *_: setattr(widget, "text_size", (widget.width, None))
        )
        transcript_card.add_widget(self.partial_label)

        self.transcript = styled_input(readonly=True, multiline=True)
        transcript_card.add_widget(self.transcript)
        root.add_widget(transcript_card)

        Clock.schedule_interval(self._tick, 0.1)
        return root

    # --- Вспомогательные обновления UI -------------------------------

    def _post(self, func, *args) -> None:
        Clock.schedule_once(lambda _dt: func(*args))

    def _set_status(self, state: str, text: str) -> None:
        colors = {
            "offline": MUTED,
            "connecting": AMBER,
            "online": ACCENT,
            "error": DANGER,
        }
        color = colors.get(state, MUTED)

        def apply() -> None:
            self.status_dot.dot_color = list(color)
            self.status_text.text = text
            self.status_text.color = color

        self._post(apply)

    def _set_hint(self, text: str, color=MUTED) -> None:
        def apply() -> None:
            self.hint_label.text = text
            self.hint_label.color = color

        self._post(apply)

    def _set_recording_ui(self, recording: bool) -> None:
        def apply() -> None:
            if recording:
                self.record_button.text = "■"
                self.record_button.accent = list(DANGER)
                self.record_button.color = list(DANGER_TEXT)
                self.record_button.pulsing = True
            else:
                self.record_button.text = "●"
                self.record_button.accent = list(ACCENT)
                self.record_button.color = list(ACCENT_TEXT)
                self.record_button.pulsing = False

        self._post(apply)

    def _cycle_language(self, *_args) -> None:
        self._language = "en-US" if self._language == "ru-RU" else "ru-RU"
        self.lang_button.text = f"Язык: {self._language}"

    # --- Текст -------------------------------------------------------

    def _append(self, text: str) -> None:
        current = self.transcript.text
        self.transcript.text = f"{current}\n{text}" if current else text
        Clock.schedule_once(lambda _dt: setattr(self.transcript, "scroll_y", 0), 0)

    def _clear(self) -> None:
        self.partial_label.text = ""
        self.transcript.text = ""
        self._set_hint("Текст очищен", MUTED)

    def _copy(self) -> None:
        text = self.transcript.text.strip()
        if not text:
            self._set_hint("Нечего копировать", AMBER)
            return
        Clipboard.copy(text)
        self._set_hint("Текст скопирован в буфер обмена", ACCENT)

    # --- Управление записью ------------------------------------------

    def _toggle(self, *_args) -> None:
        if self._recording:
            self._stop()
        else:
            self._start()

    def _build_url(self) -> str:
        url = self.url_input.text.strip()
        params = {"lang": self._language, "platform": "android"}
        token = self.token_input.text.strip()
        if token:
            params["token"] = token
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}{urlencode(params)}"

    def _start(self) -> None:
        if not self.url_input.text.strip():
            self._set_hint("Укажите адрес сервера", DANGER)
            return

        full_url = self._build_url()

        if self._connection is not None and (
            self._connection.is_closed or self._current_url != full_url
        ):
            self._connection.close()
            self._connection = None

        if self._connection is None:
            self._set_status("connecting", "Подключение…")
            self._set_hint("Устанавливаю соединение…", AMBER)
            self._connection = Connection(
                full_url,
                on_message=lambda message: self._post(self._handle_message, message),
                on_open=lambda: self._post(self._on_open),
                on_error=lambda error: self._post(self._on_error, error),
            )
            self._current_url = full_url
            self._connection.start()

        try:
            self._recorder = (
                AndroidRecorder(self._on_chunk)
                if ANDROID
                else DesktopRecorder(self._on_chunk)
            )
            self._recorder.start()
        except Exception as exc:
            self._set_hint(f"Микрофон: {exc}", DANGER)
            self._recorder = None
            return

        self._recording = True
        self._record_started = time.time()
        self._level_raw = 0.0
        self._level_display = 0.0
        self.level_meter.level = 0
        self.timer_label.text = "00:00.0"
        self._set_recording_ui(True)
        self._set_hint("Идёт запись — говорите", ACCENT)

    def _stop(self) -> None:
        self._recording = False
        self._set_recording_ui(False)
        if self._recorder is not None:
            try:
                self._recorder.stop()
            except Exception:
                pass
            self._recorder = None
        self.level_meter.level = 0
        if self._connection is not None:
            self._connection.send_control({"type": "stop"})
            self._set_hint("Распознавание…", AMBER)
        else:
            self._set_hint("Готово", MUTED)

    def _on_chunk(self, data: bytes) -> None:
        if not self._recording or self._connection is None:
            return
        self._connection.send_audio(data)

        samples = array("h")
        try:
            samples.frombytes(data)
        except ValueError:
            return
        if not samples:
            return
        rms = math.sqrt(sum(value * value for value in samples) / len(samples))
        self._level_raw = min(1.0, rms / 5000.0)

    def _tick(self, _dt) -> None:
        if not self._recording:
            return
        elapsed = time.time() - self._record_started
        self.timer_label.text = f"{int(elapsed // 60):02d}:{elapsed % 60:04.1f}"
        self._level_display += (self._level_raw - self._level_display) * 0.35
        self.level_meter.level = max(0.0, min(1.0, self._level_display))
        self._level_raw *= 0.6

    # --- События соединения ------------------------------------------

    def _on_open(self) -> None:
        self._connected = True

    def _on_error(self, error) -> None:
        self._connected = False
        self._set_status("error", "Ошибка связи")
        self._set_hint(f"Соединение: {error}", DANGER)
        self._append(f"[ошибка] {error}")
        if self._recording:
            self._stop()
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _handle_message(self, message: dict) -> None:
        kind = message.get("type")

        if kind == "ready":
            self._provider = message.get("provider", "—")
            self._connected = True
            self._set_status("online", f"Онлайн · {self._provider}")
            if self._recording:
                self._set_hint("Идёт запись — говорите", ACCENT)
            else:
                self._set_hint("Готово к записи", MUTED)
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
            self._set_hint(
                "Идёт запись — говорите" if self._recording else "Готово",
                ACCENT if self._recording else MUTED,
            )
            return

        if kind == "error":
            message_text = message.get("message", "")
            self.partial_label.text = ""
            if message.get("fatal"):
                self._set_status("error", "Нет связи")
                self._set_hint(f"Соединение потеряно: {message_text}", DANGER)
                if self._recording:
                    self._stop()
                if self._connection is not None:
                    self._connection.close()
                    self._connection = None
            else:
                self._set_hint(f"Ошибка распознавания: {message_text}", DANGER)
                if self._provider != "—":
                    self._set_status("online", f"Онлайн · {self._provider}")

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
