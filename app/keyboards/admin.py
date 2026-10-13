"""Клавиатуры админ-панели."""
from __future__ import annotations

import calendar
from datetime import date
from typing import TYPE_CHECKING

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.models import (
    RU_EXPERIENCE,
    RU_MOTO_STATUS,
    Booking,
    Experience,
    Motorcycle,
    MotorcycleStatus,
    User,
    UserRole,
)
from app.keyboards.calendar import MONTHS, WEEKDAYS
from app.keyboards.callbacks import (
    AdmBookingCB,
    AdmCalCB,
    AdminCB,
    BroadcastCB,
    FleetCB,
    InfoEditCB,
    MenuCB,
    PageCB,
    StaffCB,
)
from app.utils.formatters import local, MOTO_ICON, STATUS_ICON

if TYPE_CHECKING:
    from app.repositories.admin import DayMark

PAGE_SIZE = 8


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def admin_menu(user: User) -> InlineKeyboardMarkup:
    """Разделы админки. Механику показываем только парк."""
    kb = InlineKeyboardBuilder()

    kb.row(
        InlineKeyboardButton(
            text="🏍 Парк", callback_data=AdminCB(action="fleet").pack()
        )
    )
    if user.role.is_admin:
        kb.row(
            InlineKeyboardButton(
                text="📋 Записи", callback_data=AdminCB(action="bookings").pack()
            ),
            InlineKeyboardButton(
                text="📊 Статистика", callback_data=AdminCB(action="stats").pack()
            ),
        )
        kb.row(
            InlineKeyboardButton(
                text="👤 Клиенты", callback_data=AdminCB(action="clients").pack()
            ),
            InlineKeyboardButton(
                text="📢 Рассылка", callback_data=AdminCB(action="broadcast").pack()
            ),
        )
        kb.row(
            InlineKeyboardButton(
                text="📝 Инфо-раздел", callback_data=AdminCB(action="info").pack()
            )
        )
        kb.row(
            InlineKeyboardButton(
                text="📷 Фото клиентам",
                callback_data=AdminCB(action="photos").pack(),
            ),
            InlineKeyboardButton(
                text="📤 Экспорт", callback_data=AdminCB(action="export").pack()
            ),
        )
    if user.role == UserRole.OWNER:
        kb.row(
            InlineKeyboardButton(
                text="👥 Сотрудники", callback_data=AdminCB(action="staff").pack()
            )
        )

    kb.row(
        InlineKeyboardButton(
            text="‹ В главное меню", callback_data=MenuCB(action="home").pack()
        )
    )
    return kb.as_markup()


def back_to_admin(label: str = "‹ В админку") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text=label, callback_data=AdminCB(action="home").pack())
    )
    return kb.as_markup()


def fleet_list(motorcycles: list[Motorcycle], page: int = 0) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    chunk = motorcycles[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    for moto in chunk:
        icon = MOTO_ICON.get(moto.status.value, "•")
        kb.row(
            InlineKeyboardButton(
                text=f"{icon} {moto.title}",
                callback_data=FleetCB(action="view", moto_id=moto.id).pack(),
            )
        )

    _add_pagination(kb, "fleet", page, len(motorcycles))
    kb.row(
        InlineKeyboardButton(
            text="➕ Добавить мотоцикл", callback_data=FleetCB(action="add").pack()
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="‹ В админку", callback_data=AdminCB(action="home").pack()
        )
    )
    return kb.as_markup()


def moto_card_keyboard(
    moto: Motorcycle, periods: list | None = None, *, can_delete: bool = False
) -> InlineKeyboardMarkup:
    """Кнопки карточки техники.

    Набор зависит от состояния: у выведенного мотоцикла вместо
    «вывести» появляется «вернуть», иначе техника осталась бы
    недоступной навсегда.
    """
    kb = InlineKeyboardBuilder()

    if moto.is_active:
        kb.row(
            InlineKeyboardButton(
                text="🔄 Сменить статус",
                callback_data=FleetCB(action="status", moto_id=moto.id).pack(),
            )
        )
        kb.row(
            InlineKeyboardButton(
                text="🔧 На обслуживание",
                callback_data=FleetCB(action="maint", moto_id=moto.id).pack(),
            )
        )
    else:
        kb.row(
            InlineKeyboardButton(
                text="♻️ Вернуть в парк",
                callback_data=FleetCB(action="restore", moto_id=moto.id).pack(),
            )
        )

    # Снятие запланированных простоев
    for period in (periods or [])[:5]:
        kb.row(
            InlineKeyboardButton(
                text=f"✖ Снять простой {local(period.period.lower):%d.%m}–"
                f"{local(period.period.upper):%d.%m}",
                callback_data=FleetCB(
                    action="maint_del", moto_id=period.id
                ).pack(),
            )
        )

    if moto.is_active:
        kb.row(
            InlineKeyboardButton(
                text="⚫ Вывести из парка",
                callback_data=FleetCB(action="retire", moto_id=moto.id).pack(),
            )
        )
    if can_delete:
        kb.row(
            InlineKeyboardButton(
                text="🗑 Удалить совсем",
                callback_data=FleetCB(action="delete", moto_id=moto.id).pack(),
            )
        )

    kb.row(
        InlineKeyboardButton(
            text="‹ К списку", callback_data=AdminCB(action="fleet").pack()
        )
    )
    return kb.as_markup()


def status_choice(moto_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for status in (
        MotorcycleStatus.AVAILABLE,
        MotorcycleStatus.MAINTENANCE,
        MotorcycleStatus.REPAIR,
    ):
        kb.row(
            InlineKeyboardButton(
                text=f"{MOTO_ICON[status.value]} {RU_MOTO_STATUS[status]}",
                callback_data=FleetCB(
                    action="set_status", moto_id=moto_id, value=status.value
                ).pack(),
            )
        )
    kb.row(
        InlineKeyboardButton(
            text="‹ Назад", callback_data=FleetCB(action="view", moto_id=moto_id).pack()
        )
    )
    return kb.as_markup()


def category_choice(prefix_action: str = "cat") -> InlineKeyboardMarkup:
    """Выбор категории техники при заведении."""
    kb = InlineKeyboardBuilder()
    for exp in Experience:
        kb.row(
            InlineKeyboardButton(
                text=RU_EXPERIENCE[exp].capitalize(),
                callback_data=FleetCB(action=prefix_action, value=exp.value).pack(),
            )
        )
    return kb.as_markup()


def admin_calendar(
    year: int,
    month: int,
    marks: "dict[date, DayMark]",
    *,
    pending: int,
    cancelled: int,
) -> InlineKeyboardMarkup:
    """Месяц записей: значок дня показывает самое важное, что в нём есть."""
    kb = InlineKeyboardBuilder()
    skip = AdmCalCB(action="skip").pack()

    prev_y, prev_m = _shift_month(year, month, -1)
    next_y, next_m = _shift_month(year, month, 1)
    kb.row(
        InlineKeyboardButton(
            text="‹", callback_data=AdmCalCB(action="month", y=prev_y, m=prev_m).pack()
        ),
        InlineKeyboardButton(text=f"{MONTHS[month - 1].capitalize()} {year}", callback_data=skip),
        InlineKeyboardButton(
            text="›", callback_data=AdmCalCB(action="month", y=next_y, m=next_m).pack()
        ),
    )
    kb.row(*(InlineKeyboardButton(text=d, callback_data=skip) for d in WEEKDAYS))

    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        row = []
        for day in week:
            if day.month != month:
                row.append(InlineKeyboardButton(text=" ", callback_data=skip))
                continue
            mark = marks.get(day)
            label = f"{mark.marker}{day.day}" if mark else str(day.day)
            row.append(
                InlineKeyboardButton(
                    text=label,
                    callback_data=AdmCalCB(
                        action="day", y=year, m=month, d=day.day
                    ).pack(),
                )
            )
        kb.row(*row)

    if pending:
        kb.row(
            InlineKeyboardButton(
                text=f"🕓 Ждут подтверждения: {pending}",
                callback_data=PageCB(scope="bk", page=0, value="pending").pack(),
            )
        )
    if cancelled:
        kb.row(
            InlineKeyboardButton(
                text=f"🗑 Удалить отменённые за {MONTHS[month - 1]} ({cancelled})",
                callback_data=AdmCalCB(action="purge", y=year, m=month).pack(),
            )
        )
    kb.row(
        InlineKeyboardButton(
            text="➕ Внести группу", callback_data=AdmBookingCB(action="add_group").pack()
        )
    )
    kb.row(
        InlineKeyboardButton(text="‹ В админку", callback_data=AdminCB(action="home").pack())
    )
    return kb.as_markup()


def calendar_day_keyboard(
    day: date, bookings: list[Booking], *, cancelled: int, can_add: bool
) -> InlineKeyboardMarkup:
    """Записи дня: каждая — кнопкой в карточку."""
    kb = InlineKeyboardBuilder()
    for b in bookings[:20]:
        kb.row(
            InlineKeyboardButton(
                text=booking_button_text(b)[:60],
                callback_data=AdmBookingCB(action="view", booking_id=b.id).pack(),
            )
        )
    if can_add:
        kb.row(
            InlineKeyboardButton(
                text="➕ Внести группу на этот день",
                callback_data=AdmBookingCB(
                    action="add_group", value=day.year * 10000 + day.month * 100 + day.day
                ).pack(),
            )
        )
    if cancelled:
        kb.row(
            InlineKeyboardButton(
                text=f"🗑 Удалить отменённые за день ({cancelled})",
                callback_data=AdmCalCB(
                    action="purge", y=day.year, m=day.month, d=day.day
                ).pack(),
            )
        )
    kb.row(
        InlineKeyboardButton(
            text=f"‹ {MONTHS[day.month - 1].capitalize()}",
            callback_data=AdmCalCB(action="month", y=day.year, m=day.month).pack(),
        ),
        InlineKeyboardButton(text="‹ В админку", callback_data=AdminCB(action="home").pack()),
    )
    return kb.as_markup()


def booking_button_text(b: Booking) -> str:
    """«🕓 10:00–12:00 · 3 чел. · Иванов Иван»"""
    icon = STATUS_ICON.get(b.status.value, "•")
    leader = next((p for p in b.participants if p.is_leader), None)
    who = ""
    if leader is not None:
        who = (leader.user.full_name if leader.user else None) or leader.full_name or ""
    span = f"{local(b.starts_at):%H:%M}–{local(b.ends_at):%H:%M}"
    text = f"{icon} {span} · {len(b.participants)} чел."
    return f"{text} · {who}" if who else text


def bookings_list(
    bookings: list[Booking], page: int = 0, scope: str = "pending"
) -> InlineKeyboardMarkup:
    """Список записей (сейчас — только ждущие подтверждения)."""
    kb = InlineKeyboardBuilder()

    chunk = bookings[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    for b in chunk:
        kb.row(
            InlineKeyboardButton(
                text=f"{local(b.starts_at):%d.%m} {booking_button_text(b)}"[:60],
                callback_data=AdmBookingCB(action="view", booking_id=b.id).pack(),
            )
        )

    _add_pagination(kb, "bk", page, len(bookings), value=scope)
    kb.row(
        InlineKeyboardButton(
            text="‹ К календарю", callback_data=AdminCB(action="bookings").pack()
        )
    )
    return kb.as_markup()


def booking_card_keyboard(booking: Booking) -> InlineKeyboardMarkup:
    """Кнопки зависят от статуса: подтверждать уже подтверждённое незачем."""
    from app.db.models import BookingStatus

    kb = InlineKeyboardBuilder()
    bid = booking.id

    if booking.status == BookingStatus.PENDING:
        kb.row(
            InlineKeyboardButton(
                text="✅ Подтвердить",
                callback_data=AdmBookingCB(action="confirm", booking_id=bid).pack(),
            ),
            InlineKeyboardButton(
                text="✖ Отклонить",
                callback_data=AdmBookingCB(action="reject", booking_id=bid).pack(),
            ),
        )
    elif booking.status == BookingStatus.CONFIRMED:
        kb.row(
            InlineKeyboardButton(
                text="🏁 Завершить",
                callback_data=AdmBookingCB(action="complete", booking_id=bid).pack(),
            ),
            InlineKeyboardButton(
                text="🚫 Не приехал",
                callback_data=AdmBookingCB(action="noshow", booking_id=bid).pack(),
            ),
        )
        kb.row(
            InlineKeyboardButton(
                text="🔄 Перенести",
                callback_data=AdmBookingCB(action="move", booking_id=bid).pack(),
            )
        )
        kb.row(
            InlineKeyboardButton(
                text="❌ Отменить запись",
                callback_data=AdmBookingCB(action="cancel", booking_id=bid).pack(),
            )
        )
    elif booking.status == BookingStatus.COMPLETED:
        kb.row(
            InlineKeyboardButton(
                text="💰 Отметить оплату",
                callback_data=AdmBookingCB(action="paid", booking_id=bid).pack(),
            )
        )

    day = local(booking.starts_at)
    kb.row(
        InlineKeyboardButton(
            text=f"‹ К {day:%d.%m}",
            callback_data=AdmCalCB(action="day", y=day.year, m=day.month, d=day.day).pack(),
        ),
        InlineKeyboardButton(
            text="📅 Календарь", callback_data=AdminCB(action="bookings").pack()
        ),
    )
    return kb.as_markup()


def broadcast_targets() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="Всем клиентам",
            callback_data=BroadcastCB(action="target", value="all").pack(),
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="Участникам ближайших выездов",
            callback_data=BroadcastCB(action="target", value="upcoming").pack(),
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="Одному клиенту",
            callback_data=BroadcastCB(action="target", value="one").pack(),
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="‹ В админку", callback_data=AdminCB(action="home").pack()
        )
    )
    return kb.as_markup()


def info_sections_admin(sections: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for section_id, title in sections:
        kb.row(
            InlineKeyboardButton(
                text=f"📝 {title}",
                callback_data=InfoEditCB(action="pick", section_id=section_id).pack(),
            )
        )
    kb.row(
        InlineKeyboardButton(
            text="‹ В админку", callback_data=AdminCB(action="home").pack()
        )
    )
    return kb.as_markup()


def info_edit_keyboard(section_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="✏️ Изменить текст",
            callback_data=InfoEditCB(action="text", section_id=section_id).pack(),
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="🖼 Добавить фото/видео",
            callback_data=InfoEditCB(action="media", section_id=section_id).pack(),
        ),
        InlineKeyboardButton(
            text="🗑 Очистить медиа",
            callback_data=InfoEditCB(action="clear", section_id=section_id).pack(),
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="‹ К разделам", callback_data=AdminCB(action="info").pack()
        )
    )
    return kb.as_markup()


def staff_list(staff: list[User]) -> InlineKeyboardMarkup:
    from app.utils.formatters import user_label

    kb = InlineKeyboardBuilder()
    for person in staff:
        kb.row(
            InlineKeyboardButton(
                text=f"{user_label(person)} — {person.role.value}",
                callback_data=StaffCB(action="remove", user_id=person.id).pack(),
            )
        )
    kb.row(
        InlineKeyboardButton(
            text="➕ Добавить сотрудника", callback_data=StaffCB(action="add").pack()
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="‹ В админку", callback_data=AdminCB(action="home").pack()
        )
    )
    return kb.as_markup()


def role_choice(user_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for role in (UserRole.MECHANIC, UserRole.ADMIN):
        kb.row(
            InlineKeyboardButton(
                text=role.value,
                callback_data=StaffCB(
                    action="set_role", user_id=user_id, value=role.value
                ).pack(),
            )
        )
    kb.row(
        InlineKeyboardButton(
            text="‹ Отмена", callback_data=AdminCB(action="staff").pack()
        )
    )
    return kb.as_markup()


def _add_pagination(
    kb: InlineKeyboardBuilder, scope: str, page: int, total: int, value: str = ""
) -> None:
    """Строка «‹ 2/5 ›», если элементов больше одной страницы."""
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if pages <= 1:
        return

    row = []
    if page > 0:
        row.append(
            InlineKeyboardButton(
                text="‹",
                callback_data=PageCB(scope=scope, page=page - 1, value=value).pack(),
            )
        )
    row.append(
        InlineKeyboardButton(
            text=f"{page + 1}/{pages}",
            callback_data=PageCB(scope="noop", page=0).pack(),
        )
    )
    if page < pages - 1:
        row.append(
            InlineKeyboardButton(
                text="›",
                callback_data=PageCB(scope=scope, page=page + 1, value=value).pack(),
            )
        )
    kb.row(*row)
