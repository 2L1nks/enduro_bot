"""Защита от слишком частых нажатий."""
from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject


class ThrottlingMiddleware(BaseMiddleware):
    """Не чаще одного действия в rate секунд на пользователя.

    Защищает не столько от злого умысла, сколько от дребезга: человек
    жмёт кнопку календаря дважды, и бот дважды делает тяжёлый запрос
    доступности. На колбэки отвечаем тихо, без всплывающих жалоб.
    """

    def __init__(self, rate: float = 0.4):
        self.rate = rate
        self._last: dict[int, float] = defaultdict(float)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)

        now = time.monotonic()
        if now - self._last[tg_user.id] < self.rate:
            if isinstance(event, CallbackQuery):
                await event.answer()  # гасим «часики» и выходим
            return None

        self._last[tg_user.id] = now
        return await handler(event, data)
