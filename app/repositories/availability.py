"""Запросы доступности техники.

Здесь только SQL, без бизнес-решений: репозиторий отвечает на вопрос
«какие мотоциклы свободны», а что с этим делать — решает сервис.
"""
from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import (
    Date,
    Integer,
    Select,
    Time,
    and_,
    bindparam,
    func,
    literal_column,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    Booking,
    MaintenancePeriod,
    Motorcycle,
    MotorcycleReservation,
    MotorcycleStatus,
)
from app.utils.timeslots import TimeWindow


def _range(window: TimeWindow) -> Range[datetime]:
    """Полуинтервал [start, end) для tstzrange."""
    return Range(window.start, window.end, bounds="[)")


def _free_motorcycles_query(window: TimeWindow) -> Select:
    """Мотоциклы, свободные на указанное окно.

    Три условия отсеивания:
      1. выведен из парка или сейчас в ремонте — status/is_active
      2. занят другим выездом — motorcycle_reservations
      3. стоит на плановом обслуживании — maintenance_periods
    """
    period = _range(window)

    busy = (
        select(MotorcycleReservation.motorcycle_id)
        .where(MotorcycleReservation.period.op("&&")(period))
        .scalar_subquery()
    )
    on_maintenance = (
        select(MaintenancePeriod.motorcycle_id)
        .where(MaintenancePeriod.period.op("&&")(period))
        .scalar_subquery()
    )

    return (
        select(Motorcycle)
        .where(
            Motorcycle.is_active.is_(True),
            Motorcycle.status == MotorcycleStatus.AVAILABLE,
            Motorcycle.id.not_in(busy),
            Motorcycle.id.not_in(on_maintenance),
        )
        .order_by(Motorcycle.id)
    )


async def free_motorcycles(
    session: AsyncSession, window: TimeWindow, *, for_update: bool = False
) -> list[Motorcycle]:
    """Список свободной техники.

    for_update=True берёт блокировку на найденные строки — используется
    внутри транзакции создания брони, чтобы между подсчётом и вставкой
    никто не увёл последний мотоцикл. Окончательную гарантию всё равно
    даёт EXCLUDE-констрейнт, блокировка лишь снижает число конфликтов.
    """
    query = _free_motorcycles_query(window)
    if for_update:
        query = query.with_for_update()
    return list((await session.scalars(query)).all())


async def count_free(session: AsyncSession, window: TimeWindow) -> int:
    """Сколько мотоциклов свободно — без выгрузки самих строк."""
    inner = _free_motorcycles_query(window).subquery()
    return await session.scalar(select(func.count()).select_from(inner)) or 0


#: Один проход по дням месяца вместо тридцати отдельных запросов —
#: календарь рисуется за один round-trip к базе.
_FREE_BY_DAY_SQL = text(
    """
    WITH days AS (
        SELECT d::date AS day FROM unnest(CAST(:days AS date[])) AS d
    ),
    windows AS (
        SELECT day,
               tstzrange(
                   (day + CAST(:start_time AS time)) AT TIME ZONE :tz,
                   (day + CAST(:start_time AS time)) AT TIME ZONE :tz
                       + make_interval(mins => :occupied_minutes),
                   '[)'
               ) AS w
        FROM days
    )
    SELECT w.day,
           (SELECT count(*) FROM motorcycles m
            WHERE m.is_active
              AND m.status = 'available'
              AND NOT EXISTS (
                  SELECT 1 FROM motorcycle_reservations r
                  WHERE r.motorcycle_id = m.id AND r.period && w.w)
              AND NOT EXISTS (
                  SELECT 1 FROM maintenance_periods mp
                  WHERE mp.motorcycle_id = m.id AND mp.period && w.w)
           ) AS free_count
    FROM windows w
    """
).bindparams(
    # Типы обязательны: asyncpg выводит тип параметра из CAST в SQL
    # и требует ровно его. Без этого список дат и время приедут
    # строками, и драйвер упадёт на кодировании.
    bindparam("days", type_=ARRAY(Date)),
    bindparam("start_time", type_=Time),
    bindparam("occupied_minutes", type_=Integer),
)


async def free_by_day(
    session: AsyncSession,
    days: list[date],
    slot_start: time,
    duration_minutes: int,
) -> dict[date, int]:
    """Сколько техники свободно в каждый из дней — одним запросом.

    Нужно для раскраски календаря на месяц вперёд. Окно занятости
    считается так же, как в reservation_window: длительность выезда
    плюс буфер после него.
    """
    if not days:
        return {}

    rows = await session.execute(
        _FREE_BY_DAY_SQL,
        {
            "days": days,
            # Именно объект time, а не строка: см. bindparams выше
            "start_time": slot_start,
            "tz": settings.timezone,
            "occupied_minutes": duration_minutes + settings.booking_buffer_minutes,
        },
    )
    return {row.day: row.free_count for row in rows}


async def bookings_overlapping(
    session: AsyncSession, motorcycle_id: int, window: TimeWindow
) -> list[Booking]:
    """Записи, которые задевает указанный интервал для этого мотоцикла.

    Используется перед выводом техники на обслуживание: админу надо
    показать, какие выезды пострадают.
    """
    query = (
        select(Booking)
        .join(MotorcycleReservation, MotorcycleReservation.booking_id == Booking.id)
        .where(
            MotorcycleReservation.motorcycle_id == motorcycle_id,
            MotorcycleReservation.period.op("&&")(_range(window)),
        )
        .order_by(Booking.starts_at)
        .distinct()
    )
    return list((await session.scalars(query)).all())


async def motorcycle_load(
    session: AsyncSession, since: datetime, until: datetime
) -> list[tuple[Motorcycle, int, float]]:
    """Загрузка парка: по каждому мотоциклу число выездов и часы.

    Для раздела статистики в админке.
    """
    # Часы считаем по времени выезда, а не по интервалу резерва:
    # резерв включает буфер на осмотр, и 4-часовой выезд давал бы 4,5 часа.
    hours = func.sum(
        func.extract("epoch", Booking.ends_at) - func.extract("epoch", Booking.starts_at)
    ) / 3600.0

    query = (
        select(
            Motorcycle,
            func.count(MotorcycleReservation.id).label("trips"),
            func.coalesce(hours, 0.0).label("hours"),
        )
        .outerjoin(
            MotorcycleReservation,
            and_(
                MotorcycleReservation.motorcycle_id == Motorcycle.id,
                func.lower(MotorcycleReservation.period) >= since,
                func.lower(MotorcycleReservation.period) < until,
            ),
        )
        .outerjoin(Booking, Booking.id == MotorcycleReservation.booking_id)
        .group_by(Motorcycle.id)
        .order_by(literal_column("trips").desc())
    )
    rows = await session.execute(query)
    return [(r[0], r[1], float(r[2])) for r in rows]
