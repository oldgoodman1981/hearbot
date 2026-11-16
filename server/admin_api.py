"""REST API панели управления Hearbot."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from .config import settings
from .manager import ConnectionManager


class ControlRequest(BaseModel):
    action: str


def require_admin(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    token: str | None = Query(default=None),
) -> None:
    """Проверка админ-токена. Если HEARBOT_ADMIN_TOKEN пуст — доступ открыт."""
    if not settings.admin_token:
        return
    if (x_admin_token or token) != settings.admin_token:
        raise HTTPException(status_code=401, detail="Неверный админ-токен")


def create_router(manager: ConnectionManager) -> APIRouter:
    router = APIRouter(
        prefix="/api",
        tags=["admin"],
        dependencies=[Depends(require_admin)],
    )

    @router.get("/state")
    async def state() -> dict:
        """Полный снимок состояния для панели."""
        return manager.snapshot()

    @router.get("/clients")
    async def clients() -> list[dict]:
        return manager.clients()

    @router.post("/clients/{client_id}/control")
    async def control(client_id: str, request: ControlRequest) -> dict:
        session = manager.get(client_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Клиент не найден")

        action = request.action

        if action == "disconnect":
            await manager.disconnect(session)
            return {"status": "disconnected", "client_id": client_id}

        if action not in {"start_recording", "stop_recording"}:
            raise HTTPException(status_code=400, detail="Неизвестное действие")

        sent = await manager.send_control(session, action)
        if not sent:
            raise HTTPException(status_code=502, detail="Не удалось отправить команду")
        return {"status": "ok", "client_id": client_id, "action": action}

    return router
