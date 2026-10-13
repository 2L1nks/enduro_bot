"""Создание, подтверждение, отмена и перенос записей.

Две вещи определяют устройство модуля.

Первое: заявка (pending) технику НЕ занимает. Резервы появляются
только при подтверждении админом, иначе брошенные заявки блокировали
бы парк.

Второе: техника резервируется по количеству, а не закрепляется
за конкретным человеком. Какой мотоцикл кому достанется, решают
на месте, глядя на людей вживую. Боту достаточно не допустить,
чтобы на одно время записали больше групп, чем есть мотоциклов.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.models import (
    AuditLog,
    Booking,
    BookingParticipant,
    BookingStatus,
    Experience,
    MotorcycleReservation,
    User,
)
from app.exceptions import BookingNotFound, SlotJustTaken, SlotUnavailable
from app.services import availability as avail
from app.utils.logger import get_logger
from app.utils.timeslots import TimeWindow, reservation_window

log = get_logger(__name__)


@dataclass(slots=True)
class ParticipantData:
    """Участник группы.

    user_id необязателен: товарищ главного может не иметь телеграма.
    Тогда остаются имя и физические параметры — их достаточно,
    чтобы на месте подобрать технику.
    """

    full_name: str
    user_id: int | None = None
    tg_username: str | None = None
    height_cm: int | None = None
    weight_kg: int | None = None
    shoe_size: int | None = None
    experience: Experience | None = None
    is_leader: bool = False
    price: Decimal = Decimal("0")


async def _drop_reservations(session: AsyncSession, booking_id: int) -> int:
    """Снимает все резервы записи.

    Явным DELETE, а не обходом связи: обращение к booking.reservations
    запускает ленивую загрузку, а в асинхронном коде она падает
    с MissingGreenlet.
    """
    result = await session.execute(
        delete(MotorcycleReservation).where(
            MotorcycleReservation.booking_id == booking_id
        )
    )
    await session.flush()
    return result.rowcount or 0


async def _reserve(
    session: AsyncSession, booking: Booking, seats: int
) -> list[int]:
    """Занимает нужное число мотоциклов на время выезда.

    Конкретные машины не закрепляются за людьми — важно только,
    чтобы их хватило. Возвращает id занятой техники.
    """
    window = TimeWindow(booking.starts_at, booking.ends_at)
    period = reservation_window(window.start, window.end)

    free = await avail.free_motorcycles(session, window, for_update=True)
    if len(free) < seats:
        raise SlotUnavailable(seats, len(free))

    taken: list[int] = []
    try:
        # Своя точка сохранения: при гонке откатываются только эти
        # резервы, а не вся сессия. Полный rollback здесь ломал
        # вызывающего, если тот сам работал внутри точки сохранения
        # (внесение группы вместе с новыми аккаунтами по ФИО).
        async with session.begin_nested():
            for moto in free[:seats]:
                session.add(
                    MotorcycleReservation(
                        motorcycle_id=moto.id,
                        booking_id=booking.id,
                        period=Range(period.start, period.end, bounds="[)"),
                    )
                )
                taken.append(moto.id)
            await session.flush()
    except IntegrityError as exc:
        if "ex_reservation_no_overlap" in str(exc.orig):
            # Гонка: между подсчётом и вставкой технику заняли.
            # База не дала создать пересекающийся резерв.
            log.warning("Гонка за технику на записи id=%s", booking.id)
            raise SlotJustTaken() from exc
        raise

    return taken


async def _load_booking(session: AsyncSession, booking_id: int) -> Booking:
    """Запись вместе с участниками — без ленивых подгрузок."""
    booking = await session.scalar(
        select(Booking)
        .where(Booking.id == booking_id)
        .options(selectinload(Booking.participants))
    )
    if booking is None:
        raise BookingNotFound()
    return booking


async def create_request(
    session: AsyncSession,
    *,
    user: User,
    window: TimeWindow,
    participants: list[ParticipantData],
    contact_phone: str | None = None,
    comment: str | None = None,
) -> Booking:
    """Заявка от клиента. Технику не резервирует.

    Состав группы сохраняется целиком: админу при подтверждении
    не придётся переспрашивать рост и вес каждого.
    """
    seats = len(participants)
    enough, free = await avail.check_capacity(session, window, seats)
    if not enough:
        raise SlotUnavailable(seats, free)

    price = settings.price_per_person
    booking = Booking(
        created_by_id=user.id,
        starts_at=window.start,
        ends_at=window.end,
        status=BookingStatus.PENDING,
        requested_seats=seats,
        meeting_point=settings.meeting_point,
        contact_phone=contact_phone,
        total_price=price * seats,
        comment=comment,
    )
    session.add(booking)
    await session.flush()

    for data in participants:
        session.add(
            BookingParticipant(
                booking_id=booking.id,
                user_id=data.user_id,
                full_name=data.full_name,
                tg_username=data.tg_username,
                is_leader=data.is_leader,
                height_cm=data.height_cm,
                weight_kg=data.weight_kg,
                shoe_size=data.shoe_size,
                experience=data.experience,
                price=price,
            )
        )
    await session.flush()

    log.info(
        "Заявка id=%s от user=%s на %s, участников %d",
        booking.id,
        user.id,
        window,
        seats,
    )
    return booking


async def confirm(
    session: AsyncSession, booking_id: int, *, actor_id: int | None = None
) -> Booking:
    """Подтверждение записи админом: резерв техники по числу участников.

    Состав группы уже заведён — заявкой клиента или формой админа.
    Здесь появляются строки в motorcycle_reservations, и только здесь.
    """
    booking = await _load_booking(session, booking_id)
    seats = len(booking.participants) or booking.requested_seats

    await _drop_reservations(session, booking_id)
    taken = await _reserve(session, booking, seats)

    booking.status = BookingStatus.CONFIRMED
    booking.requested_seats = seats
    if not booking.meeting_point:
        booking.meeting_point = settings.meeting_point
    booking.total_price = settings.price_per_person * seats
    await session.flush()

    session.add(
        AuditLog(
            actor_id=actor_id,
            action="confirm",
            entity="booking",
            entity_id=booking.id,
            details=f"мест: {seats}, техника: {taken}",
        )
    )
    log.info("Запись id=%s подтверждена, занято мотоциклов %d", booking.id, len(taken))
    return booking


async def cancel(
    session: AsyncSession,
    booking_id: int,
    *,
    reason: str | None = None,
    actor_id: int | None = None,
) -> Booking:
    """Отмена записи. Техника освобождается немедленно.

    Резервы удаляются, а не помечаются — иначе EXCLUDE-констрейнт
    продолжал бы считать мотоцикл занятым.
    """
    booking = await session.get(Booking, booking_id)
    if booking is None:
        raise BookingNotFound()

    await _drop_reservations(session, booking_id)

    booking.status = BookingStatus.CANCELLED
    booking.cancel_reason = reason
    await session.flush()

    session.add(
        AuditLog(
            actor_id=actor_id,
            action="cancel",
            entity="booking",
            entity_id=booking.id,
            details=reason,
        )
    )
    log.info("Запись id=%s отменена (%s)", booking_id, reason or "без причины")
    return booking


async def reschedule(
    session: AsyncSession,
    booking_id: int,
    new_window: TimeWindow,
    *,
    actor_id: int | None = None,
) -> Booking:
    """Перенос записи на другое время с сохранением состава группы.

    Старые резервы снимаются до поиска нового места, иначе запись
    мешала бы сама себе при пересечении интервалов. Если на новое
    время техники не хватает, всё откатывается.
    """
    booking = await _load_booking(session, booking_id)
    old_window = TimeWindow(booking.starts_at, booking.ends_at)
    seats = len(booking.participants) or booking.requested_seats
    was_confirmed = booking.status == BookingStatus.CONFIRMED

    await _drop_reservations(session, booking_id)

    booking.starts_at = new_window.start
    booking.ends_at = new_window.end
    await session.flush()

    if was_confirmed:
        await _reserve(session, booking, seats)

    session.add(
        AuditLog(
            actor_id=actor_id,
            action="reschedule",
            entity="booking",
            entity_id=booking.id,
            details=f"{old_window} -> {new_window}",
        )
    )
    log.info("Запись id=%s перенесена: %s -> %s", booking_id, old_window, new_window)
    return booking
