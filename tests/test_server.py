"""Smoke-тесты сервера на провайдере dummy (без облачных ключей)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from server.main import app


def test_health() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "provider" in payload


def test_websocket_roundtrip() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/stt?lang=ru-RU") as websocket:
        ready = websocket.receive_json()
        assert ready["type"] == "ready"
        assert ready["sample_rate"] == 16000

        # 1 секунда тишины: 16000 фреймов * 2 байта
        websocket.send_bytes(b"\x00\x00" * 16000)
        websocket.send_json({"type": "stop"})

        final = websocket.receive_json()
        assert final["type"] == "final"
        assert final["is_final"] is True
        assert final["audio_seconds"] == 1.0
        assert "1.0" in final["text"]


def test_ping_pong() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/stt") as websocket:
        assert websocket.receive_json()["type"] == "ready"
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json() == {"type": "pong"}
