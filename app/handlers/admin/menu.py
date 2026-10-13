"""Главное меню админки."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RU_ROLE, MotorcycleStatus, User
from app.keyboards.admin import admin_menu
from app.keyboards.callbacks import AdminCB, MenuCB
from app.repositories import admin as admin_repo
from app.repositories import motorcycle as moto_repo
from app.services import notifier
from app.utils import screen
from app.utils.chat import drop_message

router = Router(name="admin_menu")


async def _overview(session: AsyncSession, user: User) -> str:
    """Короткая сводка: что требует внимания прямо сейчас."""
    pending = await admin_repo.pending_count(session)
    today = await admin_repo.bookings_by_scope(session, "today")
    by_status = await moto_repo.count_by_status(session)

    available = by_status.get(MotorcycleStatus.AVAILABLE, 0)
    total = sum(by_status.values())

    lines = [
        f"<b>Админ-панель</b> · {RU_ROLE[user.role]}\n",
        f"Выездов сегодня: {len(today)}",
        f"Техника: {available} из {total} доступно",
    ]
    if pending:
        lines.append(f"\n🕓 <b>Новых заявок: {pending}</b> — ждут подтверждения")
    return "\n".join(lines)


@router.message(Command("admin"))
async def cmd_admin(
    message: Message,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    await notifier.clear_pending_alerts(bot, message.from_user.id)
    await screen.replace(
        bot, message.chat.id, await _overview(session, user), admin_menu(user)
    )
    await drop_message(bot, message.chat.id, message.message_id)
    await state.set_state(None)


@router.callback_query(MenuCB.filter(F.action == "admin"))
@router.callback_query(AdminCB.filter(F.action == "home"))
async def admin_home(
    call: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    # Уведомления о заявках висели до этого момента — админ пришёл, можно убрать
    await notifier.clear_pending_alerts(bot, call.from_user.id)
    await screen.show(call, await _overview(session, user), admin_menu(user))
    await state.set_state(None)
    await call.answer()
