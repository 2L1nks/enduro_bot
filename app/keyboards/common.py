"""Меню и типовые клавиатуры."""
from __future__ import annotations

from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings
from app.db.models import User
from app.keyboards.callbacks import (
    ConfirmCB,
    DurationCB,
    InfoCB,
    MenuCB,
    SeatsCB,
    SlotCB,
)
from app.services.availability import SlotInfo
from app.utils.timeslots import humanize_duration


def main_menu(user: User) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="📅 Записаться", callback_data=MenuCB(action="book").pack()
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="👤 Мои записи", callback_data=MenuCB(action="cabinet").pack()
        ),
        InlineKeyboardButton(
            text="📷 Мои фото", callback_data=MenuCB(action="photos").pack()
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="ℹ️ О прокате", callback_data=MenuCB(action="info").pack()
        )
    )
    if user.role.is_staff:
        kb.row(
            InlineKeyboardButton(
                text="⚙️ Админка", callback_data=MenuCB(action="admin").pack()
            )
        )
    return kb.as_markup()


def durations_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for minutes in settings.rental_durations:
        kb.row(
            InlineKeyboardButton(
                text=humanize_duration(minutes),
                callback_data=DurationCB(minutes=minutes).pack(),
            )
        )
    kb.row(
        InlineKeyboardButton(
            text="⌨️ Другое время", callback_data=DurationCB(minutes=0).pack()
        )
    )
    kb.row(
        InlineKeyboardButton(text="В меню", callback_data=MenuCB(action="home").pack())
    )
    return kb.as_markup()


def slots_keyboard(day: date, slots: list[SlotInfo], needed: int = 1) -> InlineKeyboardMarkup:
    """Времена выезда на выбранный день.

    Занятые слоты показываем, но не даём нажать — так виднее,
    что день загружен, а не что бот сломался.
    """
    from app.keyboards.callbacks import CalendarCB

    kb = InlineKeyboardBuilder()
    for slot in slots:
        if slot.free_count >= needed:
            kb.row(
                InlineKeyboardButton(
                    text=slot.label(needed),
                    callback_data=SlotCB(
                        date=day.strftime("%Y%m%d"),
                        time=slot.window.start.strftime("%H%M"),
                    ).pack(),
                )
            )
        else:
            kb.row(
                InlineKeyboardButton(
                    text=slot.label(needed), callback_data=CalendarCB(action="skip").pack()
                )
            )
    kb.row(
        InlineKeyboardButton(
            text="‹ К календарю",
            callback_data=CalendarCB(action="nav", year=day.year, month=day.month).pack(),
        ),
        InlineKeyboardButton(text="В меню", callback_data=MenuCB(action="home").pack()),
    )
    return kb.as_markup()


def seats_keyboard(max_free: int) -> InlineKeyboardMarkup:
    """Сколько человек поедет. Больше, чем свободно техники, выбрать нельзя."""
    limit = min(max_free, settings.max_group_size)
    kb = InlineKeyboardBuilder()
    buttons = [
        InlineKeyboardButton(text=str(n), callback_data=SeatsCB(count=n).pack())
        for n in range(1, limit + 1)
    ]
    for i in range(0, len(buttons), 4):
        kb.row(*buttons[i : i + 4])
    kb.row(
        InlineKeyboardButton(text="В меню", callback_data=MenuCB(action="home").pack())
    )
    return kb.as_markup()


def confirm_keyboard(yes_text: str = "✅ Отправить заявку") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text=yes_text, callback_data=ConfirmCB(action="yes").pack()),
        InlineKeyboardButton(text="✖ Отмена", callback_data=ConfirmCB(action="no").pack()),
    )
    return kb.as_markup()


def info_sections_keyboard(sections: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """sections: список (id, заголовок)."""
    kb = InlineKeyboardBuilder()
    for section_id, title in sections:
        kb.row(
            InlineKeyboardButton(
                text=title, callback_data=InfoCB(section_id=section_id).pack()
            )
        )
    kb.row(
        InlineKeyboardButton(text="В меню", callback_data=MenuCB(action="home").pack())
    )
    return kb.as_markup()


def back_to_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="В меню", callback_data=MenuCB(action="home").pack())
    )
    return kb.as_markup()
