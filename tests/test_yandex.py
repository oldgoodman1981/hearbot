"""Тесты разбора ошибок Yandex SpeechKit."""

from __future__ import annotations

import httpx

from server.stt.yandex import describe_error

ENDPOINT = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"


def _response(status_code: int, **kwargs) -> httpx.Response:
    request = httpx.Request("POST", ENDPOINT)
    return httpx.Response(status_code, request=request, **kwargs)


def test_permission_error_has_hint() -> None:
    response = _response(
        401,
        json={
            "error_code": "UNAUTHORIZED",
            "error_message": (
                "rpc error: code = PermissionDenied desc = Permission to "
                "[resource-manager.folder b1g5s7kc2pns1nks2g2r] denied"
            ),
        },
    )

    message = describe_error(response)

    assert "HTTP 401" in message
    assert "PermissionDenied" in message
    assert "ai.speechkit-stt.user" in message
    assert "yc.ai.speechkitStt.execute" in message


def test_error_message_from_nested_payload() -> None:
    response = _response(
        403,
        json={"error": {"message": "API key is invalid"}},
    )

    message = describe_error(response)

    assert "HTTP 403" in message
    assert "API key is invalid" in message


def test_plain_text_error_without_hint() -> None:
    response = _response(500, text="internal error")

    message = describe_error(response)

    assert message == "Yandex SpeechKit: HTTP 500: internal error"
