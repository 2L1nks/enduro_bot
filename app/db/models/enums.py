"""Перечисления домена. Значения в БД — строки, читаемые глазами в psql."""
from __future__ import annotations

import enum


class UserRole(str, enum.Enum):
    """Роль в системе. MECHANIC заложен на будущее: он будет вести ремонты."""

    USER = "user"
    MECHANIC = "mechanic"
    ADMIN = "admin"
    OWNER = "owner"

    @property
    def is_staff(self) -> bool:
        return self in (UserRole.MECHANIC, UserRole.ADMIN, UserRole.OWNER)

    @property
    def is_admin(self) -> bool:
        return self in (UserRole.ADMIN, UserRole.OWNER)


class Experience(str, enum.Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class MotorcycleStatus(str, enum.Enum):
    """Текущий статус техники.

    MAINTENANCE/REPAIR здесь — «прямо сейчас», плановые простои живут
    отдельными интервалами в maintenance_periods.
    """

    AVAILABLE = "available"
    MAINTENANCE = "maintenance"
    REPAIR = "repair"
    RETIRED = "retired"


class BookingStatus(str, enum.Enum):
    """PENDING — заявка от клиента, техника ещё НЕ зарезервирована.
    CONFIRMED — админ подтвердил, в motorcycle_reservations появились строки.
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


class PaymentStatus(str, enum.Enum):
    UNPAID = "unpaid"
    PAID = "paid"
    REFUNDED = "refunded"


class NotificationKind(str, enum.Enum):
    REMINDER_24H = "reminder_24h"
    REMINDER_2H = "reminder_2h"
    BOOKING_CONFIRMED = "booking_confirmed"
    BOOKING_CANCELLED = "booking_cancelled"
    BOOKING_MOVED = "booking_moved"
    MANUAL = "manual"


class NotificationStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Человекочитаемые подписи для интерфейса
RU_EXPERIENCE = {
    Experience.BEGINNER: "новичок",
    Experience.INTERMEDIATE: "любитель",
    Experience.ADVANCED: "опытный",
}

RU_MOTO_STATUS = {
    MotorcycleStatus.AVAILABLE: "доступен",
    MotorcycleStatus.MAINTENANCE: "на обслуживании",
    MotorcycleStatus.REPAIR: "в ремонте",
    MotorcycleStatus.RETIRED: "выведен из парка",
}

RU_BOOKING_STATUS = {
    BookingStatus.PENDING: "заявка",
    BookingStatus.CONFIRMED: "подтверждена",
    BookingStatus.CANCELLED: "отменена",
    BookingStatus.COMPLETED: "завершена",
    BookingStatus.NO_SHOW: "не приехал",
}

RU_ROLE = {
    UserRole.USER: "клиент",
    UserRole.MECHANIC: "механик",
    UserRole.ADMIN: "администратор",
    UserRole.OWNER: "владелец",
}
