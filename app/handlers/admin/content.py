"""Редактирование инфо-раздела из бота."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InfoContent, User
from app.exceptions import ValidationError
from app.keyboards.admin import back_to_admin, info_edit_keyboard, info_sections_admin
from app.keyboards.callbacks import AdminCB, InfoEditCB
from app.states.admin import InfoForm
from app.utils.formatters import escape
from app.utils import screen
from app.utils.logger import get_logger
from app.utils.validators import clean_text

log = get_logger(__name__)
router = Router(name="admin_content")


@router.callback_query(AdminCB.filter(F.action == "info"))
async def sections(
    call: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    await state.clear()
    rows = await session.scalars(
        select(InfoContent).order_by(InfoContent.sort_order, InfoContent.id)
    )
    items = [(c.id, c.title) for c in rows]

    await screen.show(call, "<b>Инфо-раздел</b>\n\nВыберите, что редактировать:", info_sections_admin(items))
    await call.answer()


@router.callback_query(InfoEditCB.filter(F.action == "pick"))
async def pick_section(
    call: CallbackQuery, callback_data: InfoEditCB, session: AsyncSession
) -> None:
    content = await session.get(InfoContent, callback_data.section_id)
    if content is None:
        await call.answer("Раздел не найден.", show_alert=True)
        return

    media_count = len(content.media or [])
    await screen.show(call, f"<b>{escape(content.title)}</b>\n\n"
        f"{escape(content.body) or '<i>текст не заполнен</i>'}\n\n"
        f"Медиа: {media_count}", info_edit_keyboard(content.id))
    await call.answer()


@router.callback_query(InfoEditCB.filter(F.action == "text"))
async def ask_text(
    call: CallbackQuery, callback_data: InfoEditCB, state: FSMContext
) -> None:
    await state.update_data(section_id=callback_data.section_id)
    await state.set_state(InfoForm.body)
    await screen.show(call, "Отправьте новый текст раздела.\n\n"
        "Поддерживается разметка Telegram. Старый текст заменится целиком.", back_to_admin("✖ Отмена"))
    await call.answer()


@router.message(InfoForm.body)
async def save_text(
    message: Message, user: User, session: AsyncSession, state: FSMContext
) -> None:
    try:
        body = clean_text(message.html_text or message.text or "", max_length=3500)
    except ValidationError as exc:
        await message.answer(exc.user_message)
        return

    data = await state.get_data()
    content = await session.get(InfoContent, data["section_id"])
    if content is None:
        await message.answer("Раздел исчез. Начните заново.")
        await state.clear()
        return

    content.body = body
    content.updated_by_id = user.id
    await session.flush()
    await state.clear()

    await message.answer(
        f"✅ Текст раздела «{escape(content.title)}» обновлён.\n\n"
        "Клиенты увидят изменения сразу.",
        reply_markup=info_edit_keyboard(content.id),
    )
    log.info("Админ %s обновил раздел %s", user.id, content.slug)


@router.callback_query(InfoEditCB.filter(F.action == "media"))
async def ask_media(
    call: CallbackQuery, callback_data: InfoEditCB, state: FSMContext
) -> None:
    await state.update_data(section_id=callback_data.section_id)
    await state.set_state(InfoForm.media)
    await screen.show(call, "Пришлите фото или видео — оно добавится в галерею раздела.\n\n"
        "Можно прислать несколько подряд. Когда закончите, "
        "нажмите «Отмена» — всё уже сохранено.", back_to_admin("✓ Готово"))
    await call.answer()


@router.message(InfoForm.media, F.photo | F.video)
async def save_media(
    message: Message, user: User, session: AsyncSession, state: FSMContext
) -> None:
    data = await state.get_data()
    content = await session.get(InfoContent, data["section_id"])
    if content is None:
        await message.answer("Раздел исчез. Начните заново.")
        await state.clear()
        return

    if message.photo:
        # Берём самый крупный размер: он последний в списке
        item = {
            "type": "photo",
            "file_id": message.photo[-1].file_id,
            "caption": message.caption or "",
        }
    else:
        item = {
            "type": "video",
            "file_id": message.video.file_id,
            "caption": message.caption or "",
        }

    # JSONB-поле нужно переприсвоить целиком, иначе SQLAlchemy
    # не заметит изменения внутри списка
    media = list(content.media or [])
    if len(media) >= 10:
        await message.answer("В разделе уже 10 медиа — это предел.")
        return

    media.append(item)
    content.media = media
    content.updated_by_id = user.id
    await session.flush()

    await message.answer(f"Добавлено. Всего медиа в разделе: {len(media)}")


@router.message(InfoForm.media)
async def media_wrong_type(message: Message) -> None:
    await message.answer("Нужно фото или видео. Или нажмите «Готово».")


@router.callback_query(InfoEditCB.filter(F.action == "clear"))
async def clear_media(
    call: CallbackQuery, callback_data: InfoEditCB, user: User, session: AsyncSession
) -> None:
    content = await session.get(InfoContent, callback_data.section_id)
    if content is None:
        await call.answer("Раздел не найден.", show_alert=True)
        return

    content.media = []
    content.updated_by_id = user.id
    await session.flush()

    await screen.show(call, f"<b>{escape(content.title)}</b>\n\nГалерея очищена.", info_edit_keyboard(content.id))
    await call.answer("Очищено")
