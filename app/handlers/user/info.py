"""Раздел «О прокате».

Порядок вывода принципиален: сначала уходят фото и видео, и только
потом — текст с кнопками. Тогда меню оказывается в самом низу,
под постом, а не над ним.

Медиа нельзя приклеить к текстовому сообщению, поэтому они идут
отдельными сообщениями. Их идентификаторы запоминаются и стираются
при переходе в другой раздел, чтобы чат не зарастал картинками.
"""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InfoContent
from app.keyboards.callbacks import InfoCB, MenuCB
from app.keyboards.common import info_sections_keyboard
from app.utils import screen
from app.utils.chat import drop_message, drop_tracked, track
from app.utils.formatters import escape
from app.utils.logger import get_logger

log = get_logger(__name__)
router = Router(name="info")

MAX_MEDIA = 10
#: Длина подписи к медиа ограничена Telegram
CAPTION_LIMIT = 1024


@router.callback_query(MenuCB.filter(F.action == "info"))
async def show_sections(
    call: CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot
) -> None:
    rows = await session.scalars(
        select(InfoContent).order_by(InfoContent.sort_order, InfoContent.id)
    )
    sections = [(c.id, c.title) for c in rows]

    if not sections:
        await call.answer("Раздел пока не заполнен.", show_alert=True)
        return

    text = "<b>О прокате</b>\n\nВыберите, что интересует:"
    keyboard = info_sections_keyboard(sections)

    await screen.show(call, text, keyboard)
    await call.answer()


@router.callback_query(InfoCB.filter())
async def show_section(
    call: CallbackQuery,
    callback_data: InfoCB,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    content = await session.get(InfoContent, callback_data.section_id)
    if content is None:
        await call.answer("Раздел не найден.", show_alert=True)
        return

    chat_id = call.message.chat.id

    # 1. Снимаем медиа прошлого раздела
    await drop_tracked(bot, chat_id)
    # 2. Убираем сообщение, на котором была нажата кнопка —
    #    иначе меню осталось бы над новыми фото
    await drop_message(bot, chat_id, call.message.message_id)

    # 3. Сначала медиа
    media = (content.media or [])[:MAX_MEDIA]
    sent_ids: list[int] = []
    for item in media:
        file_id = item.get("file_id")
        if not file_id:
            continue
        caption = (item.get("caption") or "")[:CAPTION_LIMIT] or None
        try:
            if item.get("type") == "video":
                msg = await bot.send_video(chat_id, file_id, caption=caption)
            else:
                msg = await bot.send_photo(chat_id, file_id, caption=caption)
            sent_ids.append(msg.message_id)
        except Exception:  # noqa: BLE001 — битый file_id не должен ломать раздел
            log.warning("Не удалось отправить медиа раздела %s", content.slug)

    if sent_ids:
        track(call.message.chat.id, *sent_ids)

    # 4. И только потом текст с кнопками — он остаётся внизу
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="‹ К разделам", callback_data=MenuCB(action="info").pack()
        ),
        InlineKeyboardButton(
            text="В меню", callback_data=MenuCB(action="home").pack()
        ),
    )

    body = content.body or "Раздел пока не заполнен."
    sent = await bot.send_message(
        chat_id,
        screen.wide(f"<b>{escape(content.title)}</b>\n\n{body}"),
        reply_markup=kb.as_markup(),
    )
    # Текст с кнопками — последний, он же становится текущим экраном
    screen.remember(chat_id, sent.message_id)
    await call.answer()
