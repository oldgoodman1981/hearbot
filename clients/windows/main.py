"""Hearbot: клиент для Windows.

Захватывает микрофон, отправляет PCM 16-bit mono 16 kHz на сервер по
WebSocket и показывает распознанный текст.

Запуск:
    pip install -r clients/windows/requirements.txt
    python clients/windows/main.py
"""

from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.parse import urlencode

import pyaudio
import websocket

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_FORMAT = pyaudio.paInt16
CHUNK_FRAMES = 800  # 50 мс
DEFAULT_URL = "ws://127.0.0.1:8000/ws/stt"


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
            self._on_message({"type": "error", "message": str(exc)})

    def close(self) -> None:
        self._closed.set()
        self._outbox.put(None)
        try:
            self._ws.close()
        except Exception:
            pass


class HearbotApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Hearbot — онлайн-распознавание речи")
        self.geometry("760x540")
        self.minsize(560, 420)

        self._connection: STTConnection | None = None
        self._audio: pyaudio.PyAudio | None = None
        self._stream = None
        self._recording = False

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # --- UI ---------------------------------------------------------

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        top = ttk.Frame(self)
        top.pack(fill="x", **pad)

        ttk.Label(top, text="Сервер:").pack(side="left")
        self._url = tk.StringVar(value=DEFAULT_URL)
        ttk.Entry(top, textvariable=self._url).pack(
            side="left", fill="x", expand=True, padx=(6, 6)
        )

        ttk.Label(top, text="Язык:").pack(side="left")
        self._lang = tk.StringVar(value="ru-RU")
        ttk.Combobox(
            top,
            textvariable=self._lang,
            values=("ru-RU", "en-US"),
            width=8,
            state="readonly",
        ).pack(side="left")

        controls = ttk.Frame(self)
        controls.pack(fill="x", **pad)

        self._button = ttk.Button(controls, text="Начать запись", command=self._toggle)
        self._button.pack(side="left")

        self._status = tk.StringVar(value="Готово")
        ttk.Label(controls, textvariable=self._status).pack(side="left", padx=12)

        ttk.Button(controls, text="Очистить", command=self._clear).pack(side="right")

        self._partial = tk.StringVar(value="")
        ttk.Label(
            self,
            textvariable=self._partial,
            foreground="#2563eb",
            wraplength=720,
            justify="left",
        ).pack(fill="x", padx=8)

        self._transcript = tk.Text(self, wrap="word", height=18)
        self._transcript.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        self._transcript.configure(state="disabled")

    def _set_status(self, text: str) -> None:
        self._status.set(text)

    def _append(self, text: str) -> None:
        self._transcript.configure(state="normal")
        if self._transcript.get("1.0", "end-1c"):
            self._transcript.insert("end", "\n")
        self._transcript.insert("end", text)
        self._transcript.see("end")
        self._transcript.configure(state="disabled")

    def _clear(self) -> None:
        self._partial.set("")
        self._transcript.configure(state="normal")
        self._transcript.delete("1.0", "end")
        self._transcript.configure(state="disabled")

    # --- Управление записью -----------------------------------------

    def _toggle(self) -> None:
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        url = self._url.get().strip()
        if not url:
            messagebox.showerror("Hearbot", "Укажите адрес сервера")
            return

        if self._connection is None:
            separator = "&" if "?" in url else "?"
            full_url = f"{url}{separator}{urlencode({'lang': self._lang.get(), 'platform': 'windows'})}"
            self._set_status("Подключение…")
            try:
                self._connection = STTConnection(full_url, self._on_message)
            except Exception as exc:
                self._set_status("Готово")
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
            self._set_status("Готово")
            messagebox.showerror("Hearbot", f"Не удалось открыть микрофон:\n{exc}")
            return

        self._recording = True
        self._button.configure(text="Остановить")
        self._set_status("Запись…")

    def _stop_recording(self) -> None:
        self._recording = False
        self._button.configure(text="Начать запись")
        self._cleanup_audio()
        if self._connection is not None:
            self._connection.send_control({"type": "stop"})
            self._set_status("Распознавание…")
        else:
            self._set_status("Готово")

    def _on_audio(self, in_data, frame_count, time_info, status_flags):
        if self._connection is not None and self._recording:
            self._connection.send_audio(in_data)
        return (None, pyaudio.paContinue)

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
            provider = message.get("provider", "?")
            self._set_status(f"Подключено ({provider})")
            return

        if kind == "control":
            action = message.get("action")
            if action == "stop_recording" and self._recording:
                self._stop_recording()
            elif action == "start_recording" and not self._recording:
                self._start_recording()
            return

        if kind == "partial":
            self._partial.set((message.get("text") or "").strip())
            return

        if kind == "final":
            text = (message.get("text") or "").strip()
            self._partial.set("")
            if text:
                self._append(text)
            self._set_status("Запись…" if self._recording else "Готово")
            return

        if kind == "error":
            self._partial.set("")
            self._set_status("Ошибка")
            self._append(f"[ошибка] {message.get('message', '')}")
            if self._connection is not None:
                self._connection.close()
                self._connection = None

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
    app = HearbotApp()
    app.mainloop()


if __name__ == "__main__":
    main()
