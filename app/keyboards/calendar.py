"""Инлайн-календарь с подсветкой загрузки парка."""
from __future__ import annotations

import calendar
from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.keyboards.callbacks import CalendarCB, MenuCB
from app.services.availability import DayInfo

WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
MONTHS = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)

#: Пустая ячейка-заполнитель
_SKIP = CalendarCB(action="skip").pack()


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return divmod(index, 12)[0], divmod(index, 12)[1] + 1


def build_calendar(
    year: int, month: int, days: dict[date, DayInfo]
) -> InlineKeyboardMarkup:
    """Сетка месяца.

    Доступные дни помечены кружком: зелёный — техники много,
    жёлтый — осталось мало, красный — занято. Прошедшие дни
    и дни без свободной техники не нажимаются.
    """
    kb = InlineKeyboardBuilder()

    # Заголовок с переключением месяцев
    prev_y, prev_m = _shift_month(year, month, -1)
    next_y, next_m = _shift_month(year, month, +1)
    kb.row(
        InlineKeyboardButton(
            text="‹", callback_data=CalendarCB(action="nav", year=prev_y, month=prev_m).pack()
        ),
        InlineKeyboardButton(
            text=f"{MONTHS[month - 1]} {year}", callback_data=_SKIP
        ),
        InlineKeyboardButton(
            text="›", callback_data=CalendarCB(action="nav", year=next_y, month=next_m).pack()
        ),
    )
    kb.row(*(InlineKeyboardButton(text=d, callback_data=_SKIP) for d in WEEKDAYS))

    today = date.today()
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        row = []
        for day in week:
            if day.month != month:
                row.append(InlineKeyboardButton(text=" ", callback_data=_SKIP))
                continue

            info = days.get(day)
            if day < today or info is None or info.free_count == 0:
                # Прошедшее или занятое — показываем, но не даём нажать
                label = "·" if day < today else f"{day.day}✖"
                row.append(InlineKeyboardButton(text=label, callback_data=_SKIP))
            else:
                row.append(
                    InlineKeyboardButton(
                        text=f"{info.marker}{day.day}",
                        callback_data=CalendarCB(
                            action="day", year=year, month=month, day=day.day
                        ).pack(),
                    )
                )
        kb.row(*row)

    kb.row(
        InlineKeyboardButton(
            text="‹ Назад", callback_data=CalendarCB(action="back").pack()
        ),
        InlineKeyboardButton(
            text="В меню", callback_data=MenuCB(action="home").pack()
        ),
    )
    return kb.as_markup()


def calendar_legend() -> str:
    return (
        "🟢 много техники · 🟡 осталось мало · ✖ занято\n"
        "Выберите дату выезда:"
    )
