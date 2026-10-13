"""Сервис доступности: сколько техники свободно и что можно предложить."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Motorcycle
from app.repositories import availability as repo
from app.utils.timeslots import TimeWindow, generate_slots, is_past, reservation_window


@dataclass(slots=True)
class SlotInfo:
    """Слот для показа пользователю.

    Пользователь видит только количество — номера и статусы техники
    остаются внутренней информацией.
    """

    window: TimeWindow
    free_count: int

    @property
    def is_available(self) -> bool:
        return self.free_count > 0

    def label(self, needed: int = 1) -> str:
        if self.free_count == 0:
            return f"{self.window.start:%H:%M} — занято"
        if self.free_count < needed:
            return f"{self.window.start:%H:%M} — осталось {self.free_count}"
        return f"{self.window.start:%H:%M} — свободно {self.free_count}"


@dataclass(slots=True)
class DayInfo:
    """День календаря."""

    day: date
    free_count: int

    @property
    def marker(self) -> str:
        """Кружок для инлайн-календаря."""
        if self.free_count == 0:
            return "🔴"
        if self.free_count <= 2:
            return "🟡"
        return "🟢"


async def free_motorcycles(
    session: AsyncSession, window: TimeWindow, *, for_update: bool = False
) -> list[Motorcycle]:
    """Свободная техника на интервал выезда.

    Буфер добавляется здесь — вызывающий код передаёт время выезда
    как его видит пользователь, а не расширенный интервал.
    """
    return await repo.free_motorcycles(
        session, reservation_window(window.start, window.end), for_update=for_update
    )


async def count_free(session: AsyncSession, window: TimeWindow) -> int:
    return await repo.count_free(
        session, reservation_window(window.start, window.end)
    )


async def day_slots(
    session: AsyncSession,
    day: date,
    duration_minutes: int,
    *,
    step_minutes: int = 60,
    hide_past: bool = True,
) -> list[SlotInfo]:
    """Все слоты дня с количеством свободной техники."""
    result: list[SlotInfo] = []
    for slot in generate_slots(day, duration_minutes, step_minutes):
        if hide_past and is_past(slot.start):
            continue
        result.append(SlotInfo(slot, await count_free(session, slot)))
    return result


async def month_overview(
    session: AsyncSession,
    year: int,
    month: int,
    duration_minutes: int,
    *,
    hide_past: bool = True,
) -> dict[date, DayInfo]:
    """Загрузка парка по дням месяца — для раскраски календаря.

    Считается по типовому слоту, начинающемуся в начале рабочего дня:
    точное число мест пользователь увидит, открыв конкретный день.
    """
    first = date(year, month, 1)
    next_month = date(year + (month == 12), month % 12 + 1, 1)
    days = [first + timedelta(days=i) for i in range((next_month - first).days)]

    today = date.today()
    if hide_past:
        days = [d for d in days if d >= today]
    if not days:
        return {}

    counts = await repo.free_by_day(
        session, days, settings.workday_start, duration_minutes
    )
    return {d: DayInfo(d, counts.get(d, 0)) for d in days}


async def check_capacity(
    session: AsyncSession, window: TimeWindow, needed: int
) -> tuple[bool, int]:
    """Хватает ли техники на группу. Возвращает (хватает, сколько свободно)."""
    free = await count_free(session, window)
    return free >= needed, free
