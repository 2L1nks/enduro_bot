"""Клиенты: поиск, карточка, блокировка, заметки, фото.

Раздел закрывает требование «поиск клиента» и даёт админу одно место,
где видно всё про человека: параметры, поездки, деньги, снимки.
"""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RU_BOOKING_STATUS, User
from app.exceptions import ValidationError
from app.keyboards.admin import back_to_admin
from app.keyboards.callbacks import AdminCB, ClientCB
from app.repositories import admin as admin_repo
from app.repositories import booking as booking_repo
from app.services import photos as photo_service
from app.states.admin import ClientForm
from app.utils.formatters import local, escape, money, user_card, user_label
from app.utils import screen
from app.utils.chat import drop_tracked, track
from app.utils.logger import get_logger
from app.utils.timeslots import humanize_duration
from app.utils.validators import clean_text

log = get_logger(__name__)
router = Router(name="admin_clients")


def _card_keyboard(person: User, photo_count: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="📋 Записи",
            callback_data=ClientCB(action="bookings", user_id=person.id).pack(),
        ),
        InlineKeyboardButton(
            text=f"📷 Фото ({photo_count})",
            callback_data=ClientCB(action="photos", user_id=person.id).pack(),
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="📞 Телефон",
            callback_data=ClientCB(action="phone", user_id=person.id).pack(),
        ),
        InlineKeyboardButton(
            text="📝 Заметка",
            callback_data=ClientCB(action="note", user_id=person.id).pack(),
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="✅ Разблокировать" if person.is_blocked else "⛔ Заблокировать",
            callback_data=ClientCB(action="block", user_id=person.id).pack(),
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="🔍 Другой клиент",
            callback_data=AdminCB(action="clients").pack(),
        ),
        InlineKeyboardButton(
            text="‹ В админку", callback_data=AdminCB(action="home").pack()
        ),
    )
    return kb.as_markup()


async def _show_card(
    call: CallbackQuery, session: AsyncSession, user_id: int, note: str = ""
) -> None:
    person = await session.get(User, user_id)
    if person is None:
        await call.answer("Клиент не найден.", show_alert=True)
        return

    stats = await booking_repo.user_stats(session, person.id)
    photo_count = await photo_service.count_photos(session, person.id)

    text = (
        user_card(person)
        + f"\n\nПоездок: {stats['visits']}"
        + f"\nВремя в выездах: {humanize_duration(stats['minutes'])}"
        + f"\nПотрачено: {money(stats['spent'])}"
        + f"\nПредстоит: {stats['upcoming']}"
    )
    if note:
        text += f"\n\n{note}"

    await screen.show(call, text, _card_keyboard(person, photo_count))


@router.callback_query(AdminCB.filter(F.action == "clients"))
async def start_search(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ClientForm.search)
    await screen.show(
        call,
        "<b>Поиск клиента</b>\n\n"
        "Введите имя, @username или Telegram ID.", back_to_admin("✖ Отмена"))
    await call.answer()


@router.message(ClientForm.search)
async def do_search(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Введите хотя бы два символа.")
        return

    found = await admin_repo.search_users(session, query, limit=15)
    if not found:
        await message.answer(
            f"По запросу «{escape(query)}» никого нет.\n"
            "Клиент появляется в базе после первого обращения к боту."
        )
        return

    kb = InlineKeyboardBuilder()
    for person in found:
        mark = "⛔ " if person.is_blocked else ""
        kb.row(
            InlineKeyboardButton(
                text=f"{mark}{user_label(person)}"[:60],
                callback_data=ClientCB(action="view", user_id=person.id).pack(),
            )
        )
    await message.answer(f"Найдено: {len(found)}", reply_markup=kb.as_markup())


@router.callback_query(ClientCB.filter(F.action == "view"))
async def view_client(
    call: CallbackQuery, callback_data: ClientCB, session: AsyncSession,
    state: FSMContext,
) -> None:
    await state.clear()
    await _show_card(call, session, callback_data.user_id)
    await call.answer()


@router.callback_query(ClientCB.filter(F.action == "bookings"))
async def client_bookings(
    call: CallbackQuery, callback_data: ClientCB, session: AsyncSession
) -> None:
    items = await booking_repo.user_bookings(session, callback_data.user_id)
    person = await session.get(User, callback_data.user_id)

    if not items:
        await call.answer("У клиента нет записей.", show_alert=True)
        return

    lines = [f"<b>Записи: {user_label(person)}</b>\n"]
    for b in items[:25]:
        lines.append(
            f"• №{b.id} · {local(b.starts_at):%d.%m.%Y %H:%M} · "
            f"{RU_BOOKING_STATUS[b.status]}"
        )
    if len(items) > 25:
        lines.append(f"\n…и ещё {len(items) - 25}")

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="‹ К карточке",
            callback_data=ClientCB(action="view", user_id=callback_data.user_id).pack(),
        )
    )
    await screen.show(call, "\n".join(lines), kb.as_markup())
    await call.answer()


@router.callback_query(ClientCB.filter(F.action == "photos"))
async def client_photos(
    call: CallbackQuery,
    callback_data: ClientCB,
    session: AsyncSession,
    bot: Bot,
) -> None:
    items = await photo_service.user_photos(session, callback_data.user_id, limit=10)
    if not items:
        await call.answer("У клиента нет фото.", show_alert=True)
        return

    await drop_tracked(bot, call.message.chat.id)
    await call.answer(f"Отправляю {len(items)}…")
    for photo in items:
        kb = InlineKeyboardBuilder()
        kb.row(
            InlineKeyboardButton(
                text="🗑 Удалить это фото",
                callback_data=ClientCB(
                    action="delphoto", user_id=callback_data.user_id, value=photo.id
                ).pack(),
            )
        )
        try:
            sent = await bot.send_photo(
                call.message.chat.id,
                photo.telegram_file_id or "",
                caption=photo.caption,
                reply_markup=kb.as_markup(),
            )
            # Снимки уберутся при переходе в другой раздел
            track(call.message.chat.id, sent.message_id)
        except Exception:  # noqa: BLE001 — кеш мог протухнуть, грузим из хранилища
            try:
                message_id = await photo_service.send_photo(
                    bot, session, call.message.chat.id, photo
                )
                if message_id:
                    track(call.message.chat.id, message_id)
            except Exception:
                log.exception("Не удалось показать фото id=%s", photo.id)


@router.callback_query(ClientCB.filter(F.action == "delphoto"))
async def delete_photo(
    call: CallbackQuery, callback_data: ClientCB, session: AsyncSession
) -> None:
    from app.db.models import Photo

    photo = await session.get(Photo, callback_data.value)
    if photo is None:
        await call.answer("Фото уже удалено.", show_alert=True)
        return

    await photo_service.delete_photo(session, photo)
    try:
        await call.message.delete()
    except Exception:  # noqa: BLE001
        pass
    await call.answer("Фото удалено")


@router.callback_query(ClientCB.filter(F.action == "block"))
async def toggle_block(
    call: CallbackQuery, callback_data: ClientCB, user: User, session: AsyncSession
) -> None:
    person = await session.get(User, callback_data.user_id)
    if person is None:
        await call.answer("Клиент не найден.", show_alert=True)
        return
    if person.role.is_staff:
        await call.answer("Сотрудника блокировать нельзя.", show_alert=True)
        return

    person.is_blocked = not person.is_blocked
    await session.flush()
    log.info(
        "Админ %s %s клиента %s",
        user.id,
        "заблокировал" if person.is_blocked else "разблокировал",
        person.id,
    )

    await _show_card(
        call,
        session,
        person.id,
        "⛔ Клиент заблокирован — бот перестанет ему отвечать."
        if person.is_blocked
        else "✅ Доступ восстановлен.",
    )
    await call.answer()


@router.callback_query(ClientCB.filter(F.action == "phone"))
async def ask_phone(
    call: CallbackQuery, callback_data: ClientCB, state: FSMContext
) -> None:
    await state.update_data(client_id=callback_data.user_id)
    await state.set_state(ClientForm.phone)
    await screen.show(call, "Введите телефон клиента.\nОчистить — отправьте «-».", back_to_admin("✖ Отмена"))
    await call.answer()


@router.message(ClientForm.phone)
async def save_phone(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    data = await state.get_data()
    person = await session.get(User, data.get("client_id", 0))
    if person is None:
        await message.answer("Клиент потерялся, начните заново.")
        await state.clear()
        return

    text = (message.text or "").strip()
    person.phone = None if text == "-" else text[:32]
    await session.flush()
    await state.clear()

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="‹ К карточке",
            callback_data=ClientCB(action="view", user_id=person.id).pack(),
        )
    )
    await message.answer("✅ Телефон сохранён.", reply_markup=kb.as_markup())


@router.callback_query(ClientCB.filter(F.action == "note"))
async def ask_note(
    call: CallbackQuery, callback_data: ClientCB, state: FSMContext
) -> None:
    await state.update_data(client_id=callback_data.user_id)
    await state.set_state(ClientForm.note)
    await screen.show(call, "Заметка о клиенте — видна только сотрудникам.\n"
        "Очистить — отправьте «-».", back_to_admin("✖ Отмена"))
    await call.answer()


@router.message(ClientForm.note)
async def save_note(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    data = await state.get_data()
    person = await session.get(User, data.get("client_id", 0))
    if person is None:
        await message.answer("Клиент потерялся, начните заново.")
        await state.clear()
        return

    text = (message.text or "").strip()
    if text == "-":
        person.notes = None
    else:
        try:
            person.notes = clean_text(text, max_length=1000, field="Заметка")
        except ValidationError as exc:
            await message.answer(exc.user_message)
            return
    await session.flush()
    await state.clear()

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="‹ К карточке",
            callback_data=ClientCB(action="view", user_id=person.id).pack(),
        )
    )
    await message.answer("✅ Заметка сохранена.", reply_markup=kb.as_markup())
