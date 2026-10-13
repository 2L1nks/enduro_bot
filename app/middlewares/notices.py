"""Уборка уведомлений после действия человека."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.utils.chat import drop_notices


class NoticeCleanupMiddleware(BaseMiddleware):
    """Убирает уведомления, когда человек что-то нажал или написал.

    Подтверждение записи, напоминание, перенос приходят без спроса и
    встают под экран. Пока человек ничего не делает — висят, чтобы их
    можно было прочитать. Как только он нажал кнопку или ввёл текст,
    значит, видел: уведомление убирается, экран снова оказывается
    последним сообщением в чате.

    Чистим до обработчика, а не после: убираем только то, что человек
    видел в момент нажатия. Уведомление, которое этот же обработчик
    сейчас отправит — например, админ записал в группу сам себя, —
    должно дожить до следующего действия, а не исчезнуть мгновенно.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        bot = data.get("bot")
        if tg_user is not None and bot is not None:
            await drop_notices(bot, tg_user.id)
        return await handler(event, data)
