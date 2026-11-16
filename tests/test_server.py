"""Smoke-тесты сервера, панели управления и API (провайдер dummy)."""

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


def test_dashboard_page() -> None:
    client = TestClient(app)
    response = client.get("/admin")
    assert response.status_code == 200
    assert "Hearbot Control" in response.text
    assert "Подключённые клиенты" in response.text


def test_websocket_roundtrip() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/stt?lang=ru-RU") as websocket:
        ready = websocket.receive_json()
        assert ready["type"] == "ready"
        assert ready["sample_rate"] == 16000
        assert ready["client_id"]

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


def test_client_visible_and_admin_control() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/stt?platform=windows") as websocket:
        ready = websocket.receive_json()
        client_id = ready["client_id"]

        state = client.get("/api/state").json()
        sessions = {session["id"]: session for session in state["clients"]}
        assert client_id in sessions
        assert sessions[client_id]["platform"] == "windows"
        assert sessions[client_id]["recording"] is True

        response = client.post(
            f"/api/clients/{client_id}/control",
            json={"action": "stop_recording"},
        )
        assert response.status_code == 200

        control = websocket.receive_json()
        assert control == {"type": "control", "action": "stop_recording"}

        state = client.get("/api/state").json()
        session = next(item for item in state["clients"] if item["id"] == client_id)
        assert session["recording"] is False


def test_audio_metrics() -> None:
    client = TestClient(app)
    before = client.get("/api/state").json()["stats"]["total_utterances"]

    with client.websocket_connect("/ws/stt?platform=android") as websocket:
        websocket.receive_json()
        websocket.send_bytes(b"\x00\x00" * 8000)  # 0.5 с
        websocket.send_json({"type": "stop"})
        final = websocket.receive_json()
        assert final["type"] == "final"
        assert final["audio_seconds"] == 0.5

        state = client.get("/api/state").json()
        assert state["stats"]["total_utterances"] == before + 1
        session = next(item for item in state["clients"] if item["platform"] == "android")
        assert session["recording"] is False
        assert session["last_text"]


def test_unknown_client_control_returns_404() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/clients/does-not-exist/control",
        json={"action": "stop_recording"},
    )
    assert response.status_code == 404


def test_unknown_action_returns_400() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/stt?platform=windows") as websocket:
        client_id = websocket.receive_json()["client_id"]
        response = client.post(
            f"/api/clients/{client_id}/control",
            json={"action": "dance"},
        )
        assert response.status_code == 400
