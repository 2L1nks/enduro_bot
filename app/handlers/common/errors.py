"""Единый перехват ошибок.

Доменные ошибки показываем человеку их собственным текстом,
всё остальное — нейтральным сообщением, а подробности в лог.
Пользователь не должен видеть стектрейс или название таблицы.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, ErrorEvent, Message

from app.exceptions import DomainError
from app.utils.chat import remember_notice
from app.utils.logger import get_logger

log = get_logger(__name__)
router = Router(name="errors")

GENERIC = "Что-то пошло не так. Попробуйте ещё раз или напишите администратору."


@router.error()
async def on_error(event: ErrorEvent) -> bool:
    exc = event.exception

    # Повторное нажатие кнопки, когда текст не изменился —
    # Telegram считает это ошибкой, пользователю она не нужна
    if isinstance(exc, TelegramBadRequest) and "message is not modified" in str(exc):
        if event.update.callback_query:
            await event.update.callback_query.answer()
        return True

    if isinstance(exc, DomainError):
        log.info("Доменная ошибка: %s", exc)
        text = exc.user_message
    else:
        log.exception("Необработанная ошибка при обработке апдейта", exc_info=exc)
        text = GENERIC

    update = event.update
    try:
        if isinstance(update.callback_query, CallbackQuery):
            await update.callback_query.answer(text, show_alert=True)
        elif isinstance(update.message, Message):
            # Как уведомление: уберётся при следующем действии
            sent = await update.message.answer(text)
            remember_notice(sent.chat.id, sent.message_id)
    except Exception:  # noqa: BLE001 — сообщить не вышло, но падать нельзя
        log.warning("Не удалось доставить сообщение об ошибке")

    return True
