"""Один экран на чат.

Бот показывает ровно одно «живое» сообщение с меню. Переход в другой
раздел не плодит сообщения, а переписывает это. Когда переписать
нельзя — под кнопками оказался файл или фото, — старое убирается
и шлётся новое.

Без этого чат зарастал: каждое нажатие оставляло экран предыдущего
раздела, а выгрузка Excel ещё и раздваивала меню.
"""
from __future__ import annotations

from aiogram import Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.utils.chat import (
    current_screen,
    drop_message,
    drop_tracked,
    remember_screen,
    track,
)
from app.utils.logger import get_logger

log = get_logger(__name__)

#: Невидимые символы, растягивающие пузырь сообщения на всю ширину.
#: Telegram подгоняет ширину под самую длинную строку, поэтому
#: короткий текст схлопывал кнопки и меню каждый раз выглядело
#: по-разному. Брайлевский пробел не печатается, но занимает место.
#:
#: Единственное место, где настраивается ширина меню. На десктопе
#: пузырь шире, чем на телефоне, поэтому распорка намеренно с запасом:
#: если она не влезла в строку, перенос всё равно не виден, зато
#: ширина упирается в край и больше не зависит от текста внутри.
WIDTH_BLANK = "⠀"
WIDTH_HOLDER = WIDTH_BLANK * 46


def wide(text: str) -> str:
    """Текст с фиксированной шириной пузыря.

    Повторный вызов ничего не добавляет: распорку ставит screen при
    показе, и случайный ручной `wide()` в хендлере не удвоит её.
    """
    if text.endswith(WIDTH_HOLDER):
        return text
    return f"{text}\n{WIDTH_HOLDER}"


def remember(chat_id: int, message_id: int) -> None:
    remember_screen(chat_id, message_id)


async def show(
    call: CallbackQuery,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
    *,
    cleanup: bool = True,
) -> None:
    """Показать экран на месте нажатой кнопки."""
    bot: Bot = call.bot
    chat_id = call.message.chat.id

    if cleanup:
        await drop_tracked(bot, chat_id)

    try:
        await call.message.edit_text(wide(text), reply_markup=keyboard)
        remember_screen(chat_id, call.message.message_id)
        return
    except Exception:  # noqa: BLE001 — под кнопкой файл, фото или оно удалено
        pass

    await drop_message(bot, chat_id, call.message.message_id)
    sent = await bot.send_message(chat_id, wide(text), reply_markup=keyboard)
    remember_screen(chat_id, sent.message_id)


async def replace(
    bot: Bot,
    chat_id: int,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> int:
    """Новое сообщение вместо прежнего экрана.

    Нужно там, где редактировать нечего: ответ на команду или
    на введённый текст. Прежний экран убирается, чтобы не дублировался.
    """
    await drop_tracked(bot, chat_id)
    previous = current_screen(chat_id)
    if previous:
        await drop_message(bot, chat_id, previous)

    sent = await bot.send_message(chat_id, wide(text), reply_markup=keyboard)
    remember_screen(chat_id, sent.message_id)
    return sent.message_id


def _typed_by_person(message: Message) -> bool:
    """Сообщение написал человек, а не бот (call.message — сообщение бота)."""
    return bool(message.from_user and not message.from_user.is_bot)


async def reply(
    message: Message, text: str, keyboard: InlineKeyboardMarkup | None = None
) -> int:
    """Следующий шаг формы в ответ на ввод.

    Экран заменяется, а то, что человек напечатал («178 75 43»,
    телефон), убирается — его ответ уже виден на новом экране.
    Иначе форма из пяти шагов оставляла пять реплик в чате.
    """
    message_id = await replace(message.bot, message.chat.id, text, keyboard)
    if _typed_by_person(message):
        await drop_message(message.bot, message.chat.id, message.message_id)
    return message_id


async def warn(message: Message, text: str) -> None:
    """Подсказка при неверном вводе.

    Экран с вопросом остаётся — человеку надо видеть, что от него
    хотят. Подсказка и неверный ввод уберутся на следующем шаге.
    """
    sent = await message.answer(text)
    ids = [sent.message_id]
    if _typed_by_person(message):
        ids.append(message.message_id)
    track(message.chat.id, *ids)
