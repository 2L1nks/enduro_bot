"""Запросы по парку техники."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    MaintenancePeriod,
    Motorcycle,
    MotorcycleReservation,
    MotorcycleStatus,
)


async def get(session: AsyncSession, motorcycle_id: int) -> Motorcycle | None:
    return await session.get(Motorcycle, motorcycle_id)


async def list_all(
    session: AsyncSession, *, include_retired: bool = False
) -> list[Motorcycle]:
    query = select(Motorcycle).order_by(Motorcycle.title)
    if not include_retired:
        query = query.where(Motorcycle.is_active.is_(True))
    return list((await session.scalars(query)).all())


async def count_by_status(session: AsyncSession) -> dict[MotorcycleStatus, int]:
    rows = await session.execute(
        select(Motorcycle.status, func.count())
        .where(Motorcycle.is_active.is_(True))
        .group_by(Motorcycle.status)
    )
    return {status: count for status, count in rows}


async def has_history(session: AsyncSession, motorcycle_id: int) -> bool:
    """Был ли мотоцикл хоть раз в выезде.

    Технику с историей не удаляем физически — иначе развалится
    статистика и прошлые записи. Вместо удаления снимаем is_active.
    """
    exists = await session.scalar(
        select(func.count())
        .select_from(MotorcycleReservation)
        .where(MotorcycleReservation.motorcycle_id == motorcycle_id)
    )
    return bool(exists)


async def maintenance_periods(
    session: AsyncSession, motorcycle_id: int, *, since: datetime | None = None
) -> list[MaintenancePeriod]:
    query = (
        select(MaintenancePeriod)
        .where(MaintenancePeriod.motorcycle_id == motorcycle_id)
        .order_by(func.lower(MaintenancePeriod.period))
    )
    if since is not None:
        query = query.where(func.upper(MaintenancePeriod.period) >= since)
    return list((await session.scalars(query)).all())


async def add_maintenance(
    session: AsyncSession,
    motorcycle_id: int,
    period: Range[datetime],
    reason: str,
    created_by_id: int | None,
) -> MaintenancePeriod:
    item = MaintenancePeriod(
        motorcycle_id=motorcycle_id,
        period=period,
        reason=reason,
        created_by_id=created_by_id,
    )
    session.add(item)
    await session.flush()
    return item
