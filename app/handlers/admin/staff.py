"""Управление сотрудниками. Доступно только владельцу."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RU_ROLE, AuditLog, User, UserRole
from app.keyboards.admin import back_to_admin, role_choice, staff_list
from app.keyboards.callbacks import AdminCB, StaffCB
from app.repositories import admin as admin_repo
from app.states.admin import StaffForm
from app.utils.formatters import escape, user_label
from app.utils import screen
from app.utils.logger import get_logger

log = get_logger(__name__)
router = Router(name="admin_staff")


@router.callback_query(AdminCB.filter(F.action == "staff"))
async def show_staff(
    call: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    await state.clear()
    staff = await admin_repo.staff_members(session)

    lines = ["<b>Сотрудники</b>\n"]
    for person in staff:
        lines.append(f"• {user_label(person)} — {RU_ROLE[person.role]}")
    lines.append(
        "\n<i>Механик видит только парк техники. "
        "Администратор — всё, кроме управления сотрудниками.</i>"
    )

    await screen.show(call, "\n".join(lines), staff_list(staff))
    await call.answer()


@router.callback_query(StaffCB.filter(F.action == "add"))
async def add_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(StaffForm.search)
    await screen.show(
        call,
        "Кого назначаем?\n\n"
        "Введите имя, @username или Telegram ID.\n"
        "Человек должен хотя бы раз написать боту — иначе его нет в базе.",
        back_to_admin("✖ Отмена"),
    )
    await call.answer()


@router.message(StaffForm.search)
async def add_search(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Введите хотя бы два символа.")
        return

    found = await admin_repo.search_users(session, query, telegram_only=True)
    candidates = [p for p in found if p.role == UserRole.USER]

    if not candidates:
        await message.answer(
            f"Подходящих не найдено по запросу «{escape(query)}».\n\n"
            "Либо человек ещё не писал боту, либо он уже сотрудник."
        )
        return

    kb = InlineKeyboardBuilder()
    for person in candidates[:10]:
        kb.row(
            InlineKeyboardButton(
                text=user_label(person)[:60],
                callback_data=StaffCB(action="pick", user_id=person.id).pack(),
            )
        )
    await message.answer("Выберите человека:", reply_markup=kb.as_markup())


@router.callback_query(StaffCB.filter(F.action == "pick"), StaffForm.search)
async def pick_person(
    call: CallbackQuery, callback_data: StaffCB, session: AsyncSession, state: FSMContext
) -> None:
    person = await session.get(User, callback_data.user_id)
    if person is None:
        await call.answer("Человек не найден.", show_alert=True)
        return

    await state.set_state(StaffForm.role)
    await screen.show(call, f"<b>{user_label(person)}</b>\n\nКакую роль выдать?", role_choice(person.id))
    await call.answer()


@router.callback_query(StaffCB.filter(F.action == "set_role"))
async def set_role(
    call: CallbackQuery,
    callback_data: StaffCB,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    try:
        role = UserRole(callback_data.value)
    except ValueError:
        await call.answer("Неизвестная роль.", show_alert=True)
        return

    # Владельца назначить нельзя: он один и задан в .env
    if role == UserRole.OWNER:
        await call.answer("Владелец задаётся в настройках сервера.", show_alert=True)
        return

    person = await session.get(User, callback_data.user_id)
    if person is None:
        await call.answer("Человек не найден.", show_alert=True)
        return

    person.role = role
    person.added_by_id = user.id
    session.add(
        AuditLog(
            actor_id=user.id,
            action="set_role",
            entity="user",
            entity_id=person.id,
            details=role.value,
        )
    )
    await session.flush()
    await state.clear()

    staff = await admin_repo.staff_members(session)
    await screen.show(call, f"✅ {user_label(person)} теперь {RU_ROLE[role]}.\n\n"
        "Доступ появится при следующем обращении к боту.", staff_list(staff))
    await call.answer("Роль выдана")
    log.info("Владелец %s назначил %s роль %s", user.id, person.id, role.value)


@router.callback_query(StaffCB.filter(F.action == "remove"))
async def remove_staff(
    call: CallbackQuery, callback_data: StaffCB, user: User, session: AsyncSession
) -> None:
    person = await session.get(User, callback_data.user_id)
    if person is None:
        await call.answer("Человек не найден.", show_alert=True)
        return

    if person.role == UserRole.OWNER:
        await call.answer("Владельца снять нельзя.", show_alert=True)
        return

    person.role = UserRole.USER
    session.add(
        AuditLog(
            actor_id=user.id,
            action="revoke_role",
            entity="user",
            entity_id=person.id,
        )
    )
    await session.flush()

    staff = await admin_repo.staff_members(session)
    await screen.show(call, f"{user_label(person)} больше не сотрудник.", staff_list(staff))
    await call.answer("Доступ снят")
    log.info("Владелец %s снял роль с %s", user.id, person.id)
