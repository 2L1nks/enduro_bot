"""Личный кабинет: мои записи и статистика."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RU_BOOKING_STATUS, User
from app.exceptions import DomainError
from app.keyboards.callbacks import BookingCB, MenuCB
from app.keyboards.common import back_to_menu
from app.repositories import booking as booking_repo
from app.services import booking as booking_service
from app.services import notifier
from app.utils.chat import drop_tracked
from app.utils.formatters import local, user_label
from app.utils import screen
from app.utils.logger import get_logger
from app.utils.timeslots import humanize_duration

log = get_logger(__name__)
router = Router(name="cabinet")


@router.callback_query(MenuCB.filter(F.action == "cabinet"))
async def show_cabinet(
    call: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    # Снимки предыдущего раздела не должны висеть над кабинетом
    await drop_tracked(bot, call.message.chat.id)
    stats = await booking_repo.user_stats(session, user.id)
    upcoming = await booking_repo.user_bookings(session, user.id, upcoming=True)

    text = [
        "<b>Личный кабинет</b>\n",
        f"Поездок совершено: {stats['visits']}",
        f"Время в выездах: {humanize_duration(stats['minutes'])}",
        f"Потрачено: {stats['spent']:.0f} ₽",
        "",
    ]

    kb = InlineKeyboardBuilder()
    if upcoming:
        text.append("<b>Ближайшие записи</b>")
        for b in upcoming[:5]:
            status = RU_BOOKING_STATUS[b.status]
            text.append(f"• {local(b.starts_at):%d.%m %H:%M} — {status}")
            kb.row(
                InlineKeyboardButton(
                    text=f"{local(b.starts_at):%d.%m %H:%M} — подробнее",
                    callback_data=BookingCB(action="view", booking_id=b.id).pack(),
                )
            )
    else:
        text.append("Предстоящих записей нет.")

    kb.row(
        InlineKeyboardButton(
            text="📜 История", callback_data=MenuCB(action="history").pack()
        )
    )
    kb.row(
        InlineKeyboardButton(text="В меню", callback_data=MenuCB(action="home").pack())
    )

    await screen.show(call, "\n".join(text), kb.as_markup())
    await call.answer()


@router.callback_query(MenuCB.filter(F.action == "history"))
async def show_history(
    call: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    await drop_tracked(bot, call.message.chat.id)
    past = await booking_repo.user_bookings(session, user.id, upcoming=False)

    if not past:
        await screen.show(call, "Прошедших поездок пока нет.", back_to_menu())
        await call.answer()
        return

    lines = ["<b>История поездок</b>\n"]
    for b in past[:20]:
        lines.append(
            f"• {local(b.starts_at):%d.%m.%Y %H:%M} — "
            f"{humanize_duration(b.duration_minutes)}, "
            f"{RU_BOOKING_STATUS[b.status]}"
        )
    if len(past) > 20:
        lines.append(f"\n…и ещё {len(past) - 20}")

    await screen.show(call, "\n".join(lines), back_to_menu())
    await call.answer()


@router.callback_query(BookingCB.filter(F.action == "view"))
async def view_booking(
    call: CallbackQuery,
    callback_data: BookingCB,
    user: User,
    session: AsyncSession,
) -> None:
    # Принадлежность проверяется запросом, а не доверием к callback_data
    booking = await booking_repo.get_for_user(
        session, callback_data.booking_id, user.id
    )
    if booking is None:
        await call.answer("Запись не найдена.", show_alert=True)
        return

    mine = next((p for p in booking.participants if p.user_id == user.id), None)

    lines = [
        f"<b>Запись №{booking.id}</b>\n",
        f"Когда: {local(booking.starts_at):%d.%m.%Y %H:%M}",
        f"Длительность: {humanize_duration(booking.duration_minutes)}",
        f"Статус: {RU_BOOKING_STATUS[booking.status]}",
        f"Участников: {len(booking.participants)}",
    ]
    if booking.meeting_point:
        lines.append(f"Место сбора: {booking.meeting_point}")
    if mine and mine.motorcycle:
        lines.append(f"Ваш мотоцикл: {mine.motorcycle.title}")
    if mine and mine.price:
        lines.append(f"Стоимость: {mine.price:.0f} ₽")

    kb = InlineKeyboardBuilder()
    if booking.is_active:
        kb.row(
            InlineKeyboardButton(
                text="❌ Отменить запись",
                callback_data=BookingCB(
                    action="cancel", booking_id=booking.id
                ).pack(),
            )
        )
    kb.row(
        InlineKeyboardButton(
            text="‹ Назад", callback_data=MenuCB(action="cabinet").pack()
        )
    )

    await screen.show(call, "\n".join(lines), kb.as_markup())
    await call.answer()


@router.callback_query(BookingCB.filter(F.action == "cancel"))
async def ask_cancel(call: CallbackQuery, callback_data: BookingCB, user: User,
                     session: AsyncSession) -> None:
    booking = await booking_repo.get_for_user(
        session, callback_data.booking_id, user.id
    )
    if booking is None or not booking.is_active:
        await call.answer("Запись нельзя отменить.", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="Да, отменить",
            callback_data=BookingCB(
                action="do_cancel", booking_id=booking.id
            ).pack(),
        ),
        InlineKeyboardButton(
            text="Нет",
            callback_data=BookingCB(action="view", booking_id=booking.id).pack(),
        ),
    )
    await screen.show(
        call,
        f"Отменить запись на {local(booking.starts_at):%d.%m %H:%M}?\n\n"
        "Мотоцикл освободится, и его сможет занять кто-то другой.",
        kb.as_markup(),
    )
    await call.answer()


@router.callback_query(BookingCB.filter(F.action == "do_cancel"))
async def do_cancel(
    call: CallbackQuery,
    callback_data: BookingCB,
    user: User,
    session: AsyncSession,
    bot: Bot,
) -> None:
    booking = await booking_repo.get_for_user(
        session, callback_data.booking_id, user.id
    )
    if booking is None or not booking.is_active:
        await call.answer("Запись нельзя отменить.", show_alert=True)
        return

    try:
        await booking_service.cancel(
            session, booking.id, reason="отменено клиентом", actor_id=user.id
        )
    except DomainError as exc:
        await call.answer(exc.user_message, show_alert=True)
        return

    log.info("Пользователь %s отменил запись id=%s", user.id, booking.id)

    # Снимаем напоминания и сообщаем админам — техника освободилась
    await notifier.cancel_booking_reminders(session, booking.id)
    await notifier.notify_staff(
        bot,
        session,
        f"❌ <b>Клиент отменил запись №{booking.id}</b>\n"
        f"Кто: {user_label(user)}\n"
        f"Когда был выезд: {local(booking.starts_at):%d.%m %H:%M}\n"
        f"Техника освобождена.",
    )

    await screen.show(call, "Запись отменена. Техника освобождена.", back_to_menu())
    await call.answer("Отменено")
