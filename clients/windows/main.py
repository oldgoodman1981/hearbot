"""Hearbot: клиент для Windows с современным интерфейсом.

Захватывает микрофон, отправляет PCM 16-bit mono 16 kHz на сервер по
WebSocket и показывает распознанный текст. Интерфейс оформлен в тёмной
теме, совпадающей с веб-панелью /admin: индикатор соединения, таймер,
уровень микрофона и живой текст.

Запуск:
    pip install -r clients/windows/requirements.txt
    python clients/windows/main.py
"""

from __future__ import annotations

import json
import math
import queue
import threading
import time
import tkinter as tk
from array import array
from tkinter import messagebox
from urllib.parse import urlencode

import customtkinter as ctk
import pyaudio
import websocket

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_FORMAT = pyaudio.paInt16
CHUNK_FRAMES = 800  # 50 мс
DEFAULT_URL = "ws://127.0.0.1:8000/ws/stt"

# Палитра в стиле панели управления
BG = "#020617"
PANEL = "#0f172a"
PANEL_2 = "#111c33"
BORDER = "#1e293b"
TEXT = "#e2e8f0"
MUTED = "#94a3b8"
ACCENT = "#2dd4bf"
ACCENT_HOVER = "#14b8a6"
DANGER = "#f87171"
DANGER_HOVER = "#ef4444"
AMBER = "#fbbf24"


class STTConnection:
    """WebSocket-соединение с сервером.

    Чтение и запись выполняются в отдельных потоках: websocket-client
    не рассчитан на одновременные send/recv из разных потоков, но
    раздельные направления работают корректно.
    """

    def __init__(self, url: str, on_message) -> None:
        self._ws = websocket.create_connection(url, timeout=15)
        self._on_message = on_message
        self._outbox: queue.Queue[object] = queue.Queue()
        self._closed = threading.Event()

        threading.Thread(target=self._writer, daemon=True).start()
        threading.Thread(target=self._reader, daemon=True).start()

    @property
    def is_closed(self) -> bool:
        return self._closed.is_set()

    def send_audio(self, data: bytes) -> None:
        if not self._closed.is_set():
            self._outbox.put(data)

    def send_control(self, payload: dict) -> None:
        if not self._closed.is_set():
            self._outbox.put(json.dumps(payload))

    def _writer(self) -> None:
        while True:
            try:
                item = self._outbox.get(timeout=0.2)
            except queue.Empty:
                if self._closed.is_set():
                    return
                continue

            if item is None:
                return

            try:
                if isinstance(item, bytes):
                    self._ws.send_binary(item)
                else:
                    self._ws.send(item)
            except Exception as exc:  # соединение разорвано
                self._fail(exc)
                return

    def _reader(self) -> None:
        while not self._closed.is_set():
            try:
                raw = self._ws.recv()
            except Exception:
                return
            if not raw:
                return
            try:
                message = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            self._on_message(message)

    def _fail(self, exc: Exception) -> None:
        if not self._closed.is_set():
            self._closed.set()
            self._on_message({"type": "error", "message": str(exc), "fatal": True})

    def close(self) -> None:
        self._closed.set()
        self._outbox.put(None)
        try:
            self._ws.close()
        except Exception:
            pass


class HearbotApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__(fg_color=BG)
        self.title("Hearbot — распознавание речи")
        self.geometry("880x700")
        self.minsize(720, 600)

        self._connection: STTConnection | None = None
        self._current_url = ""
        self._audio: pyaudio.PyAudio | None = None
        self._stream = None
        self._recording = False
        self._provider = "—"
        self._level = 0.0
        self._level_display = 0.0
        self._record_started = 0.0

        self._build_ui()
        self._set_status("offline", "Не подключён")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # --- UI ---------------------------------------------------------

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # Шапка
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 6))
        header.grid_columnconfigure(0, weight=1)

        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            titles,
            text="Hearbot",
            font=ctk.CTkFont(size=27, weight="bold"),
            text_color=TEXT,
        ).pack(anchor="w")
        ctk.CTkLabel(
            titles,
            text="Онлайн-распознавание речи",
            font=ctk.CTkFont(size=13),
            text_color=MUTED,
        ).pack(anchor="w")

        self._status_pill = ctk.CTkLabel(
            header,
            text="  Не подключён  ",
            height=32,
            corner_radius=16,
            fg_color=PANEL_2,
            text_color=MUTED,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self._status_pill.grid(row=0, column=1, sticky="e")

        # Настройки подключения
        settings = ctk.CTkFrame(
            self, fg_color=PANEL, corner_radius=16, border_width=1, border_color=BORDER
        )
        settings.grid(row=1, column=0, sticky="ew", padx=20, pady=6)
        settings.grid_columnconfigure(1, weight=1)
        settings.grid_columnconfigure(3, weight=1)

        label_font = ctk.CTkFont(size=13)
        entry_style = {
            "height": 36,
            "fg_color": BG,
            "border_color": BORDER,
            "border_width": 1,
            "text_color": TEXT,
            "font": ctk.CTkFont(size=14),
        }

        ctk.CTkLabel(settings, text="Сервер", text_color=MUTED, font=label_font).grid(
            row=0, column=0, padx=(16, 10), pady=(14, 6), sticky="w"
        )
        self._url = tk.StringVar(value=DEFAULT_URL)
        ctk.CTkEntry(settings, textvariable=self._url, **entry_style).grid(
            row=0, column=1, columnspan=3, padx=(0, 16), pady=(14, 6), sticky="ew"
        )

        ctk.CTkLabel(settings, text="Язык", text_color=MUTED, font=label_font).grid(
            row=1, column=0, padx=(16, 10), pady=(6, 14), sticky="w"
        )
        self._lang = tk.StringVar(value="ru-RU")
        ctk.CTkOptionMenu(
            settings,
            variable=self._lang,
            values=["ru-RU", "en-US"],
            width=110,
            height=34,
            fg_color=PANEL_2,
            button_color=BORDER,
            button_hover_color="#334155",
            text_color=TEXT,
            font=ctk.CTkFont(size=13),
            dropdown_font=ctk.CTkFont(size=13),
        ).grid(row=1, column=1, padx=0, pady=(6, 14), sticky="w")

        ctk.CTkLabel(settings, text="Токен", text_color=MUTED, font=label_font).grid(
            row=1, column=2, padx=(16, 10), pady=(6, 14), sticky="e"
        )
        self._token = tk.StringVar()
        ctk.CTkEntry(
            settings,
            textvariable=self._token,
            placeholder_text="если требуется",
            show="•",
            **entry_style,
        ).grid(row=1, column=3, padx=(0, 16), pady=(6, 14), sticky="ew")

        # Запись
        record = ctk.CTkFrame(
            self, fg_color=PANEL, corner_radius=16, border_width=1, border_color=BORDER
        )
        record.grid(row=2, column=0, sticky="ew", padx=20, pady=6)
        record.grid_columnconfigure(1, weight=1)

        self._record_button = ctk.CTkButton(
            record,
            text="●  Начать запись",
            width=220,
            height=64,
            corner_radius=16,
            font=ctk.CTkFont(size=17, weight="bold"),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="#04211d",
            command=self._toggle,
        )
        self._record_button.grid(row=0, column=0, rowspan=3, padx=16, pady=16)

        self._timer = ctk.CTkLabel(
            record,
            text="00:00.0",
            font=ctk.CTkFont(family="Consolas", size=30, weight="bold"),
            text_color=TEXT,
        )
        self._timer.grid(row=0, column=1, sticky="w", padx=(0, 16), pady=(16, 0))

        self._hint = ctk.CTkLabel(
            record,
            text="Нажмите кнопку или F9, чтобы начать запись",
            text_color=MUTED,
            font=ctk.CTkFont(size=13),
            anchor="w",
        )
        self._hint.grid(row=1, column=1, sticky="ew", padx=(0, 16))

        self._meter = ctk.CTkProgressBar(
            record,
            height=10,
            corner_radius=5,
            progress_color=ACCENT,
            fg_color="#1e293b",
        )
        self._meter.set(0)
        self._meter.grid(row=2, column=1, sticky="ew", padx=(0, 16), pady=(6, 18))

        # Текст
        transcript_card = ctk.CTkFrame(
            self, fg_color=PANEL, corner_radius=16, border_width=1, border_color=BORDER
        )
        transcript_card.grid(row=3, column=0, sticky="nsew", padx=20, pady=6)
        transcript_card.grid_columnconfigure(0, weight=1)
        transcript_card.grid_rowconfigure(2, weight=1)

        bar = ctk.CTkFrame(transcript_card, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        bar.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            bar,
            text="Распознанный текст",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=TEXT,
        ).grid(row=0, column=0, sticky="w")

        small_button = {
            "width": 104,
            "height": 28,
            "fg_color": PANEL_2,
            "hover_color": BORDER,
            "border_width": 1,
            "border_color": BORDER,
            "text_color": TEXT,
            "font": ctk.CTkFont(size=12),
        }
        ctk.CTkButton(bar, text="Копировать", command=self._copy, **small_button).grid(
            row=0, column=1, padx=(8, 0)
        )
        ctk.CTkButton(bar, text="Очистить", command=self._clear, **small_button).grid(
            row=0, column=2, padx=(8, 0)
        )

        self._partial = ctk.CTkLabel(
            transcript_card,
            text="",
            text_color=ACCENT,
            anchor="w",
            font=ctk.CTkFont(size=14),
        )
        self._partial.grid(row=1, column=0, sticky="ew", padx=18)

        self._transcript = ctk.CTkTextbox(
            transcript_card,
            fg_color=BG,
            border_width=1,
            border_color=BORDER,
            corner_radius=12,
            text_color=TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=15),
            wrap="word",
        )
        self._transcript.grid(row=2, column=0, sticky="nsew", padx=16, pady=(4, 16))
        self._transcript.configure(state="disabled")

        ctk.CTkLabel(
            self,
            text="F9 — старт/стоп   ·   Ctrl+L — очистить",
            text_color="#64748b",
            font=ctk.CTkFont(size=11),
        ).grid(row=4, column=0, pady=(2, 12))

        self.bind("<F9>", lambda _event: self._toggle())
        self.bind("<Control-l>", lambda _event: self._clear())

    # --- Статус и подсказки -----------------------------------------

    def _set_status(self, state: str, text: str) -> None:
        palette = {
            "offline": (PANEL_2, MUTED),
            "connecting": ("#422006", AMBER),
            "online": ("#042f2e", ACCENT),
            "error": ("#450a0a", DANGER),
        }
        background, foreground = palette.get(state, palette["offline"])
        self._status_pill.configure(
            text=f"  {text}  ", fg_color=background, text_color=foreground
        )

    def _set_hint(self, text: str, color: str = MUTED) -> None:
        self._hint.configure(text=text, text_color=color)

    def _set_recording_ui(self, recording: bool) -> None:
        if recording:
            self._record_button.configure(
                text="■  Остановить",
                fg_color=DANGER,
                hover_color=DANGER_HOVER,
                text_color="#200606",
            )
        else:
            self._record_button.configure(
                text="●  Начать запись",
                fg_color=ACCENT,
                hover_color=ACCENT_HOVER,
                text_color="#04211d",
            )

    # --- Текст ------------------------------------------------------

    def _append(self, text: str) -> None:
        self._transcript.configure(state="normal")
        if self._transcript.get("1.0", "end-1c").strip():
            self._transcript.insert("end", "\n")
        self._transcript.insert("end", text)
        self._transcript.see("end")
        self._transcript.configure(state="disabled")

    def _clear(self) -> None:
        self._partial.configure(text="")
        self._transcript.configure(state="normal")
        self._transcript.delete("1.0", "end")
        self._transcript.configure(state="disabled")
        self._set_hint("Текст очищен", MUTED)

    def _copy(self) -> None:
        text = self._transcript.get("1.0", "end-1c").strip()
        if not text:
            self._set_hint("Нечего копировать", AMBER)
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_hint("Текст скопирован в буфер обмена", ACCENT)

    # --- Управление записью -----------------------------------------

    def _toggle(self) -> None:
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _build_url(self) -> str:
        url = self._url.get().strip()
        params = {"lang": self._lang.get(), "platform": "windows"}
        token = self._token.get().strip()
        if token:
            params["token"] = token
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}{urlencode(params)}"

    def _start_recording(self) -> None:
        if not self._url.get().strip():
            messagebox.showerror("Hearbot", "Укажите адрес сервера")
            return

        full_url = self._build_url()

        if self._connection is not None and (
            self._connection.is_closed or self._current_url != full_url
        ):
            self._connection.close()
            self._connection = None

        if self._connection is None:
            self._set_status("connecting", "Подключение…")
            self._set_hint("Устанавливаю соединение с сервером…", AMBER)
            try:
                self._connection = STTConnection(full_url, self._on_message)
                self._current_url = full_url
            except Exception as exc:
                self._set_status("error", "Нет связи")
                self._set_hint(f"Не удалось подключиться: {exc}", DANGER)
                messagebox.showerror("Hearbot", f"Не удалось подключиться:\n{exc}")
                return

        try:
            if self._audio is None:
                self._audio = pyaudio.PyAudio()
            self._stream = self._audio.open(
                format=SAMPLE_FORMAT,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_FRAMES,
                stream_callback=self._on_audio,
            )
            self._stream.start_stream()
        except Exception as exc:
            self._cleanup_audio()
            self._set_status("error", "Микрофон")
            self._set_hint(f"Не удалось открыть микрофон: {exc}", DANGER)
            messagebox.showerror("Hearbot", f"Не удалось открыть микрофон:\n{exc}")
            return

        self._recording = True
        self._level = 0.0
        self._level_display = 0.0
        self._record_started = time.time()
        self._timer.configure(text="00:00.0")
        self._meter.set(0)
        self._set_recording_ui(True)
        self._set_hint("Идёт запись — говорите", ACCENT)
        self._tick()

    def _stop_recording(self) -> None:
        self._recording = False
        self._set_recording_ui(False)
        self._cleanup_audio()
        self._meter.set(0)
        if self._connection is not None:
            self._connection.send_control({"type": "stop"})
            self._set_hint("Распознавание…", AMBER)
        else:
            self._set_hint("Готово", MUTED)

    def _on_audio(self, in_data, frame_count, time_info, status_flags):
        if self._connection is not None and self._recording:
            self._connection.send_audio(in_data)

            samples = array("h")
            samples.frombytes(in_data)
            if samples:
                rms = math.sqrt(sum(value * value for value in samples) / len(samples))
                self._level = min(1.0, rms / 5000.0)
        return (None, pyaudio.paContinue)

    def _tick(self) -> None:
        if not self._recording:
            return

        elapsed = time.time() - self._record_started
        self._timer.configure(text=f"{int(elapsed // 60):02d}:{elapsed % 60:04.1f}")

        self._level_display += (self._level - self._level_display) * 0.35
        self._meter.set(max(0.0, min(1.0, self._level_display)))
        self._level *= 0.6
        self.after(80, self._tick)

    def _cleanup_audio(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._audio is not None:
            try:
                self._audio.terminate()
            except Exception:
                pass
            self._audio = None

    # --- Сообщения сервера ------------------------------------------

    def _on_message(self, message: dict) -> None:
        # Приходит из фонового потока — переключаемся в UI-поток.
        self.after(0, lambda: self._handle_message(message))

    def _handle_message(self, message: dict) -> None:
        kind = message.get("type")

        if kind == "ready":
            self._provider = message.get("provider", "—")
            self._set_status("online", f"Онлайн · {self._provider}")
            if self._recording:
                self._set_hint("Идёт запись — говорите", ACCENT)
            else:
                self._set_hint("Готово к записи", MUTED)
            return

        if kind == "control":
            action = message.get("action")
            if action == "stop_recording" and self._recording:
                self._stop_recording()
            elif action == "start_recording" and not self._recording:
                self._start_recording()
            return

        if kind == "partial":
            self._partial.configure(text=(message.get("text") or "").strip())
            return

        if kind == "final":
            text = (message.get("text") or "").strip()
            self._partial.configure(text="")
            if text:
                self._append(text)
            self._set_hint(
                "Идёт запись — говорите" if self._recording else "Готово",
                ACCENT if self._recording else MUTED,
            )
            return

        if kind == "error":
            message_text = message.get("message", "")
            self._partial.configure(text="")
            if message.get("fatal"):
                self._set_status("error", "Нет связи")
                self._set_hint(f"Соединение потеряно: {message_text}", DANGER)
                if self._connection is not None:
                    self._connection.close()
                    self._connection = None
                if self._recording:
                    self._recording = False
                    self._set_recording_ui(False)
                    self._cleanup_audio()
            else:
                self._set_hint(f"Ошибка распознавания: {message_text}", DANGER)
                if self._provider != "—":
                    self._set_status("online", f"Онлайн · {self._provider}")

    # --- Завершение -------------------------------------------------

    def _on_close(self) -> None:
        if self._recording:
            self._recording = False
            self._cleanup_audio()
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        self.destroy()


def main() -> None:
    ctk.set_appearance_mode("dark")
    app = HearbotApp()
    app.mainloop()


if __name__ == "__main__":
    main()
