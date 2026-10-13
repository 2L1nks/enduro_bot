"""Единое оформление сущностей для сообщений."""
from __future__ import annotations

from decimal import Decimal

from app.db.models import (
    RU_BOOKING_STATUS,
    RU_EXPERIENCE,
    RU_MOTO_STATUS,
    RU_ROLE,
    Booking,
    BookingParticipant,
    Motorcycle,
    User,
)
from app.config import settings
from app.utils.timeslots import humanize_duration

def local(value):
    """Время в часовом поясе проката.

    PostgreSQL возвращает timestamptz в зоне соединения — обычно UTC.
    Если форматировать его как есть, клиент увидит 07:00 вместо 10:00.
    Поэтому любое время перед выводом проходит через эту функцию.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=settings.tz)
    return value.astimezone(settings.tz)


STATUS_ICON = {
    "pending": "🕓",
    "confirmed": "✅",
    "cancelled": "✖",
    "completed": "🏁",
    "no_show": "🚫",
}

MOTO_ICON = {
    "available": "🟢",
    "maintenance": "🔧",
    "repair": "🔴",
    "retired": "⚫",
}


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """Число с согласованным словом: 1 выезд, 3 выезда, 5 выездов.

    forms — формы для 1, 2 и 5: ("выезд", "выезда", "выездов").
    """
    n_abs = abs(n)
    if n_abs % 10 == 1 and n_abs % 100 != 11:
        word = forms[0]
    elif 2 <= n_abs % 10 <= 4 and not 12 <= n_abs % 100 <= 14:
        word = forms[1]
    else:
        word = forms[2]
    return f"{n} {word}"


def money(value: Decimal | int | float | None) -> str:
    return f"{Decimal(value or 0):,.0f}".replace(",", " ") + " ₽"


def escape(text: str | None) -> str:
    """Экранирование для HTML-разметки Telegram.

    Клиент может написать «<3» в комментарии, и без экранирования
    сообщение просто не отправится.
    """
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def user_label(user: User | None) -> str:
    if user is None:
        return "—"
    name = escape(user.full_name) or f"id{user.id}"
    if user.tg_id is None:
        return f"{name} (без телеграма)"
    if user.username:
        return f"{name} (@{user.username})"
    return name


def user_card(user: User) -> str:
    lines = [f"<b>{user_label(user)}</b>", f"Роль: {RU_ROLE[user.role]}"]
    if user.phone:
        lines.append(f"Телефон: {escape(user.phone)}")
    params = []
    if user.height_cm:
        params.append(f"рост {user.height_cm}")
    if user.weight_kg:
        params.append(f"вес {user.weight_kg}")
    if user.shoe_size:
        params.append(f"нога {user.shoe_size}")
    if user.experience:
        params.append(RU_EXPERIENCE[user.experience])
    if params:
        lines.append("Параметры: " + ", ".join(params))
    if user.notes:
        lines.append(f"Заметка: {escape(user.notes)}")
    if user.is_blocked:
        lines.append("⛔ Заблокирован")
    return "\n".join(lines)


def moto_card(moto: Motorcycle) -> str:
    lines = [
        f"{MOTO_ICON.get(moto.status.value, '•')} <b>{escape(moto.title)}</b>",
        f"Модель: {escape(moto.model) or '—'}",
        f"Госномер: {escape(moto.reg_number) or '—'}",
        f"Категория: {RU_EXPERIENCE[moto.category]}",
        f"Статус: {RU_MOTO_STATUS[moto.status]}",
    ]
    if moto.engine_cc:
        lines.append(f"Двигатель: {moto.engine_cc} см³")
    if moto.seat_height_mm:
        lines.append(f"Высота по седлу: {moto.seat_height_mm} мм")

    limits = []
    if moto.min_rider_height_cm or moto.max_rider_height_cm:
        lo = moto.min_rider_height_cm or "—"
        hi = moto.max_rider_height_cm or "—"
        limits.append(f"рост {lo}–{hi} см")
    if moto.max_rider_weight_kg:
        limits.append(f"вес до {moto.max_rider_weight_kg} кг")
    lines.append("Ограничения: " + (", ".join(limits) if limits else "нет"))

    if not moto.is_active:
        lines.append("⚫ Выведен из парка")
    if moto.notes:
        lines.append(f"\nЗаметка: {escape(moto.notes)}")
    return "\n".join(lines)


def participant_line(p: BookingParticipant) -> str:
    bits = []
    if p.height_cm:
        bits.append(f"{p.height_cm} см")
    if p.weight_kg:
        bits.append(f"{p.weight_kg} кг")
    if p.shoe_size:
        bits.append(f"нога {p.shoe_size}")
    if p.experience:
        bits.append(RU_EXPERIENCE[p.experience])
    params = ", ".join(bits) or "параметры не указаны"

    name = escape(p.full_name) or user_label(p.user)
    if p.tg_username:
        name += f" (@{escape(p.tg_username)})"
    mark = " ⭐" if p.is_leader else ""
    return f"• {name}{mark} — {params}"


def booking_card(booking: Booking, *, for_admin: bool = False) -> str:
    """Карточка записи. Для админа — с участниками и их параметрами."""
    icon = STATUS_ICON.get(booking.status.value, "•")
    lines = [
        f"{icon} <b>Запись №{booking.id}</b>",
        f"Когда: {local(booking.starts_at):%d.%m.%Y %H:%M}"
        f" – {local(booking.ends_at):%H:%M}",
        f"Длительность: {humanize_duration(booking.duration_minutes)}",
        f"Статус: {RU_BOOKING_STATUS[booking.status]}",
    ]
    if booking.meeting_point:
        lines.append(f"Место сбора: {escape(booking.meeting_point)}")
    if booking.total_price:
        lines.append(f"Сумма: {money(booking.total_price)}")
    if booking.comment:
        lines.append(f"Комментарий: {escape(booking.comment)}")
    if booking.cancel_reason:
        lines.append(f"Причина отмены: {escape(booking.cancel_reason)}")

    if for_admin and booking.participants:
        lines.append(f"\n<b>Участники ({len(booking.participants)})</b>")
        lines.extend(participant_line(p) for p in booking.participants)
    elif not for_admin:
        lines.append(f"Участников: {len(booking.participants)}")

    return "\n".join(lines)
