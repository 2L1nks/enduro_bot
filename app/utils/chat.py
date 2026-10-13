"""Уборка за собой в чате.

Бот держит один «живой» экран на чат и помнит, какие сообщения
он дополнительно прислал (фото, файлы). При переходе в другой раздел
лишнее убирается, иначе чат зарастает.

Состояние живёт в памяти процесса, а не в FSM: многие обработчики
вызывают state.clear(), и ссылка на экран терялась бы вместе
с данными формы. После перезапуска бот просто пришлёт новый экран —
старые сообщения останутся в истории, это не мешает.
"""
from __future__ import annotations

from aiogram import Bot

from app.utils.logger import get_logger

log = get_logger(__name__)

#: chat_id -> id сообщений, которые надо убрать при следующем переходе
_EXTRAS: dict[int, list[int]] = {}
#: chat_id -> id текущего экрана
_SCREEN: dict[int, int] = {}
#: chat_id -> id уведомлений, пришедших без спроса: подтверждение
#: записи, напоминание, перенос. Висят, пока человек не нажмёт что-то —
#: раз нажал, значит видел.
_NOTICES: dict[int, list[int]] = {}

#: Чтобы словари не росли бесконечно в многолюдном чате
_MAX_EXTRAS = 30


def track(chat_id: int, *message_ids: int) -> None:
    """Запомнить сообщения, которые надо убрать при следующем переходе."""
    ids = _EXTRAS.setdefault(chat_id, [])
    ids.extend(message_ids)
    del ids[:-_MAX_EXTRAS]


def remember_screen(chat_id: int, message_id: int) -> None:
    _SCREEN[chat_id] = message_id


def current_screen(chat_id: int) -> int | None:
    return _SCREEN.get(chat_id)


async def drop_message(bot: Bot, chat_id: int, message_id: int) -> None:
    """Удалить одно сообщение, не падая, если уже нельзя.

    Telegram не даёт удалять сообщения старше 48 часов и чужие,
    поэтому ошибка здесь — обычное дело, а не повод ломать переход.
    """
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:  # noqa: BLE001
        pass


async def drop_tracked(bot: Bot, chat_id: int) -> None:
    """Убрать фото и файлы предыдущего раздела."""
    ids = _EXTRAS.pop(chat_id, [])
    for message_id in ids:
        await drop_message(bot, chat_id, message_id)


def remember_notice(chat_id: int, message_id: int) -> None:
    """Запомнить уведомление, чтобы убрать его после следующего действия."""
    ids = _NOTICES.setdefault(chat_id, [])
    ids.append(message_id)
    del ids[:-_MAX_EXTRAS]


async def drop_notices(bot: Bot, chat_id: int) -> int:
    """Убрать уведомления, которые человек уже увидел."""
    ids = _NOTICES.pop(chat_id, [])
    for message_id in ids:
        await drop_message(bot, chat_id, message_id)
    return len(ids)
