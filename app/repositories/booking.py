"""Запросы по записям пользователя."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.models import (
    Booking,
    BookingParticipant,
    BookingStatus,
    Payment,
    PaymentStatus,
)


#: Статусы, при которых выезд ещё впереди
ACTIVE_STATUSES = (BookingStatus.PENDING, BookingStatus.CONFIRMED)


def _active(now: datetime):
    """Запись ещё актуальна: ждёт подтверждения или подтверждена и не закончилась.

    Единственное определение «ближайших» на весь кабинет. История —
    строго всё остальное, поэтому каждая запись попадает ровно в один
    список. Раньше история бралась по дате начала, а ближайшие — по дате
    и статусу, и запись, завершённая админом до своей даты, выпадала
    из обоих списков, хотя в счётчике поездок уже была.
    """
    return and_(
        Booking.status.in_(ACTIVE_STATUSES),
        Booking.ends_at >= now,
    )


async def user_bookings(
    session: AsyncSession, user_id: int, *, upcoming: bool | None = None
) -> list[Booking]:
    """Записи пользователя.

    upcoming=True — актуальные, False — история (всё остальное),
    None — все. Пользователь видит исключительно свои записи:
    фильтр по user_id стоит в запросе, а не в коде выше.
    """
    now = datetime.now(tz=settings.tz)

    query = (
        select(Booking)
        .join(BookingParticipant, BookingParticipant.booking_id == Booking.id)
        .where(BookingParticipant.user_id == user_id)
        .options(selectinload(Booking.participants))
        .distinct()
    )
    if upcoming is True:
        query = query.where(_active(now)).order_by(Booking.starts_at)
    elif upcoming is False:
        query = query.where(not_(_active(now))).order_by(Booking.starts_at.desc())
    else:
        query = query.order_by(Booking.starts_at.desc())

    return list((await session.scalars(query)).all())


async def get_for_user(
    session: AsyncSession, booking_id: int, user_id: int
) -> Booking | None:
    """Запись по id — только если пользователь в ней участвует.

    Защита от подбора идентификаторов: проверка принадлежности
    встроена в запрос, обойти её нельзя.
    """
    return await session.scalar(
        select(Booking)
        .join(BookingParticipant, BookingParticipant.booking_id == Booking.id)
        .where(Booking.id == booking_id, BookingParticipant.user_id == user_id)
        .options(selectinload(Booking.participants))
    )


async def user_stats(session: AsyncSession, user_id: int) -> dict:
    """Статистика пользователя: визиты, часы, потрачено."""
    now = datetime.now(tz=settings.tz)

    visits, minutes = (
        await session.execute(
            select(
                func.count(Booking.id),
                func.coalesce(
                    func.sum(
                        func.extract("epoch", Booking.ends_at)
                        - func.extract("epoch", Booking.starts_at)
                    )
                    / 60.0,
                    0,
                ),
            )
            .join(BookingParticipant, BookingParticipant.booking_id == Booking.id)
            .where(
                BookingParticipant.user_id == user_id,
                Booking.status == BookingStatus.COMPLETED,
            )
        )
    ).one()

    spent = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.user_id == user_id, Payment.status == PaymentStatus.PAID
        )
    )

    upcoming = await session.scalar(
        select(func.count(func.distinct(Booking.id)))
        .join(BookingParticipant, BookingParticipant.booking_id == Booking.id)
        .where(BookingParticipant.user_id == user_id, _active(now))
    )

    return {
        "visits": visits or 0,
        "minutes": int(minutes or 0),
        "spent": Decimal(spent or 0),
        "upcoming": upcoming or 0,
    }
