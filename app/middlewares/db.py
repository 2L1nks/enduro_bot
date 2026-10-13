"""Сессия БД на каждое обновление."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.db.session import session_factory


class DatabaseMiddleware(BaseMiddleware):
    """Открывает сессию на обработку апдейта и коммитит при успехе.

    Коммит один на весь апдейт: хендлер может сделать несколько
    изменений, и либо применятся все, либо ни одного.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with session_factory() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
