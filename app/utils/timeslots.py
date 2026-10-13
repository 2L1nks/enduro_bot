"""Работа со временем: окна резервации и сетка слотов.

КОНВЕНЦИЯ БУФЕРА — важно, читать перед правками.

Буфер нужен на осмотр и заправку ПОСЛЕ выезда, поэтому интервал
резервации расширяется только вправо:

    выезд 10:00-14:00 при буфере 30 мин  ->  резерв [10:00, 14:30)

Если бы мы расширяли в обе стороны, а потом так же в обе стороны
искали пересечения, буфер считался бы дважды и между выездами
требовался бы разрыв в 60 минут вместо 30.

При такой конвенции разрыв между соседними выездами ровно
один буфер, а проверка пересечения тривиальна: два резерва
конфликтуют, если их интервалы пересекаются.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.config import settings


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """Полуинтервал [start, end)."""

    start: datetime
    end: datetime

    def overlaps(self, other: "TimeWindow") -> bool:
        return self.start < other.end and other.start < self.end

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def __str__(self) -> str:
        if self.start.date() == self.end.date():
            return f"{self.start:%d.%m %H:%M}-{self.end:%H:%M}"
        return f"{self.start:%d.%m %H:%M} - {self.end:%d.%m %H:%M}"


def reservation_window(starts_at: datetime, ends_at: datetime) -> TimeWindow:
    """Интервал, на который техника реально занята: выезд плюс буфер после."""
    buffer = timedelta(minutes=settings.booking_buffer_minutes)
    return TimeWindow(starts_at, ends_at + buffer)


def day_bounds(day: date) -> TimeWindow:
    """Рабочие часы конкретного дня с учётом таймзоны."""
    start = datetime.combine(day, settings.workday_start, tzinfo=settings.tz)
    end = datetime.combine(day, settings.workday_end, tzinfo=settings.tz)
    return TimeWindow(start, end)


def generate_slots(
    day: date, duration_minutes: int, step_minutes: int = 60
) -> list[TimeWindow]:
    """Сетка возможных выездов на день.

    Слот попадает в сетку, только если выезд целиком помещается
    в рабочие часы. Буфер здесь не учитывается: он относится
    к занятости техники, а не к расписанию проката — последний
    выезд может заканчиваться ровно в конце рабочего дня.
    """
    bounds = day_bounds(day)
    duration = timedelta(minutes=duration_minutes)
    step = timedelta(minutes=step_minutes)

    slots: list[TimeWindow] = []
    cursor = bounds.start
    while cursor + duration <= bounds.end:
        slots.append(TimeWindow(cursor, cursor + duration))
        cursor += step
    return slots


def is_past(moment: datetime) -> bool:
    return moment < datetime.now(tz=settings.tz)


def humanize_duration(minutes: int) -> str:
    """4 -> '4 мин', 120 -> '2 ч', 150 -> '2 ч 30 мин'."""
    hours, mins = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} ч")
    if mins:
        parts.append(f"{mins} мин")
    return " ".join(parts) or "0 мин"
