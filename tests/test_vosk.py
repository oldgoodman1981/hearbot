"""Тесты офлайн-провайдера Vosk и выбора провайдера."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from server.stt.base import STTError
from server.stt.factory import create_provider
from server.stt.vosk import VoskProvider, _load_model


def test_extract_text() -> None:
    assert VoskProvider._extract_text('{"text": "привет мир"}') == "привет мир"


def test_extract_text_handles_empty_and_broken_json() -> None:
    assert VoskProvider._extract_text('{"text": ""}') == ""
    assert VoskProvider._extract_text("not json") == ""
    assert VoskProvider._extract_text("") == ""


def test_missing_model_has_clear_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("server.stt.vosk._model", None)
    monkeypatch.setattr("server.stt.vosk._model_path", None)

    with pytest.raises(STTError, match="Модель Vosk не найдена"):
        _load_model(tmp_path / "missing-model")


def test_factory_builds_vosk_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        "server.stt.factory.settings", SimpleNamespace(stt_provider="vosk")
    )
    assert isinstance(create_provider(), VoskProvider)


def test_factory_rejects_unknown_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        "server.stt.factory.settings", SimpleNamespace(stt_provider="unknown")
    )
    with pytest.raises(ValueError, match="Неизвестный STT_PROVIDER"):
        create_provider()
