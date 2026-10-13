"""Записи: просмотр, подтверждение, смена статуса, замена техники."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    AuditLog,
    Booking,
    MotorcycleReservation,
    BookingStatus,
    User,
)
from app.exceptions import DomainError, ValidationError
from app.keyboards.admin import (
    admin_calendar,
    back_to_admin,
    booking_button_text,
    booking_card_keyboard,
    bookings_list,
    calendar_day_keyboard,
)
from app.keyboards.calendar import MONTHS
from app.keyboards.callbacks import AdmBookingCB, AdmCalCB, AdminCB, PageCB
from app.repositories import admin as admin_repo
from app.services import booking as booking_service
from app.services import notifier
from app.services import payments
from app.utils.formatters import escape, local, booking_card, money, user_label
from app.utils import screen
from app.utils.logger import get_logger
from app.states.admin import MoveForm
from app.utils.timeslots import TimeWindow
from app.utils.validators import parse_datetime

log = get_logger(__name__)
router = Router(name="admin_bookings")

WEEKDAYS_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
MONTHS_GEN = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    since = datetime(year, month, 1, tzinfo=settings.tz)
    until = (
        datetime(year + 1, 1, 1, tzinfo=settings.tz)
        if month == 12
        else datetime(year, month + 1, 1, tzinfo=settings.tz)
    )
    return since, until


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    since = datetime.combine(day, time.min, tzinfo=settings.tz)
    return since, since + timedelta(days=1)


def _valid_day(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


# ---------- Календарь ----------


async def _render_month(
    call: CallbackQuery, session: AsyncSession, year: int, month: int, *, note: str = ""
) -> None:
    since, until = _month_bounds(year, month)
    marks = await admin_repo.month_marks(session, since, until)
    pending = await admin_repo.pending_count(session)
    deletable, _ = await admin_repo.cancelled_for_purge(session, since, until)

    rides = sum(m.pending + m.active + m.done for m in marks.values())
    today = datetime.now(tz=settings.tz).date()
    lines = [f"<b>📋 Записи · {MONTHS[month - 1].capitalize()} {year}</b>"]
    if note:
        lines.append(f"\n{note}")
    lines.append(
        f"\nВыездов в месяце: {rides}"
        + (f" · ждут подтверждения: {pending}" if pending else "")
    )
    lines.append(
        "🕓 ждёт подтверждения · 🟢 подтверждён · 🏁 прошёл · ✖ только отменённые"
    )
    if today.year == year and today.month == month:
        lines.append(f"Сегодня {today.day} {MONTHS_GEN[month - 1]}.")
    lines.append("\nНажмите на дату, чтобы увидеть записи.")

    await screen.show(
        call,
        "\n".join(lines),
        admin_calendar(year, month, marks, pending=pending, cancelled=len(deletable)),
    )


@router.callback_query(AdminCB.filter(F.action == "bookings"))
async def show_bookings(
    call: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    await state.clear()
    now = datetime.now(tz=settings.tz)
    await _render_month(call, session, now.year, now.month)
    await call.answer()


@router.callback_query(AdmCalCB.filter(F.action == "month"))
async def calendar_month(
    call: CallbackQuery, callback_data: AdmCalCB, session: AsyncSession
) -> None:
    if not 1 <= callback_data.m <= 12:
        await call.answer()
        return
    await _render_month(call, session, callback_data.y, callback_data.m)
    await call.answer()


@router.callback_query(AdmCalCB.filter(F.action == "skip"))
async def calendar_skip(call: CallbackQuery) -> None:
    await call.answer()


async def _render_day(
    call: CallbackQuery, session: AsyncSession, day: date, *, note: str = ""
) -> None:
    since, until = _day_bounds(day)
    bookings = await admin_repo.bookings_between(session, since, until)
    deletable, _ = await admin_repo.cancelled_for_purge(session, since, until)
    today = datetime.now(tz=settings.tz).date()

    title = (
        f"<b>📅 {WEEKDAYS_SHORT[day.weekday()].capitalize()}, "
        f"{day.day} {MONTHS_GEN[day.month - 1]} {day.year}</b>"
    )
    lines = [title]
    if note:
        lines.append(f"\n{note}")

    if not bookings:
        lines.append("\nЗаписей на этот день нет.")
    else:
        live = [b for b in bookings if b.status != BookingStatus.CANCELLED]
        people_total = sum(len(b.participants) for b in live)
        lines.append(f"\nЗаписей: {len(bookings)}, человек едет: {people_total}")
        lines.append("Нажмите на запись, чтобы открыть карточку.")

    await screen.show(
        call,
        "\n".join(lines),
        calendar_day_keyboard(
            day, bookings, cancelled=len(deletable), can_add=day >= today
        ),
    )


@router.callback_query(AdmCalCB.filter(F.action == "day"))
async def calendar_day(
    call: CallbackQuery, callback_data: AdmCalCB, session: AsyncSession
) -> None:
    day = _valid_day(callback_data.y, callback_data.m, callback_data.d)
    if day is None:
        await call.answer()
        return
    await _render_day(call, session, day)
    await call.answer()


# ---------- Удаление отменённых ----------


def _purge_bounds(data: AdmCalCB) -> tuple[datetime, datetime, str, date | None] | None:
    """Интервал удаления: день (d>0) или весь месяц (d=0)."""
    if data.d:
        day = _valid_day(data.y, data.m, data.d)
        if day is None:
            return None
        since, until = _day_bounds(day)
        return since, until, f"за {day.day} {MONTHS_GEN[day.month - 1]}", day
    if not 1 <= data.m <= 12:
        return None
    since, until = _month_bounds(data.y, data.m)
    return since, until, f"за {MONTHS[data.m - 1]} {data.y}", None


@router.callback_query(AdmCalCB.filter(F.action == "purge"))
async def purge_ask(
    call: CallbackQuery, callback_data: AdmCalCB, session: AsyncSession
) -> None:
    bounds = _purge_bounds(callback_data)
    if bounds is None:
        await call.answer()
        return
    since, until, period, day = bounds
    deletable, kept = await admin_repo.cancelled_for_purge(session, since, until)

    if not deletable:
        await call.answer("Удалять нечего.", show_alert=True)
        return

    lines = [f"<b>🗑 Удалить отменённые {period}?</b>\n"]
    for b in deletable[:15]:
        lines.append(f"• {local(b.starts_at):%d.%m} {escape(booking_button_text(b))}")
    if len(deletable) > 15:
        lines.append(f"…и ещё {len(deletable) - 15}")
    if kept:
        lines.append(f"\nОставлено: {kept} — по ним записана оплата.")
    lines.append(
        "\nЗаписи исчезнут из календаря и базы насовсем, восстановить "
        "их будет нельзя. В статистике пропадут из «Отменено»."
    )

    back = (
        AdmCalCB(action="day", y=day.year, m=day.month, d=day.day)
        if day
        else AdmCalCB(action="month", y=callback_data.y, m=callback_data.m)
    )
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=f"🗑 Да, удалить ({len(deletable)})",
            callback_data=AdmCalCB(
                action="purge_ok", y=callback_data.y, m=callback_data.m, d=callback_data.d
            ).pack(),
        )
    )
    kb.row(InlineKeyboardButton(text="Отмена", callback_data=back.pack()))
    await screen.show(call, "\n".join(lines), kb.as_markup())
    await call.answer()


@router.callback_query(AdmCalCB.filter(F.action == "purge_ok"))
async def purge_do(
    call: CallbackQuery, callback_data: AdmCalCB, user: User, session: AsyncSession
) -> None:
    bounds = _purge_bounds(callback_data)
    if bounds is None:
        await call.answer()
        return
    since, until, period, day = bounds
    deletable, _ = await admin_repo.cancelled_for_purge(session, since, until)
    ids = [b.id for b in deletable]
    removed = await admin_repo.delete_bookings(session, ids)

    session.add(
        AuditLog(
            actor_id=user.id,
            action="purge_cancelled",
            entity="booking",
            details=f"{period}: удалено {removed}, id={ids}"[:2000],
        )
    )
    log.info("Админ %s удалил отменённые записи %s: %s", user.id, period, ids)

    note = f"🗑 Удалено отменённых записей: {removed}."
    if day:
        await _render_day(call, session, day, note=note)
    else:
        await _render_month(call, session, callback_data.y, callback_data.m, note=note)
    await call.answer("Удалено")


# ---------- Ждущие подтверждения ----------


async def _render_list(
    call: CallbackQuery, session: AsyncSession, scope: str, page: int = 0
) -> None:
    bookings = await admin_repo.bookings_by_scope(session, scope)
    text = (
        f"<b>🕓 Ждут подтверждения</b> — {len(bookings)}"
        if bookings
        else "<b>🕓 Ждут подтверждения</b>\n\nНовых заявок нет."
    )
    await screen.show(call, text, bookings_list(bookings, page=page, scope=scope))


@router.callback_query(PageCB.filter(F.scope == "bk"))
async def bookings_page(
    call: CallbackQuery, callback_data: PageCB, session: AsyncSession
) -> None:
    await _render_list(call, session, "pending", page=callback_data.page)
    await call.answer()


@router.callback_query(AdmBookingCB.filter(F.action == "view"))
async def view_booking(
    call: CallbackQuery, callback_data: AdmBookingCB, session: AsyncSession
) -> None:
    booking = await admin_repo.get_booking(session, callback_data.booking_id)
    if booking is None:
        await call.answer("Запись не найдена.", show_alert=True)
        return

    await screen.show(call, booking_card(booking, for_admin=True), booking_card_keyboard(booking))
    await call.answer()


@router.callback_query(AdmBookingCB.filter(F.action == "confirm"))
async def confirm_booking(
    call: CallbackQuery,
    callback_data: AdmBookingCB,
    user: User,
    session: AsyncSession,
    bot: Bot,
) -> None:
    """Подтверждение заявки: назначаем технику и резервируем."""
    booking = await admin_repo.get_booking(session, callback_data.booking_id)
    if booking is None or booking.status != BookingStatus.PENDING:
        await call.answer("Заявку нельзя подтвердить.", show_alert=True)
        return

    if not booking.participants:
        await call.answer(
            "В заявке нет участников — подтверждать нечего.", show_alert=True
        )
        return

    # Состав группы уже сохранён заявкой клиента или формой админа:
    # пересоздавать его не нужно, достаточно занять технику по числу людей
    short = booking.requested_seats - len(booking.participants)

    try:
        await booking_service.confirm(session, booking.id, actor_id=user.id)
    except DomainError as exc:
        await session.rollback()
        await call.answer(exc.user_message, show_alert=True)
        return

    fresh = await admin_repo.get_booking(session, booking.id)
    note = "\n\n✅ Запись подтверждена, техника закреплена."
    if short > 0:
        note += (
            f"\n\n⚠️ В заявке мест было {short + len(fresh.participants)}, "
            f"а участников заведено {len(fresh.participants)}."
        )
    await screen.show(call, booking_card(fresh, for_admin=True) + note, booking_card_keyboard(fresh))
    await call.answer("Подтверждено")

    await _notify_participants(
        bot,
        fresh,
        f"✅ Ваша запись на {local(fresh.starts_at):%d.%m %H:%M} подтверждена!\n"
        + (f"Место сбора: {fresh.meeting_point}\n" if fresh.meeting_point else "")
        + "Напомним за сутки и за два часа до выезда.",
    )

    # Напоминания ставим в очередь сразу после подтверждения

    await notifier.schedule_booking_reminders(session, fresh)


@router.callback_query(AdmBookingCB.filter(F.action == "reject"))
async def reject_booking(
    call: CallbackQuery,
    callback_data: AdmBookingCB,
    user: User,
    session: AsyncSession,
    bot: Bot,
) -> None:
    booking = await admin_repo.get_booking(session, callback_data.booking_id)
    if booking is None:
        await call.answer("Запись не найдена.", show_alert=True)
        return

    try:
        await booking_service.cancel(
            session, booking.id, reason="отклонено администратором", actor_id=user.id
        )
    except DomainError as exc:
        await call.answer(exc.user_message, show_alert=True)
        return

    await _notify_participants(
        bot,
        booking,
        f"К сожалению, запись на {local(booking.starts_at):%d.%m %H:%M} не подтверждена. "
        "Свяжитесь с нами, чтобы подобрать другое время.",
    )
    await _render_list(call, session, "pending")
    await call.answer("Заявка отклонена")


@router.callback_query(AdmBookingCB.filter(F.action == "cancel"))
async def cancel_booking(
    call: CallbackQuery,
    callback_data: AdmBookingCB,
    user: User,
    session: AsyncSession,
    bot: Bot,
) -> None:
    booking = await admin_repo.get_booking(session, callback_data.booking_id)
    if booking is None:
        await call.answer("Запись не найдена.", show_alert=True)
        return

    try:
        await booking_service.cancel(
            session, booking.id, reason="отменено администратором", actor_id=user.id
        )
    except DomainError as exc:
        await call.answer(exc.user_message, show_alert=True)
        return


    await notifier.cancel_booking_reminders(session, booking.id)
    await _notify_participants(
        bot,
        booking,
        f"Запись на {local(booking.starts_at):%d.%m %H:%M} отменена. "
        "Если это ошибка — напишите нам.",
    )

    fresh = await admin_repo.get_booking(session, booking.id)
    await screen.show(call, booking_card(fresh, for_admin=True) + "\n\n❌ Запись отменена, техника свободна.", booking_card_keyboard(fresh))
    await call.answer("Отменено")


def _not_started(booking: Booking) -> str | None:
    """Почему выезд нельзя закрыть сейчас, или None, если можно.

    «Завершён» и «не пришёл» — это факты о том, что уже случилось.
    Нажатые заранее, они засчитывали клиенту поездку, которой не было,
    снимали напоминания и убирали запись из ближайших.
    """
    if booking.starts_at > datetime.now(tz=settings.tz):
        return (
            f"Выезд ещё не начался — он в {local(booking.starts_at):%d.%m %H:%M}.\n"
            "Отметить можно после начала. Если клиент передумал — "
            "отмените запись."
        )
    return None


@router.callback_query(AdmBookingCB.filter(F.action == "complete"))
async def complete_booking(
    call: CallbackQuery,
    callback_data: AdmBookingCB,
    user: User,
    session: AsyncSession,
) -> None:
    booking = await admin_repo.get_booking(session, callback_data.booking_id)
    if booking is None:
        await call.answer("Запись не найдена.", show_alert=True)
        return

    reason = _not_started(booking)
    if reason:
        await call.answer(reason, show_alert=True)
        return

    booking.status = BookingStatus.COMPLETED
    await session.flush()

    fresh = await admin_repo.get_booking(session, booking.id)
    await screen.show(call, booking_card(fresh, for_admin=True)
        + "\n\n🏁 Выезд завершён. Не забудьте отметить оплату.", booking_card_keyboard(fresh))
    await call.answer("Завершено")


@router.callback_query(AdmBookingCB.filter(F.action == "noshow"))
async def noshow_booking(
    call: CallbackQuery, callback_data: AdmBookingCB, session: AsyncSession
) -> None:
    booking = await admin_repo.get_booking(session, callback_data.booking_id)
    if booking is None:
        await call.answer("Запись не найдена.", show_alert=True)
        return

    reason = _not_started(booking)
    if reason:
        await call.answer(reason, show_alert=True)
        return

    booking.status = BookingStatus.NO_SHOW
    # Техника освобождается: клиент не приехал, держать резерв незачем
    await session.execute(
        delete(MotorcycleReservation).where(
            MotorcycleReservation.booking_id == booking.id
        )
    )
    await session.flush()

    fresh = await admin_repo.get_booking(session, booking.id)
    await screen.show(call, booking_card(fresh, for_admin=True) + "\n\n🚫 Отмечено как неявка.", booking_card_keyboard(fresh))
    await call.answer("Отмечено")


@router.callback_query(AdmBookingCB.filter(F.action == "paid"))
async def payment_screen(
    call: CallbackQuery, callback_data: AdmBookingCB, session: AsyncSession
) -> None:
    """Экран оплаты: кто уже заплатил, сколько осталось.

    Раньше это была одна кнопка со всплывающим сообщением — было
    непонятно, что произошло и за кого. Теперь видно состав, суммы
    и что именно изменится.
    """
    booking = await admin_repo.get_booking(session, callback_data.booking_id)
    if booking is None:
        await call.answer("Запись не найдена.", show_alert=True)
        return

    pay = await payments.state(session, booking)

    lines = [
        f"<b>Оплата · запись №{booking.id}</b>",
        f"{local(booking.starts_at):%d.%m.%Y %H:%M}\n",
        f"Цена с человека: {money(pay.price)}",
        f"Человек в группе: {len(pay.seats)}",
        f"Итого к оплате: <b>{money(pay.total)}</b>\n",
    ]

    for seat in pay.seats:
        participant = seat.participant
        name = escape(participant.full_name) or user_label(participant.user)
        if seat.paid:
            lines.append(f"✅ {name} — оплачено")
        elif seat.payer_id and seat.payer_id != participant.user_id:
            lines.append(f"⬜ {name} — платит ответственный")
        else:
            lines.append(f"⬜ {name} — не оплачено")

    kb = InlineKeyboardBuilder()
    if not pay.settled:
        lines.append(
            f"\nНажмите кнопку ниже, когда получите деньги. "
            f"Отметится {pay.unpaid_seats} из {len(pay.seats)} "
            f"на сумму {money(pay.due)}."
        )
        kb.row(
            InlineKeyboardButton(
                text=f"💰 Отметить оплату · {money(pay.due)}",
                callback_data=AdmBookingCB(
                    action="paid_ok", booking_id=booking.id
                ).pack(),
            )
        )
    else:
        lines.append("\nВся группа рассчиталась.")

    kb.row(
        InlineKeyboardButton(
            text="‹ К записи",
            callback_data=AdmBookingCB(action="view", booking_id=booking.id).pack(),
        )
    )

    await screen.show(call, "\n".join(lines), kb.as_markup())
    await call.answer()


@router.callback_query(AdmBookingCB.filter(F.action == "paid_ok"))
async def mark_paid(
    call: CallbackQuery,
    callback_data: AdmBookingCB,
    user: User,
    session: AsyncSession,
) -> None:
    """Отмечает оплату за все неоплаченные места группы.

    Считается по местам, а не по людям: ответственный, который платит
    за трёх безаккаунтных товарищей, получает три строки оплаты, а не
    одну. Повторное нажатие ничего не задваивает.
    """
    booking = await admin_repo.get_booking(session, callback_data.booking_id)
    if booking is None:
        await call.answer("Запись не найдена.", show_alert=True)
        return

    added = await payments.mark_all(session, booking, actor_id=user.id)

    log.info("Оплата по записи id=%s: %s", booking.id, added)
    await call.answer(
        f"Отмечено: {money(added)}" if added else "Всё уже оплачено"
    )
    await payment_screen(call, callback_data, session)


# ---------- Перенос записи ----------


@router.callback_query(AdmBookingCB.filter(F.action == "move"))
async def move_ask(
    call: CallbackQuery, callback_data: AdmBookingCB, state: FSMContext
) -> None:
    await state.update_data(booking_id=callback_data.booking_id)
    await state.set_state(MoveForm.datetime)
    await screen.show(call, "<b>Перенос записи</b>\n\n"
        "Новые дата и время начала:\n"
        "<code>15.11.2026 10:00</code>\n\n"
        "Длительность и состав группы сохранятся. "
        "Техника подберётся заново — если на новое время её не хватит, "
        "запись останется на прежнем месте.", back_to_admin("✖ Отмена"))
    await call.answer()


@router.message(MoveForm.datetime)
async def move_apply(
    message: Message,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    data = await state.get_data()
    booking = await admin_repo.get_booking(session, data.get("booking_id", 0))
    if booking is None:
        await message.answer("Запись не найдена.", reply_markup=back_to_admin())
        await state.clear()
        return

    try:
        start = parse_datetime(message.text or "")
    except ValidationError as exc:
        await message.answer(exc.user_message)
        return

    duration = booking.duration_minutes
    new_window = TimeWindow(start, start + timedelta(minutes=duration))
    old_text = f"{local(booking.starts_at):%d.%m %H:%M}"

    try:
        await booking_service.reschedule(
            session, booking.id, new_window, actor_id=user.id
        )
    except DomainError as exc:
        await message.answer(
            f"Перенести не удалось.\n\n{exc.user_message}",
            reply_markup=back_to_admin(),
        )
        await state.clear()
        return

    await state.clear()
    fresh = await admin_repo.get_booking(session, booking.id)

    # Старые напоминания указывали бы на прежнее время

    await notifier.schedule_booking_reminders(session, fresh)

    await message.answer(
        booking_card(fresh, for_admin=True)
        + f"\n\n🔄 Перенесено с {old_text}. Напоминания пересозданы.",
        reply_markup=booking_card_keyboard(fresh),
    )
    await _notify_participants(
        bot,
        fresh,
        f"🔄 Ваш выезд перенесён: было {old_text}, "
        f"стало {local(fresh.starts_at):%d.%m %H:%M}.",
    )


async def _notify_participants(bot: Bot, booking: Booking, text: str) -> None:
    """Прямое уведомление участников.

    Пишем сразу, а не через очередь: сообщение привязано к действию
    админа, который ждёт результата здесь и сейчас.
    """
    for participant in booking.participants:
        if participant.user is None or not participant.user.has_telegram:
            continue
        try:
            await notifier.push(bot, participant.user.tg_id, text)
        except Exception as exc:  # noqa: BLE001 — один недоступный чат не ломает остальные
            log.warning(
                "Не удалось уведомить tg_id=%s: %s", participant.user.tg_id, exc
            )
