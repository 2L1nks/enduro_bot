"""Загрузка фото админом и привязка к клиенту."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import User
from app.exceptions import DomainError
from app.keyboards.admin import back_to_admin
from app.keyboards.callbacks import AdmBookingCB, AdminCB
from app.repositories import admin as admin_repo
from app.services import photos as photo_service
from app.states.admin import PhotoForm
from app.utils.formatters import escape, user_label
from app.utils import screen
from app.utils.logger import get_logger

log = get_logger(__name__)
router = Router(name="admin_photos")


@router.callback_query(AdminCB.filter(F.action == "photos"))
async def photos_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(PhotoForm.search)
    await screen.show(call, "<b>Загрузка фото</b>\n\n"
        "Кому загружаем? Введите имя, @username или Telegram ID клиента.", back_to_admin("✖ Отмена"))
    await call.answer()


@router.message(PhotoForm.search)
async def photos_search(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Введите хотя бы два символа.")
        return

    found = await admin_repo.search_users(session, query, telegram_only=True)
    if not found:
        await message.answer(
            f"Клиент «{escape(query)}» не найден.\n"
            "Он должен хотя бы раз написать боту."
        )
        return

    kb = InlineKeyboardBuilder()
    for person in found[:10]:
        kb.row(
            InlineKeyboardButton(
                text=user_label(person)[:60],
                callback_data=AdmBookingCB(action="photo_to", value=person.id).pack(),
            )
        )
    await message.answer("Кому привязать фото?", reply_markup=kb.as_markup())


@router.callback_query(AdmBookingCB.filter(F.action == "photo_to"), PhotoForm.search)
async def photos_pick_owner(
    call: CallbackQuery,
    callback_data: AdmBookingCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    person = await session.get(User, callback_data.value)
    if person is None:
        await call.answer("Клиент не найден.", show_alert=True)
        return

    await state.update_data(owner_id=person.id)
    await state.set_state(PhotoForm.upload)

    count = await photo_service.count_photos(session, person.id)
    await screen.show(call, f"Получатель: <b>{user_label(person)}</b>\n"
        f"Сейчас у него фото: {count}\n\n"
        "Присылайте снимки — можно несколько подряд. "
        "Клиент увидит их в разделе «Мои фото».\n\n"
        "Когда закончите, нажмите «Готово».", back_to_admin("✓ Готово"))
    await call.answer()


@router.message(PhotoForm.upload, F.photo)
async def receive_photo(
    message: Message,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    data = await state.get_data()
    owner_id = data.get("owner_id")
    if not owner_id:
        await message.answer("Получатель не выбран, начните заново.")
        await state.clear()
        return

    owner = await session.get(User, owner_id)
    if owner is None:
        await message.answer("Клиент потерялся, начните заново.")
        await state.clear()
        return

    try:
        if settings.storage_backend == "yadisk":
            photo = await photo_service.upload_to_disk(
                session,
                bot,
                file_id=message.photo[-1].file_id,
                owner=owner,
                uploaded_by_id=user.id,
                caption=message.caption,
            )
        else:
            photo = await photo_service.upload_from_telegram(
                session,
                bot,
                file_id=message.photo[-1].file_id,
                owner_id=owner_id,
                uploaded_by_id=user.id,
                caption=message.caption,
            )
    except DomainError as exc:
        await message.answer(exc.user_message)
        return
    except Exception:
        log.exception("Ошибка загрузки фото")
        await message.answer(
            "Не удалось сохранить фото. Проверьте, доступно ли хранилище "
            "(папка storage или S3)."
        )
        return

    total = await photo_service.count_photos(session, owner_id)
    where = ""
    if owner.disk_public_url:
        where = "\nПапка клиента на Диске создана, доступ только у него."
    await message.answer(
        f"✅ Сохранено. Всего у клиента: {total}{where}",
        reply_markup=back_to_admin("✓ Готово"),
    )
    log.info("Админ %s загрузил фото id=%s", user.id, photo.id)


@router.message(PhotoForm.upload)
async def wrong_type(message: Message) -> None:
    await message.answer("Нужно фото. Или нажмите «Готово».")
