"""«Мои фото» в личном кабинете.

Как и в разделе «О прокате»: снимки уходят первыми, меню — последним,
а при переходе в другой раздел картинки убираются из чата.
"""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import User
from app.keyboards.callbacks import MenuCB
from app.keyboards.common import back_to_menu
from app.services import photos as photo_service
from app.utils.chat import drop_message, drop_tracked, track
from app.utils import screen
from app.utils.logger import get_logger

log = get_logger(__name__)
router = Router(name="user_photos")

BATCH = 10


@router.callback_query(MenuCB.filter(F.action == "photos"))
async def my_photos(
    call: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    chat_id = call.message.chat.id
    await drop_tracked(bot, chat_id)

    items = await photo_service.user_photos(session, user.id, limit=BATCH)

    # Если фото лежат на Яндекс.Диске, даём ссылку на личную папку.
    # Она ведёт только в его папку: имя случайное, чужие снимки
    # по этой ссылке недоступны.
    if settings.storage_backend == "yadisk" and user.disk_public_url:
        total = await photo_service.count_photos(session, user.id)
        kb = InlineKeyboardBuilder()
        kb.row(
            InlineKeyboardButton(
                text="📂 Открыть мои фото", url=user.disk_public_url
            )
        )
        kb.row(
            InlineKeyboardButton(
                text="В меню", callback_data=MenuCB(action="home").pack()
            )
        )
        await screen.show(
            call,
            f"<b>Ваши фото</b>\n\n"
            f"Снимков в папке: {total}\n\n"
            "Это ваша личная папка — ссылка ведёт только в неё, "
            "фото других клиентов по ней не открыть.",
            kb.as_markup(),
        )
        await call.answer()
        return

    if not items:
        await screen.show(
            call,
            "Ваших фото пока нет.\n\n"
            "Снимки с выездов загружает администратор — "
            "они появятся здесь после поездки.",
            back_to_menu(),
        )
        await call.answer()
        return

    total = await photo_service.count_photos(session, user.id)
    await call.answer(f"Отправляю {len(items)}…")

    # Сообщение с кнопкой уйдёт последним, поэтому текущее убираем
    await drop_message(bot, chat_id, call.message.message_id)

    sent_ids: list[int] = []
    for photo in items:
        try:
            message_id = await photo_service.send_photo(
                bot, session, chat_id, photo
            )
            if message_id:
                sent_ids.append(message_id)
        except Exception:  # noqa: BLE001
            log.exception("Не удалось отправить фото id=%s", photo.id)

    if sent_ids:
        track(call.message.chat.id, *sent_ids)

    tail = f"\nВсего у вас фото: {total}." if total > len(items) else ""
    # Кнопка уходит последней, под снимками, и становится экраном —
    # иначе /start не знал бы о ней и оставлял второе меню
    sent = await bot.send_message(
        chat_id,
        screen.wide(f"Отправлено: {len(sent_ids)}.{tail}"),
        reply_markup=back_to_menu(),
    )
    screen.remember(chat_id, sent.message_id)
